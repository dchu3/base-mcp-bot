from types import SimpleNamespace

import pytest

from app.handlers.commands import HandlerContext, send_planner_response
from app.planner import PlannerResult
from app.store.db import Database
from app.store.repository import Repository


class DummyPlanner:
    def __init__(self, message: str = "", tokens: list | None = None) -> None:
        self.result = PlannerResult(message=message, tokens=tokens or [])
        self.last_payload = None

    async def run(self, message: str, payload: dict) -> PlannerResult:
        self.last_payload = payload
        return self.result


class DummyMessage:
    def __init__(self) -> None:
        self.calls = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.calls.append((text, kwargs))


@pytest.mark.asyncio
async def test_send_planner_response_includes_token_context(tmp_path) -> None:
    """Test that token context is included in planner payload."""
    db_path = tmp_path / "bot_context.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    planner = DummyPlanner(message="Test response", tokens=[])
    message_obj = DummyMessage()

    update = SimpleNamespace(
        message=message_obj,
        effective_user=SimpleNamespace(id=12345),
        effective_chat=SimpleNamespace(id=12345),
    )

    context = SimpleNamespace(
        args=[],
        application=SimpleNamespace(
            bot_data={
                "ctx": HandlerContext(
                    db=db,
                    planner=planner,
                    rate_limiter=None,
                    admin_ids=[],
                    allowed_chat_id=None,
                )
            }
        ),
    )

    # Save some token context first
    async with db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(12345)
        await repo.save_token_context(
            user.id,
            [{"address": "0xabc123", "symbol": "TEST", "source": "dexscreener"}],
        )

    await send_planner_response(update, context, "check TEST")

    # Verify planner received token context
    assert planner.last_payload is not None
    assert "cachedWatchlist" in planner.last_payload
    assert len(planner.last_payload["cachedWatchlist"]) == 1
    assert planner.last_payload["cachedWatchlist"][0]["symbol"] == "TEST"


@pytest.mark.asyncio
async def test_empty_response_uses_plain_text(tmp_path) -> None:
    """Ensure empty planner response sends plain text without markdown errors."""
    db_path = tmp_path / "bot_empty.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    planner = DummyPlanner(message="", tokens=[])
    message_obj = DummyMessage()

    update = SimpleNamespace(
        message=message_obj,
        effective_user=SimpleNamespace(id=12345),
        effective_chat=SimpleNamespace(id=12345),
    )

    context = SimpleNamespace(
        args=[],
        application=SimpleNamespace(
            bot_data={
                "ctx": HandlerContext(
                    db=db,
                    planner=planner,
                    rate_limiter=None,
                    admin_ids=[],
                    allowed_chat_id=None,
                )
            }
        ),
    )

    await send_planner_response(update, context, "test query")

    # Verify the "No recent data" message was sent
    assert len(message_obj.calls) == 1
    text, kwargs = message_obj.calls[0]

    # Check the message content
    assert "No recent data returned for that request." in text

    # CRITICAL: Verify parse_mode=None is explicitly set
    assert "parse_mode" in kwargs
    assert kwargs["parse_mode"] is None

    # Verify disable_web_page_preview is set
    assert kwargs.get("disable_web_page_preview") is True
