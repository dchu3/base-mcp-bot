"""Gemini-powered planner that selects MCP tool calls."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence

import google.generativeai as genai

from app.mcp_client import MCPManager
from app.utils.formatting import (
    append_not_financial_advice,
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


class GeminiPlanner:
    """Use Gemini to decide which MCP tools to call."""

    MODEL = "gemini-1.5-flash"

    def __init__(
        self,
        api_key: str,
        mcp_manager: MCPManager,
        router_keys: Sequence[str],
    ) -> None:
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name=self.MODEL)
        self.mcp_manager = mcp_manager
        self.router_keys = router_keys

    async def run(self, message: str, context: Dict[str, Any]) -> str:
        plan = await self._plan(message, context)
        if not plan:
            logger.warning("planner_no_plan", message=message)
            return "I could not determine a suitable tool to answer that. Please rephrase or specify a router/token."

        results = await self._execute_plan(plan)
        return self._render_response(message, context, results)

    async def _plan(self, message: str, context: Dict[str, Any]) -> List[ToolInvocation]:
        prompt = self._build_prompt(message, context)
        logger.info("planner_prompt", prompt=prompt)
        response = await asyncio.to_thread(
            self.model.generate_content,
            [{"role": "user", "parts": [{"text": prompt}]}],
        )

        text = ""
        if response.candidates:
            parts = response.candidates[0].content.parts  # type: ignore[attr-defined]
            text_fragments = []
            for part in parts:
                value = getattr(part, "text", None)
                if value:
                    text_fragments.append(value)
            text = "".join(text_fragments)

        logger.info("planner_raw_response", output=text)
        if not text:
            return []

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            logger.error("planner_invalid_json", output=text)
            return []

        invocations = []
        for entry in payload.get("tools", []):
            client = entry.get("client")
            method = entry.get("method")
            params = entry.get("params", {})
            if client not in {"base", "dexscreener"} or not method:
                continue
            invocations.append(ToolInvocation(client=client, method=method, params=params))
        return invocations

    def _build_prompt(self, message: str, context: Dict[str, Any]) -> str:
        routers = ", ".join(self.router_keys)
        default_lookback = context.get("default_lookback")
        return (
            "You are an assistant for a Base blockchain Telegram bot. "
            "Choose which MCP tools to call so the bot can answer the user.\n"
            "Available tools (client.method):\n"
            "- base.getDexRouterActivity(router: str, sinceMinutes: int)\n"
            "- base.getTransactionByHash(hash: str)\n"
            "- base.getContractABI(address: str)\n"
            "- base.resolveToken(address: str)\n"
            "- dexscreener.getTokenOverview(tokenAddress: str)\n"
            "- dexscreener.searchPairs(query: str)\n"
            "- dexscreener.getPairByAddress(pairAddress: str)\n\n"
            f"Known router keys: {routers} (network: {context.get('network')}). "
            f"Default lookback_minutes is {default_lookback}.\n"
            "Respond strictly as JSON with this schema: "
            '{"tools": [{"client": "base|dexscreener", "method": "<method>", "params": {...}}]}.\n'
            "Do not include any text outside the JSON.\n"
            f"User request: {message}"
        )

    async def _execute_plan(
        self,
        plan: Sequence[ToolInvocation],
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for call in plan:
            try:
                if call.client == "base":
                    result = await self.mcp_manager.base.call_tool(call.method, call.params)
                else:
                    result = await self.mcp_manager.dexscreener.call_tool(call.method, call.params)
                results.append({"call": call, "result": result})
            except Exception as exc:  # pragma: no cover - network/process errors
                logger.error(
                    "planner_tool_error",
                    client=call.client,
                    method=call.method,
                    error=str(exc),
                )
                results.append({"call": call, "error": str(exc)})
        return results

    def _render_response(
        self,
        message: str,
        context: Dict[str, Any],
        results: Iterable[Dict[str, Any]],
    ) -> str:
        sections: List[str] = []
        add_nfa = False

        for entry in results:
            call: ToolInvocation = entry["call"]
            title = f"{call.client}.{call.method}"
            if "error" in entry:
                sections.append(f"*{title}*: failed — {entry['error']}")
                continue

            result = entry.get("result")
            if call.method == "getDexRouterActivity":
                sections.append(self._format_router_activity(result))
            elif call.method in {"getTokenOverview", "searchPairs", "getPairByAddress"}:
                sections.append(self._format_token_result(result))
                add_nfa = True
            else:
                sections.append(f"*{title}*:\n```\n{json.dumps(result, indent=2)[:1500]}\n```")

        summary = join_messages(sections)
        if add_nfa:
            summary = append_not_financial_advice(summary)
        return summary or "No data returned for that query."

    def _format_router_activity(self, result: Any) -> str:
        if not isinstance(result, list):
            return "*base.getDexRouterActivity*: no transactions."

        lines = [format_transaction(self._normalize_tx(tx)) for tx in result]
        return join_messages(["Recent transactions", "\n".join(lines)])

    def _format_token_result(self, result: Any) -> str:
        if isinstance(result, dict):
            tokens = result.get("tokens") or result.get("results") or []
        else:
            tokens = result
        if not isinstance(tokens, list) or not tokens:
            return "No token summaries available."
        lines = [format_token_summary(self._normalize_token(tok)) for tok in tokens]
        return join_messages(["Token summaries", "\n".join(lines)])

    @staticmethod
    def _normalize_tx(tx: Any) -> Dict[str, str]:
        return {
            "method": str(tx.get("method") or tx.get("function") or "txn"),
            "amount": str(tx.get("amount") or ""),
            "timestamp": str(tx.get("timestamp") or tx.get("time", "")),
            "hash": str(tx.get("hash") or tx.get("txHash") or ""),
            "explorer_url": str(tx.get("url") or tx.get("explorerUrl") or ""),
        } if isinstance(tx, dict) else {}

    @staticmethod
    def _normalize_token(token: Any) -> Dict[str, str]:
        if not isinstance(token, dict):
            return {}
        symbol = token.get("symbol") or token.get("baseToken", {}).get("symbol") or token.get("pair")
        price = token.get("priceUsd") or token.get("price")
        volume = token.get("volume24h") or token.get("fdv")
        liquidity = token.get("liquidity") or token.get("liquidityUsd")
        change = token.get("priceChange24h") or token.get("change24h")
        url = token.get("url") or token.get("dexscreenerUrl") or token.get("pairAddress")
        return {
            "symbol": str(symbol or "TOKEN"),
            "price": str(price or "?"),
            "volume24h": str(volume or "?"),
            "liquidity": str(liquidity or "?"),
            "change24h": str(change or "?"),
            "url": str(url or ""),
        }
