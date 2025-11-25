from typing import Any, Dict
from app.agents.base import BaseAgent
from app.agents.context import AgentContext


class MarketAgent(BaseAgent):
    """Agent responsible for on-chain market activity."""

    def __init__(self, model, mcp_manager):
        super().__init__("market", model, mcp_manager)

    async def run(self, context: AgentContext) -> Dict[str, Any]:
        # Format routers with addresses for the prompt
        router_lines = []
        network = context.network or "base-mainnet"
        # Normalize network name
        if network == "base":
            network = "base-mainnet"
        for key, networks in context.router_map.items():
            addr = networks.get(network)
            if addr:
                router_lines.append(f"- {key}: {addr}")
        routers_str = (
            "\n".join(router_lines) if router_lines else "No routers configured"
        )

        prompt = self._load_prompt(
            "market.md", message=context.message, routers=routers_str
        )
        return await self._plan_and_execute(prompt)
