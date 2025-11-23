"""Helpers for resolving router metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

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
        "base-mainnet": "0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24",
        "base-sepolia": "0x0000000000000000000000000000000000000000",
    },
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
