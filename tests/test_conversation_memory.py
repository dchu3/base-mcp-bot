"""Test conversation memory functionality."""

from datetime import datetime, timedelta

import pytest

from app.store.db import ConversationMessage, Database
from app.store.repository import Repository


@pytest.mark.asyncio
async def test_save_and_retrieve_conversation(tmp_path):
    """Test saving and retrieving conversation messages."""
    db_path = tmp_path / "conversation.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    async with db.session() as session:
        repo = Repository(session)

        user = await repo.get_or_create_user(123456)

        session_id = await repo.get_or_create_session(user.id)

        await repo.save_conversation_message(
            user_id=user.id,
            role="user",
            content="What's PEPE doing?",
            session_id=session_id,
        )

        await repo.save_conversation_message(
            user_id=user.id,
            role="assistant",
            content="PEPE is up 15% with $2.3M volume",
            session_id=session_id,
            tokens_mentioned=["0xabc123"],
        )

        history = await repo.get_conversation_history(user_id=user.id, limit=10)

        assert len(history) == 2
        assert history[0].role == "user"
        assert history[0].content == "What's PEPE doing?"
        assert history[1].role == "assistant"
        assert "PEPE" in history[1].content


@pytest.mark.asyncio
async def test_session_management(tmp_path):
    """Test session creation and inactivity timeout."""
    db_path = tmp_path / "session.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    async with db.session() as session:
        repo = Repository(session)

        user = await repo.get_or_create_user(789012)

        session_id_1 = await repo.get_or_create_session(user.id)

        await repo.save_conversation_message(
            user_id=user.id,
            role="user",
            content="First message",
            session_id=session_id_1,
        )

        session_id_2 = await repo.get_or_create_session(user.id)

        assert session_id_1 == session_id_2, "Should reuse session within timeout"


@pytest.mark.asyncio
async def test_purge_old_conversations(tmp_path):
    """Test purging old conversation messages."""
    db_path = tmp_path / "purge.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    async with db.session() as session:
        repo = Repository(session)

        user = await repo.get_or_create_user(345678)

    async with db.session() as session:
        old_message = ConversationMessage(
            user_id=user.id,
            role="user",
            content="Old message",
            created_at=datetime.utcnow() - timedelta(hours=25),
            session_id="test-session",
        )
        session.add(old_message)

        recent_message = ConversationMessage(
            user_id=user.id,
            role="user",
            content="Recent message",
            created_at=datetime.utcnow() - timedelta(hours=1),
            session_id="test-session",
        )
        session.add(recent_message)
        await session.commit()

    async with db.session() as session:
        repo = Repository(session)
        await repo.purge_old_conversations(retention_hours=24)

    async with db.session() as session:
        repo = Repository(session)
        history = await repo.get_conversation_history(user_id=user.id, limit=10)

        assert len(history) == 1
        assert history[0].content == "Recent message"


@pytest.mark.asyncio
async def test_conversation_history_limit(tmp_path):
    """Test conversation history respects limit parameter."""
    db_path = tmp_path / "limit.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    async with db.session() as session:
        repo = Repository(session)

        user = await repo.get_or_create_user(567890)

        session_id = await repo.get_or_create_session(user.id)

        for i in range(15):
            await repo.save_conversation_message(
                user_id=user.id,
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message {i}",
                session_id=session_id,
            )

        history = await repo.get_conversation_history(user_id=user.id, limit=5)

        assert len(history) == 5
        assert history[0].content == "Message 10"
        assert history[4].content == "Message 14"


@pytest.mark.asyncio
async def test_clear_conversation_history(tmp_path) -> None:
    """Test clearing conversation history for a user."""
    db_path = tmp_path / "clear.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    async with db.session() as session:
        repo = Repository(session)

        user = await repo.get_or_create_user(12345)

        # Add some conversation history
        session_id = await repo.get_or_create_session(user.id)
        for i in range(5):
            await repo.save_conversation_message(
                user_id=user.id,
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message {i}",
                session_id=session_id,
            )

        # Verify messages exist
        history = await repo.get_conversation_history(user.id, limit=10)
        assert len(history) == 5

        # Clear history
        count = await repo.clear_conversation_history(user.id)
        assert count == 5

        # Verify messages deleted
        history = await repo.get_conversation_history(user.id, limit=10)
        assert len(history) == 0


@pytest.mark.asyncio
async def test_clear_conversation_history_empty(tmp_path) -> None:
    """Test clearing when there's no history returns 0."""
    db_path = tmp_path / "clear_empty.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    async with db.session() as session:
        repo = Repository(session)

        user = await repo.get_or_create_user(99999)

        # Clear with no history
        count = await repo.clear_conversation_history(user.id)
        assert count == 0
