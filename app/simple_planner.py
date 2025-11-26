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
        card = format_token_card(best_pair)

        # Optionally add AI insight
        if self.enable_ai_insights and len(pairs) > 0:
            insight = await self._generate_insight(best_pair, "token lookup")
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
        card = format_token_list(base_pairs, max_tokens=3)

        return PlannerResult(message=card, tokens=base_pairs[:5])

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
        intro = "*🔥 Trending Tokens*\n\n"
        card = format_token_list(base_tokens, max_tokens=5)

        return PlannerResult(message=intro + card, tokens=base_tokens[:5])

    async def _handle_router_activity(
        self, matched: MatchedIntent, context: Dict[str, Any]
    ) -> PlannerResult:
        """Handle DEX router activity request."""
        router_name = matched.router_name or "uniswap"
        logger.info("router_activity", router=router_name)

        # Get router address
        router_address = self._get_router_address(router_name)
        if not router_address:
            return PlannerResult(
                message=escape_markdown(
                    f"Router '{router_name}' not configured. "
                    "Available: uniswap_v2, uniswap_v3, aerodrome"
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
                message=escape_markdown(
                    f"No recent activity found on {router_name.title()}."
                ),
                tokens=[],
            )

        # Get full transaction details for recent swaps
        swap_txs = [
            tx
            for tx in transactions
            if "swap" in (tx.get("method") or tx.get("function") or "").lower()
        ][:5]

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

        # Look up tokens on Dexscreener
        token_data = []
        for addr in token_addresses[:5]:  # Limit to 5 tokens
            try:
                dex_result = await self.mcp_manager.dexscreener.call_tool(
                    "getPairsByToken",
                    {"chainId": self.chain_id, "tokenAddress": addr},
                )
                pairs = self._extract_pairs(dex_result)
                if pairs:
                    # Add the best pair for this token
                    token_data.append(pairs[0])
            except Exception as exc:
                logger.warning("token_lookup_failed", address=addr, error=str(exc))

        # Format with token cards
        if token_data:
            card = format_swap_activity(token_data, transactions, router_name.title())
        else:
            # Fallback to simple summary if no token data
            card = format_activity_summary(transactions, router_name.title())

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
        result = await self.mcp_manager.honeypot.call_tool(
            "check_token", {"address": address, "chainId": 8453}
        )

        card = format_safety_result(result)

        # Also get token info for context
        dex_result = await self.mcp_manager.dexscreener.call_tool(
            "getPairsByToken", {"chainId": self.chain_id, "tokenAddress": address}
        )
        pairs = self._extract_pairs(dex_result)
        if pairs:
            token_card = format_token_card(pairs[0])
            card = token_card + "\n\n" + card

        return PlannerResult(message=card, tokens=pairs[:1])

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

    async def _generate_insight(
        self, token_data: Dict[str, Any], context_type: str
    ) -> Optional[str]:
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

    def _get_router_address(self, router_name: str) -> Optional[str]:
        """Get router address from config."""
        # Normalize router name
        name_map = {
            "uniswap": "uniswap_v2",
            "uniswap_v2": "uniswap_v2",
            "uniswap_v3": "uniswap_v3",
            "aerodrome": "aerodrome",
            "baseswap": "baseswap",
            "sushi": "sushiswap",
            "sushiswap": "sushiswap",
        }
        normalized = name_map.get(router_name.lower())
        if not normalized:
            return None

        router_networks = self.router_map.get(normalized, {})
        return router_networks.get("base-mainnet") or router_networks.get("base")

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
