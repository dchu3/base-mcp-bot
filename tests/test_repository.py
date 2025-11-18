import pytest

from app.store.db import Database
from app.store.repository import Repository


@pytest.mark.asyncio
async def test_get_or_create_user(tmp_path):
    """Test user creation and retrieval."""
    db_path = tmp_path / "users.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    async with db.session() as session:
        repo = Repository(session)
        
        # Create user
        user1 = await repo.get_or_create_user(12345)
        assert user1.chat_id == 12345
        
        # Get existing user
        user2 = await repo.get_or_create_user(12345)
        assert user1.id == user2.id


@pytest.mark.asyncio
async def test_token_context_save_and_retrieve(tmp_path):
    """Test saving and retrieving token context."""
    db_path = tmp_path / "context.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    async with db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(99999)

        # Save token context
        await repo.save_token_context(
            user.id,
            [
                {
                    "symbol": "TEST",
                    "address": "0xabc123",
                    "source": "dexscreener",
                    "pairAddress": "0xpair",
                }
            ],
        )

        # Retrieve active context
        contexts = await repo.list_active_token_context(user.id)
        assert len(contexts) > 0
        assert contexts[0].symbol == "TEST"
        assert contexts[0].token_address == "0xabc123"
