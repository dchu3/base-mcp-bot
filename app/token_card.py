"""Token card formatter for consistent Telegram display."""

from typing import Any, Dict, List, Optional

from app.utils.formatting import escape_markdown, escape_markdown_url


def format_safety_badge(honeypot_data: Optional[Dict[str, Any]]) -> Optional[str]:
    """Format a compact safety badge from honeypot check result.

    Args:
        honeypot_data: Result from honeypot.check_token, or None.

    Returns:
        Single-line safety badge string, or None if no data.
    """
    if not honeypot_data:
        return None

    summary = honeypot_data.get("summary", {})
    verdict = summary.get("verdict") or honeypot_data.get("verdict") or "UNKNOWN"
    risk = summary.get("risk") or honeypot_data.get("risk")

    # Get tax info if available
    simulation = honeypot_data.get("simulationResult", {})
    buy_tax = simulation.get("buyTax") or honeypot_data.get("buyTax")
    sell_tax = simulation.get("sellTax") or honeypot_data.get("sellTax")

    # Determine badge
    if verdict in ("SAFE_TO_TRADE", "SAFE", "OK"):
        badge = "✅ Safe"
    elif verdict in ("CAUTION", "WARNING"):
        badge = "⚠️ Caution"
        # Add reason if we have high tax
        if buy_tax and float(buy_tax) > 5:
            badge += f" \\- Buy tax {buy_tax}%"
        elif sell_tax and float(sell_tax) > 5:
            badge += f" \\- Sell tax {sell_tax}%"
        elif risk:
            badge += f" \\- {escape_markdown(str(risk))}"
    elif verdict in ("HONEYPOT", "DANGER", "DO_NOT_TRADE"):
        badge = "🚨 Risk \\- Do not trade"
    else:
        badge = "❓ Unknown safety"

    return badge


def format_token_card(
    token_data: Dict[str, Any], honeypot_data: Optional[Dict[str, Any]] = None
) -> str:
    """Format Dexscreener token data as a Telegram card.

    Args:
        token_data: Raw token/pair data from Dexscreener.
        honeypot_data: Optional honeypot check result.

    Returns:
        Formatted Telegram MarkdownV2 message.
    """
    # Extract base token info
    base_token = token_data.get("baseToken", {})
    symbol = base_token.get("symbol") or token_data.get("symbol") or "TOKEN"
    name = base_token.get("name") or token_data.get("name") or ""
    address = base_token.get("address") or token_data.get("tokenAddress") or ""

    # Price info
    price_usd = token_data.get("priceUsd") or token_data.get("price") or "?"
    price_change_24h = token_data.get("priceChange", {}).get("h24")
    if price_change_24h is None:
        price_change_24h = token_data.get("change24h")

    # Liquidity and volume
    liquidity = token_data.get("liquidity", {})
    if isinstance(liquidity, dict):
        liquidity_usd = liquidity.get("usd")
    else:
        liquidity_usd = liquidity

    volume_24h = token_data.get("volume", {}).get("h24")
    if volume_24h is None:
        volume_24h = token_data.get("volume24h")

    fdv = token_data.get("fdv")
    market_cap = token_data.get("marketCap")

    # Links
    dex_url = token_data.get("url") or ""
    chain_id = token_data.get("chainId") or "base"

    # Build card
    lines = []

    # Title line
    title = f"*{escape_markdown(symbol)}*"
    if name and name != symbol:
        title += f" \\({escape_markdown(name)}\\)"
    lines.append(title)

    # Price line with change
    price_line = f"💰 Price: ${_format_number(price_usd)}"
    if price_change_24h is not None:
        change_str = _format_change(price_change_24h)
        price_line += f" {change_str}"
    lines.append(escape_markdown(price_line))

    # Metrics line
    metrics = []
    if liquidity_usd:
        metrics.append(f"💧 Liq: ${_format_number(liquidity_usd)}")
    if volume_24h:
        metrics.append(f"📊 Vol: ${_format_number(volume_24h)}")
    if metrics:
        lines.append(escape_markdown(" · ".join(metrics)))

    # FDV/Market cap
    if fdv:
        lines.append(escape_markdown(f"📈 FDV: ${_format_number(fdv)}"))
    elif market_cap:
        lines.append(escape_markdown(f"📈 MCap: ${_format_number(market_cap)}"))

    # Address (truncated)
    if address:
        short_addr = f"{address[:6]}...{address[-4:]}"
        lines.append(f"📍 `{short_addr}`")

    # Safety badge (if honeypot data provided)
    safety_badge = format_safety_badge(honeypot_data)
    if safety_badge:
        lines.append(safety_badge)

    # Dexscreener link
    if dex_url:
        safe_url = escape_markdown_url(dex_url)
        lines.append(f"[View on Dexscreener]({safe_url})")
    elif address:
        # Construct URL
        dex_link = f"https://dexscreener.com/{chain_id}/{address}"
        safe_url = escape_markdown_url(dex_link)
        lines.append(f"[View on Dexscreener]({safe_url})")

    return "\n".join(lines)


def format_token_list(tokens: List[Dict[str, Any]], max_tokens: int = 5) -> str:
    """Format a list of tokens as compact cards.

    Args:
        tokens: List of token/pair data from Dexscreener.
        max_tokens: Maximum number of tokens to display.

    Returns:
        Formatted Telegram MarkdownV2 message.
    """
    if not tokens:
        return escape_markdown("No tokens found.")

    cards = []
    for token in tokens[:max_tokens]:
        cards.append(format_token_card(token))

    result = "\n\n".join(cards)

    if len(tokens) > max_tokens:
        remaining = len(tokens) - max_tokens
        result += f"\n\n_{escape_markdown(f'... and {remaining} more')}_"

    return result


def format_activity_summary(
    transactions: List[Dict[str, Any]], router_name: Optional[str] = None
) -> str:
    """Format router activity as a summary.

    Args:
        transactions: List of transaction data from Blockscout.
        router_name: Name of the router (e.g., "Uniswap V2").

    Returns:
        Formatted Telegram MarkdownV2 message.
    """
    if not transactions:
        return escape_markdown("No recent activity found.")

    # Count transaction types
    swaps = 0
    adds = 0
    removes = 0

    for tx in transactions:
        method = (tx.get("method") or tx.get("function") or "").lower()
        if "swap" in method:
            swaps += 1
        elif "add" in method:
            adds += 1
        elif "remove" in method:
            removes += 1

    total = len(transactions)

    lines = []

    # Title
    if router_name:
        lines.append(f"*{escape_markdown(router_name)} Activity*")
    else:
        lines.append("*DEX Activity*")

    # Summary stats
    lines.append(escape_markdown(f"📊 {total} transactions in the last hour"))

    breakdown = []
    if swaps:
        breakdown.append(f"🔄 {swaps} swaps")
    if adds:
        breakdown.append(f"➕ {adds} adds")
    if removes:
        breakdown.append(f"➖ {removes} removes")

    if breakdown:
        lines.append(escape_markdown(" · ".join(breakdown)))

    # Show a few recent transactions
    if transactions:
        lines.append("")
        lines.append("*Recent:*")
        for tx in transactions[:3]:
            method = tx.get("method") or tx.get("function") or "unknown"
            tx_hash = tx.get("hash") or tx.get("transaction_hash") or ""
            if tx_hash:
                short_hash = f"{tx_hash[:10]}..."
                lines.append(escape_markdown(f"• {method} ({short_hash})"))
            else:
                lines.append(escape_markdown(f"• {method}"))

    return "\n".join(lines)


def format_swap_activity(
    tokens: List[Dict[str, Any]],
    transactions: List[Dict[str, Any]],
    router_name: Optional[str] = None,
    honeypot_results: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """Format swap activity with token cards.

    Shows the tokens that were swapped with their Dexscreener data,
    followed by a transaction summary.

    Args:
        tokens: List of token/pair data from Dexscreener lookups.
        transactions: List of transaction data from Blockscout.
        router_name: Name of the router (e.g., "Uniswap V2").
        honeypot_results: Optional dict mapping token address to honeypot check result.

    Returns:
        Formatted Telegram MarkdownV2 message.
    """
    lines = []
    honeypot_results = honeypot_results or {}

    # Title
    title = f"🔄 *Recent {escape_markdown(router_name or 'DEX')} Swaps*"
    lines.append(title)
    lines.append("")

    # Show token cards
    if tokens:
        for token in tokens[:5]:
            # Get honeypot data for this token
            base_token = token.get("baseToken", {})
            address = (
                base_token.get("address") or token.get("tokenAddress") or ""
            ).lower()
            honeypot_data = honeypot_results.get(address)

            card = format_token_card(token, honeypot_data)
            lines.append(card)
            lines.append("")
    else:
        lines.append(escape_markdown("No token data available for recent swaps."))
        lines.append("")

    # Transaction summary
    swap_count = sum(
        1
        for tx in transactions
        if "swap" in (tx.get("method") or tx.get("function") or "").lower()
    )

    if swap_count > 0:
        lines.append(escape_markdown(f"📊 {swap_count} swaps in the last hour"))

    # Add disclaimer
    lines.append("")
    lines.append(escape_markdown("⚠️ DYOR - Not financial advice"))

    return "\n".join(lines)


def format_safety_result(honeypot_data: Dict[str, Any]) -> str:
    """Format honeypot check result.

    Args:
        honeypot_data: Result from honeypot.check_token.

    Returns:
        Formatted Telegram MarkdownV2 message.
    """
    summary = honeypot_data.get("summary", {})
    verdict = summary.get("verdict") or honeypot_data.get("verdict") or "UNKNOWN"
    risk = summary.get("risk") or honeypot_data.get("risk")

    # Verdict emoji
    if verdict in ("SAFE_TO_TRADE", "SAFE", "OK"):
        emoji = "✅"
        verdict_text = "SAFE TO TRADE"
    elif verdict in ("CAUTION", "WARNING"):
        emoji = "⚠️"
        verdict_text = "CAUTION"
    else:
        emoji = "🚨"
        verdict_text = "DO NOT TRADE"

    lines = ["*Safety Check*", f"{emoji} *{escape_markdown(verdict_text)}*"]

    if risk:
        # Handle risk as dict (from honeypot MCP) or scalar
        if isinstance(risk, dict):
            risk_level = risk.get("riskLevel")
            if risk_level is not None:
                lines.append(escape_markdown(f"Risk Level: {risk_level}"))
        else:
            lines.append(escape_markdown(f"Risk Level: {risk}"))

    # Add any specific warnings
    flags = honeypot_data.get("flags", {})
    if flags:
        warnings = []
        if isinstance(flags, dict):
            # Handle dict format from honeypot MCP
            if flags.get("isHoneypot"):
                warnings.append("Honeypot detected")
            if flags.get("openSource") is False:
                warnings.append("Contract source not verified")
            if flags.get("isProxy"):
                warnings.append("Proxy contract")
            if flags.get("simulationSuccess") is False:
                warnings.append("Simulation failed")
        elif isinstance(flags, list):
            # Handle legacy list format
            warnings = flags[:5]

        if warnings:
            lines.append("")
            lines.append("*Warnings:*")
            for warning in warnings[:5]:
                lines.append(escape_markdown(f"• {warning}"))

    return "\n".join(lines)


def _format_number(value: Any) -> str:
    """Format a number with K/M/B suffixes.

    Note: This function expects non-negative values (prices, volumes, etc.).
    Negative values are not expected in token metrics.
    """
    if value is None or value == "?":
        return "?"

    try:
        num = float(value)
    except (ValueError, TypeError):
        return str(value)

    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.2f}B"
    elif num >= 1_000_000:
        return f"{num / 1_000_000:.2f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.2f}K"
    elif num >= 1:
        return f"{num:.2f}"
    elif num >= 0.0001:
        return f"{num:.6f}"
    else:
        return f"{num:.10f}"


def _format_change(change: Any) -> str:
    """Format a percentage change with emoji."""
    try:
        pct = float(change)
    except (ValueError, TypeError):
        return ""

    if pct > 0:
        return f"(📈 +{pct:.1f}%)"
    elif pct < 0:
        return f"(📉 {pct:.1f}%)"
    else:
        return "(→ 0%)"


def format_pool_list(
    pools: List[Dict[str, Any]],
    network: str = "base",
    max_pools: int = 5,
) -> str:
    """Format DexPaprika pool data as a Telegram card list.

    Args:
        pools: List of pool data from DexPaprika getNetworkPools.
        network: Network name for display.
        max_pools: Maximum number of pools to display.

    Returns:
        Formatted Telegram MarkdownV2 message.
    """
    if not pools:
        return escape_markdown(f"No pools found on {network}.")

    lines = []
    lines.append(f"*🏊 Top Pools on {escape_markdown(network.title())}*")
    lines.append("")

    for i, pool in enumerate(pools[:max_pools], 1):
        # Extract pool info
        dex_name = pool.get("dex_name") or pool.get("dex_id") or "Unknown DEX"
        volume_usd = pool.get("volume_usd") or 0
        price_usd = pool.get("price_usd") or 0
        txns = pool.get("transactions") or 0
        price_change_24h = pool.get("last_price_change_usd_24h")

        # Get token pair
        tokens = pool.get("tokens", [])
        if len(tokens) >= 2:
            pair = f"{tokens[0].get('symbol', '?')}/{tokens[1].get('symbol', '?')}"
        elif len(tokens) == 1:
            pair = tokens[0].get("symbol", "?")
        else:
            pair = "Unknown"

        # Build pool entry
        lines.append(f"*{i}\\. {escape_markdown(pair)}* \\({escape_markdown(dex_name)}\\)")

        # Volume and price
        vol_str = f"📊 Vol: ${_format_number(volume_usd)}"
        price_str = f"💰 ${_format_number(price_usd)}"
        if price_change_24h is not None:
            change_str = _format_change(price_change_24h)
            price_str += f" {change_str}"
        lines.append(escape_markdown(f"{vol_str} · {price_str}"))

        # Transactions
        lines.append(escape_markdown(f"🔄 {_format_number(txns)} txns (24h)"))
        lines.append("")

    if len(pools) > max_pools:
        remaining = len(pools) - max_pools
        lines.append(f"_{escape_markdown(f'... and {remaining} more pools')}_")

    return "\n".join(lines)
