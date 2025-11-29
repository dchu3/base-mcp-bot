"""Pattern-based intent matching for fast query routing."""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.utils.routers import match_router_name


class Intent(Enum):
    """Recognized user intents."""

    TOKEN_LOOKUP = "token_lookup"  # User provided a token address
    TOKEN_SEARCH = "token_search"  # User searching by name/symbol
    TRENDING = "trending"  # User wants trending/hot tokens
    POOL_ANALYTICS = "pool_analytics"  # User wants pool/liquidity data
    ROUTER_ACTIVITY = "router_activity"  # User wants DEX activity
    SAFETY_CHECK = "safety_check"  # User asking if token is safe
    WEB_SEARCH = "web_search"  # User wants to search the web
    UNKNOWN = "unknown"  # Fallback to LLM


@dataclass
class MatchedIntent:
    """Result of intent matching."""

    intent: Intent
    token_address: Optional[str] = None
    token_symbol: Optional[str] = None
    router_name: Optional[str] = None
    router_key: Optional[str] = None  # Internal router key (e.g., "uniswap_v2")
    search_query: Optional[str] = None  # Query for web search
    network: Optional[str] = None  # Target network (e.g., "base", "ethereum")
    confidence: float = 1.0


# Regex patterns
ADDRESS_PATTERN = re.compile(r"\b(0x[a-fA-F0-9]{40})\b")
TRENDING_KEYWORDS = {"trending", "hot", "popular", "boosted", "movers"}
POOL_KEYWORDS = {"pools", "pool", "liquidity", "tvl", "lp"}
ACTIVITY_KEYWORDS = {"activity", "swaps", "trades", "transactions", "volume", "transfers"}
SAFETY_KEYWORDS = {"safe", "scam", "rug", "honeypot", "risk", "legit"}
NETWORK_ALIASES = {
    "base": "base",
    "ethereum": "ethereum",
    "eth": "ethereum",
    "solana": "solana",
    "sol": "solana",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "optimism": "optimism",
    "op": "optimism",
    "polygon": "polygon",
    "matic": "polygon",
    "bsc": "bsc",
    "binance": "bsc",
    "avalanche": "avalanche",
    "avax": "avalanche",
}
WEB_SEARCH_PATTERNS = [
    r"search\s+(?:the\s+)?web\s+(?:for\s+)?(.+)",
    r"web\s+search\s+(?:for\s+)?(.+)",
    r"google\s+(.+)",
    r"look\s+up\s+(.+)",
    r"find\s+(?:info|information)\s+(?:on|about)\s+(.+)",
]


def match_intent(message: str) -> MatchedIntent:
    """Match user message to an intent using patterns.

    Args:
        message: The user's input message.

    Returns:
        MatchedIntent with the detected intent and extracted parameters.
    """
    lower_msg = message.lower().strip()

    # Check for explicit web search first
    for pattern in WEB_SEARCH_PATTERNS:
        match = re.search(pattern, lower_msg, re.IGNORECASE)
        if match:
            query = match.group(1).strip()
            return MatchedIntent(
                intent=Intent.WEB_SEARCH,
                search_query=query,
                confidence=0.95,
            )

    # Check for token address (highest priority)
    address_match = ADDRESS_PATTERN.search(message)
    if address_match:
        address = address_match.group(1)
        # Check if also asking about safety
        if any(kw in lower_msg for kw in SAFETY_KEYWORDS):
            return MatchedIntent(
                intent=Intent.SAFETY_CHECK,
                token_address=address,
                confidence=0.95,
            )
        return MatchedIntent(
            intent=Intent.TOKEN_LOOKUP,
            token_address=address,
            confidence=0.95,
        )

    # Check for trending/hot tokens
    if any(kw in lower_msg for kw in TRENDING_KEYWORDS):
        return MatchedIntent(intent=Intent.TRENDING, confidence=0.9)

    # Check for pool/liquidity analytics (before router activity)
    if any(kw in lower_msg for kw in POOL_KEYWORDS):
        # Extract network if mentioned
        network = "base"  # Default to Base
        for alias, net_id in NETWORK_ALIASES.items():
            if alias in lower_msg:
                network = net_id
                break
        return MatchedIntent(
            intent=Intent.POOL_ANALYTICS,
            network=network,
            confidence=0.9,
        )

    # Check for router/DEX activity
    if any(kw in lower_msg for kw in ACTIVITY_KEYWORDS):
        # Try to identify which router using the router module
        router_key = match_router_name(lower_msg)
        router_name = None
        if router_key:
            # Extract friendly name from key (e.g., "uniswap_v2" -> "uniswap")
            router_name = router_key.split("_")[0]
        return MatchedIntent(
            intent=Intent.ROUTER_ACTIVITY,
            router_name=router_name,
            router_key=router_key,
            confidence=0.85,
        )

    # Check for safety questions without address
    if any(kw in lower_msg for kw in SAFETY_KEYWORDS):
        # Try to extract a token symbol (capitalized word, 2-10 chars)
        symbol_match = re.search(r"\b([A-Z]{2,10})\b", message)
        if symbol_match:
            return MatchedIntent(
                intent=Intent.SAFETY_CHECK,
                token_symbol=symbol_match.group(1),
                confidence=0.7,
            )
        return MatchedIntent(intent=Intent.SAFETY_CHECK, confidence=0.5)

    # Check for token symbol search (capitalized words like PEPE, DOGE)
    symbol_match = re.search(r"\b([A-Z]{2,10})\b", message)
    if symbol_match:
        symbol = symbol_match.group(1)
        # Filter out common words
        if symbol not in {"THE", "AND", "FOR", "BUT", "NOT", "YOU", "ARE", "THIS"}:
            return MatchedIntent(
                intent=Intent.TOKEN_SEARCH,
                token_symbol=symbol,
                confidence=0.6,
            )

    # Unknown - fallback to LLM
    return MatchedIntent(intent=Intent.UNKNOWN, confidence=0.0)
