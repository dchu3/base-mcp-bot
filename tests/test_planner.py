import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

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


def test_allows_empty_params_for_paramless_dex_tools() -> None:
    planner = _make_planner()
    assert planner._allows_empty_params("dexscreener", "getLatestBoostedTokens")
    assert planner._allows_empty_params("dexscreener", "getMostActiveBoostedTokens")
    assert not planner._allows_empty_params("dexscreener", "searchPairs")


def test_format_recent_tokens_outputs_json() -> None:
    planner = _make_planner()
    tokens = [
        {
            "symbol": "AAA/BBB",
            "baseSymbol": "AAA",
            "name": "Token AAA",
            "address": "0xabc",
            "source": "uniswap_v3",
        }
    ]
    payload = planner._format_recent_tokens(tokens)
    data = json.loads(payload)
    assert data[0]["baseSymbol"] == "AAA"
    assert data[0]["source"] == "uniswap_v3"


def test_select_honeypot_targets_prefers_liquid_pair() -> None:
    planner = _make_planner()
    tokens = [
        {"address": "0xabc", "pairAddress": "0xpair1", "liquidity": "100"},
        {"address": "0xabc", "pairAddress": "0xpair2", "liquidity": "1000"},
    ]
    targets = planner._select_honeypot_targets([{"tokens": tokens}], {})
    assert targets
    assert targets[0].pair == "0xpair2"


def test_get_cached_pair_expires() -> None:
    planner = _make_planner()
    planner._honeypot_discovery_cache = {"token:0": (time.time() - 1, "0xpair")}
    assert planner._get_cached_pair("token:0") is None


def test_derive_chain_id_defaults_to_base() -> None:
    planner = _make_planner()
    assert planner._derive_chain_id(None) == "base"
    assert planner._derive_chain_id("base-mainnet") == "base"
    assert planner._derive_chain_id("base-sepolia") == "base"
    assert planner._derive_chain_id("arbitrum-mainnet") == "arbitrum"


def test_render_response_prefers_token_summaries() -> None:
    """Token summaries should appear before transaction list."""
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

    assert isinstance(response.message, str)
    # Token summaries should appear before transactions
    dex_pos = response.message.find("Dexscreener snapshots")
    tx_pos = response.message.find("Recent transactions")
    assert dex_pos != -1, "Dexscreener snapshots should be present"
    assert tx_pos != -1, "Recent transactions should be present"
    assert dex_pos < tx_pos, "Token summaries should appear before transactions"
    assert "AAA/BBB" in response.message
    assert response.tokens


@pytest.mark.asyncio
async def test_evaluate_honeypot_discovers_after_pair_failure() -> None:
    planner = _make_planner()
    planner._honeypot_missing_cache = {}
    planner._honeypot_discovery_cache = {}

    class FakeHoneypotClient:
        def __init__(self) -> None:
            self.check_calls = 0

        async def call_tool(self, method: str, params: dict) -> dict:
            if method == "check_token":
                self.check_calls += 1
                if self.check_calls == 1:
                    raise RuntimeError("Request failed with status code 404")
                return {
                    "summary": {"verdict": "SAFE_TO_TRADE", "reason": "ok"},
                    "raw": {"contractCode": {"openSource": True}},
                }
            if method == "discover_pairs":
                return {
                    "pairs": [
                        {"pair": "0xdiscovered", "liquidityUsd": "12345"},
                    ]
                }
            return {}

    client = FakeHoneypotClient()
    verdict = await planner._evaluate_honeypot_target(
        client, "0x1234567890abcdef1234567890abcdef12345678", 8453, "0xbroken"
    )

    assert verdict and verdict["verdict"] == "SAFE_TO_TRADE"
    assert client.check_calls == 2


@pytest.mark.asyncio
async def test_summarize_transactions_returns_token_summary() -> None:
    planner = _make_planner()

    class FakeDex:
        def __init__(self) -> None:
            self.calls = []

        async def call_tool(self, method: str, params: dict) -> list:
            self.calls.append((method, params))
            return [
                {
                    "chainId": "base",
                    "pairAddress": "0xpair",
                    "baseToken": {"symbol": "AAA"},
                    "quoteToken": {"symbol": "BBB"},
                    "priceUsd": "1.01",
                    "volume": {"h24": 1234},
                    "liquidity": {"usd": 5678},
                }
            ]

    fake_dex = FakeDex()
    planner.mcp_manager = SimpleNamespace(dexscreener=fake_dex)

    transactions = [
        {"hash": "0x1", "token0Address": "0xToken"},
        {"hash": "0x2", "token1Address": "0xToken"},
    ]

    summary = await planner.summarize_transactions(
        "uniswap_v3", transactions, "base-mainnet"
    )

    assert summary is not None
    assert "Dexscreener snapshots for uniswap\\_v3" in summary.message
    assert "AAA/BBB" in summary.message
    assert summary.tokens
    assert fake_dex.calls


@pytest.mark.asyncio
async def test_execute_single_tool_attaches_tokens_for_paramless_calls() -> None:
    planner = _make_planner()

    class FakeDex:
        def __init__(self) -> None:
            self.calls = []

        async def call_tool(self, method: str, params: dict) -> dict:
            self.calls.append((method, params))
            return {
                "pairs": [
                    {
                        "pairAddress": "0xpair",
                        "baseToken": {"symbol": "AAA"},
                        "quoteToken": {"symbol": "BBB"},
                        "priceUsd": "1.00",
                    }
                ]
            }

    planner.mcp_manager = SimpleNamespace(dexscreener=FakeDex())

    call = ToolInvocation(
        client="dexscreener", method="getLatestBoostedTokens", params={}
    )

    result = await planner._execute_single_tool(call)

    assert "tokens" in result
    assert result["tokens"][0]["symbol"] == "AAA/BBB"


def test_format_prior_results() -> None:
    """Test formatting of prior results for prompt injection."""
    planner = _make_planner()

    # Empty results
    assert planner._format_prior_results([]) == "none"
    assert planner._format_prior_results(None) == "none"

    # Router activity result
    results = [
        {
            "call": ToolInvocation(
                client="base",
                method="getDexRouterActivity",
                params={"router": "uniswap_v3"},
            ),
            "result": {"items": [{"hash": "0x1"}, {"hash": "0x2"}]},
        }
    ]

    formatted = planner._format_prior_results(results)
    assert "base.getDexRouterActivity: 2 transactions" in formatted

    # Dexscreener result with tokens
    results_with_tokens = [
        {
            "call": ToolInvocation(
                client="dexscreener", method="searchPairs", params={"query": "PEPE"}
            ),
            "result": {},
            "tokens": [{"symbol": "PEPE/WETH"}, {"symbol": "PEPE/USDC"}],
        }
    ]

    formatted2 = planner._format_prior_results(results_with_tokens)
    assert "dexscreener.searchPairs: PEPE/WETH, PEPE/USDC" in formatted2

    # Error result
    results_with_error = [
        {
            "call": ToolInvocation(client="honeypot", method="check_token", params={}),
            "error": "Token not found on network",
        }
    ]

    formatted3 = planner._format_prior_results(results_with_error)
    assert "honeypot.check_token: FAILED" in formatted3


def test_is_plan_complete_heuristics() -> None:
    """Test the heuristic for determining if a plan needs refinement."""
    planner = _make_planner()

    # Plan is complete when no errors and all tokens fetched
    dex_call = ToolInvocation(client="dexscreener", method="searchPairs", params={})
    complete_results = [{"call": dex_call, "result": {}}]

    assert planner._is_plan_complete(complete_results, "check PEPE", [dex_call])

    # Plan is incomplete if there are errors
    results_with_error = [{"call": dex_call, "error": "Failed"}]
    assert not planner._is_plan_complete(results_with_error, "check PEPE", [dex_call])

    # Plan is incomplete if user wants tokens but no dex calls made
    router_call = ToolInvocation(
        client="base", method="getDexRouterActivity", params={}
    )
    router_results = [{"call": router_call, "result": {}}]

    assert not planner._is_plan_complete(
        router_results,
        "show me token prices",  # Contains "token" keyword
        [router_call],
    )

    # Plan is incomplete if router activity found tokens but no dex calls
    planner._extract_token_entries = lambda x: [{"symbol": "TEST"}]  # Mock method
    assert not planner._is_plan_complete(router_results, "what's moving", [router_call])


def test_summarize_results_for_refinement() -> None:
    """Test summary generation for refinement prompts."""
    planner = _make_planner()

    results = [
        {
            "call": ToolInvocation(
                client="base", method="getDexRouterActivity", params={}
            ),
            "result": {"items": [1, 2, 3]},
        },
        {
            "call": ToolInvocation(
                client="dexscreener", method="searchPairs", params={}
            ),
            "result": {},
            "tokens": [{"symbol": "PEPE"}, {"symbol": "DOGE"}],
        },
        {
            "call": ToolInvocation(client="honeypot", method="check_token", params={}),
            "error": "Not found",
        },
    ]

    summary = planner._summarize_results_for_refinement(results)

    assert "base.getDexRouterActivity: SUCCESS (3 items)" in summary
    assert "dexscreener.searchPairs: SUCCESS (tokens: PEPE (?), DOGE (?))" in summary
    assert "honeypot.check_token: ERROR (Not found)" in summary


def test_build_refinement_prompt() -> None:
    """Test construction of refinement prompt."""
    planner = _make_planner()

    message = "What's moving on Base?"
    context = {"network": "base-mainnet"}
    results_summary = "base.getDexRouterActivity: SUCCESS (5 items)"

    prompt = planner._build_refinement_prompt(message, context, results_summary)

    assert "What's moving on Base?" in prompt
    assert "base.getDexRouterActivity: SUCCESS (5 items)" in prompt
    assert "additional tools" in prompt
    assert '{"tools": []}' in prompt
    assert "Respond with JSON only" in prompt


def test_normalize_resolve_token_valid() -> None:
    planner = _make_planner()
    params = {"query": "0x1234567890123456789012345678901234567890"}
    normalized = planner._normalize_params("base", "resolveToken", params)
    assert normalized == {"address": "0x1234567890123456789012345678901234567890"}


def test_normalize_resolve_token_invalid() -> None:
    planner = _make_planner()
    params = {"query": "CHARLIE"}
    normalized = planner._normalize_params("base", "resolveToken", params)
    assert normalized == {}


def test_normalize_resolve_token_with_address_key() -> None:
    planner = _make_planner()
    params = {"address": "0x1234567890123456789012345678901234567890"}
    normalized = planner._normalize_params("base", "resolveToken", params)
    assert normalized == {"address": "0x1234567890123456789012345678901234567890"}


@pytest.mark.asyncio
async def test_handle_chitchat_escapes_markdown() -> None:
    planner = _make_planner()
    planner.model = MagicMock()
    # Mock the Gemini response
    mock_response = MagicMock()
    mock_candidate = MagicMock()
    mock_part = MagicMock()
    mock_part.text = "Hello! I can help you with tokens."
    mock_candidate.content.parts = [mock_part]
    mock_response.candidates = [mock_candidate]

    # Mock generate_content to return the mock response
    planner.model.generate_content.return_value = mock_response

    context = {"conversation_history": []}
    result = await planner._handle_chitchat("Hi", context)

    # The text "Hello! I can help you with tokens." contains '!', which is reserved in MarkdownV2
    # It should be escaped to "Hello\! I can help you with tokens\."

    assert "\\" in result.message
    assert result.message == "Hello\\! I can help you with tokens\\."
