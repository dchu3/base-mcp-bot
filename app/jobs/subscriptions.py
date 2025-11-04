"""Scheduler for subscription updates."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.error import BadRequest

from app.mcp_client import MCPManager
from app.planner import GeminiPlanner
from app.store.db import Database, Subscription
from app.store.repository import Repository
from app.utils.formatting import escape_markdown, join_messages
from app.utils.logging import get_logger
from app.utils.routers import RouterInfo, resolve_router

logger = get_logger(__name__)


class SubscriptionService:
    """Poll subscribed routers on a cadence and push updates to Telegram users."""

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
        self.scheduler.add_job(self._run_cycle, "interval", minutes=self.interval_minutes)
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
                except Exception as exc:  # pragma: no cover - background errors are logged
                    logger.error(
                        "subscription_cycle_error",
                        router_key=subscription.router_key,
                        error=str(exc),
                    )

    async def _process_subscription(self, subscription: Subscription, repo: Repository) -> None:
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
            message = summary
        else:
            message = join_messages(
                [
                    escape_markdown(
                        f"No Dexscreener summaries for {subscription.router_key}"
                        f" in the last {subscription.lookback_minutes} minutes."
                    ),
                ]
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
