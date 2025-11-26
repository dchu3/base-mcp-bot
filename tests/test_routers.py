"""Tests for router utilities."""

from app.utils.routers import (
    DEFAULT_ROUTERS,
    ROUTER_ALIASES,
    get_router_display_name,
    list_routers,
    match_router_name,
)


class TestMatchRouterName:
    """Tests for router name matching."""

    def test_match_uniswap_default(self) -> None:
        """'uniswap' should match uniswap_v2 by default."""
        assert match_router_name("uniswap swaps") == "uniswap_v2"

    def test_match_uniswap_v3(self) -> None:
        """'uniswap v3' should match uniswap_v3."""
        assert match_router_name("uniswap v3 activity") == "uniswap_v3"

    def test_match_uni_alias(self) -> None:
        """'uni' should match uniswap_v2."""
        assert match_router_name("uni swaps") == "uniswap_v2"

    def test_match_aerodrome(self) -> None:
        """'aerodrome' should match aerodrome_v2."""
        assert match_router_name("aerodrome activity") == "aerodrome_v2"

    def test_match_aero_alias(self) -> None:
        """'aero' should match aerodrome_v2."""
        assert match_router_name("show me aero swaps") == "aerodrome_v2"

    def test_match_pancakeswap(self) -> None:
        """'pancake' should match pancakeswap_v2."""
        assert match_router_name("pancake trades") == "pancakeswap_v2"

    def test_match_pancake_v3(self) -> None:
        """'pancake v3' should match pancakeswap_v3."""
        assert match_router_name("pancakeswap v3 activity") == "pancakeswap_v3"

    def test_match_sushi(self) -> None:
        """'sushi' should match sushiswap_v2."""
        assert match_router_name("sushi swaps") == "sushiswap_v2"

    def test_no_match_returns_none(self) -> None:
        """Unknown router should return None."""
        assert match_router_name("random dex activity") is None

    def test_case_insensitive(self) -> None:
        """Matching should be case insensitive."""
        assert match_router_name("UNISWAP V3") == "uniswap_v3"
        assert match_router_name("Aerodrome") == "aerodrome_v2"


class TestGetRouterDisplayName:
    """Tests for router display name lookup."""

    def test_uniswap_v2(self) -> None:
        """Should return 'Uniswap V2'."""
        assert get_router_display_name("uniswap_v2") == "Uniswap V2"

    def test_aerodrome_v2(self) -> None:
        """Should return 'Aerodrome V2'."""
        assert get_router_display_name("aerodrome_v2") == "Aerodrome V2"

    def test_unknown_fallback(self) -> None:
        """Unknown key should generate title-cased name."""
        assert get_router_display_name("unknown_v1") == "Unknown V1"


class TestListRouters:
    """Tests for listing routers."""

    def test_list_returns_all_mainnet_routers(self) -> None:
        """Should return all routers with mainnet addresses."""
        routers = list_routers("base-mainnet")
        # Should have at least the main routers
        keys = [r[0] for r in routers]
        assert "uniswap_v2" in keys
        assert "aerodrome_v2" in keys

    def test_list_excludes_zero_address(self) -> None:
        """Routers with zero address should be excluded."""
        routers = list_routers("base-mainnet")
        for key, name, address in routers:
            assert address != "0x" + "0" * 40

    def test_list_includes_display_name(self) -> None:
        """Each router should include display name."""
        routers = list_routers("base-mainnet")
        for key, display_name, address in routers:
            assert display_name  # Not empty
            assert " " in display_name  # e.g., "Uniswap V2"


class TestRouterAliases:
    """Tests for router alias coverage."""

    def test_all_routers_have_aliases(self) -> None:
        """Every router in DEFAULT_ROUTERS should have at least one alias."""
        for router_key in DEFAULT_ROUTERS:
            found = False
            for alias, key in ROUTER_ALIASES.items():
                if key == router_key:
                    found = True
                    break
            assert found, f"No alias found for {router_key}"
