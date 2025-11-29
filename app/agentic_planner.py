"""Agentic planner using Gemini native function calling.

This planner lets Gemini decide which tools to call, supports multi-turn
reasoning, and synthesizes natural language responses.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import google.generativeai as genai

from app.mcp_client import MCPManager
from app.planner_types import PlannerResult
from app.tool_converter import parse_function_call_name
from app.utils.formatting import escape_markdown
from app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ToolCall:
    """Represents a single tool call made by the model."""

    client: str
    method: str
    params: Dict[str, Any]
    result: Optional[Any] = None
    error: Optional[str] = None


@dataclass
class AgenticContext:
    """Tracks state across iterations of the agentic loop."""

    iteration: int = 0
    total_tool_calls: int = 0
    tool_calls: List[ToolCall] = field(default_factory=list)
    tokens_found: List[Dict[str, str]] = field(default_factory=list)


# System prompt for agentic mode
AGENTIC_SYSTEM_PROMPT = """You are a crypto trading assistant for Base blockchain.

## Your Capabilities
You can call tools to:
- Search tokens and get prices (dexscreener)
- Get pool/liquidity data (dexpaprika)
- Check token safety for honeypots (honeypot)
- Query on-chain transactions (base)
- Search the web for crypto news (websearch)

## Workflow
1. Analyze the user's request
2. Call the relevant tools to gather data
3. If you need more data, call additional tools
4. When you have enough information, provide a helpful response

## Guidelines
- For token safety, ALWAYS call honeypot_check_token before recommending any token
- Use dexpaprika for pool analytics (getNetworkPools, getTokenPools)
- Use dexscreener for token search and trending (searchPairs, getLatestBoostedTokens)
- Synthesize tool results into conversational responses
- Include relevant numbers: price, volume, liquidity, market cap
- Warn clearly about risks (honeypot, high tax, low liquidity)
- Be concise - this is for Telegram

## Response Format
After gathering data, provide a natural language summary. Include:
- Key findings from your tool calls
- Safety assessment if checking tokens
- Relevant metrics and data points
- Any warnings or caveats
"""


class AgenticPlanner:
    """Planner using Gemini native function calling for agentic behavior."""

    DEFAULT_MAX_ITERATIONS = 5
    DEFAULT_MAX_TOOL_CALLS = 20
    DEFAULT_TIMEOUT_SECONDS = 60

    def __init__(
        self,
        api_key: str,
        mcp_manager: MCPManager,
        model_name: str = "gemini-1.5-flash-latest",
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the agentic planner.

        Args:
            api_key: Gemini API key
            mcp_manager: MCP client manager
            model_name: Gemini model to use
            max_iterations: Maximum number of think-act-observe loops
            max_tool_calls: Maximum total tool calls per request
            timeout_seconds: Overall timeout for the request
        """
        genai.configure(api_key=api_key)
        self.mcp_manager = mcp_manager
        self.model_name = model_name
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls
        self.timeout_seconds = timeout_seconds
        self._model: Optional[genai.GenerativeModel] = None
        self._functions: Optional[List[genai.protos.FunctionDeclaration]] = None

    def _ensure_model(self) -> genai.GenerativeModel:
        """Lazy-load the model with function declarations."""
        if self._model is None:
            # Get function declarations from MCP tools
            self._functions = self.mcp_manager.get_gemini_functions()

            # Create tool with all function declarations
            tools = None
            if self._functions:
                tools = [genai.protos.Tool(function_declarations=self._functions)]

            self._model = genai.GenerativeModel(
                model_name=self.model_name,
                tools=tools,
                system_instruction=AGENTIC_SYSTEM_PROMPT,
            )

            logger.info(
                "agentic_model_initialized",
                model=self.model_name,
                function_count=len(self._functions) if self._functions else 0,
            )

        return self._model

    async def run(
        self, message: str, context: Optional[Dict[str, Any]] = None
    ) -> PlannerResult:
        """Execute the agentic loop to answer the user's query.

        Args:
            message: User's message
            context: Optional context (conversation history, recent tokens, etc.)

        Returns:
            PlannerResult with the response and any tokens found
        """
        context = context or {}
        agentic_ctx = AgenticContext()

        try:
            return await asyncio.wait_for(
                self._run_loop(message, context, agentic_ctx),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "agentic_timeout",
                message=message,
                iterations=agentic_ctx.iteration,
                tool_calls=agentic_ctx.total_tool_calls,
            )
            # Return partial results if we have any
            if agentic_ctx.tool_calls:
                return self._synthesize_partial_response(message, agentic_ctx)
            return PlannerResult(
                message=escape_markdown(
                    "Request timed out. Please try a simpler query."
                ),
                tokens=[],
            )
        except Exception as exc:
            logger.error("agentic_error", message=message, error=str(exc))
            return PlannerResult(
                message=escape_markdown(f"Sorry, I encountered an error: {exc}"),
                tokens=[],
            )

    async def _run_loop(
        self,
        message: str,
        context: Dict[str, Any],
        agentic_ctx: AgenticContext,
    ) -> PlannerResult:
        """Run the think-act-observe loop."""
        model = self._ensure_model()

        # Build initial messages
        messages = self._build_initial_messages(message, context)

        for iteration in range(self.max_iterations):
            agentic_ctx.iteration = iteration + 1

            logger.info(
                "agentic_iteration",
                iteration=agentic_ctx.iteration,
                tool_calls_so_far=agentic_ctx.total_tool_calls,
            )

            # Generate response (may include function calls)
            response = await asyncio.to_thread(
                model.generate_content,
                messages,
            )

            # Check if model wants to call functions
            function_calls = self._extract_function_calls(response)

            if not function_calls:
                # Model is done, extract final response
                final_text = self._extract_text_response(response)
                return PlannerResult(
                    message=escape_markdown(final_text),
                    tokens=agentic_ctx.tokens_found,
                )

            # Check limits
            if agentic_ctx.total_tool_calls + len(function_calls) > self.max_tool_calls:
                logger.warning(
                    "agentic_tool_limit",
                    current=agentic_ctx.total_tool_calls,
                    requested=len(function_calls),
                    max=self.max_tool_calls,
                )
                # Execute only up to the limit
                remaining = self.max_tool_calls - agentic_ctx.total_tool_calls
                function_calls = function_calls[:remaining]

            # Execute function calls in parallel
            results = await self._execute_tools_parallel(function_calls, agentic_ctx)

            # Add model response and function results to conversation
            messages.append(
                {"role": "model", "parts": response.candidates[0].content.parts}
            )

            # Add function responses
            function_response_parts = []
            for fc, result in zip(function_calls, results):
                function_response_parts.append(
                    genai.protos.Part(
                        function_response=genai.protos.FunctionResponse(
                            name=fc.name,
                            response={"result": result},
                        )
                    )
                )

            messages.append({"role": "user", "parts": function_response_parts})

            # Check if we've hit tool call limit
            if agentic_ctx.total_tool_calls >= self.max_tool_calls:
                logger.info(
                    "agentic_tool_limit_reached", total=agentic_ctx.total_tool_calls
                )
                break

        # Max iterations reached, synthesize response from what we have
        return self._synthesize_partial_response(message, agentic_ctx)

    def _build_initial_messages(
        self, message: str, context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Build the initial message list for the conversation."""
        messages = []

        # Add conversation history if available
        history = context.get("conversation_history", [])
        for entry in history[-5:]:  # Last 5 messages
            role = entry.get("role", "user")
            content = entry.get("content", "")
            if role in ("user", "model"):
                messages.append({"role": role, "parts": [{"text": content}]})

        # Add context about recent tokens if available
        recent_tokens = context.get("recent_tokens", [])
        context_text = ""
        if recent_tokens:
            token_info = ", ".join(
                f"{t.get('symbol', '?')} ({t.get('address', '?')[:10]}...)"
                for t in recent_tokens[:5]
            )
            context_text = f"\n\n[Context: Recent tokens: {token_info}]"

        # Add current user message
        messages.append(
            {
                "role": "user",
                "parts": [{"text": message + context_text}],
            }
        )

        return messages

    def _extract_function_calls(
        self, response: genai.types.GenerateContentResponse
    ) -> List[genai.protos.FunctionCall]:
        """Extract function calls from model response."""
        function_calls = []

        if not response.candidates:
            return function_calls

        for part in response.candidates[0].content.parts:
            if hasattr(part, "function_call") and part.function_call:
                function_calls.append(part.function_call)

        return function_calls

    def _extract_text_response(
        self, response: genai.types.GenerateContentResponse
    ) -> str:
        """Extract text content from model response."""
        if not response.candidates:
            return "I couldn't generate a response."

        text_parts = []
        for part in response.candidates[0].content.parts:
            if hasattr(part, "text") and part.text:
                text_parts.append(part.text)

        return "\n".join(text_parts) if text_parts else "No response generated."

    async def _execute_tools_parallel(
        self,
        function_calls: List[genai.protos.FunctionCall],
        agentic_ctx: AgenticContext,
    ) -> List[Any]:
        """Execute multiple tool calls in parallel.

        Args:
            function_calls: List of function calls from model
            agentic_ctx: Context to track state

        Returns:
            List of results (or error dicts) in same order as function_calls
        """
        tasks = [self._execute_single_tool(fc, agentic_ctx) for fc in function_calls]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert exceptions to error dicts
        processed_results = []
        for result in results:
            if isinstance(result, Exception):
                processed_results.append({"error": str(result)})
            else:
                processed_results.append(result)

        return processed_results

    async def _execute_single_tool(
        self,
        function_call: genai.protos.FunctionCall,
        agentic_ctx: AgenticContext,
    ) -> Any:
        """Execute a single tool call.

        Args:
            function_call: Function call from model
            agentic_ctx: Context to track state

        Returns:
            Tool result or error dict
        """
        agentic_ctx.total_tool_calls += 1

        # Parse the namespaced function name
        client_name, method_name = parse_function_call_name(function_call.name)

        # Convert proto args to dict
        params = dict(function_call.args) if function_call.args else {}

        tool_call = ToolCall(
            client=client_name,
            method=method_name,
            params=params,
        )
        agentic_ctx.tool_calls.append(tool_call)

        logger.info(
            "agentic_tool_call",
            client=client_name,
            method=method_name,
            params=params,
        )

        # Get the MCP client
        client = self.mcp_manager.get_client(client_name)
        if not client:
            error = f"Unknown client: {client_name}"
            tool_call.error = error
            logger.warning("agentic_unknown_client", client=client_name)
            return {"error": error}

        try:
            result = await client.call_tool(method_name, params)
            tool_call.result = result

            # Extract tokens from result for context
            self._extract_tokens_from_result(result, agentic_ctx)

            # Truncate large results for the model
            return self._truncate_result(result)
        except Exception as exc:
            error = str(exc)
            tool_call.error = error
            logger.warning(
                "agentic_tool_error",
                client=client_name,
                method=method_name,
                error=error,
            )
            return {"error": error}

    def _truncate_result(self, result: Any, max_items: int = 10) -> Any:
        """Truncate large results to avoid context overflow."""
        if isinstance(result, dict):
            # Handle pool/token lists
            for key in ("pools", "tokens", "pairs", "transactions"):
                if key in result and isinstance(result[key], list):
                    if len(result[key]) > max_items:
                        result = result.copy()
                        result[key] = result[key][:max_items]
                        result[f"_{key}_truncated"] = True
            return result

        if isinstance(result, list) and len(result) > max_items:
            return result[:max_items]

        return result

    def _extract_tokens_from_result(
        self, result: Any, agentic_ctx: AgenticContext
    ) -> None:
        """Extract token information from tool results for context."""
        if not isinstance(result, dict):
            return

        # Extract from pools
        pools = result.get("pools", [])
        for pool in pools[:5]:
            tokens = pool.get("tokens", [])
            for token in tokens:
                if isinstance(token, dict) and token.get("id"):
                    agentic_ctx.tokens_found.append(
                        {
                            "address": token.get("id", ""),
                            "symbol": token.get("symbol", ""),
                            "name": token.get("name", ""),
                            "chain": pool.get("chain", "base"),
                        }
                    )

        # Extract from pairs
        pairs = result.get("pairs", [])
        for pair in pairs[:5]:
            base_token = pair.get("baseToken", {})
            if base_token.get("address"):
                agentic_ctx.tokens_found.append(
                    {
                        "address": base_token.get("address", ""),
                        "symbol": base_token.get("symbol", ""),
                        "name": base_token.get("name", ""),
                        "chain": pair.get("chainId", "base"),
                    }
                )

    def _synthesize_partial_response(
        self, message: str, agentic_ctx: AgenticContext
    ) -> PlannerResult:
        """Synthesize a response from partial results when iteration limit reached."""
        if not agentic_ctx.tool_calls:
            return PlannerResult(
                message=escape_markdown(
                    "I couldn't complete the analysis. Please try a simpler query."
                ),
                tokens=[],
            )

        # Build summary from tool calls
        lines = ["*Analysis Results*\n"]

        successful_calls = [tc for tc in agentic_ctx.tool_calls if tc.result]
        failed_calls = [tc for tc in agentic_ctx.tool_calls if tc.error]

        if successful_calls:
            lines.append(f"Completed {len(successful_calls)} tool calls:\n")
            for tc in successful_calls[:5]:
                lines.append(f"• {tc.client}.{tc.method}")

        if failed_calls:
            lines.append(f"\n⚠️ {len(failed_calls)} calls failed")

        lines.append("\n_Response incomplete due to iteration limit._")

        return PlannerResult(
            message="\n".join(lines),
            tokens=agentic_ctx.tokens_found,
        )
