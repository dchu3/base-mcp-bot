"""Scheduler for subscription updates."""

from __future__ import annotations

from typing import Dict, Iterable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.mcp_client import MCPManager
from app.store.db import Database, Subscription
from app.store.repository import Repository
from app.utils.formatting import format_transaction, join_messages
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
        routers: Dict[str, Dict[str, str]],
        network: str,
        bot,
        interval_minutes: int,
    ) -> None:
        self.scheduler = scheduler
        self.db = db
        self.mcp = mcp_manager
        self.routers = routers
        self.network = network
        self.bot = bot
        self.interval_minutes = interval_minutes
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
        if not isinstance(result, list):
            logger.warning(
                "subscription_invalid_result",
                router_key=subscription.router_key,
                result_type=type(result).__name__,
            )
            return

        fresh = []
        for tx in result:
            tx_hash = (tx.get("hash") if isinstance(tx, dict) else None) or ""
            if not tx_hash or await repo.is_seen(tx_hash):
                continue
            fresh.append(tx)
            await repo.mark_seen(tx_hash, subscription.router_key)

        if not fresh:
            return

        user = await repo.get_user_by_id(subscription.user_id)
        if not user:
            logger.warning("subscription_missing_user", user_id=subscription.user_id)
            return

        lines = [format_transaction(tx) for tx in fresh]
        message = join_messages(
            [
                f"Updates for *{subscription.router_key}* ({self.network})",
                "\n".join(lines),
            ]
        )
        await self.bot.send_message(chat_id=user.chat_id, text=message, parse_mode="MarkdownV2")

    def _resolve_router(self, router_key: str) -> RouterInfo:
        return resolve_router(router_key, self.network, self.routers)
