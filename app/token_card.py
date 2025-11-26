"""Token card formatter for consistent Telegram display."""

from typing import Any, Dict, List, Optional

from app.utils.formatting import escape_markdown, escape_markdown_url


def format_token_card(token_data: Dict[str, Any]) -> str:
    """Format Dexscreener token data as a Telegram card.

    Args:
        token_data: Raw token/pair data from Dexscreener.

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
) -> str:
    """Format swap activity with token cards.

    Shows the tokens that were swapped with their Dexscreener data,
    followed by a transaction summary.

    Args:
        tokens: List of token/pair data from Dexscreener lookups.
        transactions: List of transaction data from Blockscout.
        router_name: Name of the router (e.g., "Uniswap V2").

    Returns:
        Formatted Telegram MarkdownV2 message.
    """
    lines = []

    # Title
    title = f"🔄 *Recent {escape_markdown(router_name or 'DEX')} Swaps*"
    lines.append(title)
    lines.append("")

    # Show token cards
    if tokens:
        for token in tokens[:5]:
            card = format_token_card(token)
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
        lines.append(escape_markdown(f"Risk Level: {risk}"))

    # Add any specific warnings
    flags = honeypot_data.get("flags", [])
    if flags:
        lines.append("")
        lines.append("*Warnings:*")
        for flag in flags[:5]:
            lines.append(escape_markdown(f"• {flag}"))

    return "\n".join(lines)


def _format_number(value: Any) -> str:
    """Format a number with K/M/B suffixes."""
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
