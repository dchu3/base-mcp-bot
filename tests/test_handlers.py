from types import SimpleNamespace

import pytest

from app.handlers.commands import (
    HandlerContext,
    send_planner_response,
    subscriptions_command,
    unwatch_all_command,
    unwatch_command,
    watch_command,
    watchlist_command,
)
from app.planner import PlannerResult
from app.store.db import Database, TokenWatch
from app.store.repository import Repository
from app.utils.routers import DEFAULT_ROUTERS
from app.utils.formatting import escape_markdown


class DummyPlanner:
    def __init__(self, message: str = "", tokens: list | None = None) -> None:
        self.result = PlannerResult(message=message, tokens=tokens or [])
        self.last_payload = None

    async def run(self, message: str, payload: dict) -> PlannerResult:
        self.last_payload = payload
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
async def test_send_planner_response_includes_watchlist_tokens(tmp_path) -> None:
    db_path = tmp_path / "planner.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    async with db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(5)
        await repo.add_watch_token(
            user.id,
            "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            token_symbol="AAA",
            label="Alpha",
        )

    message = DummyMessage()
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=5),
        effective_chat=SimpleNamespace(id=5),
    )
    planner = DummyPlanner(message="ok")
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

    await send_planner_response(update, context, "any request")

    payload = planner.last_payload
    assert payload is not None
    assert payload["watchlist_tokens"]
    assert any(
        token["address"] == "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        for token in payload["recent_tokens"]
    )


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
            + escape_markdown(DEFAULT_ROUTERS["aerodrome_v2"]["base-mainnet"])
            + "` every 30 minutes",
            "• "
            + escape_markdown("uniswap_v3")
            + " — `"
            + escape_markdown(DEFAULT_ROUTERS["uniswap_v3"]["base-mainnet"])
            + "` every 15 minutes",
        ]
    )
    assert text == expected


@pytest.mark.asyncio
async def test_watch_commands_flow(tmp_path) -> None:
    db_path = tmp_path / "watch.db"
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
    bot_data = {"ctx": handler_ctx}
    user_id = 7

    def build_update(message):
        return SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=user_id),
            effective_chat=SimpleNamespace(id=user_id),
        )

    def build_context(args):
        return SimpleNamespace(
            application=SimpleNamespace(bot_data=bot_data),
            args=args,
        )

    token_address = "0x1111111111111111111111111111111111111111"
    message = DummyMessage()
    await watch_command(
        build_update(message),
        build_context([token_address, "LUNA", "Moon", "Bag"]),
    )
    assert "Watchlist updated" in message.calls[0][0]
    assert "LUNA (Moon Bag)" in message.calls[0][0]

    list_message = DummyMessage()
    await watchlist_command(build_update(list_message), build_context([]))
    text, kwargs = list_message.calls[0]
    assert "Your watchlist:" in text
    assert "LUNA" in text and token_address in text
    assert kwargs.get("parse_mode") == "MarkdownV2"

    db_user_id = None
    async with db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(user_id)
        db_user_id = user.id
        session.add(
            TokenWatch(
                user_id=user.id,
                token_address=token_address.upper(),
                token_symbol="DUPLICATE",
            )
        )
        await session.commit()

    dedup_message = DummyMessage()
    await watchlist_command(build_update(dedup_message), build_context([]))
    dedup_text, _ = dedup_message.calls[0]
    bullet_lines = [line for line in dedup_text.split("\n") if line.startswith("• ")]
    assert len(bullet_lines) == 1
    async with db.session() as session:
        await session.execute(
            TokenWatch.__table__.delete().where(
                TokenWatch.user_id == db_user_id,
                TokenWatch.token_address == token_address.upper(),
            )
        )
        await session.commit()

    remove_message = DummyMessage()
    await unwatch_command(
        build_update(remove_message),
        build_context([token_address]),
    )
    assert "Removed" in remove_message.calls[0][0]

    empty_message = DummyMessage()
    await watchlist_command(build_update(empty_message), build_context([]))
    assert empty_message.calls[0][0] == "Your watchlist is empty."

    await watch_command(
        build_update(DummyMessage()),
        build_context(["0x2222222222222222222222222222222222222222"]),
    )
    await watch_command(
        build_update(DummyMessage()),
        build_context(["0x3333333333333333333333333333333333333333", "PEPE"]),
    )

    clear_message = DummyMessage()
    await unwatch_all_command(
        build_update(clear_message),
        build_context([]),
    )
    assert clear_message.calls[0][0] == "Watchlist cleared."

    final_message = DummyMessage()
    await watchlist_command(build_update(final_message), build_context([]))
    assert final_message.calls[0][0] == "Your watchlist is empty."
