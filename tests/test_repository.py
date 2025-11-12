import pytest
from sqlalchemy import text

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


@pytest.mark.asyncio
async def test_token_context_lifecycle(tmp_path):
    db_path = tmp_path / "tokens.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    async with db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(999)

        await repo.save_token_context(
            user.id,
            [
                {
                    "symbol": "BLUEPEPE",
                    "address": "0xabc",
                    "pairAddress": "0xpair",
                    "url": "https://dexscreener.com/base/0xpair",
                    "chainId": "base",
                }
            ],
            source="uniswap_v3",
            ttl_minutes=-1,  # force expiry
        )
        assert await repo.list_active_token_context(user.id) == []

        await repo.save_token_context(
            user.id,
            [
                {
                    "symbol": "LIVECASTER/WETH",
                    "baseSymbol": "LIVECASTER",
                    "name": "Livecaster",
                    "address": "0xdef",
                    "pairAddress": "0xpair2",
                }
            ],
            source="aerodrome_v2",
            ttl_minutes=5,
        )
        rows = await repo.list_active_token_context(user.id)
        assert len(rows) == 1
        assert rows[0].symbol == "LIVECASTER/WETH"
        assert rows[0].source == "aerodrome_v2"
        assert rows[0].base_symbol == "LIVECASTER"
        assert rows[0].token_name == "Livecaster"

        await repo.purge_expired_token_context()
        rows = await repo.list_active_token_context(user.id)
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_token_context_schema_upgrade(tmp_path):
    db_path = tmp_path / "legacy.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    async with db.session() as session:
        # Simulate old schema without new columns.
        await session.execute(text("DROP TABLE IF EXISTS tokencontext"))
        await session.execute(
            text(
                """
                CREATE TABLE tokencontext (
                    user_id INTEGER NOT NULL,
                    token_address VARCHAR NOT NULL,
                    symbol VARCHAR,
                    source VARCHAR,
                    pair_address VARCHAR,
                    url VARCHAR,
                    chain_id VARCHAR,
                    saved_at DATETIME,
                    expires_at DATETIME,
                    PRIMARY KEY (user_id, token_address)
                )
                """
            )
        )
        await session.commit()

        repo = Repository(session)
        user = await repo.get_or_create_user(1)
        await repo.save_token_context(
            user.id,
            [{"symbol": "AAA/BBB", "address": "0xabc"}],
            source="uniswap_v3",
        )

        # Columns should now exist, and listing should not raise.
        pragma = await session.execute(text("PRAGMA table_info('tokencontext')"))
        cols = {row[1] for row in pragma}
        assert "base_symbol" in cols
        assert "token_name" in cols
        rows = await repo.list_active_token_context(user.id)
        assert len(rows) == 1
        assert rows[0].symbol == "AAA/BBB"


@pytest.mark.asyncio
async def test_token_watch_crud(tmp_path):
    db_path = tmp_path / "watch.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    async with db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(42)
        other = await repo.get_or_create_user(43)

        await repo.add_watch_token(
            user.id, "0xaaa", token_symbol="AAA", label="First token"
        )
        await repo.add_watch_token(user.id, "0xbbb", token_symbol="BBB")
        await repo.add_watch_token(other.id, "0xccc", token_symbol="CCC")

        user_tokens = await repo.list_watch_tokens(user.id)
        assert [token.token_address for token in user_tokens] == ["0xaaa", "0xbbb"]

        await repo.add_watch_token(
            user.id, "0xaaa", token_symbol="AAA-NEW", label="Renamed"
        )
        user_tokens = await repo.list_watch_tokens(user.id)
        assert user_tokens[0].token_symbol == "AAA-NEW"
        assert user_tokens[0].label == "Renamed"

        await repo.add_watch_token(
            user.id,
            "0xAAA",
            token_symbol="AAA-LOWER",
        )
        user_tokens = await repo.list_watch_tokens(user.id)
        assert len(user_tokens) == 2
        assert user_tokens[0].token_address == "0xaaa"
        assert user_tokens[0].token_symbol == "AAA-LOWER"

        await repo.add_watch_token(user.id, "0xaaa")
        user_tokens = await repo.list_watch_tokens(user.id)
        assert user_tokens[0].label == "Renamed"

        await repo.remove_watch_token(user.id, "0xbbb")
        user_tokens = await repo.list_watch_tokens(user.id)
        assert len(user_tokens) == 1

        await repo.remove_all_watch_tokens(user.id)
        assert await repo.list_watch_tokens(user.id) == []

        all_tokens = await repo.all_watch_tokens()
        assert [token.token_address for token in all_tokens] == ["0xccc"]
