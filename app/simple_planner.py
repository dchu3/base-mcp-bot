"""Simplified planner with pattern-based routing and template formatting."""

from typing import Any, Dict, List, Optional
import google.generativeai as genai

from app.intent_matcher import Intent, MatchedIntent, match_intent
from app.mcp_client import MCPManager
from app.planner_types import PlannerResult
from app.token_card import (
    format_token_card,
    format_token_list,
    format_activity_summary,
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

logger = get_logger(__name__)


class SimplePlanner:
    """Hybrid planner: pattern matching + direct MCP calls + optional AI enhancement."""

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

        # Format list
        max_results = context.get("max_results", 5)
        intro = "*🔥 Trending Tokens*\n\n"
        card = format_token_list(base_tokens, max_tokens=max_results)

        return PlannerResult(message=intro + card, tokens=base_tokens[:max_results])

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
        """Handle unknown intent with AI."""
        logger.info("unknown_intent_fallback", message=message)

        # Use AI to understand and respond
        prompt = f"""You are a helpful crypto assistant for Base chain tokens.

User message: "{message}"

If the user is asking about a specific token, tell them to provide the token address or symbol.
If they're asking about DEX activity, mention they can ask about Uniswap, Aerodrome, etc.
If they're asking about safety, tell them to provide a token address.

Keep your response brief (2-3 sentences). Be helpful and friendly."""

        try:
            response = await self.model.generate_content_async(prompt)
            return PlannerResult(
                message=escape_markdown(response.text.strip()),
                tokens=[],
            )
        except Exception as exc:
            logger.error("ai_fallback_error", error=str(exc))
            return PlannerResult(
                message=escape_markdown(
                    "I'm not sure how to help with that. "
                    "Try asking about a specific token (by address or symbol), "
                    "trending tokens, or DEX activity."
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
