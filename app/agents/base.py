"""Abstract base class for agents."""

import json
from abc import ABC, abstractmethod
from pathlib import Path
from string import Template
from typing import Any, Dict
import google.generativeai as genai

from app.mcp_client import MCPManager
from app.agents.context import AgentContext
from app.utils.logging import get_logger

logger = get_logger(__name__)


class BaseAgent(ABC):
    """Base class for specialized agents (Discovery, Safety, etc)."""

    def __init__(
        self, name: str, model: genai.GenerativeModel, mcp_manager: MCPManager
    ):
        self.name = name
        self.model = model
        self.mcp_manager = mcp_manager

    @abstractmethod
    async def run(self, context: AgentContext) -> Dict[str, Any]:
        """
        Execute the agent's main logic.

        Returns:
            Dict containing 'output' (text summary) and optionally 'data' (structured results).
        """
        pass

    async def _plan_and_execute(self, prompt: str) -> Dict[str, Any]:
        """Common logic: Generate JSON plan -> Execute tools -> Return results."""
        try:
            response = await self._generate_content(prompt)
            plan = self._parse_json(response)

            if not plan.get("tools"):
                return {"output": "No tools needed.", "data": []}

            results = []
            for tool in plan["tools"]:
                client = tool.get("client")
                method = tool.get("method")
                params = tool.get("params", {})

                if client and method:
                    res = await self._execute_tool(client, method, params)
                    results.append(res)

            return {"output": plan.get("reasoning", ""), "data": results}

        except Exception as exc:
            logger.error(f"agent_{self.name}_plan_failed", error=str(exc))
            return {"output": "", "data": [], "error": str(exc)}

    async def _generate_content(self, prompt: str) -> str:
        """Generate content from the LLM."""
        response = await self.model.generate_content_async(
            [{"role": "user", "parts": [{"text": prompt}]}],
            generation_config={"response_mime_type": "application/json"},
        )
        return response.text

    def _parse_json(self, text: str) -> Dict[str, Any]:
        """Parse JSON from LLM response."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Robust cleanup for markdown code blocks
            cleaned = text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            return json.loads(cleaned)

    def _load_prompt(self, filename: str, **kwargs) -> str:
        """Load and substitute a prompt template."""
        path = Path(f"prompts/agents/{filename}")
        if not path.exists():
            # Fallback for testing or different cwd
            path = Path(f"app/prompts/agents/{filename}")

        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {filename}")

        with open(path, "r") as f:
            template = Template(f.read())

        return template.safe_substitute(**kwargs)

    async def _execute_tool(
        self, client_name: str, method: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Helper to execute a single MCP tool."""
        try:
            client = getattr(self.mcp_manager, client_name, None)
            if not client:
                raise ValueError(f"MCP client '{client_name}' not available")

            logger.info(
                f"agent_{self.name}_call",
                client=client_name,
                method=method,
                params=params,
            )
            result = await client.call_tool(method, params)
            return {
                "call": {"client": client_name, "method": method, "params": params},
                "result": result,
            }
        except Exception as exc:
            logger.error(f"agent_{self.name}_error", error=str(exc))
            return {
                "call": {"client": client_name, "method": method, "params": params},
                "error": str(exc),
            }
