"""Tests for the simplified planner components."""

from app.intent_matcher import Intent, match_intent
from app.token_card import (
    format_token_card,
    format_token_list,
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
        for query in ["trending tokens", "what's hot", "top tokens", "popular coins"]:
            result = match_intent(query)
            assert result.intent == Intent.TRENDING, f"Failed for: {query}"

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
                "summary": {"verdict": "DO_NOT_TRADE", "reason": "Honeypot detected"},
                "flags": {
                    "isHoneypot": True,
                    "simulationSuccess": True,
                    "openSource": True,
                },
            }
        )
        assert "🚨" in result
        assert "DO NOT TRADE" in result
        assert "Honeypot detected" in result

    def test_format_safety_with_dict_flags_unverified(self) -> None:
        """Test safety result with unverified source flag."""
        result = format_safety_result(
            {
                "summary": {"verdict": "CAUTION", "reason": "High risk"},
                "flags": {
                    "isHoneypot": False,
                    "simulationSuccess": True,
                    "openSource": False,
                },
            }
        )
        assert "⚠️" in result
        assert "Contract source not verified" in result

    def test_format_safety_with_list_flags(self) -> None:
        """Test safety result with legacy list flags."""
        result = format_safety_result(
            {
                "summary": {"verdict": "CAUTION", "reason": "Some risks"},
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
