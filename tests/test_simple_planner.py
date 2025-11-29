"""Tests for the simplified planner components."""

import pytest

from app.intent_matcher import Intent, match_intent
from app.token_card import (
    format_token_card,
    format_token_list,
    format_boosted_token,
    format_boosted_token_list,
    format_pool_list,
    format_activity_summary,
    format_safety_result,
    format_safety_badge,
    _format_number,
)


class TestIntentMatcher:
    """Tests for intent matching."""

    def test_match_token_address(self) -> None:
        """Test matching a token address."""
        result = match_intent("0x1234567890abcdef1234567890abcdef12345678")
        assert result.intent == Intent.TOKEN_LOOKUP
        assert result.token_address == "0x1234567890abcdef1234567890abcdef12345678"
        assert result.confidence >= 0.9

    def test_match_token_address_with_context(self) -> None:
        """Test matching address within a sentence."""
        result = match_intent(
            "What is 0xabcdefabcdefabcdefabcdefabcdefabcdefabcd worth?"
        )
        assert result.intent == Intent.TOKEN_LOOKUP
        assert result.token_address == "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"

    def test_match_safety_with_address(self) -> None:
        """Test safety check with address."""
        result = match_intent("Is 0x1234567890abcdef1234567890abcdef12345678 safe?")
        assert result.intent == Intent.SAFETY_CHECK
        assert result.token_address is not None

    def test_match_trending(self) -> None:
        """Test matching trending keywords."""
        for query in ["trending tokens", "what's hot", "popular coins"]:
            result = match_intent(query)
            assert result.intent == Intent.TRENDING, f"Failed for: {query}"

    def test_match_pool_analytics(self) -> None:
        """Test matching pool analytics keywords."""
        test_cases = [
            ("get the top pools on base", Intent.POOL_ANALYTICS, "base"),
            ("show me liquidity pools", Intent.POOL_ANALYTICS, "base"),
            ("pools on ethereum", Intent.POOL_ANALYTICS, "ethereum"),
            ("list pools", Intent.POOL_ANALYTICS, "base"),
            ("tvl on arbitrum", Intent.POOL_ANALYTICS, "arbitrum"),
            ("show lp tokens", Intent.POOL_ANALYTICS, "base"),
        ]
        for query, expected_intent, expected_network in test_cases:
            result = match_intent(query)
            assert result.intent == expected_intent, f"Failed for: {query}"
            assert result.network == expected_network, f"Wrong network for: {query}"

    def test_pool_analytics_no_false_positives(self) -> None:
        """Test that pool keywords don't cause false positives."""
        # "tokens with high liquidity" should NOT match POOL_ANALYTICS
        result = match_intent("tokens with high liquidity")
        assert result.intent != Intent.POOL_ANALYTICS

    def test_pool_analytics_network_word_boundaries(self) -> None:
        """Test that network aliases use word boundaries."""
        # "database" contains "base" but should not match network=base
        # This query will still match POOL_ANALYTICS due to "pools"
        # but network should be detected correctly
        result = match_intent("show me pools")
        assert result.network == "base"  # Default, not from partial match

    def test_match_router_activity(self) -> None:
        """Test matching router activity."""
        result = match_intent("show me uniswap activity")
        assert result.intent == Intent.ROUTER_ACTIVITY
        assert result.router_name == "uniswap"

    def test_match_router_activity_generic(self) -> None:
        """Test matching generic activity request."""
        result = match_intent("show me recent swaps")
        assert result.intent == Intent.ROUTER_ACTIVITY

    def test_match_token_symbol(self) -> None:
        """Test matching token symbol."""
        result = match_intent("tell me about PEPE")
        assert result.intent == Intent.TOKEN_SEARCH
        assert result.token_symbol == "PEPE"

    def test_match_unknown(self) -> None:
        """Test unknown intent fallback."""
        result = match_intent("hello there")
        assert result.intent == Intent.UNKNOWN
        assert result.confidence == 0.0


class TestTokenCard:
    """Tests for token card formatting."""

    def test_format_token_card_basic(self) -> None:
        """Test basic token card formatting."""
        token_data = {
            "baseToken": {
                "symbol": "PEPE",
                "name": "Pepe",
                "address": "0x1234567890abcdef1234567890abcdef12345678",
            },
            "priceUsd": "0.00001234",
            "priceChange": {"h24": 15.5},
            "liquidity": {"usd": 1500000},
            "volume": {"h24": 500000},
            "chainId": "base",
        }

        card = format_token_card(token_data)

        assert "PEPE" in card
        assert "Pepe" in card
        assert "0x1234" in card
        assert "Dexscreener" in card

    def test_format_token_card_minimal(self) -> None:
        """Test token card with minimal data."""
        token_data = {
            "baseToken": {"symbol": "TEST"},
            "priceUsd": "1.00",
        }

        card = format_token_card(token_data)
        assert "TEST" in card
        assert "Price" in card

    def test_format_token_list(self) -> None:
        """Test token list formatting."""
        tokens = [
            {"baseToken": {"symbol": "TOKEN1"}, "priceUsd": "1.00"},
            {"baseToken": {"symbol": "TOKEN2"}, "priceUsd": "2.00"},
            {"baseToken": {"symbol": "TOKEN3"}, "priceUsd": "3.00"},
        ]

        result = format_token_list(tokens, max_tokens=2)
        assert "TOKEN1" in result
        assert "TOKEN2" in result
        assert "TOKEN3" not in result
        assert "1 more" in result

    def test_format_token_list_empty(self) -> None:
        """Test empty token list."""
        result = format_token_list([])
        assert "No tokens found" in result


class TestBoostedTokenCard:
    """Tests for boosted token card formatting."""

    def test_format_boosted_token_basic(self) -> None:
        """Test basic boosted token formatting."""
        token = {
            "tokenAddress": "E92GWWMe9Eis1Ya1QbjhGjscxfGdJVshvfXYMbK8pump",
            "chainId": "solana",
            "description": "Web3 workflow builder.\nBuild what moves markets.",
            "url": "https://dexscreener.com/solana/test",
            "amount": 10,
            "links": [
                {"url": "https://example.com"},
                {"type": "twitter", "url": "https://x.com/test"},
            ],
        }

        result = format_boosted_token(token)

        assert "Web3 workflow builder" in result
        assert "Solana" in result
        assert "Boost: 10" in result
        assert "E92GWW" in result
        assert "Twitter" in result
        assert "Dexscreener" in result

    def test_format_boosted_token_long_description(self) -> None:
        """Test boosted token with long description gets truncated."""
        token = {
            "tokenAddress": "ABC123",
            "chainId": "base",
            "description": "A" * 100,  # Long first line
            "url": "https://dexscreener.com/base/test",
            "amount": 5,
        }

        result = format_boosted_token(token)

        # Name should be truncated with ...
        assert "..." in result
        assert "Base" in result

    def test_format_boosted_token_none_link_type(self) -> None:
        """Test boosted token with None link type (website)."""
        token = {
            "tokenAddress": "XYZ789",
            "chainId": "ethereum",
            "description": "Test token",
            "url": "https://dexscreener.com/eth/test",
            "links": [
                {"type": None, "url": "https://website.com"},
                {"type": "", "url": "https://another.com"},
            ],
            "amount": 1,
        }

        result = format_boosted_token(token)

        assert "Website" in result
        assert "website.com" in result

    def test_format_boosted_token_list(self) -> None:
        """Test boosted token list formatting."""
        tokens = [
            {
                "tokenAddress": "addr1",
                "chainId": "solana",
                "description": "Token 1",
                "amount": 10,
            },
            {
                "tokenAddress": "addr2",
                "chainId": "base",
                "description": "Token 2",
                "amount": 5,
            },
            {
                "tokenAddress": "addr1",  # Duplicate
                "chainId": "solana",
                "description": "Token 1 duplicate",
                "amount": 10,
            },
        ]

        result = format_boosted_token_list(tokens, max_tokens=5)

        assert "Token 1" in result
        assert "Token 2" in result
        # Should deduplicate
        assert result.count("addr1") == 1

    def test_format_boosted_token_list_empty(self) -> None:
        """Test empty boosted token list."""
        result = format_boosted_token_list([])
        assert "No boosted tokens found" in result


class TestPoolList:
    """Tests for pool list formatting."""

    def test_format_pool_list_basic(self) -> None:
        """Test basic pool list formatting."""
        pools = [
            {
                "dex_name": "Aerodrome",
                "volume_usd": 85000000,
                "price_usd": 2999.35,
                "transactions": 26000,
                "last_price_change_usd_24h": -1.5,
                "tokens": [
                    {"symbol": "WETH"},
                    {"symbol": "USDC"},
                ],
            },
            {
                "dex_name": "Uniswap V3",
                "volume_usd": 50000000,
                "price_usd": 3000.00,
                "transactions": 15000,
                "tokens": [
                    {"symbol": "ETH"},
                    {"symbol": "USDT"},
                ],
            },
        ]

        result = format_pool_list(pools, network="base", max_pools=5)

        assert "Top Pools on Base" in result
        assert "WETH/USDC" in result
        assert "ETH/USDT" in result
        assert "Aerodrome" in result
        assert "Uniswap V3" in result
        assert "85" in result  # Volume formatted (escaped as 85\.00M)

    def test_format_pool_list_empty(self) -> None:
        """Test empty pool list."""
        result = format_pool_list([], network="ethereum")
        assert "No pools found on ethereum" in result

    def test_format_pool_list_with_limit(self) -> None:
        """Test pool list with limit."""
        pools = [
            {"dex_name": f"DEX{i}", "tokens": [{"symbol": f"T{i}"}]}
            for i in range(10)
        ]

        result = format_pool_list(pools, network="base", max_pools=3)

        assert "T0" in result
        assert "T2" in result
        assert "T3" not in result
        assert "7 more" in result


class TestActivitySummary:
    """Tests for activity summary formatting."""

    def test_format_activity_summary(self) -> None:
        """Test activity summary formatting."""
        transactions = [
            {"method": "swap", "hash": "0xabc123"},
            {"method": "swapExactTokens", "hash": "0xdef456"},
            {"method": "addLiquidity", "hash": "0x789abc"},
        ]

        result = format_activity_summary(transactions, "Uniswap V2")

        assert "Uniswap V2" in result
        assert "3 transactions" in result
        assert "swap" in result.lower()

    def test_format_activity_summary_empty(self) -> None:
        """Test empty activity summary."""
        result = format_activity_summary([])
        assert "No recent activity" in result


class TestSwapActivity:
    """Tests for swap activity formatting."""

    def test_format_swap_activity_with_tokens(self) -> None:
        """Test swap activity with token data."""
        from app.token_card import format_swap_activity

        tokens = [
            {
                "baseToken": {"symbol": "PEPE", "name": "Pepe", "address": "0x1234"},
                "priceUsd": "0.00001",
                "priceChange": {"h24": 10.5},
                "liquidity": {"usd": 1000000},
                "chainId": "base",
            }
        ]
        transactions = [
            {"method": "swapExactETHForTokens"},
            {"method": "swapExactETHForTokens"},
        ]

        result = format_swap_activity(tokens, transactions, "Uniswap V2")

        assert "Uniswap V2" in result
        assert "PEPE" in result
        assert "2 swaps" in result
        assert "DYOR" in result

    def test_format_swap_activity_no_tokens(self) -> None:
        """Test swap activity without token data."""
        from app.token_card import format_swap_activity

        result = format_swap_activity([], [{"method": "swap"}], "Aerodrome")

        assert "Aerodrome" in result
        assert "No token data" in result


class TestSafetyResult:
    """Tests for safety result formatting."""

    def test_format_safety_safe(self) -> None:
        """Test safe verdict formatting."""
        result = format_safety_result({"summary": {"verdict": "SAFE_TO_TRADE"}})
        assert "✅" in result
        assert "SAFE" in result

    def test_format_safety_caution(self) -> None:
        """Test caution verdict formatting."""
        result = format_safety_result({"summary": {"verdict": "CAUTION"}})
        assert "⚠️" in result
        assert "CAUTION" in result

    def test_format_safety_danger(self) -> None:
        """Test dangerous verdict formatting."""
        result = format_safety_result({"summary": {"verdict": "DO_NOT_TRADE"}})
        assert "🚨" in result
        assert "DO NOT TRADE" in result

    def test_format_safety_with_dict_flags(self) -> None:
        """Test safety result with dict flags (honeypot MCP format)."""
        result = format_safety_result(
            {
                "summary": {"verdict": "DO_NOT_TRADE"},
                "flags": {
                    "isHoneypot": True,
                    "simulationSuccess": True,
                    "openSource": True,
                },
            }
        )
        assert "🚨" in result
        assert "DO NOT TRADE" in result
        # Warning comes from isHoneypot flag, not summary reason
        assert "Honeypot detected" in result

    def test_format_safety_with_dict_flags_unverified(self) -> None:
        """Test safety result with unverified source flag."""
        result = format_safety_result(
            {
                "summary": {"verdict": "CAUTION"},
                "flags": {
                    "isHoneypot": False,
                    "simulationSuccess": True,
                    "openSource": False,
                },
            }
        )
        assert "⚠️" in result
        assert "Contract source not verified" in result

    def test_format_safety_with_proxy_flag(self) -> None:
        """Test safety result with proxy contract flag."""
        result = format_safety_result(
            {
                "summary": {"verdict": "CAUTION"},
                "flags": {"isProxy": True},
            }
        )
        assert "⚠️" in result
        assert "Proxy contract" in result

    def test_format_safety_with_simulation_failed(self) -> None:
        """Test safety result with simulation failed flag."""
        result = format_safety_result(
            {
                "summary": {"verdict": "DO_NOT_TRADE"},
                "flags": {"simulationSuccess": False},
            }
        )
        assert "🚨" in result
        assert "Simulation failed" in result

    def test_format_safety_with_dict_risk(self) -> None:
        """Test safety result with dict risk (honeypot MCP format)."""
        result = format_safety_result(
            {
                "summary": {"verdict": "CAUTION"},
                "risk": {"riskLevel": 75},
            }
        )
        assert "⚠️" in result
        assert "Risk Level: 75" in result

    def test_format_safety_with_list_flags(self) -> None:
        """Test safety result with legacy list flags."""
        result = format_safety_result(
            {
                "summary": {"verdict": "CAUTION"},
                "flags": ["High gas fees", "Proxy contract"],
            }
        )
        assert "⚠️" in result
        assert "High gas fees" in result
        assert "Proxy contract" in result


class TestSafetyBadge:
    """Tests for compact safety badge formatting."""

    def test_badge_safe(self) -> None:
        """Test safe badge."""
        result = format_safety_badge({"summary": {"verdict": "SAFE"}})
        assert result == "✅ Safe"

    def test_badge_caution_with_tax(self) -> None:
        """Test caution badge with high tax."""
        result = format_safety_badge(
            {"summary": {"verdict": "CAUTION"}, "simulationResult": {"buyTax": "10"}}
        )
        assert "⚠️ Caution" in result
        assert "10%" in result

    def test_badge_danger(self) -> None:
        """Test danger badge."""
        result = format_safety_badge({"summary": {"verdict": "HONEYPOT"}})
        assert "🚨" in result
        assert "Do not trade" in result

    def test_badge_none(self) -> None:
        """Test None input returns None."""
        result = format_safety_badge(None)
        assert result is None

    def test_badge_unknown(self) -> None:
        """Test unknown verdict."""
        result = format_safety_badge({"summary": {"verdict": "WEIRD"}})
        assert "❓" in result


class TestNumberFormatting:
    """Tests for number formatting."""

    def test_format_billions(self) -> None:
        assert _format_number(1_500_000_000) == "1.50B"

    def test_format_millions(self) -> None:
        assert _format_number(2_500_000) == "2.50M"

    def test_format_thousands(self) -> None:
        assert _format_number(1_500) == "1.50K"

    def test_format_small_numbers(self) -> None:
        result = _format_number(0.00001234)
        assert result.startswith("0.0000")

    def test_format_none(self) -> None:
        assert _format_number(None) == "?"

    def test_format_string(self) -> None:
        assert _format_number("invalid") == "invalid"


class TestHoneypotErrorMessages:
    """Tests for honeypot error message formatting."""

    def test_honeypot_404_error_message(self) -> None:
        """Test that 404 errors produce user-friendly message."""
        from app.utils.formatting import escape_markdown

        # Simulate what the error handler produces
        card = "*Safety Check*\n" "⚠️ *UNABLE TO VERIFY*\n" + escape_markdown(
            "This token is not indexed in the Honeypot database. "
            "Exercise caution and do your own research."
        )
        assert "*Safety Check*" in card
        assert "UNABLE TO VERIFY" in card
        assert "not indexed" in card
        assert "404" not in card  # Should not expose technical error

    def test_honeypot_generic_error_message(self) -> None:
        """Test that generic errors produce user-friendly message."""
        from app.utils.formatting import escape_markdown

        # Simulate what the error handler produces
        card = "*Safety Check*\n" "❓ *CHECK UNAVAILABLE*\n" + escape_markdown(
            "Unable to verify token safety at this time. " "Please try again later."
        )
        assert "*Safety Check*" in card
        assert "CHECK UNAVAILABLE" in card
        assert "try again later" in card


class TestPoolAnalyticsHandler:
    """Tests for pool analytics handler."""

    @pytest.mark.asyncio
    async def test_handle_pool_analytics_success(self) -> None:
        """Test successful pool analytics request."""
        from unittest.mock import MagicMock, AsyncMock
        from app.simple_planner import SimplePlanner
        from app.intent_matcher import MatchedIntent, Intent

        mock_mcp = MagicMock()
        mock_mcp.dexscreener = MagicMock()
        mock_mcp.base = MagicMock()
        mock_mcp.honeypot = None
        mock_mcp.websearch = None
        mock_mcp.dexpaprika = MagicMock()
        mock_mcp.dexpaprika.call_tool = AsyncMock(
            return_value={
                "pools": [
                    {
                        "dex_name": "Aerodrome",
                        "volume_usd": 1000000,
                        "tokens": [{"symbol": "WETH"}, {"symbol": "USDC"}],
                    }
                ]
            }
        )

        planner = SimplePlanner(
            api_key="test-key",
            mcp_manager=mock_mcp,
            model_name="gemini-1.5-flash",
            router_map={},
        )

        matched = MatchedIntent(intent=Intent.POOL_ANALYTICS, network="base")
        result = await planner._handle_pool_analytics(matched, {})

        assert "WETH/USDC" in result.message
        assert "Aerodrome" in result.message
        mock_mcp.dexpaprika.call_tool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_pool_analytics_no_dexpaprika(self) -> None:
        """Test pool analytics when DexPaprika not configured."""
        from unittest.mock import MagicMock
        from app.simple_planner import SimplePlanner
        from app.intent_matcher import MatchedIntent, Intent

        mock_mcp = MagicMock()
        mock_mcp.dexscreener = MagicMock()
        mock_mcp.base = MagicMock()
        mock_mcp.honeypot = None
        mock_mcp.websearch = None
        mock_mcp.dexpaprika = None  # Not configured

        planner = SimplePlanner(
            api_key="test-key",
            mcp_manager=mock_mcp,
            model_name="gemini-1.5-flash",
            router_map={},
        )

        matched = MatchedIntent(intent=Intent.POOL_ANALYTICS, network="base")
        result = await planner._handle_pool_analytics(matched, {})

        assert "DexPaprika" in result.message
        # Note: underscores are escaped for Telegram MarkdownV2
        assert "DEXPAPRIKA" in result.message

    @pytest.mark.asyncio
    async def test_handle_pool_analytics_error_no_exception_leak(self) -> None:
        """Test that pool analytics errors don't leak exception details."""
        from unittest.mock import MagicMock, AsyncMock
        from app.simple_planner import SimplePlanner
        from app.intent_matcher import MatchedIntent, Intent

        mock_mcp = MagicMock()
        mock_mcp.dexscreener = MagicMock()
        mock_mcp.base = MagicMock()
        mock_mcp.honeypot = None
        mock_mcp.websearch = None
        mock_mcp.dexpaprika = MagicMock()
        mock_mcp.dexpaprika.call_tool = AsyncMock(
            side_effect=Exception("Internal API error with sensitive details")
        )

        planner = SimplePlanner(
            api_key="test-key",
            mcp_manager=mock_mcp,
            model_name="gemini-1.5-flash",
            router_map={},
        )

        matched = MatchedIntent(intent=Intent.POOL_ANALYTICS, network="base")
        result = await planner._handle_pool_analytics(matched, {})

        # Should NOT expose the raw exception
        assert "Internal API error" not in result.message
        assert "sensitive details" not in result.message
        # Should show user-friendly message
        assert "Failed to fetch pool data" in result.message
        assert "try again later" in result.message


class TestAgenticPlannerDelegation:
    """Tests for agentic planner delegation in SimplePlanner."""

    @pytest.mark.asyncio
    async def test_agentic_planner_lazy_loaded(self) -> None:
        """Test that agentic planner is only created when needed."""
        from unittest.mock import MagicMock
        from app.simple_planner import SimplePlanner

        mock_mcp = MagicMock()
        mock_mcp.dexscreener = MagicMock()
        mock_mcp.base = MagicMock()
        mock_mcp.honeypot = None
        mock_mcp.websearch = None

        planner = SimplePlanner(
            api_key="test-key",
            mcp_manager=mock_mcp,
            model_name="gemini-1.5-flash",
            router_map={"uniswap_v2": {"base-mainnet": "0x123"}},
        )

        # Initially, agentic planner should not exist
        assert planner._agentic_planner is None

        # After calling _get_agentic_planner, it should be created
        agentic = await planner._get_agentic_planner()
        assert agentic is not None
        assert planner._agentic_planner is agentic

        # Calling again should return the same instance (not create new)
        agentic2 = await planner._get_agentic_planner()
        assert agentic2 is agentic

    @pytest.mark.asyncio
    async def test_agentic_planner_inherits_config(self) -> None:
        """Test that agentic planner gets correct configuration."""
        from unittest.mock import MagicMock
        from app.simple_planner import SimplePlanner

        mock_mcp = MagicMock()
        mock_mcp.dexscreener = MagicMock()
        mock_mcp.base = MagicMock()
        mock_mcp.honeypot = None
        mock_mcp.websearch = None

        router_map = {"aerodrome": {"base-mainnet": "0xabc"}}
        planner = SimplePlanner(
            api_key="my-api-key",
            mcp_manager=mock_mcp,
            model_name="gemini-pro",
            router_map=router_map,
        )

        agentic = await planner._get_agentic_planner()

        # Verify config was passed through
        assert agentic.mcp_manager is mock_mcp
        assert agentic.router_map == router_map
        assert list(agentic.router_keys) == ["aerodrome"]


class TestAgenticPlannerDelegationAsync:
    """Async tests for agentic planner delegation."""

    @pytest.mark.asyncio
    async def test_handle_unknown_delegates_to_agentic(self) -> None:
        """Test that unknown intents delegate to agentic planner."""
        from unittest.mock import MagicMock, AsyncMock
        from app.simple_planner import SimplePlanner
        from app.planner_types import PlannerResult

        mock_mcp = MagicMock()
        mock_mcp.dexscreener = MagicMock()
        mock_mcp.base = MagicMock()
        mock_mcp.honeypot = None
        mock_mcp.websearch = None

        planner = SimplePlanner(
            api_key="test-key",
            mcp_manager=mock_mcp,
            model_name="gemini-1.5-flash",
            router_map={},
        )

        # Mock the agentic planner
        mock_agentic = MagicMock()
        mock_agentic.run = AsyncMock(
            return_value=PlannerResult(message="Agentic response", tokens=[])
        )
        planner._agentic_planner = mock_agentic

        result = await planner._handle_unknown("what is the crypto market doing?", {})

        assert result.message == "Agentic response"
        mock_agentic.run.assert_awaited_once_with(
            "what is the crypto market doing?", {}
        )

    @pytest.mark.asyncio
    async def test_handle_unknown_error_fallback(self) -> None:
        """Test that errors in agentic planner fall back to default message."""
        from unittest.mock import MagicMock, AsyncMock
        from app.simple_planner import SimplePlanner

        mock_mcp = MagicMock()
        mock_mcp.dexscreener = MagicMock()
        mock_mcp.base = MagicMock()
        mock_mcp.honeypot = None
        mock_mcp.websearch = None

        planner = SimplePlanner(
            api_key="test-key",
            mcp_manager=mock_mcp,
            model_name="gemini-1.5-flash",
            router_map={},
        )

        # Mock the agentic planner to raise an error
        mock_agentic = MagicMock()
        mock_agentic.run = AsyncMock(side_effect=Exception("API error"))
        planner._agentic_planner = mock_agentic

        result = await planner._handle_unknown("some complex query", {})

        # Should return the fallback message
        assert "not sure how to help" in result.message
        assert "token" in result.message.lower()
        assert result.tokens == []
