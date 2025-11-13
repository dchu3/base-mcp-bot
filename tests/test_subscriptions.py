from datetime import datetime, timedelta, timezone

import pytest
from telegram.error import BadRequest

from app.jobs.subscriptions import SubscriptionService
from app.planner import TokenSummary
from app.store.db import Database, Subscription
from app.store.repository import Repository
from app.utils.formatting import append_not_financial_advice
from app.utils.routers import DEFAULT_ROUTERS


class DummyScheduler:
    def __init__(self) -> None:
        self.running = False

    def add_job(self, *args, **kwargs) -> None:  # pragma: no cover - not used in tests
        pass

    def start(self) -> None:  # pragma: no cover - not used in tests
        self.running = True

    def shutdown(
        self, wait: bool = False
    ) -> None:  # pragma: no cover - not used in tests
        self.running = False


class DummyBaseClient:
    def __init__(self, payload, responses: dict | None = None):
        self.payload = payload
        self.responses = responses or {}
        self.calls = []

    async def call_tool(self, method: str, params: dict) -> dict:
        self.calls.append((method, params))
        return self.responses.get(method, self.payload)


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
    def __init__(self, summary: TokenSummary | None) -> None:
        self.summary = summary
        self.calls = []
        self.watch_summary = summary
        self.watch_calls = []

    async def summarize_transactions(self, router_key, transactions, network):
        self.calls.append((router_key, transactions, network))
        return self.summary

    async def summarize_tokens_from_context(self, addresses, label, network):
        self.watch_calls.append((addresses, label, network))
        return self.watch_summary


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
    planner = DummyPlanner(TokenSummary(message="Dex summary", tokens=[]))
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
    planner = DummyPlanner(TokenSummary(message="Dex nested summary", tokens=[]))
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
    planner = DummyPlanner(TokenSummary(message="ignored", tokens=[]))
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
    planner = DummyPlanner(TokenSummary(message="Dex fallback summary", tokens=[]))
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
    assert "No Dexscreener summaries for uniswap_v2" in payload["text"].replace(
        "\\", ""
    )


@pytest.mark.asyncio
async def test_watchlist_cycle_sends_summary(tmp_path) -> None:
    db_path = tmp_path / "watch.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    token_address = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    recent_ts = int((datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp())
    old_ts = int((datetime.now(timezone.utc) - timedelta(minutes=120)).timestamp())
    transfer_payload = {
        "items": [
            {
                "hash": "0xwatch-new",
                "timestamp": recent_ts,
                "from": "0x1111111111111111111111111111111111111111",
                "to": "0x2222222222222222222222222222222222222222",
                "amount": "123450000000000000000",
            },
            {
                "hash": "0xwatch-old",
                "timestamp": old_ts,
                "from": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "amount": "999",
            },
        ]
    }
    base_client = DummyBaseClient(
        payload={},
        responses={
            "getTokenTransfers": transfer_payload,
            "resolveToken": {
                "address": token_address,
                "name": "AAA Token",
                "symbol": "AAA",
                "decimals": 18,
                "totalSupply": None,
                "holders": None,
                "type": "ERC20",
            },
        },
    )
    bot = DummyBot()
    planner = DummyPlanner(None)
    planner.watch_summary = TokenSummary(
        message=append_not_financial_advice("Dex block"),
        tokens=[{"address": token_address, "symbol": "AAA"}],
    )
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
        user = await repo.get_or_create_user(202)
        await repo.add_watch_token(user.id, token_address, label="Alpha token")
        await repo.add_watch_token(user.id, token_address.upper())
        await service._process_watchlists(repo)
        tokens = await repo.list_watch_tokens(user.id)

    assert len(bot.calls) == 1
    message = bot.calls[0]
    assert "Dex block" in message["text"]
    plain_text = message["text"].replace("\\", "")
    assert "All tokens can rug pull" in plain_text
    assert SubscriptionService.WATCHLIST_TRANSFERS_DISABLED not in plain_text
    assert "0xwatch-old" not in plain_text
    assert "0xwatch-" in plain_text
    assert "Alpha token" in plain_text
    assert "123.45 AAA" in plain_text
    assert message["parse_mode"] == "MarkdownV2"
    called_methods = {call[0] for call in base_client.calls}
    assert "getTokenTransfers" in called_methods
    assert "resolveToken" in called_methods
    assert planner.watch_calls
    assert planner.watch_calls[0][1] == "watchlist (1 token)"
    assert tokens[0].token_symbol == "AAA"


@pytest.mark.asyncio
async def test_watchlist_cycle_handles_no_recent_transfers(tmp_path) -> None:
    db_path = tmp_path / "watch-none.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    token_address = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    base_client = DummyBaseClient(
        payload={},
        responses={
            "getTokenTransfers": {"items": []},
            "resolveToken": {
                "address": token_address,
                "name": "BBB Token",
                "symbol": "BBB",
                "decimals": 18,
                "totalSupply": None,
                "holders": None,
                "type": "ERC20",
            },
        },
    )
    bot = DummyBot()
    planner = DummyPlanner(
        TokenSummary(
            message=append_not_financial_advice("Dex block"),
            tokens=[{"address": token_address, "symbol": "BBB"}],
        )
    )
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
        user = await repo.get_or_create_user(303)
        await repo.add_watch_token(user.id, token_address, label="Beta token")
        await service._process_watchlists(repo)

    assert len(bot.calls) == 1
    message = bot.calls[0]["text"].replace("\\", "")
    assert SubscriptionService.WATCHLIST_TRANSFERS_DISABLED not in message
    assert "No transfers in the last" in message


@pytest.mark.asyncio
async def test_watchlist_cycle_handles_legacy_timestamp_field(tmp_path) -> None:
    db_path = tmp_path / "watch-legacy.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    token_address = "0xcccccccccccccccccccccccccccccccccccccccc"
    recent_ts = int((datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp())
    base_client = DummyBaseClient(
        payload={},
        responses={
            "getTokenTransfers": {
                "items": [
                    {
                        "hash": "0xlegacy",
                        "timeStamp": str(recent_ts),
                        "from": "0x3333333333333333333333333333333333333333",
                        "to": "0x4444444444444444444444444444444444444444",
                        "amount": "500000000000000000",
                    }
                ]
            },
            "resolveToken": {
                "address": token_address,
                "name": "CCC Token",
                "symbol": "CCC",
                "decimals": 18,
            },
        },
    )
    bot = DummyBot()
    planner = DummyPlanner(
        TokenSummary(
            message=append_not_financial_advice("Dex block"),
            tokens=[{"address": token_address, "symbol": "CCC"}],
        )
    )
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
        user = await repo.get_or_create_user(404)
        await repo.add_watch_token(user.id, token_address, label="Gamma token")
        await service._process_watchlists(repo)

    assert len(bot.calls) == 1
    plain_text = bot.calls[0]["text"].replace("\\", "")
    assert SubscriptionService.WATCHLIST_TRANSFERS_DISABLED not in plain_text
    assert "0xlegacy" in plain_text


@pytest.mark.asyncio
async def test_fetch_transfer_logs_retries_and_caches(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "watch-cache.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    db.connect()
    await db.init_models()

    class RaisingBase:
        def __init__(self) -> None:
            self.calls = 0

        async def call_tool(self, method: str, params: dict) -> dict:
            self.calls += 1
            raise RuntimeError("Service unavailable (524)")

    async def fake_sleep(_: float) -> None:  # pragma: no cover - patched in test
        return None

    monkeypatch.setattr("app.jobs.subscriptions.asyncio.sleep", fake_sleep)

    base_client = RaisingBase()
    service = SubscriptionService(
        scheduler=DummyScheduler(),
        db=db,
        mcp_manager=DummyMCPManager(base_client),
        planner=DummyPlanner(None),
        routers=DEFAULT_ROUTERS,
        network="base-mainnet",
        bot=DummyBot(),
        interval_minutes=5,
    )

    logs, metadata, error = await service._fetch_transfer_logs(
        "0x1111111111111111111111111111111111111111",
        SubscriptionService.MAX_WATCH_TRANSFER_FETCH,
    )

    assert logs == []
    assert metadata is None
    assert error == "Base explorer timed out fetching transfers."
    assert base_client.calls == service.TRANSFER_FETCH_RETRIES

    logs, metadata, error = await service._fetch_transfer_logs(
        "0x1111111111111111111111111111111111111111",
        SubscriptionService.MAX_WATCH_TRANSFER_FETCH,
    )

    assert logs == []
    assert metadata is None
    assert error == "Base explorer recovering from a recent timeout."
    assert base_client.calls == service.TRANSFER_FETCH_RETRIES
