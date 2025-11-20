from types import SimpleNamespace

import pytest

from app.handlers.commands import (
    HandlerContext,
    ensure_user,
    send_planner_response,
)
from app.planner import PlannerResult
from app.store.db import Database
from app.store.repository import Repository
from telegram.error import BadRequest


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


class FailingMessage(DummyMessage):
    def __init__(self) -> None:
        super().__init__()
        self.failed_once = False

    async def reply_text(self, text: str, **kwargs) -> None:
        parse_mode = kwargs.get("parse_mode")
        if not self.failed_once and parse_mode == "MarkdownV2":
            self.failed_once = True
            raise BadRequest("invalid markdown")
        await super().reply_text(text, **kwargs)


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
    assert "recent_tokens" in planner.last_payload
    assert len(planner.last_payload["recent_tokens"]) == 1
    assert planner.last_payload["recent_tokens"][0]["symbol"] == "TEST"


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


@pytest.mark.asyncio
async def test_markdown_fallback_on_bad_request(tmp_path) -> None:
    """Planner reply falls back to plain text if Telegram rejects markdown."""
    db_path = tmp_path / "bot_markdown.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    planner = DummyPlanner(message="value_with_underscore", tokens=[])
    message_obj = FailingMessage()

    update = SimpleNamespace(
        message=message_obj,
        effective_user=SimpleNamespace(id=999),
        effective_chat=SimpleNamespace(id=999),
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

    await send_planner_response(update, context, "test markdown error")

    # First call fails, second falls back
    assert len(message_obj.calls) == 1
    text, kwargs = message_obj.calls[0]
    assert kwargs.get("parse_mode") is None
    assert "value_with_underscore" in text


@pytest.mark.asyncio
async def test_ensure_user_restricts_chat() -> None:
    """ensure_user blocks requests from unexpected chat IDs."""
    message_obj = DummyMessage()
    update = SimpleNamespace(
        message=message_obj,
        effective_chat=SimpleNamespace(id=222),
        effective_user=SimpleNamespace(id=222),
    )
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "ctx": HandlerContext(
                    db=None,
                    planner=None,
                    rate_limiter=None,
                    admin_ids=[],
                    allowed_chat_id=111,
                )
            }
        )
    )

    allowed = await ensure_user(update, context)
    assert allowed is False
    assert message_obj.calls
