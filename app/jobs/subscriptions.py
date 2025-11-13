"""Scheduler for subscription updates."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set

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
    escape_markdown_url,
    join_messages,
)
from app.utils.logging import get_logger
from app.utils.routers import RouterInfo, resolve_router

logger = get_logger(__name__)


class SubscriptionService:
    """Poll subscribed routers on a cadence and push updates to Telegram users."""

    MAX_WATCH_TOKENS = 5
    MAX_WATCH_TXNS = 4
    MAX_WATCH_TRANSFER_FETCH = 40
    WATCH_ACTIVITY_LOOKBACK_MINUTES = 60
    TRANSFER_TOPIC = (
        "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    )
    TRANSFER_FETCH_TIMEOUT_SECONDS = 10
    TRANSFER_FETCH_RETRIES = 3
    TRANSFER_FAILURE_TTL_SECONDS = 120
    WATCHLIST_TRANSFERS_DISABLED = "Watchlist transfer feed is temporarily disabled."

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
        self._token_metadata_cache: Dict[str, Dict[str, Any] | None] = {}
        self._transfer_error_cache: Dict[str, float] = {}

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
        deduped_tokens: List[TokenWatch] = []
        for token in tokens:
            address = (token.token_address or "").strip()
            if not address:
                continue
            normalized = address.lower()
            if normalized in unique_addresses:
                continue
            unique_addresses[normalized] = address
            deduped_tokens.append(token)
            if len(unique_addresses) >= self.MAX_WATCH_TOKENS:
                break

        token_addresses = list(unique_addresses.values())
        watchlist_lower = {addr.lower(): addr for addr in token_addresses}
        unique_count = len(token_addresses)

        insights = await self._collect_watchlist_insights(deduped_tokens)
        planner_insights = self._prepare_planner_insights(insights)
        summary: TokenSummary | None = None
        if token_addresses:
            try:
                summary = await self.planner.summarize_tokens_from_context(
                    token_addresses,
                    f"watchlist ({unique_count} token"
                    f"{'s' if unique_count != 1 else ''})",
                    self.network,
                    token_insights=planner_insights or None,
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

        used_addresses = self._extract_summary_addresses(summary)
        activity_sections = self._build_insight_sections(insights, used_addresses)
        if not summary and not activity_sections:
            logger.info("watchlist_transfers_disabled_notice")
            activity_sections = [escape_markdown(self.WATCHLIST_TRANSFERS_DISABLED)]

        summary_links: List[str] = []
        if summary:
            for addr in used_addresses:
                original = watchlist_lower.get(addr)
                if not original:
                    continue
                url = self._dexscreener_url(original)
                if url:
                    summary_links.append(
                        f"[View on Dexscreener]({escape_markdown_url(url)})"
                    )

        sections: List[str] = []
        if summary and summary.message:
            summary_payload = self._strip_nfa(summary.message)
            if summary_links and "View on Dexscreener" not in summary_payload:
                link_block = "\n".join(summary_links)
                summary_payload = "\n".join(
                    part for part in [summary_payload, link_block] if part
                )
            sections.append(summary_payload)
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

    async def _collect_watchlist_insights(
        self, tokens: Sequence[TokenWatch]
    ) -> Dict[str, Dict[str, str]]:
        if not tokens:
            return {}
        cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=self.WATCH_ACTIVITY_LOOKBACK_MINUTES
        )
        insights: Dict[str, Dict[str, str]] = {}
        for token in tokens:
            insight = await self._build_watch_token_insight(token, cutoff)
            if insight:
                insights[insight["address"]] = insight
        return insights

    async def _build_watch_token_insight(
        self, token: TokenWatch, cutoff: datetime
    ) -> Dict[str, str] | None:
        address = (token.token_address or "").strip()
        if not address:
            return None
        normalized_addr = address.lower()
        logs, metadata, error = await self._fetch_transfer_logs(
            address, self.MAX_WATCH_TRANSFER_FETCH
        )
        if not metadata:
            metadata = await self._get_token_metadata(address)

        recent_logs = (self._filter_recent_logs(logs, cutoff) if logs else [])[
            : self.MAX_WATCH_TXNS
        ]
        structured_events: List[Dict[str, Any]] = []
        for entry in recent_logs:
            normalized = self._normalize_watch_transfer(entry, metadata)
            if normalized:
                structured_events.append(
                    {
                        "timestamp": normalized.get("timestamp"),
                        "amount": normalized.get("raw_amount")
                        or normalized.get("amount"),
                        "from": normalized.get("fromAddress"),
                        "to": normalized.get("toAddress"),
                        "hash": normalized.get("hash"),
                    }
                )
        display_label = (
            (token.label or "").strip()
            or (token.token_symbol or "").strip()
            or self._short_address(address)
        )
        lookback = self.WATCH_ACTIVITY_LOOKBACK_MINUTES
        summary_text: str | None = None
        if structured_events:
            try:
                summary_text = await self.planner.summarize_transfer_activity(
                    display_label, structured_events
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning(
                    "watchlist_transfer_summary_failed",
                    token=address,
                    error=str(exc),
                )
        if not summary_text:
            summary_text = error or f"No transfers in the last {lookback} minutes."

        detail_block = ""
        link = self._dexscreener_url(address)
        return {
            "address": normalized_addr,
            "original_address": address,
            "label": display_label,
            "summary": summary_text,
            "details": detail_block,
            "link": link,
        }

    def _prepare_planner_insights(
        self, insights: Mapping[str, Dict[str, str]]
    ) -> Dict[str, Dict[str, str]]:
        payload: Dict[str, Dict[str, str]] = {}
        for addr, info in insights.items():
            summary = info.get("summary")
            details = info.get("details")
            if not summary and not details:
                continue
            payload[addr] = {
                "activitySummary": summary or "",
                "activityDetails": details or "",
            }
        return payload

    def _build_insight_sections(
        self, insights: Mapping[str, Dict[str, str]], used_addresses: Set[str]
    ) -> List[str]:
        sections: List[str] = []
        for addr, info in insights.items():
            if addr in used_addresses:
                continue
            section = self._format_insight_section(info)
            if section:
                sections.append(section)
        return sections

    def _format_insight_section(self, info: Mapping[str, str]) -> str | None:
        label = info.get("label")
        summary = info.get("summary")
        details = info.get("details")
        link = info.get("link")
        if not label and not summary and not details:
            return None
        lookback = self.WATCH_ACTIVITY_LOOKBACK_MINUTES
        header = (
            f"*{escape_markdown(label or 'Token')}* transfers "
            f"\\(last {lookback}m\\)"
        )
        lines = [header]
        if summary:
            lines.append(escape_markdown(summary))
        if details:
            lines.append(details)
        if link:
            safe_url = escape_markdown_url(str(link))
            lines.append(f"[View on Dexscreener]({safe_url})")
        return "\n".join(lines)

    @staticmethod
    def _extract_summary_addresses(summary: TokenSummary | None) -> Set[str]:
        if not summary or not summary.tokens:
            return set()
        used: Set[str] = set()
        for token in summary.tokens:
            address = token.get("address")
            if isinstance(address, str):
                used.add(address.lower())
        return used

    async def _fetch_transfer_logs(
        self, token_address: str, page_size: int
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any] | None, str | None]:
        if not token_address:
            return [], None, "invalid token address"
        normalized_addr = token_address.lower()
        now = time.time()
        cached_failure = self._transfer_error_cache.get(normalized_addr)
        if cached_failure and cached_failure > now:
            return [], None, "Base explorer recovering from a recent timeout."

        params = {
            "address": token_address,
            "pageSize": max(1, min(page_size, self.MAX_WATCH_TRANSFER_FETCH)),
        }

        last_error: str | None = None
        delay = 0.5
        for attempt in range(1, self.TRANSFER_FETCH_RETRIES + 1):
            start = time.perf_counter()
            logger.debug(
                "watchlist_fetch_begin",
                token=token_address,
                attempt=attempt,
                params=params,
            )
            try:
                payload = await asyncio.wait_for(
                    self.mcp.base.call_tool("getTokenTransfers", params),
                    timeout=self.TRANSFER_FETCH_TIMEOUT_SECONDS,
                )
            except Exception as exc:  # pragma: no cover - network/process errors
                friendly = self._classify_transfer_error(exc)
                last_error = friendly
                duration = (time.perf_counter() - start) * 1000
                logger.warning(
                    "watchlist_token_fetch_failed",
                    token=token_address,
                    attempt=attempt,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    duration_ms=round(duration, 2),
                )
                if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
                    break
                if attempt < self.TRANSFER_FETCH_RETRIES:
                    await asyncio.sleep(delay)
                    delay *= 2
                continue

            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                return [], None, "no transfer data returned"
            duration = (time.perf_counter() - start) * 1000
            logger.debug(
                "watchlist_fetch_success",
                token=token_address,
                attempt=attempt,
                duration_ms=round(duration, 2),
                item_count=len(items),
            )
            metadata: Dict[str, Any] | None = None
            normalized: List[Dict[str, Any]] = []
            for event in items:
                if not metadata:
                    event_meta = event.get("token")
                    if isinstance(event_meta, dict):
                        metadata = event_meta
                converted = self._transfer_event_to_log(event)
                if converted:
                    normalized.append(converted)
            if normalized_addr in self._transfer_error_cache:
                self._transfer_error_cache.pop(normalized_addr, None)
            return normalized, metadata, None

        if last_error:
            self._transfer_error_cache[normalized_addr] = (
                now + self._transfer_failure_ttl()
            )
            return [], None, last_error
        return [], None, "transfer fetch failed"

    def _transfer_failure_ttl(self) -> float:
        interval_seconds = max(1, self.interval_minutes) * 60
        return max(self.TRANSFER_FAILURE_TTL_SECONDS, interval_seconds)

    def _filter_recent_logs(
        self, logs: Sequence[Dict[str, Any]], cutoff: datetime
    ) -> List[Dict[str, Any]]:
        recent: List[Dict[str, Any]] = []
        for entry in logs:
            parsed = self._parse_log_timestamp(self._log_timestamp_value(entry))
            if parsed and parsed >= cutoff:
                recent.append(entry)
        return recent

    async def _get_token_metadata(self, token_address: str) -> Dict[str, Any] | None:
        normalized = token_address.lower()
        if normalized in self._token_metadata_cache:
            return self._token_metadata_cache[normalized]
        try:
            metadata = await self.mcp.base.call_tool(
                "resolveToken", {"address": token_address}
            )
        except Exception as exc:  # pragma: no cover - metadata lookups best-effort
            logger.warning(
                "token_metadata_resolve_failed", token=token_address, error=str(exc)
            )
            self._token_metadata_cache[normalized] = None
            return None
        self._token_metadata_cache[normalized] = metadata
        return metadata

    def _normalize_watch_transfer(
        self, log: Dict[str, Any], metadata: Dict[str, Any] | None
    ) -> Dict[str, str] | None:
        topics = log.get("topics")
        if not isinstance(topics, list) or len(topics) < 3:
            return None
        from_addr = self._topic_address(topics[1])
        to_addr = self._topic_address(topics[2])
        timestamp = GeminiPlanner._format_timestamp(self._log_timestamp_value(log))
        hash_value = (
            log.get("transactionHash") or log.get("hash") or log.get("txHash") or ""
        )
        amount = self._format_transfer_amount(log.get("data"), metadata)
        amount_display = (
            f"{amount} ({self._short_address(from_addr)}→{self._short_address(to_addr)})"
            if amount
            else f"{self._short_address(from_addr)}→{self._short_address(to_addr)}"
        )
        explorer = f"https://basescan.org/tx/{hash_value}" if hash_value else ""
        return {
            "method": "Transfer",
            "amount": amount_display,
            "raw_amount": amount,
            "timestamp": timestamp,
            "hash": str(hash_value),
            "explorer_url": explorer,
            "fromAddress": from_addr,
            "toAddress": to_addr,
            "tokenSymbol": (
                metadata.get("symbol") if isinstance(metadata, dict) else None
            ),
        }

    def _strip_nfa(self, message: str) -> str:
        if not message:
            return ""
        footer = f"\n\n{escape_markdown(NOT_FINANCIAL_ADVICE)}"
        if message.endswith(footer):
            return message[: -len(footer)].rstrip()
        return message

    @staticmethod
    def _parse_log_timestamp(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                return datetime.fromtimestamp(float(stripped), tz=timezone.utc)
            if stripped.startswith("0x") or stripped.startswith("0X"):
                try:
                    return datetime.fromtimestamp(int(stripped, 16), tz=timezone.utc)
                except ValueError:
                    return None
            try:
                normalized = (
                    stripped.replace("Z", "+00:00") if "Z" in stripped else stripped
                )
                return datetime.fromisoformat(normalized)
            except ValueError:
                return None
        return None

    @staticmethod
    def _log_timestamp_value(entry: Dict[str, Any] | None) -> Any:
        if not isinstance(entry, dict):
            return None
        for key in ("timestamp", "timeStamp", "block_timestamp"):
            value = entry.get(key)
            if value is not None:
                return value
        return None

    @staticmethod
    def _classify_transfer_error(error: Exception | str) -> str:
        if isinstance(error, TimeoutError) or isinstance(error, asyncio.TimeoutError):
            return "Base explorer timed out fetching transfers."
        message = str(error) if not isinstance(error, str) else error
        lowered = message.lower()
        if "524" in lowered or "timeout" in lowered:
            return "Base explorer timed out fetching transfers."
        if "service unavailable" in lowered or "cloudflare" in lowered:
            return "Base explorer is temporarily unavailable."
        if "connection" in lowered or "network" in lowered:
            return "Network error while fetching transfers."
        return "Unable to fetch transfers right now."

    def _format_transfer_amount(
        self, data: Any, metadata: Dict[str, Any] | None
    ) -> str | None:
        if not isinstance(data, str):
            return None
        try:
            value = int(data, 16)
        except ValueError:
            return None
        if value == 0:
            return None
        decimals = metadata.get("decimals") if isinstance(metadata, dict) else None
        symbol = metadata.get("symbol") if isinstance(metadata, dict) else None
        decimals = (
            decimals if isinstance(decimals, (int, float)) and decimals >= 0 else 18
        )
        amount = value / (10 ** int(decimals))
        if amount >= 1:
            formatted = f"{amount:,.2f}"
        else:
            formatted = f"{amount:.6f}".rstrip("0").rstrip(".")
        if symbol:
            return f"{formatted} {symbol}"
        return formatted

    @staticmethod
    def _topic_address(topic: Any) -> str:
        if not isinstance(topic, str) or len(topic) < 42:
            return "0x0000000000000000000000000000000000000000"
        return "0x" + topic[-40:].lower()

    @staticmethod
    def _short_address(address: str | None) -> str:
        if not address or len(address) < 10:
            return address or "?"
        return f"{address[:6]}…{address[-4:]}"

    def _dexscreener_url(self, token_address: str) -> str | None:
        if not token_address:
            return None
        address = token_address.strip().lower()
        if not address:
            return None
        network = (self.network or "base").lower()
        if network in {"base", "base-mainnet"}:
            slug = "base"
        elif network in {"base-sepolia", "sepolia"}:
            slug = "base-sepolia"
        else:
            slug = "base"
        return f"https://dexscreener.com/{slug}/{address}"

    def _transfer_event_to_log(self, event: Dict[str, Any]) -> Dict[str, Any] | None:
        if not isinstance(event, dict):
            return None
        tx_hash = event.get("hash") or event.get("transactionHash")
        amount_raw = event.get("amount") or event.get("value")
        from_addr = event.get("from")
        to_addr = event.get("to")
        if not tx_hash or not from_addr or not to_addr:
            return None
        timestamp = event.get("timestamp") or event.get("timeStamp")
        data_hex: str | None = None
        if isinstance(amount_raw, str):
            amount_str = amount_raw.strip()
            if amount_str:
                base = 16 if amount_str.startswith("0x") else 10
                try:
                    data_hex = hex(int(amount_str, base))
                except ValueError:
                    data_hex = None
        topics = [
            self.TRANSFER_TOPIC,
            self._address_to_topic(from_addr),
            self._address_to_topic(to_addr),
        ]
        return {
            "transactionHash": tx_hash,
            "timestamp": timestamp,
            "topics": topics,
            "data": data_hex,
        }

    @staticmethod
    def _address_to_topic(address: Any) -> str:
        if not isinstance(address, str) or not address.startswith("0x"):
            return "0x" + ("0" * 64)
        stripped = address[2:]
        if len(stripped) > 40:
            stripped = stripped[-40:]
        padded = stripped.rjust(64, "0")
        return "0x" + padded.lower()

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
