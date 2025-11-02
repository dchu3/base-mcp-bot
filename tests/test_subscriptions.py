import pytest
from telegram.error import BadRequest

from app.jobs.subscriptions import SubscriptionService
from app.store.db import Database, Subscription
from app.store.repository import Repository
from app.utils.routers import DEFAULT_ROUTERS


class DummyScheduler:
    def __init__(self) -> None:
        self.running = False

    def add_job(self, *args, **kwargs) -> None:  # pragma: no cover - not used in tests
        pass

    def start(self) -> None:  # pragma: no cover - not used in tests
        self.running = True

    def shutdown(self, wait: bool = False) -> None:  # pragma: no cover - not used in tests
        self.running = False


class DummyBaseClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def call_tool(self, method: str, params: dict) -> dict:
        self.calls.append((method, params))
        return self.payload


class DummyMCPManager:
    def __init__(self, base_client: DummyBaseClient):
        self.base = base_client


class DummyBot:
    def __init__(self) -> None:
        self.calls = []

    async def send_message(self, **kwargs) -> None:
        self.calls.append(kwargs)


class FallbackBot(DummyBot):
    def __init__(self) -> None:
        super().__init__()
        self.fail_once = True

    async def send_message(self, **kwargs) -> None:
        if kwargs.get("parse_mode") == "MarkdownV2" and self.fail_once:
            self.fail_once = False
            raise BadRequest("markdown error")
        await super().send_message(**kwargs)


class DummyPlanner:
    def __init__(self, summary: str | None) -> None:
        self.summary = summary
        self.calls = []

    async def summarize_transactions(self, router_key, transactions, network):
        self.calls.append((router_key, transactions, network))
        return self.summary


@pytest.mark.asyncio
async def test_process_subscription_handles_dict_payload(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    payload = {
        "router": "0x2626664c2603336E57B271c5C0b26F421741e481",
        "items": [
            {
                "hash": "0xabc",
                "method": "swap",
                "timestamp": "2024-01-01T00:00:00Z",
                "amount": "10 TOKEN",
            }
        ],
    }

    base_client = DummyBaseClient(payload)
    planner = DummyPlanner("Dex summary")
    service = SubscriptionService(
        scheduler=DummyScheduler(),
        db=db,
        mcp_manager=DummyMCPManager(base_client),
        planner=planner,
        routers=DEFAULT_ROUTERS,
        network="base-mainnet",
        bot=DummyBot(),
        interval_minutes=5,
        override_chat_id=None,
    )

    async with db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(555)
        subscription = Subscription(
            user_id=user.id,
            router_key="uniswap_v3",
            lookback_minutes=15,
        )

        await service._process_subscription(subscription, repo)
        assert len(service.bot.calls) == 1
        message_kwargs = service.bot.calls[0]
        assert message_kwargs["chat_id"] == user.chat_id
        assert message_kwargs["text"] == "Dex summary"
        assert message_kwargs["parse_mode"] == "MarkdownV2"
        assert planner.calls[0][0] == "uniswap_v3"

        assert len(base_client.calls) == 1
        method, params = base_client.calls[0]
        assert method == "getDexRouterActivity"
        assert params["router"] == DEFAULT_ROUTERS["uniswap_v3"]["base-mainnet"]
        assert params["sinceMinutes"] == 15

        assert await repo.is_seen("0xabc") is True

        await service._process_subscription(subscription, repo)
        assert len(service.bot.calls) == 1


@pytest.mark.asyncio
async def test_process_subscription_handles_nested_item_dict(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    payload = {
        "router": "0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24",
        "items": {
            "count": 1,
            "records": [
                {
                    "hash": "0xdef",
                    "method": "swap",
                    "timestamp": "2024-01-01T01:00:00Z",
                    "amount": "5 TOKEN",
                }
            ],
        },
    }

    base_client = DummyBaseClient(payload)
    bot = DummyBot()
    planner = DummyPlanner("Dex nested summary")
    service = SubscriptionService(
        scheduler=DummyScheduler(),
        db=db,
        mcp_manager=DummyMCPManager(base_client),
        planner=planner,
        routers=DEFAULT_ROUTERS,
        network="base-mainnet",
        bot=bot,
        interval_minutes=5,
    )

    async with db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(321)
        subscription = Subscription(
            user_id=user.id,
            router_key="uniswap_v2",
            lookback_minutes=10,
        )

        await service._process_subscription(subscription, repo)
        assert len(bot.calls) == 1
        assert bot.calls[0]["text"] == "Dex nested summary"


@pytest.mark.asyncio
async def test_process_subscription_ignores_unexpected_payload(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    base_client = DummyBaseClient("unexpected")
    planner = DummyPlanner("ignored")
    service = SubscriptionService(
        scheduler=DummyScheduler(),
        db=db,
        mcp_manager=DummyMCPManager(base_client),
        planner=planner,
        routers=DEFAULT_ROUTERS,
        network="base-mainnet",
        bot=DummyBot(),
        interval_minutes=5,
    )

    async with db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(999)
        subscription = Subscription(
            user_id=user.id,
            router_key="uniswap_v3",
            lookback_minutes=30,
        )

        await service._process_subscription(subscription, repo)
        assert service.bot.calls == []
        assert planner.calls == []


@pytest.mark.asyncio
async def test_process_subscription_falls_back_to_plain_text(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    payload = {
        "items": [
            {
                "hash": "0x987",
                "method": "swap(uint256,uint256)",
                "timestamp": "2024-01-02T00:00:00Z",
            }
        ]
    }

    base_client = DummyBaseClient(payload)
    bot = FallbackBot()
    planner = DummyPlanner("Dex fallback summary")
    service = SubscriptionService(
        scheduler=DummyScheduler(),
        db=db,
        mcp_manager=DummyMCPManager(base_client),
        planner=planner,
        routers=DEFAULT_ROUTERS,
        network="base-mainnet",
        bot=bot,
        interval_minutes=5,
    )

    async with db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(777)
        subscription = Subscription(
            user_id=user.id,
            router_key="uniswap_v2",
            lookback_minutes=20,
        )

        await service._process_subscription(subscription, repo)

    assert len(bot.calls) == 1
    assert bot.calls[0]["chat_id"] == user.chat_id
    assert "parse_mode" not in bot.calls[0]
    assert bot.calls[0]["text"] == "Dex fallback summary"


@pytest.mark.asyncio
async def test_process_subscription_handles_missing_summary(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    payload = {
        "items": [
            {
                "hash": "0xaaa",
                "method": "swap",
                "timestamp": "2024-01-03T00:00:00Z",
            }
        ]
    }

    base_client = DummyBaseClient(payload)
    planner = DummyPlanner(None)
    bot = DummyBot()
    service = SubscriptionService(
        scheduler=DummyScheduler(),
        db=db,
        mcp_manager=DummyMCPManager(base_client),
        planner=planner,
        routers=DEFAULT_ROUTERS,
        network="base-mainnet",
        bot=bot,
        interval_minutes=5,
    )

    async with db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(111)
        subscription = Subscription(
            user_id=user.id,
            router_key="uniswap_v2",
            lookback_minutes=25,
        )

        await service._process_subscription(subscription, repo)

    assert len(bot.calls) == 1
    payload = bot.calls[0]
    assert payload["parse_mode"] == "MarkdownV2"
    assert "No Dexscreener summaries for uniswap_v2" in payload["text"].replace("\\", "")
