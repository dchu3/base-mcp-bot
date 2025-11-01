import types

from app.planner import GeminiPlanner, ToolInvocation


def _make_planner() -> GeminiPlanner:
    # Bypass __init__ to avoid external dependencies; only formatting helpers are used.
    planner = object.__new__(GeminiPlanner)
    return planner  # type: ignore[return-value]


def test_normalize_tx_formats_timestamp() -> None:
    planner = _make_planner()
    tx = {
        "hash": "0xabcdef1234567890abcdef1234567890abcdef12",
        "timestamp": 1_700_000_000,
        "method": "swap",
        "value": "42",
        "url": "https://example.org/tx/0xabcdef1234567890abcdef1234567890abcdef12",
    }

    normalized = planner._normalize_tx(tx)

    assert normalized["hash"] == "0xabcdef1234567890abcdef1234567890abcdef12"
    assert normalized["method"] == "swap"
    assert normalized["amount"] == "42"
    assert normalized["timestamp"].endswith("Z")


def test_format_router_activity_produces_message() -> None:
    planner = _make_planner()
    call = ToolInvocation(
        client="base",
        method="getDexRouterActivity",
        params={"router": "0xRouter", "routerKey": "uniswap_v3"},
    )
    tx = {
        "hash": "0xabcdef1234567890abcdef1234567890abcdef12",
        "timestamp": 1_700_000_100,
        "method": "swap",
        "value": "100",
        "url": "https://example.org/tx/0xabcdef1234567890abcdef1234567890abcdef12",
    }
    output = planner._format_router_activity(call, {"items": [tx]})

    assert "Recent transactions for" in output
    assert "swap" in output


def test_normalize_token_handles_pairs() -> None:
    planner = _make_planner()
    pair = {
        "chainId": "base",
        "pairAddress": "0xpair",
        "baseToken": {"symbol": "AAA"},
        "quoteToken": {"symbol": "BBB"},
        "priceUsd": "1.23",
        "volume": {"h24": 1000},
        "liquidity": {"usd": 50000},
        "priceChange": {"h24": 12.5},
        "url": "https://dexscreener.com/base/0xpair",
    }
    normalized = planner._normalize_token(pair)
    assert normalized["symbol"] == "AAA/BBB"
    assert normalized["price"] == "1.23"
    assert normalized["volume24h"] == "1000"
    assert normalized["liquidity"] == "50000"
    assert normalized["change24h"] == "12.5"
    assert normalized["url"].endswith("/0xpair")


def test_extract_token_entries_handles_list() -> None:
    planner = _make_planner()
    pair = {
        "chainId": "base",
        "pairAddress": "0xpair",
        "baseToken": {"symbol": "AAA"},
        "quoteToken": {"symbol": "BBB"},
        "priceUsd": "1.00",
    }
    entries = planner._extract_token_entries([pair])
    assert entries[0]["symbol"] == "AAA/BBB"


def test_derive_chain_id_defaults_to_base() -> None:
    planner = _make_planner()
    assert planner._derive_chain_id(None) == "base"
    assert planner._derive_chain_id("base-mainnet") == "base"
    assert planner._derive_chain_id("base-sepolia") == "base"
    assert planner._derive_chain_id("arbitrum-mainnet") == "arbitrum"


def test_render_response_prefers_token_summaries() -> None:
    planner = _make_planner()
    base_call = ToolInvocation(
        client="base",
        method="getDexRouterActivity",
        params={"router": "0xRouter", "routerKey": "uniswap_v3"},
    )
    dex_call = ToolInvocation(
        client="dexscreener",
        method="getPairsByToken",
        params={"chainId": "base", "tokenAddress": "0xToken"},
    )

    tx = {
        "hash": "0xabc",
        "timestamp": 1_700_000_001,
        "method": "swap",
        "value": "1",
        "url": "https://example.org/tx/0xabc",
    }
    token_pair = {
        "chainId": "base",
        "pairAddress": "0xpair",
        "baseToken": {"symbol": "AAA"},
        "quoteToken": {"symbol": "BBB"},
        "priceUsd": "1.00",
    }

    response = planner._render_response(
        "msg",
        {"network": "base-mainnet"},
        [
            {"call": base_call, "result": {"items": [tx]}},
            {"call": dex_call, "result": [token_pair]},
        ],
    )

    assert "Recent transactions" not in response
    assert "Dexscreener snapshots for uniswap\\_v3" in response
    assert "AAA/BBB" in response
