"""Scheduler for subscription updates."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.error import BadRequest

from app.mcp_client import MCPManager
from app.planner import GeminiPlanner, TokenSummary
from app.store.db import Database, Subscription, TokenWatch
from app.store.repository import Repository
from app.utils.formatting import (
    NOT_FINANCIAL_ADVICE,
    append_not_financial_advice,
    escape_markdown,
    format_transaction,
    join_messages,
)
from app.utils.logging import get_logger
from app.utils.routers import RouterInfo, resolve_router

logger = get_logger(__name__)


class SubscriptionService:
    """Poll subscribed routers on a cadence and push updates to Telegram users."""

    MAX_WATCH_TOKENS = 5
    MAX_WATCH_TXNS = 4

    def __init__(
        self,
        scheduler: AsyncIOScheduler,
        db: Database,
        mcp_manager: MCPManager,
        planner: GeminiPlanner,
        routers: Dict[str, Dict[str, str]],
        network: str,
        bot,
        interval_minutes: int,
        override_chat_id: int | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.db = db
        self.mcp = mcp_manager
        self.planner = planner
        self.routers = routers
        self.network = network
        self.bot = bot
        self.interval_minutes = interval_minutes
        self.override_chat_id = override_chat_id
        self._job = None

    def start(self) -> None:
        if self.scheduler.running:
            return
        self.scheduler.add_job(
            self._run_cycle, "interval", minutes=self.interval_minutes
        )
        self.scheduler.start()
        logger.info("subscription_scheduler_started", interval=self.interval_minutes)

    async def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("subscription_scheduler_stopped")

    async def _run_cycle(self) -> None:
        async with self.db.session() as session:
            repo = Repository(session)
            subscriptions: Iterable[Subscription] = await repo.all_subscriptions()

            for subscription in subscriptions:
                try:
                    await self._process_subscription(subscription, repo)
                except (
                    Exception
                ) as exc:  # pragma: no cover - background errors are logged
                    logger.error(
                        "subscription_cycle_error",
                        router_key=subscription.router_key,
                        error=str(exc),
                    )
            try:
                await self._process_watchlists(repo)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.error("watchlist_cycle_error", error=str(exc))

    async def _process_subscription(
        self, subscription: Subscription, repo: Repository
    ) -> None:
        router = self._resolve_router(subscription.router_key)
        params = {
            "router": router.address,
            "sinceMinutes": subscription.lookback_minutes,
        }
        result = await self.mcp.base.call_tool("getDexRouterActivity", params)
        if not isinstance(result, (list, dict)):
            logger.warning(
                "subscription_invalid_result",
                router_key=subscription.router_key,
                result_type=type(result).__name__,
            )
            return

        transactions = self._iter_transactions(result)
        if not transactions:
            return

        fresh = []
        for tx in transactions:
            tx_hash = (tx.get("hash") if isinstance(tx, dict) else None) or ""
            if not tx_hash or await repo.is_seen(tx_hash):
                continue
            fresh.append(tx)
            await repo.mark_seen(tx_hash, subscription.router_key)

        if not fresh:
            return

        user = await repo.get_user_by_id(subscription.user_id)
        if not user and not self.override_chat_id:
            logger.warning("subscription_missing_user", user_id=subscription.user_id)
            return

        target_chat_id = self.override_chat_id or (user.chat_id if user else None)
        if not target_chat_id:
            logger.warning("subscription_missing_chat_id", user_id=subscription.user_id)
            return

        summary = await self.planner.summarize_transactions(
            subscription.router_key,
            fresh,
            self.network,
        )

        if summary:
            message = summary.message
        else:
            message = join_messages(
                [
                    escape_markdown(
                        f"No Dexscreener summaries for {subscription.router_key}"
                        f" in the last {subscription.lookback_minutes} minutes."
                    ),
                ]
            )

        if summary and user:
            await repo.save_token_context(
                user.id,
                summary.tokens,
                source=subscription.router_key,
            )

        try:
            await self.bot.send_message(
                chat_id=target_chat_id,
                text=message,
                parse_mode="MarkdownV2",
                disable_web_page_preview=True,
            )
        except BadRequest:
            await self.bot.send_message(
                chat_id=target_chat_id,
                text=message,
                disable_web_page_preview=True,
            )

    async def _process_watchlists(self, repo: Repository) -> None:
        entries = await repo.all_watch_tokens()
        if not entries:
            return
        grouped: Dict[int, List[TokenWatch]] = {}
        for entry in entries:
            grouped.setdefault(entry.user_id, []).append(entry)
        for user_id, tokens in grouped.items():
            try:
                await self._dispatch_watchlist(user_id, tokens, repo)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.error(
                    "watchlist_dispatch_failed", user_id=user_id, error=str(exc)
                )

    async def _dispatch_watchlist(
        self,
        user_id: int,
        tokens: Sequence[TokenWatch],
        repo: Repository,
    ) -> None:
        if not tokens:
            return
        user = await repo.get_user_by_id(user_id)
        target_chat_id = self.override_chat_id or (user.chat_id if user else None)
        if not target_chat_id:
            logger.warning("watchlist_missing_chat_id", user_id=user_id)
            return

        unique_addresses: Dict[str, str] = {}
        for token in tokens:
            address = (token.token_address or "").strip()
            if not address:
                continue
            normalized = address.lower()
            if normalized not in unique_addresses:
                unique_addresses[normalized] = address
            if len(unique_addresses) >= self.MAX_WATCH_TOKENS:
                break

        token_addresses = list(unique_addresses.values())
        unique_count = len(token_addresses)

        summary: TokenSummary | None = None
        if token_addresses:
            try:
                summary = await self.planner.summarize_tokens_from_context(
                    token_addresses,
                    f"watchlist ({unique_count} token"
                    f"{'s' if unique_count != 1 else ''})",
                    self.network,
                )
                if summary and summary.tokens:
                    for token_entry in summary.tokens:
                        address = token_entry.get("address")
                        symbol = token_entry.get("baseSymbol") or token_entry.get(
                            "symbol"
                        )
                        if not address or not symbol:
                            continue
                        await repo.add_watch_token(
                            user_id,
                            address,
                            token_symbol=symbol,
                        )
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.error(
                    "watchlist_summary_failed",
                    user_id=user_id,
                    error=str(exc),
                )

        activity_sections = await self._build_watchlist_activity(
            tokens[: self.MAX_WATCH_TOKENS]
        )

        sections: List[str] = []
        if summary and summary.message:
            sections.append(self._strip_nfa(summary.message))
        if activity_sections:
            sections.append(join_messages(activity_sections))

        if not sections:
            return

        message = append_not_financial_advice(join_messages(sections))

        try:
            await self.bot.send_message(
                chat_id=target_chat_id,
                text=message,
                parse_mode="MarkdownV2",
                disable_web_page_preview=True,
            )
        except BadRequest:
            await self.bot.send_message(
                chat_id=target_chat_id,
                text=message,
                disable_web_page_preview=True,
            )

    async def _build_watchlist_activity(
        self, tokens: Sequence[TokenWatch]
    ) -> List[str]:
        sections: List[str] = []
        for token in tokens[: self.MAX_WATCH_TOKENS]:
            transfers = await self._fetch_token_transfers(token.token_address)
            if not transfers:
                continue
            label = token.token_symbol or token.label or token.token_address
            header = (
                f"*{escape_markdown(label)}* — `{escape_markdown(token.token_address)}`"
            )
            lines = [
                format_transaction(entry) for entry in transfers[: self.MAX_WATCH_TXNS]
            ]
            sections.append(join_messages([header, "\n".join(lines)]))
        return sections

    async def _fetch_token_transfers(self, token_address: str) -> List[Dict[str, str]]:
        if not token_address:
            return []
        params = {"address": token_address, "pageSize": self.MAX_WATCH_TXNS}
        try:
            payload = await self.mcp.base.call_tool("getTokenTransfers", params)
        except Exception as exc:  # pragma: no cover - network/process errors
            logger.warning(
                "watchlist_token_fetch_failed", token=token_address, error=str(exc)
            )
            return []
        transfers = self._iter_transactions(payload)
        normalized = [self._normalize_watch_transfer(tx) for tx in transfers]
        return [entry for entry in normalized if entry.get("hash")]

    def _normalize_watch_transfer(self, tx: Dict[str, Any]) -> Dict[str, str]:
        if not isinstance(tx, dict):
            return {}
        hash_value = tx.get("hash") or tx.get("txHash") or ""
        timestamp = GeminiPlanner._format_timestamp(
            tx.get("timestamp") or tx.get("time") or tx.get("blockTime")
        )
        method = tx.get("method") or tx.get("function") or "transfer"
        amount = tx.get("value") or tx.get("amount") or tx.get("quantity") or ""
        symbol = tx.get("symbol") or tx.get("tokenSymbol")
        if symbol and amount:
            amount = f"{amount} {symbol}"
        explorer = tx.get("url") or tx.get("explorerUrl")
        if hash_value and not explorer:
            explorer = f"https://basescan.org/tx/{hash_value}"
        return {
            "method": str(method or "transfer"),
            "amount": str(amount or ""),
            "timestamp": timestamp,
            "hash": str(hash_value),
            "explorer_url": str(explorer or ""),
        }

    def _strip_nfa(self, message: str) -> str:
        if not message:
            return ""
        footer = f"\n\n{escape_markdown(NOT_FINANCIAL_ADVICE)}"
        if message.endswith(footer):
            return message[: -len(footer)].rstrip()
        return message

    def _resolve_router(self, router_key: str) -> RouterInfo:
        return resolve_router(router_key, self.network, self.routers)

    @staticmethod
    def _iter_transactions(payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, dict):
            items = payload.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
            if isinstance(items, dict):
                for key in ("items", "data", "records", "entries"):
                    candidate = items.get(key)
                    if isinstance(candidate, list):
                        return [item for item in candidate if isinstance(item, dict)]
                return []
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []
