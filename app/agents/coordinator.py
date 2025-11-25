import json
from typing import Any, Dict, List
import google.generativeai as genai

from app.mcp_client import MCPManager
from app.agents.context import AgentContext
from app.agents.discovery import DiscoveryAgent
from app.agents.safety import SafetyAgent
from app.agents.market import MarketAgent
from app.planner_types import PlannerResult
from app.utils.formatting import escape_markdown
from app.utils.json_utils import parse_llm_json
from app.utils.logging import get_logger

logger = get_logger(__name__)


class CoordinatorAgent:
    """Top-level agent that orchestrates sub-agents. Does not inherit BaseAgent as it has a different interface."""

    MAX_ITERATIONS = 5

    def __init__(
        self,
        api_key: str,
        mcp_manager: MCPManager,
        model_name: str,
        router_map: Dict[str, Dict[str, str]],
    ):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name=model_name)
        self.mcp_manager = mcp_manager
        self.router_map = router_map
        self.agents = {
            "discovery": DiscoveryAgent(self.model, mcp_manager),
            "safety": SafetyAgent(self.model, mcp_manager),
            "market": MarketAgent(self.model, mcp_manager),
        }

    def _load_prompt(self, filename: str, **kwargs) -> str:
        """Load and substitute a prompt template."""
        from pathlib import Path
        from string import Template

        path = Path(f"prompts/agents/{filename}")
        if not path.exists():
            path = Path(f"app/prompts/agents/{filename}")

        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {filename}")

        with open(path, "r") as f:
            template = Template(f.read())

        return template.safe_substitute(**kwargs)

    async def _generate_content(self, prompt: str) -> str:
        """Generate content from the LLM."""
        response = await self.model.generate_content_async(
            [{"role": "user", "parts": [{"text": prompt}]}],
            generation_config={"response_mime_type": "application/json"},
        )
        return response.text

    def _parse_json(self, text: str) -> Dict[str, Any]:
        """Parse JSON from LLM response."""
        return parse_llm_json(text)

    async def run(self, message: str, context_data: Dict[str, Any]) -> PlannerResult:
        """Main entry point for the agent system."""

        # Initialize context
        ctx = AgentContext(
            message=message,
            network=context_data.get("network", "base"),
            conversation_history=context_data.get("conversation_history", []),
            router_map=self.router_map,
        )

        # Pre-populate context with recent tokens if available
        if context_data.get("recent_tokens"):
            ctx.add_tokens(context_data["recent_tokens"])

        for step in range(self.MAX_ITERATIONS):
            logger.info("coordinator_step", iteration=step + 1)

            # Prepare prompt
            prompt = self._load_prompt(
                "coordinator.md",
                message=ctx.message,
                history=json.dumps(ctx.conversation_history[-5:], default=str),
                found_tokens=json.dumps(ctx.get_recent_token_addresses(), default=str),
                tool_results=self._summarize_results(ctx.tool_results),
            )

            # Decide next step
            try:
                response = await self._generate_content(prompt)
                plan = self._parse_json(response)
            except Exception as exc:
                logger.error("coordinator_planning_failed", error=str(exc))
                return PlannerResult(
                    message="I encountered an error while planning. Please try again.",
                    tokens=[],
                )

            next_agent = plan.get("next_agent")
            reasoning = plan.get("reasoning")
            logger.info(
                "coordinator_decision", next_agent=next_agent, reasoning=reasoning
            )

            if next_agent == "FINISH":
                final_response = plan.get("final_response", "I'm done.")
                return PlannerResult(
                    message=escape_markdown(final_response),
                    tokens=ctx.found_tokens,
                )

            if next_agent not in self.agents:
                logger.warning("coordinator_invalid_agent", agent=next_agent)
                # Fallback: try to finish if agent is invalid
                return PlannerResult(
                    message=escape_markdown(
                        "I couldn't determine the next step correctly."
                    ),
                    tokens=ctx.found_tokens,
                )

            # Execute sub-agent
            agent = self.agents[next_agent]
            result = await agent.run(ctx)

            # Handle agent errors
            if result.get("error"):
                logger.warning(
                    "agent_returned_error", agent=next_agent, error=result["error"]
                )
                ctx.add_result({"agent": next_agent, "error": result["error"]})
                continue  # Let the LLM decide what to do next

            # Update context
            if result.get("data"):
                for item in result["data"]:
                    ctx.add_result(item)
                    # Extract tokens if discovery agent
                    if next_agent == "discovery":
                        self._extract_and_add_tokens(ctx, item)

            # Add agent output to results for visibility
            ctx.add_result({"agent": next_agent, "summary": result.get("output")})

        return PlannerResult(
            message=escape_markdown(
                "I reached the maximum number of steps without finishing."
            ),
            tokens=ctx.found_tokens,
        )

    def _summarize_results(self, results: List[Dict[str, Any]]) -> str:
        """Create a concise summary of what has been done."""
        summary = []
        for res in results:
            if "agent" in res:
                summary.append(f"Agent {res['agent']}: {res['summary']}")
            elif "call" in res:
                call = res["call"]
                if "error" in res:
                    summary.append(
                        f"Tool {call['client']}.{call['method']}: Error - {res['error'][:100]}"
                    )
                else:
                    # Include useful data from the result
                    result_data = res.get("result", {})
                    details = self._extract_result_details(call, result_data)
                    summary.append(
                        f"Tool {call['client']}.{call['method']}: Success - {details}"
                    )
        return "\n".join(summary) if summary else "No results yet"

    def _extract_result_details(self, call: Dict[str, Any], result: Any) -> str:
        """Extract key details from a tool result for the summary."""
        method = call.get("method", "")

        # Router activity
        if method == "getDexRouterActivity":
            if isinstance(result, dict) and "items" in result:
                items = result["items"]
                count = len(items) if isinstance(items, list) else 0
                if count > 0:
                    # Show first few transaction methods
                    methods = []
                    for tx in items[:3]:
                        if isinstance(tx, dict):
                            m = tx.get("method") or tx.get("function") or "txn"
                            methods.append(m)
                    return f"{count} transactions found (e.g., {', '.join(methods)})"
                return "0 transactions found"
            return "No transaction data"

        # Dexscreener pairs
        if method in ("searchPairs", "getPairsByToken"):
            pairs = []
            if isinstance(result, list):
                pairs = result
            elif isinstance(result, dict):
                pairs = result.get("pairs") or result.get("tokens") or []
            count = len(pairs) if isinstance(pairs, list) else 0
            if count > 0 and isinstance(pairs, list):
                symbols = []
                for p in pairs[:3]:
                    if isinstance(p, dict):
                        base = p.get("baseToken", {})
                        sym = base.get("symbol") if isinstance(base, dict) else None
                        if sym:
                            symbols.append(sym)
                return f"{count} pairs found (e.g., {', '.join(symbols)})"
            return f"{count} pairs found"

        # Honeypot check
        if method == "check_token":
            if isinstance(result, dict):
                summary = result.get("summary", {})
                verdict = (
                    summary.get("verdict", "UNKNOWN")
                    if isinstance(summary, dict)
                    else "UNKNOWN"
                )
                return f"Verdict: {verdict}"
            return "Check completed"

        # Generic fallback
        if isinstance(result, dict):
            keys = list(result.keys())[:3]
            return f"Data with keys: {', '.join(keys)}"
        if isinstance(result, list):
            return f"{len(result)} items"
        return "Completed"

    def _extract_and_add_tokens(
        self, ctx: AgentContext, result_item: Dict[str, Any]
    ) -> None:
        """Extract tokens from Dexscreener results and add to context.

        Handles multiple Dexscreener response formats:
        - List of pairs with baseToken objects
        - Dict with 'pairs' or 'tokens' array
        - Direct token objects with 'tokenAddress'

        Args:
            ctx: The agent context to add tokens to.
            result_item: A tool result dict containing 'result' key.
        """
        if "result" not in result_item:
            return

        data = result_item["result"]
        tokens = []

        # Handle different Dexscreener response formats
        if isinstance(data, dict):
            candidates = data.get("pairs") or data.get("tokens") or []
            if isinstance(candidates, list):
                tokens.extend(candidates)
        elif isinstance(data, list):
            tokens.extend(data)

        # Normalize and add
        valid_tokens = []
        for t in tokens:
            if isinstance(t, dict) and (t.get("baseToken") or t.get("tokenAddress")):
                # Basic normalization
                norm = t.copy()
                base_token = t.get("baseToken")
                if isinstance(base_token, dict):
                    norm["address"] = base_token.get("address")
                    norm["symbol"] = base_token.get("symbol")
                    norm["name"] = base_token.get("name")
                elif "tokenAddress" in t:
                    norm["address"] = t["tokenAddress"]

                if norm.get("address"):
                    valid_tokens.append(norm)

        ctx.add_tokens(valid_tokens)
