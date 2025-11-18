"""Scheduled cleanup jobs."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.store.db import Database
from app.store.repository import Repository
from app.utils.logging import get_logger

logger = get_logger(__name__)


class CleanupService:
    """Periodic cleanup of old data."""

    def __init__(self, db: Database, scheduler: AsyncIOScheduler):
        self.db = db
        self.scheduler = scheduler

    def start(self) -> None:
        """Register cleanup jobs with the scheduler."""
        # Purge old conversations every 6 hours
        self.scheduler.add_job(
            self._purge_old_conversations,
            trigger="interval",
            hours=6,
            id="purge_conversations",
        )

        # Purge expired token context every hour
        self.scheduler.add_job(
            self._purge_expired_context,
            trigger="interval",
            hours=1,
            id="purge_token_context",
        )

        logger.info(
            "cleanup_jobs_started", jobs=["purge_conversations", "purge_token_context"]
        )

    async def _purge_old_conversations(self) -> None:
        """Remove conversation messages older than 24 hours."""
        try:
            async with self.db.session() as session:
                repo = Repository(session)
                await repo.purge_old_conversations()
            logger.info("purge_conversations_success")
        except Exception as exc:
            logger.error("purge_conversations_failed", error=str(exc))

    async def _purge_expired_context(self) -> None:
        """Remove expired token context entries."""
        try:
            async with self.db.session() as session:
                repo = Repository(session)
                await repo.purge_expired_token_context()
            logger.info("purge_token_context_success")
        except Exception as exc:
            logger.error("purge_token_context_failed", error=str(exc))
