import pytest

from app.store.db import Database
from app.store.repository import Repository


@pytest.mark.asyncio
async def test_remove_all_subscriptions_clears_entries(tmp_path):
    db_path = tmp_path / "test.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    async with db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(123)
        await repo.add_subscription(user.id, "uniswap_v3", 15)
        await repo.add_subscription(user.id, "aerodrome_v2", 30)

        existing = await repo.list_subscriptions(user.id)
        assert len(existing) == 2
        by_router = {sub.router_key: sub.lookback_minutes for sub in existing}
        assert by_router["uniswap_v3"] == 15
        assert by_router["aerodrome_v2"] == 30

        await repo.remove_all_subscriptions(user.id)
        remaining = await repo.list_subscriptions(user.id)
        assert remaining == []
