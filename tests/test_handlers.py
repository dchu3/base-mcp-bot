from types import SimpleNamespace

import pytest

from app.handlers.commands import HandlerContext, send_planner_response

class DummyPlanner:
    def __init__(self, result: str = "") -> None:
        self.result = result

    async def run(self, message: str, payload: dict) -> str:
        return self.result


class DummyMessage:
    def __init__(self) -> None:
        self.calls = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.calls.append((text, kwargs))


@pytest.mark.asyncio
async def test_send_planner_response_uses_escaped_fallback() -> None:
    message = DummyMessage()
    update = SimpleNamespace(message=message)
    planner = DummyPlanner(result="")
    handler_ctx = HandlerContext(
        db=None,
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

    await send_planner_response(update, context, "anything")

    assert len(message.calls) == 1
    text, kwargs = message.calls[0]
    assert text == "No recent data returned for that request."
    assert "parse_mode" not in kwargs or kwargs.get("parse_mode") is None
