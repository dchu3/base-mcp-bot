"""Helpers for resolving router metadata."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_ROUTERS: Dict[str, Dict[str, str]] = {
    "aerodrome_v2": {
        "base-mainnet": "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43",
        "base-sepolia": "0x0000000000000000000000000000000000000000",
    },
    "uniswap_v2": {
        "base-mainnet": "0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24",
        "base-sepolia": "0x0000000000000000000000000000000000000000",
    },
    "uniswap_v3": {
        "base-mainnet": "0x2626664c2603336E57B271c5C0b26F421741e481",
        "base-sepolia": "0x0000000000000000000000000000000000000000",
    },
    "uniswap_v4": {
        "base-mainnet": "0x6fF5693b99212Da76ad316178A184AB56D299b43",
        "base-sepolia": "0x0000000000000000000000000000000000000000",
    },
    "pancakeswap_v2": {
        "base-mainnet": "0x8cFe327CEc66d1C090Dd72bd0FF11d690C33a2Eb",
        "base-sepolia": "0xBbc55276a0b44A69955C3055333E085654F967b4",
    },
    "pancakeswap_v3": {
        "base-mainnet": "0x1b81D678ffb9C0263b24A97847620C99d213eB14",
        "base-sepolia": "0x0000000000000000000000000000000000000000",
    },
    "sushiswap_v2": {
        "base-mainnet": "0x6BDED42c6DA8FBf0d2bA55B2fa120C5e0c8D7891",
        "base-sepolia": "0x0000000000000000000000000000000000000000",
    },
}

# Display names for each router
ROUTER_DISPLAY_NAMES: Dict[str, str] = {
    "aerodrome_v2": "Aerodrome V2",
    "uniswap_v2": "Uniswap V2",
    "uniswap_v3": "Uniswap V3",
    "uniswap_v4": "Uniswap V4",
    "pancakeswap_v2": "PancakeSwap V2",
    "pancakeswap_v3": "PancakeSwap V3",
    "sushiswap_v2": "SushiSwap V2",
}

# Aliases for matching user input (lowercase)
ROUTER_ALIASES: Dict[str, str] = {
    # Aerodrome
    "aerodrome": "aerodrome_v2",
    "aerodrome v2": "aerodrome_v2",
    "aero": "aerodrome_v2",
    "aero v2": "aerodrome_v2",
    # Uniswap
    "uniswap": "uniswap_v2",
    "uniswap v2": "uniswap_v2",
    "uni": "uniswap_v2",
    "uni v2": "uniswap_v2",
    "uniswap v3": "uniswap_v3",
    "uni v3": "uniswap_v3",
    "uniswap v4": "uniswap_v4",
    "uni v4": "uniswap_v4",
    # PancakeSwap
    "pancakeswap": "pancakeswap_v2",
    "pancakeswap v2": "pancakeswap_v2",
    "pancake": "pancakeswap_v2",
    "pancake v2": "pancakeswap_v2",
    "cake": "pancakeswap_v2",
    "pancakeswap v3": "pancakeswap_v3",
    "pancake v3": "pancakeswap_v3",
    # SushiSwap
    "sushiswap": "sushiswap_v2",
    "sushiswap v2": "sushiswap_v2",
    "sushi": "sushiswap_v2",
    "sushi v2": "sushiswap_v2",
}

# Group aliases by router for display
ROUTER_ALIAS_GROUPS: Dict[str, List[str]] = {
    "aerodrome_v2": ["aero", "aerodrome"],
    "uniswap_v2": ["uni", "uniswap"],
    "uniswap_v3": ["uni v3", "uniswap v3"],
    "uniswap_v4": ["uni v4", "uniswap v4"],
    "pancakeswap_v2": ["cake", "pancake"],
    "pancakeswap_v3": ["pancake v3", "pancakeswap v3"],
    "sushiswap_v2": ["sushi", "sushiswap"],
}


@dataclass(frozen=True)
class RouterInfo:
    """Metadata describing a known router."""

    key: str
    network: str
    address: str


def load_router_map(path: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    """Load routers from JSON file or fall back to defaults."""
    if path is None:
        return DEFAULT_ROUTERS

    if not path.exists():
        raise FileNotFoundError(f"Router configuration not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    routers: Dict[str, Dict[str, str]] = {}
    for key, networks in data.items():
        routers[key] = {}
        for network, address in networks.items():
            routers[key][network] = address

    return routers


def resolve_router(
    router_key: str,
    network: str,
    routers: Dict[str, Dict[str, str]],
) -> RouterInfo:
    """Return router metadata for the requested key/network."""
    network_map = routers.get(router_key)
    if not network_map:
        raise KeyError(f"Unknown router key: {router_key}")

    address = network_map.get(network)
    if not address:
        raise KeyError(f"Router '{router_key}' has no address for network '{network}'")

    return RouterInfo(key=router_key, network=network, address=address)


def match_router_name(user_input: str) -> Optional[str]:
    """Match user input to a router key using aliases.

    Args:
        user_input: User's text that may contain a router name.

    Returns:
        Router key (e.g., "uniswap_v2") or None if no match.
    """
    lower_input = user_input.lower()

    # Sort aliases by length (longest first) to match more specific aliases first
    # e.g., "uniswap v3" should match before "uniswap"
    sorted_aliases = sorted(ROUTER_ALIASES.items(), key=lambda x: len(x[0]), reverse=True)

    for alias, key in sorted_aliases:
        if alias in lower_input:
            return key

    # Try matching base router name with version extraction
    version_match = re.search(r"v(\d)", lower_input)
    version = f"_v{version_match.group(1)}" if version_match else "_v2"

    for base_name in ["uniswap", "aerodrome", "pancakeswap", "sushiswap", "pancake"]:
        if base_name in lower_input:
            # Handle pancake -> pancakeswap
            if base_name == "pancake":
                base_name = "pancakeswap"
            candidate = f"{base_name}{version}"
            if candidate in DEFAULT_ROUTERS:
                return candidate
            # Fallback to v2 if version doesn't exist
            return f"{base_name}_v2"

    return None


def list_routers(network: str = "base-mainnet") -> List[Tuple[str, str, str]]:
    """List all available routers for a network.

    Args:
        network: Network name (default: "base-mainnet").

    Returns:
        List of (key, display_name, address) tuples for active routers.
    """
    result = []
    for key, networks in DEFAULT_ROUTERS.items():
        address = networks.get(network)
        if address and address != "0x0000000000000000000000000000000000000000":
            display_name = ROUTER_DISPLAY_NAMES.get(key, key)
            result.append((key, display_name, address))
    return result


def get_router_display_name(router_key: str) -> str:
    """Get the display name for a router key.

    Args:
        router_key: Internal router key (e.g., "uniswap_v2").

    Returns:
        Human-friendly display name (e.g., "Uniswap V2").
    """
    return ROUTER_DISPLAY_NAMES.get(router_key, router_key.replace("_", " ").title())
