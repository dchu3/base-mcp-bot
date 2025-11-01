"""Helpers for resolving router metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

DEFAULT_ROUTERS: Dict[str, Dict[str, str]] = {
    "uniswap_v3": {
        "base-mainnet": "0x925CacB0cb1cBBF56371A199b2eC27405a58Ce09",
        "base-sepolia": "0x0000000000000000000000000000000000000000",
    },
    "aerodrome_v2": {
        "base-mainnet": "0xC5cf4D1A00000000000000000000000000000000",
        "base-sepolia": "0x0000000000000000000000000000000000000000",
    },
    "pancakeswap_v3": {
        "base-mainnet": "0x0000000000000000000000000000000000000000",
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
