import asyncio
import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.jobs.cleanup import CleanupService


@pytest.mark.asyncio
async def test_cleanup_service_registers_jobs() -> None:
    """CleanupService should register its recurring jobs."""
    scheduler = AsyncIOScheduler(event_loop=asyncio.get_running_loop())
    service = CleanupService(db=object(), scheduler=scheduler)

    service.start()
    scheduler.start()

    assert scheduler.get_job("purge_conversations") is not None
    assert scheduler.get_job("purge_token_context") is not None

    scheduler.shutdown(wait=False)
