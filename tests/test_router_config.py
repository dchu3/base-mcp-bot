import pytest
from app.utils.routers import load_router_map, resolve_router, DEFAULT_ROUTERS

def test_load_router_map_defaults():
    """Verify that load_router_map returns DEFAULT_ROUTERS when no path is provided."""
    routers = load_router_map()
    assert routers == DEFAULT_ROUTERS
    assert "sushiswap_v2" in routers

def test_sushiswap_v2_configuration():
    """Verify the specific configuration for sushiswap_v2."""
    routers = load_router_map()
    sushi = routers.get("sushiswap_v2")
    assert sushi is not None
    assert sushi["base-mainnet"] == "0x6BDED42c6DA8FBf0d2bA55B2fa120C5e0c8D7891"
    assert sushi["base-sepolia"] == "0x0000000000000000000000000000000000000000"

def test_resolve_router_sushiswap():
    """Verify that resolve_router correctly resolves sushiswap_v2."""
    routers = load_router_map()
    info = resolve_router("sushiswap_v2", "base-mainnet", routers)
    
    assert info.key == "sushiswap_v2"
    assert info.network == "base-mainnet"
    assert info.address == "0x6BDED42c6DA8FBf0d2bA55B2fa120C5e0c8D7891"

def test_resolve_router_unknown_key():
    """Verify error handling for unknown router keys."""
    routers = load_router_map()
    with pytest.raises(KeyError):
        resolve_router("unknown_router", "base-mainnet", routers)

def test_resolve_router_unknown_network():
    """Verify error handling for unknown networks."""
    routers = load_router_map()
    with pytest.raises(KeyError):
        resolve_router("sushiswap_v2", "mars-net", routers)
