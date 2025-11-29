"""Simplified planner with pattern-based routing and template formatting."""

import asyncio
from typing import Any, Dict, List, Optional, TYPE_CHECKING
import google.generativeai as genai

from app.intent_matcher import Intent, MatchedIntent, match_intent
from app.mcp_client import MCPManager
from app.planner_types import PlannerResult
from app.token_card import (
    format_token_card,
    format_token_list,
    format_boosted_token_list,
    format_activity_summary,
    format_pool_list,
    format_safety_result,
    format_swap_activity,
)
from app.utils.formatting import escape_markdown
from app.utils.logging import get_logger
from app.utils.routers import (
    DEFAULT_ROUTERS,
    get_router_display_name,
    match_router_name,
)
from app.utils.tx_parser import extract_tokens_from_transactions

if TYPE_CHECKING:
    from app.planner import GeminiPlanner

logger = get_logger(__name__)


class SimplePlanner:
    """Hybrid planner: pattern matching + direct MCP calls + agentic fallback."""

    def __init__(
        self,
        api_key: str,
        mcp_manager: MCPManager,
        model_name: str,
        router_map: Dict[str, Dict[str, str]],
        enable_ai_insights: bool = True,
    ):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name=model_name)
        self.mcp_manager = mcp_manager
        self.router_map = router_map
        self.enable_ai_insights = enable_ai_insights
        self.chain_id = "base"
        self._api_key = api_key
        self._model_name = model_name
        self._agentic_planner: Optional["GeminiPlanner"] = None
        self._planner_lock = asyncio.Lock()

    async def _get_agentic_planner(self) -> "GeminiPlanner":
        """Lazy-load the agentic planner for unknown intents (thread-safe)."""
        if self._agentic_planner is None:
            async with self._planner_lock:
                # Double-check after acquiring lock
                if self._agentic_planner is None:
                    from app.planner import GeminiPlanner
                    self._agentic_planner = GeminiPlanner(
                        api_key=self._api_key,
                        mcp_manager=self.mcp_manager,
                        router_keys=list(self.router_map.keys()),
                        router_map=self.router_map,
                        model_name=self._model_name,
                    )
        return self._agentic_planner

    async def run(self, message: str, context: Dict[str, Any]) -> PlannerResult:
        """Process user message and return formatted response.

        Args:
            message: User's input message.
            context: Additional context (conversation history, etc.).

        Returns:
            PlannerResult with formatted message and discovered tokens.
        """
        logger.info("simple_planner_starting", message=message)

        # Step 1: Match intent
        matched = match_intent(message)
        logger.info(
            "intent_matched",
            intent=matched.intent.value,
            confidence=matched.confidence,
        )

        # Step 2: Execute based on intent
        try:
            if matched.intent == Intent.TOKEN_LOOKUP:
                return await self._handle_token_lookup(matched, context)

            elif matched.intent == Intent.TOKEN_SEARCH:
                return await self._handle_token_search(matched, context)

            elif matched.intent == Intent.TRENDING:
                return await self._handle_trending(context)

            elif matched.intent == Intent.POOL_DISCOVERY_SAFETY:
                return await self._handle_pool_discovery_safety(matched, context)

            elif matched.intent == Intent.POOL_ANALYTICS:
                return await self._handle_pool_analytics(matched, context)

            elif matched.intent == Intent.ROUTER_ACTIVITY:
                return await self._handle_router_activity(matched, context)

            elif matched.intent == Intent.SAFETY_CHECK:
                return await self._handle_safety_check(matched, context)

            elif matched.intent == Intent.WEB_SEARCH:
                return await self._handle_web_search(matched, context)

            else:
                # Fallback to AI for complex queries
                return await self._handle_unknown(message, context)

        except Exception as exc:
            logger.error("simple_planner_error", error=str(exc))
            return PlannerResult(
                message=escape_markdown(f"Sorry, I encountered an error: {exc}"),
                tokens=[],
            )

    async def _handle_token_lookup(
        self, matched: MatchedIntent, context: Dict[str, Any]
    ) -> PlannerResult:
        """Handle token address lookup."""
        address = matched.token_address
        logger.info("token_lookup", address=address)

        # Call Dexscreener
        result = await self.mcp_manager.dexscreener.call_tool(
            "getPairsByToken", {"chainId": self.chain_id, "tokenAddress": address}
        )

        pairs = self._extract_pairs(result)
        if not pairs:
            return PlannerResult(
                message=escape_markdown(
                    f"No trading pairs found for address {address[:10]}..."
                ),
                tokens=[],
            )

        # Format the best pair
        best_pair = pairs[0]

        # Run honeypot check
        honeypot_data = None
        try:
            logger.info("honeypot_check", address=address)
            honeypot_data = await self.mcp_manager.honeypot.call_tool(
                "check_token", {"address": address, "chainId": 8453}
            )
            logger.info(
                "honeypot_result",
                address=address,
                verdict=honeypot_data.get("summary", {}).get("verdict"),
            )
        except Exception as hp_exc:
            logger.warning("honeypot_check_failed", address=address, error=str(hp_exc))

        card = format_token_card(best_pair, honeypot_data)

        # Optionally add AI insight
        if self.enable_ai_insights and len(pairs) > 0:
            insight = await self._generate_insight(best_pair)
            if insight:
                card += f"\n\n💡 _{escape_markdown(insight)}_"

        return PlannerResult(message=card, tokens=pairs[:5])

    async def _handle_token_search(
        self, matched: MatchedIntent, context: Dict[str, Any]
    ) -> PlannerResult:
        """Handle token symbol search."""
        symbol = matched.token_symbol
        logger.info("token_search", symbol=symbol)

        # Call Dexscreener search
        result = await self.mcp_manager.dexscreener.call_tool(
            "searchPairs", {"query": symbol}
        )

        pairs = self._extract_pairs(result)
        # Filter to Base chain
        base_pairs = [p for p in pairs if p.get("chainId") == "base"]
        if not base_pairs:
            base_pairs = pairs  # Fallback to all pairs

        if not base_pairs:
            return PlannerResult(
                message=escape_markdown(f"No tokens found matching '{symbol}'."),
                tokens=[],
            )

        # Format top results
        max_results = context.get("max_results", 3)
        card = format_token_list(base_pairs, max_tokens=max_results)

        return PlannerResult(message=card, tokens=base_pairs[:max_results])

    async def _handle_trending(self, context: Dict[str, Any]) -> PlannerResult:
        """Handle trending tokens request."""
        logger.info("trending_lookup")

        # Call Dexscreener for boosted tokens
        result = await self.mcp_manager.dexscreener.call_tool(
            "getLatestBoostedTokens", {}
        )

        tokens = self._extract_tokens(result)
        # Filter to Base chain if possible
        base_tokens = [t for t in tokens if t.get("chainId") == "base"]
        if not base_tokens:
            base_tokens = tokens[:10]

        if not base_tokens:
            return PlannerResult(
                message=escape_markdown("No trending tokens found at the moment."),
                tokens=[],
            )

        # Format list using boosted token formatter
        max_results = context.get("max_results", 5)
        intro = "*🔥 Trending/Boosted Tokens*\n\n"
        card = format_boosted_token_list(base_tokens, max_tokens=max_results)

        return PlannerResult(message=intro + card, tokens=base_tokens[:max_results])

    async def _handle_pool_discovery_safety(
        self, matched: MatchedIntent, context: Dict[str, Any]
    ) -> PlannerResult:
        """Handle pool discovery with honeypot safety check.
        
        Fetches latest pools from DexPaprika and runs honeypot checks on each.
        """
        network = matched.network or "base"
        logger.info("pool_discovery_safety", network=network)

        # Check if required MCP servers are available
        if not self.mcp_manager.dexpaprika:
            return PlannerResult(
                message=escape_markdown(
                    "Pool discovery requires DexPaprika. "
                    "Set MCP_DEXPAPRIKA_CMD in your .env file."
                ),
                tokens=[],
            )

        if not self.mcp_manager.honeypot:
            return PlannerResult(
                message=escape_markdown(
                    "Safety checks require Honeypot MCP. "
                    "Set MCP_HONEYPOT_CMD in your .env file."
                ),
                tokens=[],
            )

        # Only EVM chains support honeypot checks
        if network not in ("base", "ethereum", "arbitrum", "optimism", "polygon", "bsc"):
            return PlannerResult(
                message=escape_markdown(
                    f"Honeypot checks not supported for {network}. "
                    "Only EVM chains (Base, Ethereum, etc.) are supported."
                ),
                tokens=[],
            )

        try:
            max_results = context.get("max_results", 5)
            
            # Step 1: Get latest pools
            result = await self.mcp_manager.dexpaprika.call_tool(
                "getNetworkPools",
                {
                    "network": network,
                    "orderBy": "created_at",
                    "limit": max_results,
                    "sort": "desc",
                },
            )

            pools = []
            if isinstance(result, dict):
                pools = result.get("pools", [])
            elif isinstance(result, list):
                pools = result

            if not pools:
                return PlannerResult(
                    message=escape_markdown(f"No new pools found on {network.title()}."),
                    tokens=[],
                )

            # Step 2: Honeypot check each pool's base token
            lines = [f"*🔍 New Pools on {escape_markdown(network.title())} with Safety Check*\n"]
            
            # Get chain ID for honeypot API
            chain_id_map = {
                "base": 8453,
                "ethereum": 1,
                "arbitrum": 42161,
                "optimism": 10,
                "polygon": 137,
                "bsc": 56,
            }
            chain_id = chain_id_map.get(network, 8453)

            for i, pool in enumerate(pools[:max_results], 1):
                tokens = pool.get("tokens", [])
                if not tokens:
                    continue

                # Get base token (first non-WETH/USDC token)
                base_token = None
                for t in tokens:
                    symbol = t.get("symbol", "").upper()
                    if symbol not in ("WETH", "USDC", "USDT", "DAI", "ETH"):
                        base_token = t
                        break
                
                if not base_token:
                    base_token = tokens[0]

                token_address = base_token.get("id", "")
                token_symbol = base_token.get("symbol", "?")
                token_name = base_token.get("name", "")
                
                # Format pair name
                pair_symbols = "/".join(t.get("symbol", "?") for t in tokens[:2])
                dex_name = pool.get("dex_name", "Unknown DEX")
                created_at = pool.get("created_at", "")[:16].replace("T", " ")

                # Run honeypot check
                safety_badge = "⏳"
                safety_detail = ""
                try:
                    if token_address and token_address.startswith("0x"):
                        hp_result = await self.mcp_manager.honeypot.call_tool(
                            "check_token",
                            {"address": token_address, "chainId": chain_id},
                        )
                        
                        # Extract verdict
                        summary = hp_result.get("summary", {}) if isinstance(hp_result, dict) else {}
                        verdict = summary.get("verdict", "UNKNOWN")
                        risk = hp_result.get("risk", {}) if isinstance(hp_result, dict) else {}
                        risk_level = risk.get("riskLevel") if isinstance(risk, dict) else None
                        
                        # Get sell tax if available
                        sim_result = hp_result.get("simulationResult", {}) if isinstance(hp_result, dict) else {}
                        sell_tax = sim_result.get("sellTax")
                        
                        if verdict in ("SAFE_TO_TRADE", "SAFE", "OK"):
                            safety_badge = "✅"
                            safety_detail = "Safe"
                        elif verdict in ("CAUTION", "WARNING"):
                            safety_badge = "⚠️"
                            if sell_tax and float(sell_tax) > 5:
                                safety_detail = f"Caution \\- {sell_tax}% sell tax"
                            else:
                                safety_detail = "Caution"
                        elif verdict in ("HONEYPOT", "DANGER", "DO_NOT_TRADE"):
                            safety_badge = "🚨"
                            if sell_tax and float(sell_tax) >= 100:
                                safety_detail = "HONEYPOT \\- 100% sell tax"
                            else:
                                safety_detail = "DO NOT TRADE"
                        else:
                            safety_badge = "❓"
                            safety_detail = "Unknown"
                            
                        if risk_level is not None and risk_level > 50:
                            safety_detail += f" \\(Risk: {risk_level}\\)"
                    else:
                        safety_badge = "❓"
                        safety_detail = "Invalid address"
                        
                except Exception as hp_err:
                    logger.warning("honeypot_check_failed", token=token_address, error=str(hp_err))
                    safety_badge = "❓"
                    safety_detail = "Check failed"

                # Format pool entry
                lines.append(f"*{i}\\. {escape_markdown(pair_symbols)}* \\({escape_markdown(dex_name)}\\)")
                lines.append(f"{safety_badge} {safety_detail}")
                
                # Token info
                if token_name and token_name != token_symbol:
                    lines.append(f"📍 {escape_markdown(token_name)} \\(`{token_address[:8]}...`\\)")
                else:
                    lines.append(f"📍 `{token_address[:10]}...{token_address[-4:]}`")
                
                lines.append(f"🕐 {escape_markdown(created_at)}")
                lines.append("")

            lines.append("_⚠️ New tokens are high risk\\. Always DYOR\\._")
            
            return PlannerResult(message="\n".join(lines), tokens=[])

        except Exception as exc:
            logger.error("pool_discovery_safety_error", network=network, error=str(exc))
            return PlannerResult(
                message=escape_markdown(
                    "Failed to fetch pool data. Please try again later."
                ),
                tokens=[],
            )

    async def _handle_pool_analytics(
        self, matched: MatchedIntent, context: Dict[str, Any]
    ) -> PlannerResult:
        """Handle pool/liquidity analytics request using DexPaprika."""
        network = matched.network or "base"
        logger.info("pool_analytics", network=network)

        # Check if DexPaprika is available
        if not self.mcp_manager.dexpaprika:
            return PlannerResult(
                message=escape_markdown(
                    "Pool analytics requires DexPaprika. "
                    "Set MCP_DEXPAPRIKA_CMD in your .env file."
                ),
                tokens=[],
            )

        try:
            max_results = context.get("max_results", 5)
            result = await self.mcp_manager.dexpaprika.call_tool(
                "getNetworkPools",
                {
                    "network": network,
                    "orderBy": "volume_usd",
                    "limit": max_results,
                    "sort": "desc",
                },
            )

            # Extract pools from result
            pools = []
            if isinstance(result, dict):
                pools = result.get("pools", [])
            elif isinstance(result, list):
                pools = result

            if not pools:
                return PlannerResult(
                    message=escape_markdown(
                        f"No pools found on {network.title()}."
                    ),
                    tokens=[],
                )

            card = format_pool_list(pools, network=network, max_pools=max_results)
            return PlannerResult(message=card, tokens=[])

        except Exception as exc:
            logger.error("pool_analytics_error", network=network, error=str(exc))
            return PlannerResult(
                message=escape_markdown(
                    "Failed to fetch pool data. Please try again later."
                ),
                tokens=[],
            )

    async def _handle_router_activity(
        self, matched: MatchedIntent, context: Dict[str, Any]
    ) -> PlannerResult:
        """Handle DEX router activity request."""
        # Use router_key if available, otherwise try to match from router_name
        router_key = matched.router_key
        if not router_key and matched.router_name:
            router_key = match_router_name(matched.router_name)
        if not router_key:
            router_key = "uniswap_v2"  # Default

        display_name = get_router_display_name(router_key)
        logger.info("router_activity", router=router_key, display_name=display_name)

        # Get router address from config
        router_networks = DEFAULT_ROUTERS.get(router_key)
        if not router_networks:
            return PlannerResult(
                message=escape_markdown(
                    f"Router '{router_key}' not found. Use /routers to see available options."
                ),
                tokens=[],
            )

        router_address = router_networks.get("base-mainnet")
        if not router_address or router_address == "0x" + "0" * 40:
            return PlannerResult(
                message=escape_markdown(
                    f"Router '{display_name}' not available on Base mainnet."
                ),
                tokens=[],
            )

        # Call Blockscout for transactions
        result = await self.mcp_manager.base.call_tool(
            "getDexRouterActivity",
            {"router": router_address, "sinceMinutes": 60},
        )

        transactions = self._extract_transactions(result)

        if not transactions:
            return PlannerResult(
                message=escape_markdown(f"No recent activity found on {display_name}."),
                tokens=[],
            )

        # Get full transaction details for recent swaps
        max_results = context.get("max_results", 5)
        swap_txs = [
            tx
            for tx in transactions
            if "swap" in (tx.get("method") or tx.get("function") or "").lower()
        ][:max_results]

        # Fetch full transaction details to get token transfers
        full_transactions = []
        for tx in swap_txs:
            tx_hash = tx.get("hash") or tx.get("transaction_hash")
            if tx_hash:
                try:
                    full_tx = await self.mcp_manager.base.call_tool(
                        "getTransactionByHash", {"hash": tx_hash}
                    )
                    if full_tx:
                        full_transactions.append(full_tx)
                except Exception as exc:
                    logger.warning("tx_fetch_failed", hash=tx_hash, error=str(exc))

        logger.info("fetched_full_txs", count=len(full_transactions))

        # Extract token addresses from full transaction data
        token_addresses = extract_tokens_from_transactions(full_transactions)
        logger.info(
            "extracted_tokens",
            count=len(token_addresses),
            addresses=token_addresses[:5],
        )

        # Look up tokens on Dexscreener and run honeypot checks in parallel
        token_data = []
        honeypot_results: Dict[str, Dict[str, Any]] = {}

        for addr in token_addresses[:max_results]:  # Limit to max_results tokens
            try:
                logger.info("dexscreener_lookup", address=addr)
                dex_result = await self.mcp_manager.dexscreener.call_tool(
                    "getPairsByToken",
                    {"chainId": self.chain_id, "tokenAddress": addr},
                )
                logger.info(
                    "dexscreener_result",
                    address=addr,
                    result_type=type(dex_result).__name__,
                    has_pairs=bool(self._extract_pairs(dex_result)),
                )
                pairs = self._extract_pairs(dex_result)
                if pairs:
                    # Add the best pair for this token
                    token_data.append(pairs[0])

                    # Run honeypot check for this token
                    try:
                        logger.info("honeypot_check", address=addr)
                        hp_result = await self.mcp_manager.honeypot.call_tool(
                            "check_token", {"address": addr, "chainId": 8453}
                        )
                        honeypot_results[addr.lower()] = hp_result
                        logger.info(
                            "honeypot_result",
                            address=addr,
                            verdict=hp_result.get("summary", {}).get("verdict"),
                        )
                    except Exception as hp_exc:
                        logger.warning(
                            "honeypot_check_failed", address=addr, error=str(hp_exc)
                        )
            except Exception as exc:
                logger.warning("token_lookup_failed", address=addr, error=str(exc))

        logger.info("token_data_count", count=len(token_data))

        # Format with token cards
        if token_data:
            card = format_swap_activity(
                token_data, transactions, display_name, honeypot_results
            )
        else:
            # Fallback to simple summary if no token data
            card = format_activity_summary(transactions, display_name)

        return PlannerResult(message=card, tokens=token_data)

    async def _handle_safety_check(
        self, matched: MatchedIntent, context: Dict[str, Any]
    ) -> PlannerResult:
        """Handle token safety check."""
        address = matched.token_address
        symbol = matched.token_symbol

        # If we only have a symbol, search for it first
        if not address and symbol:
            search_result = await self.mcp_manager.dexscreener.call_tool(
                "searchPairs", {"query": symbol}
            )
            pairs = self._extract_pairs(search_result)
            base_pairs = [p for p in pairs if p.get("chainId") == "base"]
            if base_pairs:
                base_token = base_pairs[0].get("baseToken", {})
                address = base_token.get("address")

        if not address:
            return PlannerResult(
                message=escape_markdown(
                    "Please provide a token address to check safety."
                ),
                tokens=[],
            )

        logger.info("safety_check", address=address)

        # Call Honeypot
        try:
            result = await self.mcp_manager.honeypot.call_tool(
                "check_token", {"address": address, "chainId": 8453}
            )
            card = format_safety_result(result)
        except Exception as exc:
            error_msg = str(exc).lower()
            if "404" in error_msg or "not found" in error_msg:
                # Token not indexed in Honeypot API
                card = "*Safety Check*\n" "⚠️ *UNABLE TO VERIFY*\n" + escape_markdown(
                    "This token is not indexed in the Honeypot database. "
                    "Exercise caution and do your own research."
                )
                logger.info("honeypot_token_not_found", address=address)
            else:
                # Other honeypot errors
                card = "*Safety Check*\n" "❓ *CHECK UNAVAILABLE*\n" + escape_markdown(
                    "Unable to verify token safety at this time. "
                    "Please try again later."
                )
                logger.warning("honeypot_check_error", address=address, error=str(exc))

        # Also get token info for context
        dex_result = await self.mcp_manager.dexscreener.call_tool(
            "getPairsByToken", {"chainId": self.chain_id, "tokenAddress": address}
        )
        pairs = self._extract_pairs(dex_result)
        if pairs:
            token_card = format_token_card(pairs[0])
            card = token_card + "\n\n" + card

        return PlannerResult(message=card, tokens=pairs[:1])

    async def _handle_web_search(
        self, matched: MatchedIntent, context: Dict[str, Any]
    ) -> PlannerResult:
        """Handle web search request."""
        query = matched.search_query
        if not query:
            return PlannerResult(
                message=escape_markdown(
                    "Please provide a search query. Example: 'search web for Bitcoin news'"
                ),
                tokens=[],
            )

        logger.info("web_search", query=query)

        # Check if websearch is available
        if not self.mcp_manager.websearch:
            return PlannerResult(
                message=escape_markdown(
                    "Web search is not configured. Set MCP_WEBSEARCH_CMD in your .env file."
                ),
                tokens=[],
            )

        try:
            result = await self.mcp_manager.websearch.call_tool(
                "search", {"query": query, "max_results": 5}
            )

            # Format the search results
            formatted = self._format_web_search_results(result, query)
            return PlannerResult(message=formatted, tokens=[])

        except Exception as exc:
            logger.error("web_search_error", query=query, error=str(exc))
            return PlannerResult(
                message=escape_markdown(f"Web search failed: {exc}"),
                tokens=[],
            )

    def _format_web_search_results(self, result: Any, query: str) -> str:
        """Format web search results for display."""
        header = f"*🔍 Web Search: {escape_markdown(query)}*\n"

        # Extract the result text
        if isinstance(result, dict):
            text = result.get("result") or result.get("content") or str(result)
        elif isinstance(result, str):
            text = result
        else:
            text = str(result)

        # Parse and format individual results
        lines = []
        # Split by numbered results (1. , 2. , etc.)
        import re
        entries = re.split(r'\n\n(?=\d+\.)', text)

        for entry in entries:
            entry = entry.strip()
            if not entry:
                continue

            # Extract title, URL, summary
            title_match = re.search(r'^\d+\.\s*(.+?)(?:\n|$)', entry)
            url_match = re.search(r'URL:\s*(https?://\S+)', entry)
            summary_match = re.search(r'Summary:\s*(.+)', entry, re.DOTALL)

            if title_match:
                title = title_match.group(1).strip()
                url = url_match.group(1).strip() if url_match else None
                summary = summary_match.group(1).strip()[:200] if summary_match else None

                # Format as clean entry
                if url:
                    lines.append(f"📰 *{escape_markdown(title)}*")
                    lines.append(f"   🔗 {url}")
                    if summary:
                        lines.append(f"   {escape_markdown(summary)}")
                    lines.append("")

        if lines:
            return header + "\n" + "\n".join(lines)
        else:
            # Fallback: just escape and return raw text
            return header + "\n" + escape_markdown(text[:1500])

    async def _handle_unknown(
        self, message: str, context: Dict[str, Any]
    ) -> PlannerResult:
        """Handle unknown intent with agentic planner."""
        logger.info("unknown_intent_agentic_fallback", message=message)

        try:
            # Delegate to the full agentic planner which can call tools
            planner = await self._get_agentic_planner()
            return await planner.run(message, context)
        except Exception as exc:
            logger.error("agentic_fallback_error", error=str(exc))
            return PlannerResult(
                message=escape_markdown(
                    "I'm not sure how to help with that. "
                    "Try asking about a specific token (by address or symbol), "
                    "trending tokens, or 'search web for <topic>'."
                ),
                tokens=[],
            )

    async def _generate_insight(self, token_data: Dict[str, Any]) -> Optional[str]:
        """Generate a brief AI insight about the token."""
        if not self.enable_ai_insights:
            return None

        try:
            base_token = token_data.get("baseToken", {})
            symbol = base_token.get("symbol") or token_data.get("symbol") or "token"
            price_change = token_data.get("priceChange", {}).get("h24")
            volume = token_data.get("volume", {}).get("h24")
            liquidity = token_data.get("liquidity", {}).get("usd")

            prompt = f"""Provide a 1-sentence insight about this token:
- Symbol: {symbol}
- 24h Price Change: {price_change}%
- 24h Volume: ${volume}
- Liquidity: ${liquidity}

Be concise and factual. No financial advice. Just observation."""

            response = await self.model.generate_content_async(prompt)
            insight = response.text.strip()
            # Limit length
            if len(insight) > 200:
                insight = insight[:197] + "..."
            return insight
        except Exception as exc:
            logger.warning("insight_generation_failed", error=str(exc))
            return None

    def _extract_pairs(self, result: Any) -> List[Dict[str, Any]]:
        """Extract pairs from Dexscreener result."""
        if not result:
            return []
        if isinstance(result, dict):
            return result.get("pairs", []) or result.get("tokens", []) or []
        if isinstance(result, list):
            return result
        return []

    def _extract_tokens(self, result: Any) -> List[Dict[str, Any]]:
        """Extract tokens from Dexscreener result."""
        if not result:
            return []
        if isinstance(result, dict):
            return result.get("tokens", []) or result.get("pairs", []) or []
        if isinstance(result, list):
            return result
        return []

    def _extract_transactions(self, result: Any) -> List[Dict[str, Any]]:
        """Extract transactions from Blockscout result."""
        if not result:
            return []
        if isinstance(result, dict):
            return result.get("items", []) or result.get("transactions", []) or []
        if isinstance(result, list):
            return result
        return []
