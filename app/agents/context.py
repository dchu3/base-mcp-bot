"""Shared context for the hierarchical agent system."""

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class AgentContext:
    """State shared between agents during a request lifecycle.

    This context object is passed between the Coordinator and sub-agents,
    allowing them to share discovered tokens, tool results, and configuration.

    Attributes:
        message: The original user request.
        network: The blockchain network (e.g., 'base', 'base-mainnet').
        conversation_history: Recent messages for context.
        found_tokens: Tokens discovered by agents (deduplicated by address).
        tool_results: Results from MCP tool calls.
        router_map: DEX router addresses keyed by name and network.
    """

    message: str
    network: str = "base"
    conversation_history: List[Dict[str, str]] = field(default_factory=list)

    # State shared between agents
    found_tokens: List[Dict[str, Any]] = field(default_factory=list)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)

    # Configuration
    router_map: Dict[str, Dict[str, str]] = field(default_factory=dict)

    def add_result(self, result: Dict[str, Any]) -> None:
        """Record a tool execution result."""
        self.tool_results.append(result)

    def add_tokens(self, tokens: List[Dict[str, Any]]) -> None:
        """Register discovered tokens for downstream agents with deduplication."""
        seen_addresses = {
            (t.get("address") or t.get("tokenAddress", "")).lower()
            for t in self.found_tokens
            if t.get("address") or t.get("tokenAddress")
        }
        for token in tokens:
            addr = token.get("address") or token.get("tokenAddress")
            if addr and isinstance(addr, str):
                addr_lower = addr.lower()
                if addr_lower not in seen_addresses:
                    self.found_tokens.append(token)
                    seen_addresses.add(addr_lower)

    def get_recent_token_addresses(self) -> List[str]:
        """Return addresses of tokens found in this session."""
        # Tokens are already deduplicated by add_tokens, just extract addresses
        addresses = []
        for t in self.found_tokens:
            addr = t.get("address") or t.get("tokenAddress")
            if addr and isinstance(addr, str):
                addresses.append(addr)
        return addresses
