"""Gemini-powered planner that selects MCP tool calls."""

from __future__ import annotations

import asyncio
import json
import re
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple
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
class HoneypotTarget:
    """Token (and optional pair) queued for honeypot checks."""

    token: str
    pair: str | None = None


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

    DEFAULT_PROMPT = Template(
        textwrap.dedent(
            """
            You are an orchestrator for a Base blockchain Telegram bot that surfaces trading opportunities.

            Follow this workflow:
            1. Analyse the user request: "$message".
            2. Determine the relevant router key(s) for network "$network" from: $routers.
            3. Call base.getDexRouterActivity for each router using the user's lookback (fallback $default_lookback minutes) to capture the freshest swaps and the tokens involved.
            4. Cross-reference those token addresses with Dexscreener tools to evaluate price action, liquidity, and unusual volume so you can highlight opportunities or noteworthy movements.
            5. For each highlighted token, call honeypot.check_token to classify it as SAFE_TO_TRADE, CAUTION, or DO_NOT_TRADE and mention that verdict in your summary.
            6. If needed, call other supporting tools (e.g. transaction lookups) to clarify context.

            Available tools (client.method):
            - base.getDexRouterActivity(router: str, sinceMinutes: int)
            - base.getTransactionByHash(hash: str)
            - base.getContractABI(address: str)
            - base.resolveToken(address: str)
            - dexscreener.getTokenOverview(tokenAddress: str)
            - dexscreener.searchPairs(query: str)
            - dexscreener.getPairByAddress(pairAddress: str)
            - honeypot.check_token(address: str, chainId: int, pair?: str, forceSimulateLiquidity?: bool)

            Respond strictly as JSON with this schema:
            {"tools": [{"client": "base|dexscreener|honeypot", "method": "<method>", "params": {...}}]}
            Do not include any commentary outside the JSON payload.
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
    ) -> None:
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name=model_name)
        self.mcp_manager = mcp_manager
        self.router_keys = router_keys
        self._prompt_template = (
            Template(prompt_template) if prompt_template is not None else self.DEFAULT_PROMPT
        )
        self.router_map = router_map
        self._honeypot_discovery_cache: Dict[str, Tuple[float, str | None]] = {}
        self._honeypot_missing_cache: Dict[str, float] = {}

    async def run(self, message: str, context: Dict[str, Any]) -> str:
        plan = await self._plan(message, context)
        if not plan:
            logger.warning("planner_no_plan", message=message)
            return "I could not determine a suitable tool to answer that. Please rephrase or specify a router/token."

        results = await self._execute_plan(plan, context)
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
            payload = json.loads(self._strip_code_fence(text))
        except json.JSONDecodeError:
            logger.error("planner_invalid_json", output=text)
            return []

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
            invocations.append(ToolInvocation(client=client, method=method, params=params))
        if invocations:
            logger.info(
                "planner_plan",
                plan=[
                    {"client": call.client, "method": call.method, "params": call.params}
                    for call in invocations
                ],
            )
        return invocations

    def _build_prompt(self, message: str, context: Dict[str, Any]) -> str:
        routers = ", ".join(self.router_keys) or "none"
        context_map = {
            "message": message,
            "network": context.get("network", "base"),
            "routers": routers,
            "default_lookback": context.get("default_lookback", 30),
        }
        prompt = self._prompt_template.safe_substitute(context_map)
        if "$" in prompt:
            logger.warning("prompt_unresolved_placeholders", prompt=prompt)
        return prompt

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        """Remove Markdown code fences from the model output if present."""
        stripped = text.strip()
        fence_match = re.match(r"```[a-zA-Z0-9]*\n(.+?)\n```", stripped, re.DOTALL)
        if fence_match:
            return fence_match.group(1).strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            return stripped[3:-3].strip()
        return stripped

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
            elif isinstance(original_router_value, str) and not original_router_value.startswith("0x"):
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

        return normalized

    @staticmethod
    def _extract_token_param(params: Dict[str, Any]) -> str | None:
        if not isinstance(params, dict):
            return None
        token = params.get("tokenAddress") or params.get("token") or params.get("pairAddress")
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

    async def _execute_plan(
        self,
        plan: Sequence[ToolInvocation],
        context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        collected_tokens: Dict[str, str] = {}
        planned_token_keys: Set[str] = set()
        chain_id = self._derive_chain_id(context.get("network"))
        chain_numeric = self._derive_chain_numeric(context.get("network"))

        for call in plan:
            try:
                logger.info(
                    "planner_tool_call",
                    client=call.client,
                    method=call.method,
                    params=call.params,
                )
                if call.client == "base":
                    result = await self.mcp_manager.base.call_tool(call.method, call.params)
                elif call.client == "dexscreener":
                    result = await self.mcp_manager.dexscreener.call_tool(call.method, call.params)
                elif call.client == "honeypot":
                    if not self.mcp_manager.honeypot:
                        raise RuntimeError("Honeypot MCP server is not configured")
                    result = await self.mcp_manager.honeypot.call_tool(call.method, call.params)
                else:  # pragma: no cover - defensive guard
                    raise RuntimeError(f"Unsupported MCP client '{call.client}'")

                entry_payload: Dict[str, Any] = {"call": call, "result": result}
                if call.client == "dexscreener" and call.method in self.DEX_TOKEN_METHODS:
                    normalized_tokens = self._extract_token_entries(result)
                    if normalized_tokens:
                        entry_payload["tokens"] = normalized_tokens
                results.append(entry_payload)
                log_extra = {"client": call.client, "method": call.method}
                if isinstance(result, dict):
                    log_extra["result_keys"] = list(result.keys())[:5]
                    if "items" in result and isinstance(result["items"], list):
                        log_extra["items"] = len(result["items"])
                elif isinstance(result, list):
                    log_extra["items"] = len(result)
                logger.info("planner_tool_success", **log_extra)

                if call.client == "dexscreener":
                    token_addr = self._extract_token_param(call.params)
                    if token_addr:
                        planned_token_keys.add(token_addr.lower())

                if call.client == "base" and call.method == "getDexRouterActivity":
                    transactions = self._iter_transactions(result)
                    for token in self._collect_token_addresses(transactions):
                        collected_tokens.setdefault(token.lower(), token)
            except Exception as exc:  # pragma: no cover - network/process errors
                logger.error(
                    "planner_tool_error",
                    client=call.client,
                    method=call.method,
                    error=str(exc),
                )
                results.append({"call": call, "error": str(exc)})

        additional_tokens = [
            address
            for key, address in collected_tokens.items()
            if key not in planned_token_keys
        ][:3]

        for token in additional_tokens:
            invocation = ToolInvocation(
                client="dexscreener",
                method="getPairsByToken",
                params={"chainId": chain_id, "tokenAddress": token},
            )
            try:
                dex_result = await self.mcp_manager.dexscreener.call_tool(
                    invocation.method,
                    invocation.params,
                )
                entry_payload: Dict[str, Any] = {"call": invocation, "result": dex_result}
                normalized_tokens = self._extract_token_entries(dex_result)
                if normalized_tokens:
                    entry_payload["tokens"] = normalized_tokens
                results.append(entry_payload)
            except Exception as exc:  # pragma: no cover - network/process errors
                logger.error(
                    "planner_token_summary_error",
                    token=token,
                    error=str(exc),
                )
                results.append({"call": invocation, "error": str(exc)})

        honeypot_targets = self._select_honeypot_targets(results, collected_tokens)
        if honeypot_targets:
            verdicts = await self._fetch_honeypot_verdicts(honeypot_targets, chain_numeric)
            if verdicts:
                self._annotate_token_verdicts(results, verdicts)

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
            pair_addr = pair if isinstance(pair, str) and pair.startswith("0x") else None
            liquidity_value = self._coerce_float(liquidity)
            if lowered not in metadata:
                metadata[lowered] = (address, pair_addr, liquidity_value)
                token_order.append(lowered)
                return
            _, existing_pair, existing_liq = metadata[lowered]
            if liquidity_value > existing_liq:
                metadata[lowered] = (address, pair_addr or existing_pair, liquidity_value)

        for entry in results:
            tokens = entry.get("tokens")
            if not tokens:
                continue
            for token in tokens:
                if not isinstance(token, dict):
                    continue
                register(token.get("address"), token.get("pairAddress"), token.get("liquidity"))

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
            verdict = await self._evaluate_honeypot_target(client, token, chain_id, target.pair)
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
        attempted_discovery = bool(pair)

        for _ in range(2):
            try:
                result = await self._call_honeypot_check(client, token, chain_id, pair)
            except Exception as exc:  # pragma: no cover - network/process errors
                if not attempted_discovery:
                    attempted_discovery = True
                    pair = await self._discover_pair_for_token(client, token, chain_id)
                    if pair:
                        continue
                fallback = self._fallback_verdict_from_error(exc)
                log_fn = logger.info if fallback else logger.warning
                log_fn("honeypot_check_failed", address=token, pair=pair, error=str(exc))
                if fallback:
                    self._honeypot_missing_cache[cache_key] = time.time() + self.HONEYPOT_NOT_FOUND_TTL_SECONDS
                return fallback

            normalized = self._normalize_honeypot_result(result)
            if normalized:
                if pair:
                    self._honeypot_discovery_cache[cache_key] = (
                        time.time() + self.HONEYPOT_DISCOVERY_TTL_SECONDS,
                        pair,
                    )
                return normalized

            logger.warning("honeypot_check_malformed", address=token, pair=pair, result=result)
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
        if "honeypot check failed" in lowered:
            return {
                "verdict": "ERROR",
                "reason": "Honeypot check failed",
            }
        return None

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
    ) -> str:
        sections: List[str] = []
        token_lines: List[str] = []
        add_nfa = False
        router_label: str | None = None
        seen_pairs: Set[str] = set()

        for entry in results:
            call: ToolInvocation = entry["call"]
            title = f"{call.client}.{call.method}"
            if "error" in entry:
                sections.append(f"*{title}*: failed — {entry['error']}")
                continue

            result = entry.get("result")
            if call.method == "getDexRouterActivity":
                if router_label is None:
                    router_label = call.params.get("routerKey") or call.params.get("router")
                continue

            if call.method in self.DEX_TOKEN_METHODS:
                normalized_tokens = entry.get("tokens") or self._extract_token_entries(result)
                for token in normalized_tokens:
                    dedupe_key = token.get("url") or token.get("symbol") or ""
                    if dedupe_key and dedupe_key in seen_pairs:
                        continue
                    if dedupe_key:
                        seen_pairs.add(dedupe_key)
                    token_lines.append(format_token_summary(token))
                if normalized_tokens:
                    add_nfa = True
                continue

            sections.append(f"*{title}*:\n```\n{json.dumps(result, indent=2)[:1500]}\n```")

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
        return summary or "No recent data returned for that request."

    async def summarize_transactions(
        self,
        router_key: str,
        transactions: Iterable[Dict[str, Any]],
        network: str,
    ) -> str | None:
        """Return Dexscreener token summaries suitable for subscription alerts."""
        addresses = list(self._collect_token_addresses(transactions))
        if not addresses:
            return None

        chain_id = self._derive_chain_id(network)
        chain_numeric = self._derive_chain_numeric(network)
        address_plan = addresses[: self.MAX_ROUTER_ITEMS]
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

        targets = self._select_honeypot_targets([
            {"tokens": [entry for entry, _ in collected_entries]}
        ], {})
        verdicts = await self._fetch_honeypot_verdicts(targets, chain_numeric)

        summaries: List[str] = []
        seen_pairs: Set[str] = set()
        add_nfa = False

        for entry, source_address in collected_entries:
            if verdicts:
                self._apply_verdict_to_token(entry, verdicts, source_address)
            dedupe_key = entry.get("url") or entry.get("symbol") or ""
            if dedupe_key and dedupe_key in seen_pairs:
                continue
            if dedupe_key:
                seen_pairs.add(dedupe_key)
            summaries.append(format_token_summary(entry))
            add_nfa = True
            if len(summaries) >= self.MAX_ROUTER_ITEMS:
                break

        label = router_key or "router"
        header = escape_markdown(f"Dexscreener snapshots for {label}")
        message = join_messages(
            [
                header,
                join_messages(summaries[: self.MAX_ROUTER_ITEMS]),
            ]
        )
        if add_nfa:
            message = append_not_financial_advice(message)
        return message

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
            tokens = result.get("tokens") or result.get("results")
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
        quote_symbol = quote_token.get("symbol") if isinstance(quote_token, dict) else None
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
        if not url and isinstance(pair_address, str) and isinstance(chain_identifier, str):
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
        if token_address:
            normalized["address"] = str(token_address)
        if pair_address:
            normalized["pairAddress"] = str(pair_address)
        if chain_identifier:
            normalized["chainId"] = str(chain_identifier)
        return normalized

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
