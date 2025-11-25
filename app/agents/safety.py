import json
from typing import Any, Dict
from app.agents.base import BaseAgent
from app.agents.context import AgentContext


class SafetyAgent(BaseAgent):
    """Agent responsible for checking token safety via Honeypot."""

    def __init__(self, model, mcp_manager):
        super().__init__("safety", model, mcp_manager)

    async def run(self, context: AgentContext) -> Dict[str, Any]:
        # Format found tokens for the prompt
        # We only send a simplified list to save tokens
        simplified_tokens = []
        for t in context.found_tokens:
            simplified_tokens.append(
                {
                    "symbol": t.get("symbol"),
                    "address": t.get("address") or t.get("tokenAddress"),
                    "name": t.get("name"),
                }
            )

        tokens_str = json.dumps(simplified_tokens, default=str)

        prompt = self._load_prompt(
            "safety.md", message=context.message, found_tokens=tokens_str
        )
        return await self._plan_and_execute(prompt)
