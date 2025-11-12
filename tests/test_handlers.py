from types import SimpleNamespace

import pytest

from app.handlers.commands import HandlerContext, send_planner_response, subscriptions_command
from app.planner import PlannerResult
from app.store.db import Database
from app.store.repository import Repository
from app.utils.routers import DEFAULT_ROUTERS
from app.utils.formatting import escape_markdown


class DummyPlanner:
    def __init__(self, message: str = "", tokens: list | None = None) -> None:
        self.result = PlannerResult(message=message, tokens=tokens or [])

    async def run(self, message: str, payload: dict) -> PlannerResult:
        return self.result

    async def summarize_tokens_from_context(self, addresses, label, network):
        return None


class DummyMessage:
    def __init__(self) -> None:
        self.calls = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.calls.append((text, kwargs))


@pytest.mark.asyncio
async def test_send_planner_response_uses_escaped_fallback(tmp_path) -> None:
    db_path = tmp_path / "bot.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    message = DummyMessage()
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=1),
    )
    planner = DummyPlanner(message="")
    handler_ctx = HandlerContext(
        db=db,
        planner=planner,
        rate_limiter=None,
        routers={},
        network="base-mainnet",
        default_lookback=30,
        subscription_service=None,
        admin_ids=[],
        allowed_chat_id=None,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={"ctx": handler_ctx})
    )

    await send_planner_response(update, context, "anything dexscreener")

    assert len(message.calls) == 1
    text, kwargs = message.calls[0]
    assert text == "No recent data returned for that request."
    assert "parse_mode" not in kwargs or kwargs.get("parse_mode") is None


@pytest.mark.asyncio
async def test_subscriptions_command_handles_empty(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    handler_ctx = HandlerContext(
        db=db,
        planner=DummyPlanner(),
        rate_limiter=None,
        routers=DEFAULT_ROUTERS,
        network="base-mainnet",
        default_lookback=30,
        subscription_service=None,
        admin_ids=[],
        allowed_chat_id=None,
    )

    message = DummyMessage()
    user_id = 42
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=user_id),
    )
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={"ctx": handler_ctx}),
        args=[],
    )

    await subscriptions_command(update, context)

    assert len(message.calls) == 1
    text, kwargs = message.calls[0]
    assert text == "No active subscriptions."
    assert "parse_mode" not in kwargs or kwargs.get("parse_mode") is None


@pytest.mark.asyncio
async def test_subscriptions_command_lists_entries(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    handler_ctx = HandlerContext(
        db=db,
        planner=DummyPlanner(),
        rate_limiter=None,
        routers=DEFAULT_ROUTERS,
        network="base-mainnet",
        default_lookback=30,
        subscription_service=None,
        admin_ids=[],
        allowed_chat_id=None,
    )

    user_id = 84
    async with db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(user_id)
        await repo.add_subscription(user.id, "uniswap_v3", 15)
        await repo.add_subscription(user.id, "aerodrome_v2", 30)

    message = DummyMessage()
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=user_id),
    )
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={"ctx": handler_ctx}),
        args=[],
    )

    await subscriptions_command(update, context)

    assert len(message.calls) == 1
    text, kwargs = message.calls[0]
    assert kwargs.get("parse_mode") == "MarkdownV2"
    expected = "\n".join(
        [
            "Active subscriptions:",
            "• "
            + escape_markdown("aerodrome_v2")
            + " — `"
            + escape_markdown(
                DEFAULT_ROUTERS["aerodrome_v2"]["base-mainnet"]
            )
            + "` every 30 minutes",
            "• "
            + escape_markdown("uniswap_v3")
            + " — `"
            + escape_markdown(
                DEFAULT_ROUTERS["uniswap_v3"]["base-mainnet"]
            )
            + "` every 15 minutes",
        ]
    )
    assert text == expected
