"""Transaction parsing utilities for extracting token addresses from swaps."""

import re
from typing import Any, Dict, List, Optional, Set


# Common Uniswap V2/V3 swap method signatures
SWAP_METHODS = {
    "swapExactETHForTokens",
    "swapETHForExactTokens",
    "swapExactTokensForETH",
    "swapTokensForExactETH",
    "swapExactTokensForTokens",
    "swapTokensForExactTokens",
    "swapExactTokensForTokensSupportingFeeOnTransferTokens",
    "swapExactETHForTokensSupportingFeeOnTransferTokens",
    "swapExactTokensForETHSupportingFeeOnTransferTokens",
    # V3 methods
    "exactInputSingle",
    "exactOutputSingle",
    "exactInput",
    "exactOutput",
    "multicall",
}

# Address pattern
ADDRESS_PATTERN = re.compile(r"0x[a-fA-F0-9]{40}")


def extract_tokens_from_transactions(transactions: List[Dict[str, Any]]) -> List[str]:
    """Extract unique token addresses from swap transactions.

    Args:
        transactions: List of transaction data from Blockscout.

    Returns:
        List of unique token addresses found in the transactions.
    """
    addresses: Set[str] = set()

    for tx in transactions:
        method = tx.get("method") or tx.get("function") or ""

        # Check if it's a swap transaction
        is_swap = any(swap_method in method for swap_method in SWAP_METHODS)
        if not is_swap and "swap" not in method.lower():
            continue

        # Extract addresses from decoded input
        decoded = tx.get("decoded_input") or tx.get("decodedInput") or {}
        if isinstance(decoded, dict):
            addresses.update(_extract_addresses_from_decoded(decoded))

        # Extract from raw input data if available
        raw_input = tx.get("input") or tx.get("raw_input") or ""
        if raw_input and len(raw_input) > 10:
            addresses.update(_extract_addresses_from_raw(raw_input))

        # Extract from token transfers in the transaction
        transfers = tx.get("token_transfers") or tx.get("tokenTransfers") or []
        for transfer in transfers:
            token_addr = transfer.get("token_address") or transfer.get("tokenAddress")
            if token_addr:
                addresses.add(token_addr.lower())

        # Extract from logs if available
        logs = tx.get("logs") or []
        for log in logs:
            # Transfer event topic
            if log.get("topics") and len(log["topics"]) > 0:
                # Look for addresses in log data
                log_data = log.get("data") or ""
                addresses.update(_extract_addresses_from_raw(log_data))

    # Filter out common non-token addresses (routers, WETH, etc.)
    filtered = _filter_addresses(addresses)

    return list(filtered)


def _extract_addresses_from_decoded(decoded: Dict[str, Any]) -> Set[str]:
    """Extract addresses from decoded transaction input."""
    addresses: Set[str] = set()

    # Common parameter names for token addresses
    token_params = {"path", "tokenIn", "tokenOut", "token0", "token1", "token"}

    for key, value in decoded.items():
        if key in token_params:
            if isinstance(value, str) and ADDRESS_PATTERN.match(value):
                addresses.add(value.lower())
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and ADDRESS_PATTERN.match(item):
                        addresses.add(item.lower())

        # Recursively check nested objects
        if isinstance(value, dict):
            addresses.update(_extract_addresses_from_decoded(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    addresses.update(_extract_addresses_from_decoded(item))

    return addresses


def _extract_addresses_from_raw(data: str) -> Set[str]:
    """Extract addresses from raw hex data."""
    addresses: Set[str] = set()

    # Find all 40-character hex sequences that could be addresses
    # In raw data, addresses are often padded to 32 bytes (64 chars)
    # Look for patterns like 000000000000000000000000{40-char-address}

    if not data.startswith("0x"):
        data = "0x" + data

    # Remove 0x prefix for processing
    hex_data = data[2:] if data.startswith("0x") else data

    # Look for 32-byte padded addresses (24 zeros + 40 char address)
    padded_pattern = re.compile(r"0{24}([a-fA-F0-9]{40})")
    for match in padded_pattern.finditer(hex_data):
        addr = "0x" + match.group(1).lower()
        addresses.add(addr)

    return addresses


def _filter_addresses(addresses: Set[str]) -> Set[str]:
    """Filter out known non-token addresses."""
    # Common addresses to exclude (routers, WETH, null address)
    exclude = {
        "0x0000000000000000000000000000000000000000",  # Null
        "0x4200000000000000000000000000000000000006",  # Base WETH
        "0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24",  # Uniswap V2 Router
        "0x2626664c2603336e57b271c5c0b26f421741e481",  # Uniswap V3 Router
        "0xcf77a3ba9a5ca399b7c97c74d54e5b1beb874e43",  # Aerodrome Router
    }

    return {addr for addr in addresses if addr.lower() not in exclude}


def get_swap_direction(tx: Dict[str, Any]) -> Optional[str]:
    """Determine swap direction (buy/sell) from transaction."""
    method = tx.get("method") or tx.get("function") or ""

    if "ForETH" in method or "ForExactETH" in method:
        return "sell"  # Selling token for ETH
    elif "ETHFor" in method or "ExactETHFor" in method:
        return "buy"  # Buying token with ETH

    return None
