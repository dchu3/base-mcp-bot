"""Gemini-powered planner that selects MCP tool calls."""

from __future__ import annotations

import asyncio
import json
import re
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple
from string import Template

import google.generativeai as genai

from app.mcp_client import MCPManager
from app.utils.formatting import (
    append_not_financial_advice,
    escape_markdown,
    format_token_summary,
    format_transaction,
    join_messages,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ToolInvocation:
    """Planned tool call produced by Gemini."""

    client: str
    method: str
    params: Dict[str, Any]


@dataclass
class PlanPayload:
    """Parsed planner response from Gemini."""

    confidence: float
    clarification: str | None
    tools: List[ToolInvocation]


@dataclass
class HoneypotTarget:
    """Token (and optional pair) queued for honeypot checks."""

    token: str
    pair: str | None = None


@dataclass
class PlannerResult:
    """Rendered planner response plus normalized token context."""

    message: str
    tokens: List[Dict[str, str]]


@dataclass
class TokenSummary:
    """Reusable Dexscreener summary payload."""

    message: str
    tokens: List[Dict[str, str]]


class GeminiPlanner:
    """Use Gemini to decide which MCP tools to call."""

    MAX_ROUTER_ITEMS = 8
    MAX_HONEYPOT_CHECKS = 6
    HONEYPOT_DISCOVERY_TTL_SECONDS = 900
    HONEYPOT_NOT_FOUND_TTL_SECONDS = 600
    DEX_TOKEN_METHODS = {
        "getPairsByToken",
        "getTokenOverview",
        "searchPairs",
        "getPairByAddress",
    }

    CLASSIFICATION_PROMPT = Template(
        textwrap.dedent(
            """
            You are a router for a crypto trading bot.
            Analyze the user message: "$message"
            
            Determine if this requires using external tools (Dexscreener, Honeypot, Base) or if it is general conversation.
            
            - TOOL_USE: "Price of PEPE", "Is this safe?", "Check 0x123...", "What's trending?", "Scan this token", "Analyze the last one".
            - CHITCHAT: "Hello", "How are you?", "What can you do?", "Thanks", "Help", "Good morning".
            
            Respond with JSON: {"intent": "TOOL_USE" | "CHITCHAT"}
            """
        ).strip()
    )

    SYNTHESIS_PROMPT = Template(
        textwrap.dedent(
            """
            You are a crypto assistant. Answer the user's question based ONLY on the tool results below.

            User Question: "$message"

            Tool Results:
            $results

            Instructions:
            - Be conversational, concise, and helpful.
            - Synthesize the data into a natural summary (don't just list JSON fields).
            - If comparing tokens, highlight key differences (price, liquidity, safety).
            - Mention any honeypot risks clearly (SAFE, CAUTION, DO_NOT_TRADE).
            - If the tool failed or returned no data, explain that simply.
            - Do not invent data.
            - Do not use markdown formatting (no backticks, bold, or italics). Use plain text.
            """
        ).strip()
    )

    DEFAULT_PROMPT = Template(
        textwrap.dedent(
            """
            You are an orchestrator for a Base blockchain Telegram bot that surfaces trading opportunities.

            Follow this workflow:
            1. Analyse the user request: "$message".
            2. If the user is asking about router activity, determine the relevant router key(s) for network "$network" from: $routers and call base.getDexRouterActivity with the user's lookback (fallback $default_lookback minutes).
            3. If the user is asking about a token (e.g. "use Dexscreener for LUNA") or references a cached token, prefer calling Dexscreener tools directly using the hints below instead of polling routers.
            4. Cached tokens (recent router context + user watchlist hints): $recent_tokens. Use these when matching user intent to token lookups. Most recent router summary: $recent_router.
            5. Cross-reference discovered token addresses with Dexscreener tools to evaluate price action, liquidity, and unusual volume so you can highlight opportunities or noteworthy movements.
            6. For each highlighted token, call honeypot.check_token to classify it as SAFE_TO_TRADE, CAUTION, or DO_NOT_TRADE and mention that verdict in your summary.
            7. If needed, call other supporting tools (e.g. transaction lookups) to clarify context.

            Available tools (client.method):
            $tool_definitions

            Respond strictly as JSON with this schema:
            {
                "reasoning": "thought process...",
                "confidence": 1.0,
                "clarification": null,
                "tools": [{"client": "base|dexscreener|honeypot", "method": "<method>", "params": {...}}]
            }
            """
        ).strip()
    )

    def __init__(
        self,
        api_key: str,
        mcp_manager: MCPManager,
        router_keys: Sequence[str],
        router_map: Dict[str, Dict[str, str]],
        model_name: str,
        prompt_template: str | None = None,
        confidence_threshold: float = 0.7,
        enable_reflection: bool = True,
        max_iterations: int = 2,
    ) -> None:
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name=model_name)
        self.mcp_manager = mcp_manager
        self.router_keys = router_keys
        self._prompt_template = (
            Template(prompt_template)
            if prompt_template is not None
            else self.DEFAULT_PROMPT
        )
        self.router_map = router_map
        self._honeypot_discovery_cache: Dict[str, Tuple[float, str | None]] = {}
        self._honeypot_missing_cache: Dict[str, float] = {}
        self.confidence_threshold = confidence_threshold
        self.enable_reflection = enable_reflection
        self.max_iterations = max_iterations

    async def run(self, message: str, context: Dict[str, Any]) -> PlannerResult:
        """Execute plan with optional iterative refinement."""
        # Step 0: Intent Classification
        intent = await self._classify_intent(message)
        if intent == "CHITCHAT":
            logger.info("planner_intent_chitchat", message=message)
            return await self._handle_chitchat(message, context)

        iteration = 1
        all_results = []

        # Initial planning pass
        plan_payload = await self._plan(message, context)

        if plan_payload.confidence < self.confidence_threshold:
            clarification_msg = (
                plan_payload.clarification
                or "I'm not sure I understood that. Could you rephrase?"
            )
            logger.info(
                "planner_requesting_clarification",
                confidence=plan_payload.confidence,
                question=clarification_msg,
            )
            return PlannerResult(message=clarification_msg, tokens=[])

        if not plan_payload.tools:
            logger.warning("planner_no_plan", message=message)
            return PlannerResult(
                message="I could not determine a suitable tool to answer that. Please specify a router/token.",
                tokens=[],
            )

        results = await self._execute_plan(plan_payload.tools, context)
        all_results.extend(results)

        # Check if refinement is needed and enabled
        if (
            self.enable_reflection
            and iteration < self.max_iterations
            and not self._is_plan_complete(results, message, plan_payload.tools)
        ):
            # Refinement pass
            logger.info("planner_attempting_refinement", iteration=iteration + 1)
            refined_plan = await self._refine_plan(message, context, all_results)

            if refined_plan.tools:
                refined_results = await self._execute_plan(refined_plan.tools, context)
                all_results.extend(refined_results)
        elif self._is_plan_complete(results, message, plan_payload.tools):
            logger.info("planner_complete_first_pass", message=message)

        # Generate deterministic result for context extraction and fallback
        deterministic_result = self._render_response(message, context, all_results)

        # Attempt conversational synthesis
        try:
            synthesized_text = await self._synthesize_response(message, all_results)
            if synthesized_text:
                # Re-attach NFA disclaimer if tokens are present
                if deterministic_result.tokens:
                    synthesized_text = append_not_financial_advice(synthesized_text)
                return PlannerResult(
                    message=synthesized_text, tokens=deterministic_result.tokens
                )
        except Exception as exc:
            logger.error("planner_synthesis_failed", error=str(exc))

        return deterministic_result

    async def _classify_intent(self, message: str) -> str:
        """Determine if message requires tools or is just conversation."""
        prompt = self.CLASSIFICATION_PROMPT.safe_substitute(message=message)
        try:
            response = await asyncio.to_thread(
                self.model.generate_content,
                [{"role": "user", "parts": [{"text": prompt}]}],
                generation_config={"response_mime_type": "application/json"},
            )
            text = self._extract_response_text(response)
            payload = json.loads(text)
            return payload.get("intent", "TOOL_USE")
        except Exception as exc:
            logger.warning("intent_classification_failed", error=str(exc))
            return "TOOL_USE"  # Fallback to tool use

    async def _handle_chitchat(
        self, message: str, context: Dict[str, Any]
    ) -> PlannerResult:
        """Generate a conversational response without tools."""
        history = self._format_conversation_history(context.get("conversation_history"))
        prompt = textwrap.dedent(
            f"""
            You are a helpful Base L2 blockchain assistant.
            
            Conversation history:
            {history}
            
            User: {message}
            
            Reply conversationally and concisely. If the user asks what you can do, mention you can check token prices, liquidity, and safety on Base.
            """
        ).strip()
        
        try:
            response = await asyncio.to_thread(
                self.model.generate_content,
                [{"role": "user", "parts": [{"text": prompt}]}],
            )
            text = self._extract_response_text(response)
            return PlannerResult(message=text, tokens=[])
        except Exception as exc:
            logger.error("chitchat_generation_failed", error=str(exc))
            return PlannerResult(
                message="I'm here to help with Base tokens. Ask me to check a token!",
                tokens=[],
            )

    async def _synthesize_response(
        self, message: str, results: List[Dict[str, Any]]
    ) -> str | None:
        """Generate a natural language response from tool results."""
        if not results:
            return None

        # Prepare results for the model (convert to JSON string)
        # Filter out 'raw' fields to reduce prompt size
        filtered_results = []
        for result in results:
            if isinstance(result, dict):
                filtered = {k: v for k, v in result.items() if k != "raw"}
                filtered_results.append(filtered)
            else:
                filtered_results.append(result)

        # Using default=str to handle any non-serializable objects
        results_text = json.dumps(filtered_results, indent=2, default=str)

        prompt = self.SYNTHESIS_PROMPT.safe_substitute(
            message=message, results=results_text
        )

        logger.info("planner_synthesis_prompt", prompt_len=len(prompt))

        response = await asyncio.to_thread(
            self.model.generate_content,
            [{"role": "user", "parts": [{"text": prompt}]}],
        )

        text = self._extract_response_text(response).strip()
        # Ensure the text is safe for Telegram MarkdownV2
        return escape_markdown(text)

    async def _plan(
        self,
        message: str,
        context: Dict[str, Any],
        prior_results: List[Dict[str, Any]] | None = None,
    ) -> PlanPayload:
        prompt = self._build_prompt(message, context, prior_results)
        logger.info("planner_prompt", prompt=prompt)
        response = await asyncio.to_thread(
            self.model.generate_content,
            [{"role": "user", "parts": [{"text": prompt}]}],
            generation_config={"response_mime_type": "application/json"},
        )

        text = self._extract_response_text(response)
        logger.info("planner_raw_response", output=text)

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            logger.error("planner_invalid_json", output=text)
            return PlanPayload(confidence=0.0, clarification=None, tools=[])

        reasoning = payload.get("reasoning", "")
        if reasoning:
            logger.info("planner_reasoning", reasoning=reasoning, message=message)

        confidence = float(payload.get("confidence", 1.0))
        clarification = payload.get("clarification")

        logger.info("planner_confidence", confidence=confidence, message=message)

        invocations = []
        for entry in payload.get("tools", []):
            client = entry.get("client")
            method = entry.get("method")
            params = self._normalize_params(
                client,
                method,
                entry.get("params", {}),
                context.get("network"),
            )
            if client not in {"base", "dexscreener", "honeypot"} or not method:
                continue

            # Skip if normalize_params returned empty dict (invalid params)
            if not params:
                logger.warning(
                    "planner_skipping_invalid_tool",
                    client=client,
                    method=method,
                    reason="empty_params_after_normalization",
                )
                continue

            invocations.append(
                ToolInvocation(client=client, method=method, params=params)
            )

        if invocations:
            logger.info(
                "planner_plan",
                plan=[
                    {
                        "client": call.client,
                        "method": call.method,
                        "params": call.params,
                    }
                    for call in invocations
                ],
            )

        return PlanPayload(
            confidence=confidence, clarification=clarification, tools=invocations
        )

    def _build_prompt(
        self,
        message: str,
        context: Dict[str, Any],
        prior_results: List[Dict[str, Any]] | None = None,
    ) -> str:
        routers = ", ".join(self.router_keys) or "none"
        token_hint = self._format_recent_tokens(context.get("recent_tokens") or [])
        last_router = context.get("last_router") or "unknown"
        conversation_history = self._format_conversation_history(
            context.get("conversation_history")
        )
        tool_definitions = self._format_tool_definitions()
        context_map = {
            "message": message,
            "network": context.get("network", "base"),
            "routers": routers,
            "default_lookback": context.get("default_lookback", 30),
            "recent_tokens": token_hint,
            "recent_router": last_router,
            "conversation_history": conversation_history,
            "tool_definitions": tool_definitions,
            "prior_results": (
                self._format_prior_results(prior_results) if prior_results else "none"
            ),
        }
        prompt = self._prompt_template.safe_substitute(context_map)
        if "$" in prompt:
            logger.warning("prompt_unresolved_placeholders", prompt=prompt)
        return prompt

    def _format_tool_definitions(self) -> str:
        """Format available MCP tools into a prompt-friendly list."""
        tools = self.mcp_manager.get_available_tools()
        if not tools:
            return "No tools available."

        lines = []
        for tool in tools:
            name = tool.get("name")
            description = tool.get("description", "No description.")
            schema = tool.get("inputSchema", {})
            props = schema.get("properties", {})
            required = set(schema.get("required", []))

            args = []
            for prop_name, prop_def in props.items():
                prop_type = prop_def.get("type", "any")
                if prop_name not in required:
                    prop_name = f"{prop_name}?"
                args.append(f"{prop_name}: {prop_type}")

            sig = f"- {name}({', '.join(args)})"
            lines.append(f"{sig}\n  {description}")

        return "\n".join(lines)

    def _format_recent_tokens(self, tokens: Any) -> str:
        if not isinstance(tokens, list):
            return "none"
        payload: List[Dict[str, str]] = []
        for token in tokens[:5]:
            if not isinstance(token, dict):
                continue
            entry: Dict[str, str] = {}
            for key in (
                "symbol",
                "baseSymbol",
                "name",
                "address",
                "chainId",
                "pairAddress",
                "source",
            ):
                value = token.get(key)
                if value:
                    entry[key] = str(value)
            if entry:
                payload.append(entry)
        return json.dumps(payload) if payload else "none"

    @staticmethod
    def _format_conversation_history(history: Any) -> str:
        """Format conversation history as numbered dialogue."""
        if not history or not isinstance(history, list):
            return "none"

        lines = []
        for msg in history[-10:]:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if not content:
                continue
            prefix = "User" if role == "user" else "Assistant"
            lines.append(f"{prefix}: {content}")

        return "\n".join(lines) if lines else "none"

    def _format_prior_results(self, results: List[Dict[str, Any]]) -> str:
        """Format prior tool call results for injection into prompt."""
        if not results:
            return "none"

        lines = []
        for entry in results:
            call = entry["call"]
            if "error" in entry:
                lines.append(f"- {call.client}.{call.method}: FAILED")
            else:
                result = entry.get("result", {})
                if isinstance(result, dict) and "items" in result:
                    count = len(result["items"])
                    lines.append(f"- {call.client}.{call.method}: {count} transactions")
                elif call.client == "dexscreener":
                    tokens = entry.get("tokens", [])
                    if tokens:
                        symbols = [t.get("symbol", "?") for t in tokens[:2]]
                        lines.append(
                            f"- {call.client}.{call.method}: {', '.join(symbols)}"
                        )
                    else:
                        lines.append(f"- {call.client}.{call.method}: SUCCESS")
                else:
                    lines.append(f"- {call.client}.{call.method}: SUCCESS")

        return "\n".join(lines)

    def _is_plan_complete(
        self,
        results: List[Dict[str, Any]],
        message: str,
        tools_called: List[ToolInvocation],
    ) -> bool:
        """Heuristic to determine if initial plan was sufficient."""
        # Check for errors in critical calls
        has_errors = any("error" in r for r in results)
        if has_errors:
            return False

        # Check if user asked about tokens but no Dexscreener calls were made
        token_intent_keywords = ["token", "price", "dex", "pair", "liquidity"]
        user_wants_tokens = any(kw in message.lower() for kw in token_intent_keywords)

        dex_calls = [t for t in tools_called if t.client == "dexscreener"]
        if user_wants_tokens and not dex_calls:
            return False

        # Check if router activity was fetched but no token analysis followed
        router_calls = [t for t in tools_called if t.method == "getDexRouterActivity"]
        if router_calls and not dex_calls:
            # Should have discovered tokens and called Dexscreener
            discovered_tokens = any(
                self._extract_token_entries(r.get("result", {})) for r in results
            )
            if discovered_tokens:
                return False

        # Check for safety/honeypot intent
        safety_keywords = ["honeypot", "safe", "safety", "audit", "risk"]
        user_wants_safety = any(kw in message.lower() for kw in safety_keywords)
        honeypot_calls = [t for t in tools_called if t.client == "honeypot"]
        
        if user_wants_safety and not honeypot_calls:
             return False

        # Default: plan is complete
        return True

    async def _refine_plan(
        self,
        message: str,
        context: Dict[str, Any],
        prior_results: List[Dict[str, Any]],
    ) -> PlanPayload:
        """Generate follow-up tool calls based on initial results."""
        results_summary = self._summarize_results_for_refinement(prior_results)

        refinement_prompt = self._build_refinement_prompt(
            message, context, results_summary
        )

        logger.info("planner_refinement_prompt", prompt=refinement_prompt)

        response = await asyncio.to_thread(
            self.model.generate_content,
            [{"role": "user", "parts": [{"text": refinement_prompt}]}],
            generation_config={"response_mime_type": "application/json"},
        )

        text = self._extract_response_text(response)
        
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            logger.error("planner_refinement_invalid_json", output=text)
            return PlanPayload(confidence=0.0, clarification=None, tools=[])

        reasoning = payload.get("reasoning", "")
        if reasoning:
            logger.info("planner_refinement_reasoning", reasoning=reasoning)

        # Ensure payload is a dict
        if not isinstance(payload, dict):
            logger.error(
                "planner_refinement_invalid_type", payload_type=type(payload).__name__
            )
            return PlanPayload(confidence=0.0, clarification=None, tools=[])

        # Parse tools
        invocations = []
        tools_list = payload.get("tools", [])
        if not isinstance(tools_list, list):
            logger.error(
                "planner_refinement_tools_not_list",
                tools_type=type(tools_list).__name__,
            )
            return PlanPayload(confidence=0.0, clarification=None, tools=[])

        for entry in tools_list:
            if not isinstance(entry, dict):
                continue
            client = entry.get("client")
            method = entry.get("method")
            params = self._normalize_params(
                client,
                method,
                entry.get("params", {}),
                context.get("network"),
            )
            if client not in {"base", "dexscreener", "honeypot"} or not method:
                continue

            # Skip if normalize_params returned empty dict (invalid params)
            if not params:
                logger.warning(
                    "planner_refinement_skipping_invalid_tool",
                    client=client,
                    method=method,
                    reason="empty_params_after_normalization",
                )
                continue

            invocations.append(
                ToolInvocation(client=client, method=method, params=params)
            )

        return PlanPayload(
            confidence=1.0,  # Refinement doesn't use confidence
            clarification=None,
            tools=invocations,
        )

    def _summarize_results_for_refinement(self, results: List[Dict[str, Any]]) -> str:
        """Create concise summary of tool call results for refinement prompt."""
        summary_lines = []

        for entry in results:
            call = entry["call"]
            status = "ERROR" if "error" in entry else "SUCCESS"

            summary = f"{call.client}.{call.method}: {status}"

            if status == "SUCCESS":
                result = entry.get("result", {})
                if isinstance(result, dict):
                    # Extract key metrics
                    if "items" in result:
                        summary += f" ({len(result['items'])} items)"
                    if call.client == "dexscreener":
                        tokens = entry.get("tokens", [])
                        if tokens and isinstance(tokens, list):
                            items = []
                            for t in tokens[:3]:
                                sym = t.get("symbol", "?") if isinstance(t, dict) else "?"
                                addr = t.get("address", "?") if isinstance(t, dict) else "?"
                                items.append(f"{sym} ({addr})")
                            summary += f" (tokens: {', '.join(items)})"
                elif isinstance(result, list):
                    summary += f" ({len(result)} items)"
            else:
                summary += f" ({entry['error'][:50]})"

            summary_lines.append(summary)

        return "\n".join(summary_lines)

    def _build_refinement_prompt(
        self, message: str, context: Dict[str, Any], results_summary: str
    ) -> str:
        """Construct prompt asking Gemini if additional tools are needed."""
        return textwrap.dedent(
            f"""
            Original user request: "{message}"
            
            I already executed these tools:
            {results_summary}
            
            Based on the results above, should I call additional tools to fully answer the user's request?
            
            If YES: Output a JSON plan with new tool calls (don't repeat calls already made).
            If NO: Output {{"tools": []}}
            
            Available tools:
            - dexscreener.searchPairs
            - dexscreener.getPairsByToken (requires tokenAddress)
            - honeypot.check_token (requires address: "0x..." and chainId: 8453)
            - base.resolveToken
            - base.getTransactionByHash
            
            IMPORTANT: 
            - Use "client" and "method" fields (NOT "tool_name")
            - honeypot.check_token requires a 0x-prefixed address, NOT a symbol
            - Get the token address from Dexscreener first before calling honeypot
            
            Example JSON format:
            {{
                "reasoning": "I need to check the safety of the token I just found.",
                "tools": [{{"client": "dexscreener", "method": "searchPairs", "params": {{"query": "PEPE"}}}}]
            }}
            
            Respond with JSON only.
        """
        ).strip()

    def _normalize_params(
        self,
        client: str | None,
        method: str | None,
        params: Dict[str, Any],
        network: Any | None = None,
    ) -> Dict[str, Any]:
        """Coerce common alias names emitted by the model into expected fields."""
        if not isinstance(params, dict):
            return {}

        normalized = dict(params)

        if client == "base" and method == "getDexRouterActivity":
            original_router_value = normalized.get("router")
            router_override = (
                normalized.pop("router_address", None)
                or normalized.pop("routerAddress", None)
                or normalized.pop("address", None)
            )
            router_label = (
                normalized.pop("router_name", None)
                or normalized.pop("routerName", None)
                or normalized.pop("router_key", None)
                or normalized.pop("routerKey", None)
            )
            if router_override:
                normalized["router"] = router_override
            router_value = normalized.get("router")
            network_str = str(network) if network else None
            if router_label and isinstance(router_label, str):
                normalized.setdefault("routerKey", router_label)
            if isinstance(router_value, str) and not router_value.startswith("0x"):
                normalized.setdefault("routerKey", router_value)
                if network_str and router_value in self.router_map:
                    network_map = self.router_map.get(router_value, {})
                    address = network_map.get(network_str)
                    if address:
                        normalized["router"] = address
            elif isinstance(
                original_router_value, str
            ) and not original_router_value.startswith("0x"):
                normalized.setdefault("routerKey", original_router_value)
                if network_str and original_router_value in self.router_map:
                    network_map = self.router_map.get(original_router_value, {})
                    address = network_map.get(network_str)
                    if address:
                        normalized.setdefault("router", address)
            normalized.pop("network", None)
            if "sinceMinutes" not in normalized:
                lookback = (
                    normalized.pop("lookback_minutes", None)
                    or normalized.pop("lookbackMinutes", None)
                    or normalized.pop("since_minutes", None)
                    or normalized.pop("minutes", None)
                )
                if lookback is not None:
                    normalized["sinceMinutes"] = lookback
            normalized.pop("lookback_minutes", None)
            normalized.pop("lookbackMinutes", None)
            normalized.pop("since_minutes", None)
            normalized.pop("minutes", None)

        # Validate honeypot check has proper address
        if client == "honeypot" and method == "check_token":
            address = normalized.get("address")
            if address and isinstance(address, str):
                # Check if it's a valid 0x address
                if not address.startswith("0x") or len(address) != 42:
                    logger.warning(
                        "honeypot_invalid_address",
                        address=address,
                        reason="Address must be 0x-prefixed 40-char hex",
                    )
                    # Don't call honeypot with invalid address
                    return {}
            else:
                logger.warning("honeypot_missing_address", params=params)
                return {}

        return normalized

    @staticmethod
    def _extract_token_param(params: Dict[str, Any]) -> str | None:
        if not isinstance(params, dict):
            return None
        token = (
            params.get("tokenAddress")
            or params.get("token")
            or params.get("pairAddress")
        )
        if isinstance(token, str) and token:
            return token
        return None

    def _iter_transactions(self, result: Any) -> List[Dict[str, Any]]:
        if isinstance(result, dict):
            items = result.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        return []

    def _collect_token_addresses(self, transactions: Iterable[Any]) -> Set[str]:
        addresses: Set[str] = set()
        for tx in transactions:
            addresses.update(self._extract_token_addresses(tx))
        return addresses

    @staticmethod
    def _extract_token_addresses(tx: Any) -> Set[str]:
        addresses: Set[str] = set()
        if not isinstance(tx, dict):
            return addresses

        direct_keys = [
            "tokenAddress",
            "token",
            "tokenInAddress",
            "tokenOutAddress",
            "token0Address",
            "token1Address",
            "baseTokenAddress",
            "quoteTokenAddress",
            "inputToken",
            "outputToken",
        ]
        for key in direct_keys:
            value = tx.get(key)
            if isinstance(value, str) and value.startswith("0x"):
                addresses.add(value)

        nested_keys = [
            "tokenIn",
            "tokenOut",
            "baseToken",
            "quoteToken",
            "token0",
            "token1",
            "fromToken",
            "toToken",
        ]
        for key in nested_keys:
            nested = tx.get(key)
            if isinstance(nested, dict):
                addr = (
                    nested.get("address")
                    or nested.get("tokenAddress")
                    or nested.get("contract")
                    or nested.get("id")
                )
                if isinstance(addr, str) and addr.startswith("0x"):
                    addresses.add(addr)

        list_keys = ["tokens", "legs"]
        for key in list_keys:
            entries = tx.get(key)
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict):
                        addr = entry.get("address") or entry.get("tokenAddress")
                        if isinstance(addr, str) and addr.startswith("0x"):
                            addresses.add(addr)

        decoded = tx.get("decoded")
        if isinstance(decoded, dict):
            params = decoded.get("params")
            addresses.update(GeminiPlanner._extract_addresses_from_value(params))

        return addresses

    @staticmethod
    def _extract_addresses_from_value(value: Any) -> Set[str]:
        addresses: Set[str] = set()
        if value is None:
            return addresses
        if isinstance(value, str):
            if value.startswith("0x") and len(value) >= 42:
                addresses.add(value)
            return addresses
        if isinstance(value, dict):
            for inner in value.values():
                addresses.update(GeminiPlanner._extract_addresses_from_value(inner))
            return addresses
        if isinstance(value, list):
            for item in value:
                addresses.update(GeminiPlanner._extract_addresses_from_value(item))
        return addresses

    async def _execute_single_tool(self, call: ToolInvocation) -> Dict[str, Any]:
        """Execute a single tool call and return the result payload."""
        try:
            logger.info(
                "planner_tool_call",
                client=call.client,
                method=call.method,
                params=call.params,
            )
            if call.client == "base":
                result = await self.mcp_manager.base.call_tool(
                    call.method, call.params
                )
            elif call.client == "dexscreener":
                result = await self.mcp_manager.dexscreener.call_tool(
                    call.method, call.params
                )
            elif call.client == "honeypot":
                if not self.mcp_manager.honeypot:
                    raise RuntimeError("Honeypot MCP server is not configured")
                result = await self.mcp_manager.honeypot.call_tool(
                    call.method, call.params
                )
            else:  # pragma: no cover - defensive guard
                raise RuntimeError(f"Unsupported MCP client '{call.client}'")

            entry_payload: Dict[str, Any] = {"call": call, "result": result}
            if (
                call.client == "dexscreener"
                and call.method in self.DEX_TOKEN_METHODS
            ):
                normalized_tokens = self._extract_token_entries(result)
                if normalized_tokens:
                    entry_payload["tokens"] = normalized_tokens
            
            log_extra = {"client": call.client, "method": call.method}
            if isinstance(result, dict):
                log_extra["result_keys"] = list(result.keys())[:5]
                if "items" in result and isinstance(result["items"], list):
                    log_extra["items"] = len(result["items"])
            elif isinstance(result, list):
                log_extra["items"] = len(result)
            logger.info("planner_tool_success", **log_extra)
            
            return entry_payload

        except Exception as exc:  # pragma: no cover - network/process errors
            logger.error(
                "planner_tool_error",
                client=call.client,
                method=call.method,
                error=str(exc),
            )
            return {"call": call, "error": str(exc)}

    async def _execute_plan(
        self,
        plan: Sequence[ToolInvocation],
        context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        collected_tokens: Dict[str, str] = {}
        planned_token_keys: Set[str] = set()
        chain_id = self._derive_chain_id(context.get("network"))
        # chain_numeric removed - was only used for auto-honeypot checks

        # 1. Execute initial plan in parallel
        tasks = [self._execute_single_tool(call) for call in plan]
        initial_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter out exceptions from gather and add valid results
        valid_results = [r for r in initial_results if not isinstance(r, Exception)]
        results.extend(valid_results)

        # 2. Process results for side effects (token collection)
        for entry in initial_results:
            if isinstance(entry, Exception):
                logger.error("planner_gather_exception", error=str(entry))
                continue
            
            if "error" in entry:
                continue
            
            call = entry["call"]
            result = entry["result"]

            if call.client == "dexscreener":
                token_addr = self._extract_token_param(call.params)
                if token_addr:
                    planned_token_keys.add(token_addr.lower())

            if call.client == "base" and call.method == "getDexRouterActivity":
                transactions = self._iter_transactions(result)
                for token in self._collect_token_addresses(transactions):
                    collected_tokens.setdefault(token.lower(), token)

        # 3. Identify additional tokens to fetch
        additional_tokens = [
            address
            for key, address in collected_tokens.items()
            if key not in planned_token_keys
        ][:3]

        # 4. Fetch additional tokens in parallel
        if additional_tokens:
            additional_tasks = []
            for token in additional_tokens:
                invocation = ToolInvocation(
                    client="dexscreener",
                    method="getPairsByToken",
                    params={"chainId": chain_id, "tokenAddress": token},
                )
                additional_tasks.append(self._execute_single_tool(invocation))
            
            additional_results = await asyncio.gather(*additional_tasks, return_exceptions=True)
            results.extend([r for r in additional_results if not isinstance(r, Exception)])

        # Disabled: Auto-honeypot checks were blocking results when API returns 404
        # Only run honeypot when explicitly requested via planner tool call
        # honeypot_targets = self._select_honeypot_targets(results, collected_tokens)
        # if honeypot_targets:
        #     verdicts = await self._fetch_honeypot_verdicts(
        #         honeypot_targets, chain_numeric
        #     )
        #     if verdicts:
        #         self._annotate_token_verdicts(results, verdicts)

        return results

    def _select_honeypot_targets(
        self,
        results: Sequence[Dict[str, Any]],
        collected_tokens: Dict[str, str],
    ) -> List[HoneypotTarget]:
        token_order: List[str] = []
        metadata: Dict[str, Tuple[str, str | None, float]] = {}

        def register(address: Any, pair: Any = None, liquidity: Any = None) -> None:
            if not isinstance(address, str) or not address.startswith("0x"):
                return
            lowered = address.lower()
            pair_addr = (
                pair if isinstance(pair, str) and pair.startswith("0x") else None
            )
            liquidity_value = self._coerce_float(liquidity)
            if lowered not in metadata:
                metadata[lowered] = (address, pair_addr, liquidity_value)
                token_order.append(lowered)
                return
            _, existing_pair, existing_liq = metadata[lowered]
            if liquidity_value > existing_liq:
                metadata[lowered] = (
                    address,
                    pair_addr or existing_pair,
                    liquidity_value,
                )

        for entry in results:
            tokens = entry.get("tokens")
            if not tokens:
                continue
            for token in tokens:
                if not isinstance(token, dict):
                    continue
                register(
                    token.get("address"),
                    token.get("pairAddress"),
                    token.get("liquidity"),
                )

        for address in collected_tokens.values():
            register(address)

        targets: List[HoneypotTarget] = []
        for lowered in token_order[: self.MAX_HONEYPOT_CHECKS]:
            original, pair_addr, _ = metadata[lowered]
            targets.append(HoneypotTarget(token=original, pair=pair_addr))
        return targets

    async def _fetch_honeypot_verdicts(
        self,
        targets: Sequence[HoneypotTarget],
        chain_id: int,
    ) -> Dict[str, Dict[str, str]]:
        client = getattr(self.mcp_manager, "honeypot", None)
        if not client or not targets:
            return {}
        self._ensure_honeypot_cache()

        verdicts: Dict[str, Dict[str, str]] = {}
        successes = 0
        fallbacks = 0
        for target in targets[: self.MAX_HONEYPOT_CHECKS]:
            token = target.token
            if not isinstance(token, str) or not token.startswith("0x"):
                continue
            verdict = await self._evaluate_honeypot_target(
                client, token, chain_id, target.pair
            )
            if verdict:
                verdicts[token.lower()] = verdict
                if verdict.get("reason") == "Token not indexed on Honeypot":
                    fallbacks += 1
                else:
                    successes += 1

        if successes or fallbacks:
            logger.info(
                "honeypot_results",
                success=successes,
                fallback=fallbacks,
                checked=len(targets[: self.MAX_HONEYPOT_CHECKS]),
            )

        return verdicts

    async def _evaluate_honeypot_target(
        self,
        client: Any,
        token: str,
        chain_id: int,
        initial_pair: str | None,
    ) -> Dict[str, str] | None:
        cache_key = self._honeypot_cache_key(token, chain_id)
        cached_missing = self._honeypot_missing_cache.get(cache_key)
        if cached_missing and cached_missing > time.time():
            logger.debug("honeypot_skip_cached_404", address=token)
            return {
                "verdict": "CAUTION",
                "reason": "Token not indexed on Honeypot",
            }

        if not initial_pair:
            cached_pair = self._get_cached_pair(cache_key)
            if cached_pair:
                initial_pair = cached_pair

        pair = initial_pair
        attempted_discovery = False

        for _ in range(2):
            try:
                result = await self._call_honeypot_check(client, token, chain_id, pair)
            except Exception as exc:  # pragma: no cover - network/process errors
                if not attempted_discovery:
                    attempted_discovery = True
                    new_pair = await self._discover_pair_for_token(
                        client, token, chain_id
                    )
                    if new_pair:
                        pair = new_pair
                        continue
                fallback = self._fallback_verdict_from_error(exc)
                log_fn = logger.info if fallback else logger.warning
                log_fn(
                    "honeypot_check_failed", address=token, pair=pair, error=str(exc)
                )
                if fallback:
                    self._honeypot_missing_cache[cache_key] = (
                        time.time() + self.HONEYPOT_NOT_FOUND_TTL_SECONDS
                    )
                return fallback

            normalized = self._normalize_honeypot_result(result)
            if normalized:
                if pair:
                    self._honeypot_discovery_cache[cache_key] = (
                        time.time() + self.HONEYPOT_DISCOVERY_TTL_SECONDS,
                        pair,
                    )
                return normalized

            logger.warning(
                "honeypot_check_malformed", address=token, pair=pair, result=result
            )
            return None

        return None

    async def _call_honeypot_check(
        self,
        client: Any,
        token: str,
        chain_id: int,
        pair: str | None,
    ) -> Any:
        params: Dict[str, Any] = {"address": token}
        if chain_id:
            params["chainId"] = chain_id
        if pair:
            params["pair"] = pair
        logger.debug(
            "honeypot_call",
            address=token,
            chainId=chain_id,
            pair=pair,
        )
        return await client.call_tool("check_token", params)

    async def _discover_pair_for_token(
        self,
        client: Any,
        token: str,
        chain_id: int,
    ) -> str | None:
        try:
            params: Dict[str, Any] = {"address": token}
            if chain_id:
                params["chainId"] = chain_id
            result = await client.call_tool("discover_pairs", params)
        except Exception as exc:  # pragma: no cover - network/process errors
            logger.info("honeypot_discover_failed", address=token, error=str(exc))
            return None

        if isinstance(result, dict):
            pairs = result.get("pairs")
        else:
            pairs = None
        if not isinstance(pairs, list):
            return None

        best_pair: str | None = None
        best_liquidity: float = -1.0
        for entry in pairs:
            if not isinstance(entry, dict):
                continue
            pair_address = entry.get("pair")
            if not isinstance(pair_address, str) or not pair_address.startswith("0x"):
                continue
            liquidity = entry.get("liquidityUsd")
            try:
                liquidity_value = float(liquidity)
            except (TypeError, ValueError):
                liquidity_value = -1.0
            if liquidity_value > best_liquidity:
                best_liquidity = liquidity_value
                best_pair = pair_address

        cache_key = self._honeypot_cache_key(token, chain_id)
        self._honeypot_discovery_cache[cache_key] = (
            time.time() + self.HONEYPOT_DISCOVERY_TTL_SECONDS,
            best_pair,
        )
        return best_pair

    def _annotate_token_verdicts(
        self,
        results: Sequence[Dict[str, Any]],
        verdicts: Dict[str, Dict[str, str]],
    ) -> None:
        if not verdicts:
            return
        for entry in results:
            tokens = entry.get("tokens")
            if not tokens:
                continue
            for token in tokens:
                self._apply_verdict_to_token(token, verdicts)

    @staticmethod
    def _normalize_honeypot_result(payload: Any) -> Dict[str, str] | None:
        if not isinstance(payload, dict):
            return None
        logger.info("honeypot_raw_payload", payload=payload)
        summary = payload.get("summary")
        if not isinstance(summary, dict):
            return None
        verdict = summary.get("verdict")
        reason = summary.get("reason") or summary.get("message")
        if not verdict:
            return None
        normalized: Dict[str, str] = {"verdict": str(verdict)}
        if isinstance(reason, str) and reason.strip():
            normalized["reason"] = reason.strip()
        risks = summary.get("risks")
        if isinstance(risks, list):
            risk_messages = [
                str(item) for item in risks if isinstance(item, (str, int, float))
            ]
            if risk_messages:
                normalized["risk"] = ", ".join(risk_messages)
        elif isinstance(risks, str):
            normalized["risk"] = risks

        raw_payload = payload.get("raw", {})
        if isinstance(raw_payload, dict):
            contract_code = raw_payload.get("contractCode", {})
            if isinstance(contract_code, dict) and not contract_code.get("openSource"):
                if normalized.get("verdict") == "SAFE_TO_TRADE":
                    normalized["verdict"] = "CAUTION"
                existing_reason = normalized.get("reason", "")
                new_reason = "Contract source code is not verified"
                if existing_reason and new_reason not in existing_reason:
                    normalized["reason"] = f"{existing_reason}, {new_reason}"
                elif not existing_reason:
                    normalized["reason"] = new_reason

        return normalized

    @staticmethod
    def _fallback_verdict_from_error(error: Exception) -> Dict[str, str] | None:
        message = str(error)
        if not message:
            return None
        lowered = message.lower()
        if "404" in lowered or "not found" in lowered:
            return {
                "verdict": "CAUTION",
                "reason": "Token not indexed on Honeypot",
            }
        return {
            "verdict": "ERROR",
            "reason": "Honeypot check failed",
        }

    @staticmethod
    def _coerce_float(value: Any) -> float:
        if value is None:
            return -1.0
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return -1.0
        return -1.0

    def _ensure_honeypot_cache(self) -> None:
        if not hasattr(self, "_honeypot_discovery_cache"):
            self._honeypot_discovery_cache = {}
        if not hasattr(self, "_honeypot_missing_cache"):
            self._honeypot_missing_cache = {}

    def _honeypot_cache_key(self, token: str, chain_id: int) -> str:
        lowered = token.lower()
        return f"{lowered}:{chain_id or 0}"

    def _get_cached_pair(self, cache_key: str) -> str | None:
        entry = self._honeypot_discovery_cache.get(cache_key)
        if not entry:
            return None
        expires_at, pair = entry
        if expires_at <= time.time():
            self._honeypot_discovery_cache.pop(cache_key, None)
            return None
        return pair

    @staticmethod
    def _apply_verdict_to_token(
        token: Dict[str, Any],
        verdicts: Dict[str, Dict[str, str]],
        fallback_address: str | None = None,
    ) -> None:
        address = token.get("address") or fallback_address
        if not isinstance(address, str) or not address:
            return
        verdict = verdicts.get(address.lower())
        if not verdict:
            return
        token["riskVerdict"] = verdict.get("verdict", "")
        if verdict.get("reason"):
            token["riskReason"] = verdict["reason"]
        if verdict.get("risk"):
            token["risk"] = verdict["risk"]

    def _render_response(
        self,
        message: str,
        context: Dict[str, Any],
        results: Iterable[Dict[str, Any]],
    ) -> PlannerResult:
        sections: List[str] = []
        token_lines: List[str] = []
        add_nfa = False
        router_label: str | None = None
        seen_pairs: Set[str] = set()
        context_tokens: List[Dict[str, str]] = []
        context_seen: Set[str] = set()

        for entry in results:
            call: ToolInvocation = entry["call"]
            title = f"{call.client}.{call.method}"
            if "error" in entry:
                sections.append(f"*{title}*: failed — {entry['error']}")
                continue

            result = entry.get("result")
            if call.method == "getDexRouterActivity":
                if router_label is None:
                    router_label = call.params.get("routerKey") or call.params.get(
                        "router"
                    )
                continue

            if call.method in self.DEX_TOKEN_METHODS:
                normalized_tokens = entry.get("tokens") or self._extract_token_entries(
                    result
                )
                for token in normalized_tokens:
                    if not isinstance(token, dict):
                        continue

                    # Filter to Base chain only (unless explicitly using another chain)
                    token_chain = token.get("chainId", "").lower()
                    if token_chain and token_chain != "base":
                        # Skip tokens from other chains
                        continue

                    dedupe_key = token.get("url") or token.get("symbol") or ""
                    if dedupe_key and dedupe_key in seen_pairs:
                        continue
                    if dedupe_key:
                        seen_pairs.add(dedupe_key)
                    token_lines.append(format_token_summary(token))
                    context_entry = self._build_token_context_entry(
                        token,
                        call.params.get("routerKey")
                        or call.params.get("router")
                        or call.method,
                    )
                    context_key = context_entry.get("address") or context_entry.get(
                        "symbol"
                    )
                    if context_key and context_key in context_seen:
                        continue
                    if context_key:
                        context_seen.add(context_key)
                    context_tokens.append(context_entry)
                if normalized_tokens:
                    add_nfa = True
                continue

            # Handle honeypot results with formatted output
            if call.client == "honeypot" and call.method == "check_token":
                summary = result.get("summary", {}) if isinstance(result, dict) else {}
                verdict = summary.get("verdict", "UNKNOWN")
                reason = summary.get("reason", "")

                from app.utils.formatting import format_honeypot_verdict

                verdict_text = format_honeypot_verdict(verdict, reason)

                if verdict_text:
                    sections.append(f"✅ Honeypot Check: {verdict_text}")

                    # Add tax info if available
                    taxes = result.get("taxes", {})
                    limits = result.get("limits", {})
                    if taxes or limits:
                        details = []
                        buy_tax = taxes.get("buyTax")
                        sell_tax = taxes.get("sellTax")
                        if buy_tax is not None:
                            details.append(f"Buy Tax: {buy_tax}%")
                        if sell_tax is not None:
                            details.append(f"Sell Tax: {sell_tax}%")
                        if details:
                            sections.append("• " + " · ".join(details))
                else:
                    sections.append(f"*{title}*: {escape_markdown(str(summary))}")
                continue

            # Fallback for other tool results
            sections.append(
                f"*{title}*:\n```\n{json.dumps(result, indent=2)[:1500]}\n```"
            )

        if token_lines:
            label = router_label or "selected router"
            header = escape_markdown(f"Dexscreener snapshots for {label}")
            sections.insert(
                0,
                join_messages(
                    [
                        header,
                        join_messages(token_lines[: self.MAX_ROUTER_ITEMS]),
                    ]
                ),
            )

        summary = join_messages(sections)
        if add_nfa:
            summary = append_not_financial_advice(summary)
        message_text = summary or "No recent data returned for that request."
        return PlannerResult(message=message_text, tokens=context_tokens)

    async def summarize_transactions(
        self,
        router_key: str,
        transactions: Iterable[Dict[str, Any]],
        network: str,
    ) -> TokenSummary | None:
        """Return Dexscreener token summaries suitable for subscription alerts."""
        addresses = list(self._collect_token_addresses(transactions))
        if not addresses:
            return None
        return await self._build_token_summary(addresses, router_key, network)

    async def summarize_tokens_from_context(
        self,
        addresses: Sequence[str],
        label: str,
        network: str,
        token_insights: Mapping[str, Dict[str, str]] | None = None,
    ) -> TokenSummary | None:
        """Fetch Dexscreener data for explicit token addresses."""
        filtered = [addr for addr in addresses if isinstance(addr, str) and addr]
        if not filtered:
            return None
        return await self._build_token_summary(filtered, label, network, token_insights)

    async def _build_token_summary(
        self,
        addresses: Sequence[str],
        label: str,
        network: str,
        token_insights: Mapping[str, Dict[str, str]] | None = None,
    ) -> TokenSummary | None:
        chain_id = self._derive_chain_id(network)
        chain_numeric = self._derive_chain_numeric(network)
        address_plan = list(dict.fromkeys(addresses))[: self.MAX_ROUTER_ITEMS]
        collected_entries: List[Tuple[Dict[str, str], str]] = []

        for address in address_plan:
            try:
                result = await self.mcp_manager.dexscreener.call_tool(
                    "getPairsByToken",
                    {"chainId": chain_id, "tokenAddress": address},
                )
            except Exception as exc:  # pragma: no cover - network/process errors
                logger.warning(
                    "subscription_token_summary_failed",
                    token=address,
                    error=str(exc),
                )
                continue

            entries = self._extract_token_entries(result)
            if not entries:
                continue
            for entry in entries:
                collected_entries.append((entry, address))

        if not collected_entries:
            return None

        targets = self._select_honeypot_targets(
            [{"tokens": [entry for entry, _ in collected_entries]}], {}
        )
        verdicts = await self._fetch_honeypot_verdicts(targets, chain_numeric)

        summaries: List[str] = []
        seen_pairs: Set[str] = set()
        context_tokens: List[Dict[str, str]] = []
        add_nfa = False

        for entry, source_address in collected_entries:
            if verdicts:
                self._apply_verdict_to_token(entry, verdicts, source_address)
            dedupe_key = entry.get("url") or entry.get("symbol") or ""
            if dedupe_key and dedupe_key in seen_pairs:
                continue
            if dedupe_key:
                seen_pairs.add(dedupe_key)
            enriched_entry = dict(entry)
            if token_insights:
                normalized_address = enriched_entry.get(
                    "address"
                ) or enriched_entry.get("tokenAddress")
                if isinstance(normalized_address, str):
                    insight = token_insights.get(normalized_address.lower())
                    if insight:
                        summary_text = insight.get("activitySummary")
                        details_text = insight.get("activityDetails")
                        if summary_text:
                            enriched_entry["activitySummary"] = summary_text
                        if details_text:
                            enriched_entry["activityDetails"] = details_text
            summaries.append(format_token_summary(enriched_entry))
            context_tokens.append(
                self._build_token_context_entry(entry, label or source_address)
            )
            add_nfa = True
            if len(summaries) >= self.MAX_ROUTER_ITEMS:
                break

        header = escape_markdown(f"Dexscreener snapshots for {label or 'router'}")
        message = join_messages(
            [
                header,
                join_messages(summaries[: self.MAX_ROUTER_ITEMS]),
            ]
        )
        if add_nfa:
            message = append_not_financial_advice(message)
        return TokenSummary(message=message, tokens=context_tokens)

    async def summarize_transfer_activity(
        self, token_label: str, events: Sequence[Mapping[str, Any]]
    ) -> str | None:
        if not events:
            return None
        trimmed = list(events[: self.MAX_ROUTER_ITEMS])
        prompt = textwrap.dedent(
            f"""
            You are assisting a Base blockchain Telegram bot.
            In a single sentence under 50 characters, describe the overall wallet flow for {token_label}.
            Use plain English, avoid mentioning wallet addresses, hex strings, or Markdown.
            Focus on direction (buying/selling, inflow/outflow) and notable counterparties without naming addresses.
            Respond with just that short sentence and nothing else.

            Transfers:
            {json.dumps(trimmed)}
            """
        ).strip()
        try:
            response = await asyncio.to_thread(
                self.model.generate_content,
                [{"role": "user", "parts": [{"text": prompt}]}],
            )
        except Exception as exc:  # pragma: no cover - network/process errors
            logger.warning(
                "transfer_summary_failed",
                token=token_label,
                error=str(exc),
            )
            return None
        text = self._extract_response_text(response).strip()
        sanitized = self._sanitize_transfer_summary(text)
        return sanitized or None

    @staticmethod
    def _sanitize_transfer_summary(text: str) -> str:
        if not text:
            return ""
        single_line = re.split(r"[\r\n]+", text)[0].strip()
        without_hex = re.sub(r"0x[a-fA-F0-9]{6,}", "wallet", single_line)
        compressed = re.sub(r"\s{2,}", " ", without_hex).strip()
        if len(compressed) > 50:
            compressed = compressed[:50].rstrip()
        return compressed

    def _format_router_activity(self, call: ToolInvocation, result: Any) -> str:
        router_key = call.params.get("routerKey")
        router_address = call.params.get("router")
        label = router_key or router_address or "router"
        label_md = escape_markdown(str(label))

        transactions = self._iter_transactions(result)
        if not transactions:
            return f"No recent transactions for `{label_md}`."

        lines = [
            format_transaction(self._normalize_tx(tx))
            for tx in transactions[: self.MAX_ROUTER_ITEMS]
        ]
        header = f"Recent transactions for {label_md}"
        return join_messages([header, "\n".join(lines)])

    def _extract_token_entries(self, result: Any) -> List[Dict[str, str]]:
        if isinstance(result, dict):
            # Dexscreener returns "pairs" for searchPairs
            tokens = (
                result.get("tokens") or result.get("results") or result.get("pairs")
            )
            if not isinstance(tokens, list):
                return []
        elif isinstance(result, list):
            tokens = result
        else:
            return []

        normalized: List[Dict[str, str]] = []
        for token in tokens:
            normalized.append(self._normalize_token(token))
        return normalized

    def _normalize_tx(self, tx: Any) -> Dict[str, str]:
        if not isinstance(tx, dict):
            return {}
        hash_value = tx.get("hash") or tx.get("txHash") or ""
        timestamp = self._format_timestamp(tx.get("timestamp") or tx.get("time"))
        method = tx.get("method") or tx.get("function") or "txn"
        decoded = tx.get("decoded")
        if not method and isinstance(decoded, dict):
            method = decoded.get("name") or decoded.get("signature") or "txn"
        amount = tx.get("value") or tx.get("amount") or tx.get("quantity") or ""
        explorer = tx.get("url") or tx.get("explorerUrl") or ""
        if hash_value and not explorer and len(hash_value) > 6:
            explorer = f"https://basescan.org/tx/{hash_value}"
        return {
            "method": str(method or "txn"),
            "amount": str(amount or ""),
            "timestamp": timestamp,
            "hash": str(hash_value),
            "explorer_url": str(explorer),
        }

    @staticmethod
    def _normalize_token(token: Any) -> Dict[str, str]:
        if not isinstance(token, dict):
            return {}
        base_token = token.get("baseToken", {})
        quote_token = token.get("quoteToken", {})
        base_symbol = base_token.get("symbol") if isinstance(base_token, dict) else None
        quote_symbol = (
            quote_token.get("symbol") if isinstance(quote_token, dict) else None
        )
        pair_label = None
        if base_symbol and quote_symbol:
            pair_label = f"{base_symbol}/{quote_symbol}"
        symbol = token.get("symbol") or pair_label or token.get("pair")

        price = token.get("priceUsd") or token.get("price")
        if price is None and isinstance(token.get("price"), dict):
            price = token["price"].get("usd")

        volume = token.get("volume24h") or token.get("fdv")
        if volume is None:
            vol_obj = token.get("volume")
            if isinstance(vol_obj, dict):
                volume = vol_obj.get("h24") or vol_obj.get("h6")

        liquidity = token.get("liquidity") or token.get("liquidityUsd")
        if isinstance(liquidity, dict):
            liquidity = liquidity.get("usd") or liquidity.get("base")

        change = token.get("priceChange24h") or token.get("change24h")
        if change is None:
            change_obj = token.get("priceChange")
            if isinstance(change_obj, dict):
                change = change_obj.get("h24") or change_obj.get("h6")

        url = token.get("url") or token.get("dexscreenerUrl")
        pair_address = token.get("pairAddress")
        chain_identifier = token.get("chainId")
        if (
            not url
            and isinstance(pair_address, str)
            and isinstance(chain_identifier, str)
        ):
            url = f"https://dexscreener.com/{chain_identifier}/{pair_address}"

        token_address = token.get("tokenAddress") or token.get("address")
        if not token_address and isinstance(base_token, dict):
            token_address = base_token.get("address")
        normalized = {
            "symbol": str(symbol or "TOKEN"),
            "price": str(price or "?"),
            "volume24h": str(volume or "?"),
            "liquidity": str(liquidity or "?"),
            "change24h": str(change or "?"),
            "url": str(url or ""),
        }
        if base_symbol:
            normalized["baseSymbol"] = str(base_symbol)
        if chain_identifier:
            normalized["chainId"] = str(chain_identifier)
        token_name = token.get("name")
        if not token_name and isinstance(base_token, dict):
            token_name = base_token.get("name")
        if token_name:
            normalized["name"] = str(token_name)
        if token_address:
            normalized["address"] = str(token_address)
        if pair_address:
            normalized["pairAddress"] = str(pair_address)
        if chain_identifier:
            normalized["chainId"] = str(chain_identifier)
        return normalized

    @staticmethod
    def _build_token_context_entry(
        token: Mapping[str, Any], source: str | None = None
    ) -> Dict[str, str]:
        entry: Dict[str, str] = {}
        for key, target in (
            ("symbol", "symbol"),
            ("baseSymbol", "baseSymbol"),
            ("name", "name"),
            ("address", "address"),
            ("tokenAddress", "address"),
            ("pairAddress", "pairAddress"),
            ("chainId", "chainId"),
            ("url", "url"),
        ):
            value = token.get(key)
            if not value:
                continue
            if target == "address" and "address" in entry:
                continue
            entry[target] = str(value)
        if source:
            entry["source"] = str(source)
        return entry

    @staticmethod
    def _extract_response_text(response: Any) -> str:
        if not response:
            return ""
        candidates = getattr(response, "candidates", None)
        if not candidates:
            return ""
        candidate = candidates[0]
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None)
        if not parts:
            return ""
        text_fragments: List[str] = []
        for part in parts:
            value = getattr(part, "text", None)
            if value:
                text_fragments.append(value)
        return "".join(text_fragments)

    @staticmethod
    def _format_timestamp(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (int, float)):
            epoch = float(value)
            if epoch > 1e18:  # nanoseconds
                epoch /= 1e9
            elif epoch > 1e15:  # microseconds
                epoch /= 1e6
            elif epoch > 1e12:  # milliseconds
                epoch /= 1e3
            dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M:%SZ")
        if isinstance(value, str):
            trimmed = value.strip()
            if not trimmed:
                return ""
            if trimmed.isdigit():
                return GeminiPlanner._format_timestamp(int(trimmed))
            try:
                dt = datetime.fromisoformat(trimmed.replace("Z", "+00:00"))
                return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
            except ValueError:
                return trimmed
        return str(value)

    @staticmethod
    def _derive_chain_id(network: Any) -> str:
        if not isinstance(network, str) or not network:
            return "base"
        lowered = network.lower()
        if lowered.startswith("base-"):
            return "base"
        if lowered in {"base", "base-mainnet"}:
            return "base"
        return lowered.split("-")[0]

    @staticmethod
    def _derive_chain_numeric(network: Any) -> int:
        if isinstance(network, (int, float)):
            return int(network)
        if isinstance(network, str):
            lowered = network.lower()
            if lowered in {"base", "base-mainnet"} or lowered.startswith("base-"):
                return 8453
            digits = "".join(ch for ch in lowered if ch.isdigit())
            if digits:
                try:
                    return int(digits)
                except ValueError:
                    pass
        return 8453
