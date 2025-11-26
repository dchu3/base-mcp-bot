"""Tests for transaction parsing utilities."""

from app.utils.tx_parser import (
    extract_tokens_from_transactions,
    get_swap_direction,
    _filter_addresses,
)


class TestFilterAddresses:
    """Tests for _filter_addresses function."""

    def test_filters_addresses_with_many_leading_zeros(self) -> None:
        """Addresses with >16 leading zeros should be filtered."""
        addresses = {
            "0x000000000000000000013b098a67cecfda9c72b4",  # 18 leading zeros
            "0x000000000000000000015cc466c961396e2b5d0c",  # 18 leading zeros
        }
        result = _filter_addresses(addresses)
        assert len(result) == 0

    def test_filters_addresses_with_low_entropy(self) -> None:
        """Addresses with <8 non-zero chars should be filtered."""
        addresses = {
            "0x0000000000000000000000000000000000000080",  # Only 2 non-zero
            "0x0000000000000000000000000000000000000002",  # Only 1 non-zero
        }
        result = _filter_addresses(addresses)
        assert len(result) == 0

    def test_passes_valid_token_addresses(self) -> None:
        """Valid token addresses should pass through."""
        addresses = {
            "0xdf3c90d5618f8ae66dcaf77f96d7e45393d7c920",
            "0x8890de1637912fbbba36b8b19365cdc99122bd6e",
            "0xcf0302c812435ea05c84ec60b9a3b57ae569cc81",
        }
        result = _filter_addresses(addresses)
        assert len(result) == 3
        assert "0xdf3c90d5618f8ae66dcaf77f96d7e45393d7c920" in result

    def test_filters_excluded_addresses(self) -> None:
        """Known non-token addresses should be filtered."""
        addresses = {
            "0x0000000000000000000000000000000000000000",  # Null
            "0x4200000000000000000000000000000000000006",  # Base WETH
            "0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24",  # Uniswap V2 Router
        }
        result = _filter_addresses(addresses)
        assert len(result) == 0

    def test_edge_case_exactly_16_leading_zeros(self) -> None:
        """Address with exactly 16 leading zeros should pass."""
        addresses = {"0x0000000000000000abcdef1234567890abcdef12"}
        result = _filter_addresses(addresses)
        assert len(result) == 1

    def test_edge_case_exactly_8_non_zero_chars(self) -> None:
        """Address with exactly 8 non-zero chars but few leading zeros should pass."""
        # This address has 8 non-zero chars and only 8 leading zeros
        addresses = {"0x00000000abcdef12abcdef12abcdef12abcdef12"}
        result = _filter_addresses(addresses)
        assert len(result) == 1


class TestGetSwapDirection:
    """Tests for get_swap_direction function."""

    def test_sell_for_eth(self) -> None:
        """Methods with ForETH should return sell."""
        assert get_swap_direction({"method": "swapExactTokensForETH"}) == "sell"
        assert get_swap_direction({"method": "swapTokensForExactETH"}) == "sell"

    def test_buy_with_eth(self) -> None:
        """Methods with ETHFor should return buy."""
        assert get_swap_direction({"method": "swapExactETHForTokens"}) == "buy"
        assert get_swap_direction({"method": "swapETHForExactTokens"}) == "buy"

    def test_token_to_token_returns_none(self) -> None:
        """Token-to-token swaps should return None."""
        assert get_swap_direction({"method": "swapExactTokensForTokens"}) is None

    def test_empty_method_returns_none(self) -> None:
        """Empty or missing method should return None."""
        assert get_swap_direction({"method": ""}) is None
        assert get_swap_direction({}) is None

    def test_function_field_fallback(self) -> None:
        """Should check function field if method is missing."""
        assert get_swap_direction({"function": "swapExactETHForTokens"}) == "buy"


class TestExtractTokensFromTransactions:
    """Tests for extract_tokens_from_transactions function."""

    def test_extracts_from_raw_input(self) -> None:
        """Should extract token addresses from rawInput field."""
        tx = {
            "method": "swapExactETHForTokens",
            "rawInput": (
                "0x7ff36ab5"
                "0000000000000000000000000000000000000000000000000000000000000000"
                "0000000000000000000000000000000000000000000000000000000000000080"
                "00000000000000000000000018220b970abee136f1fbee9bd4ea09bccb66f1ff"
                "00000000000000000000000000000000000000000000000000000000692658eb"
                "0000000000000000000000000000000000000000000000000000000000000002"
                "0000000000000000000000004200000000000000000000000000000000000006"
                "0000000000000000000000008890de1637912fbbba36b8b19365cdc99122bd6e"
            ),
        }
        tokens = extract_tokens_from_transactions([tx])
        # Should have the non-WETH token
        assert "0x8890de1637912fbbba36b8b19365cdc99122bd6e" in tokens

    def test_extracts_from_token_transfers(self) -> None:
        """Should extract from token_transfers field."""
        tx = {
            "method": "swap",
            "token_transfers": [
                {"token_address": "0xabcdef1234567890abcdef1234567890abcdef12"}
            ],
        }
        tokens = extract_tokens_from_transactions([tx])
        assert "0xabcdef1234567890abcdef1234567890abcdef12" in tokens

    def test_empty_transactions_returns_empty(self) -> None:
        """Empty transaction list should return empty list."""
        assert extract_tokens_from_transactions([]) == []

    def test_filters_invalid_addresses(self) -> None:
        """Should filter out invalid/parameter addresses."""
        tx = {
            "method": "swap",
            "rawInput": (
                "0x12345678"
                "0000000000000000000000000000000000000000000000000000000000000080"
                "0000000000000000000000008890de1637912fbbba36b8b19365cdc99122bd6e"
            ),
        }
        tokens = extract_tokens_from_transactions([tx])
        # Should only have the valid token, not the 0x...0080
        assert "0x8890de1637912fbbba36b8b19365cdc99122bd6e" in tokens
        assert all("0000000080" not in t for t in tokens)
