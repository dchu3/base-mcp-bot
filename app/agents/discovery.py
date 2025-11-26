from typing import Any, Dict
from app.agents.base import BaseAgent
from app.agents.context import AgentContext


class DiscoveryAgent(BaseAgent):
    """Agent responsible for finding tokens via Dexscreener."""

    def __init__(self, model, mcp_manager):
        super().__init__("discovery", model, mcp_manager)

    async def run(self, context: AgentContext) -> Dict[str, Any]:
        # Normalize network to Dexscreener chain ID format
        network = context.network or "base"
        if network in ("base-mainnet", "base"):
            chain_id = "base"
        elif network == "base-sepolia":
            chain_id = "base"  # Dexscreener may not support testnet
        else:
            chain_id = "base"

        prompt = self._load_prompt(
            "discovery.md", message=context.message, chain_id=chain_id
        )
        return await self._plan_and_execute(prompt)
