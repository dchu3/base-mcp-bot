"""Helpers for Telegram-safe Markdown formatting."""

from __future__ import annotations

import html
from typing import Iterable, List, Mapping, Sequence

NOT_FINANCIAL_ADVICE = "(DYOR, not financial advice)"


def escape_markdown(text: str) -> str:
    """Escape Telegram MarkdownV2 control characters."""
    special_chars = r"_*[]()~`>#+-=|{}.!\\"
    return "".join(f"\\{char}" if char in special_chars else char for char in text)


def format_transaction(entry: Mapping[str, str]) -> str:
    """Format a single transaction entry for display."""
    fn = entry.get("method", "txn")
    amount = entry.get("amount", "")
    timestamp = entry.get("timestamp", "")
    tx_hash = entry.get("hash", "")
    explorer = entry.get("explorer_url")

    summary = f"• {escape_markdown(timestamp)} — {escape_markdown(fn)}"
    if amount:
        summary += f" ({escape_markdown(amount)})"
    if explorer:
        summary += f" — [{escape_markdown(tx_hash[:8] + '…')}]({escape_markdown(explorer)})"
    else:
        summary += f" — `{escape_markdown(tx_hash)}`"
    return summary


def format_token_summary(entry: Mapping[str, str]) -> str:
    """Return a bullet summarising token stats."""
    ticker = entry.get("symbol", "TOKEN")
    price = entry.get("price", "?")
    volume = entry.get("volume24h", "?")
    liquidity = entry.get("liquidity", "?")
    change = entry.get("change24h", "?")
    link = entry.get("url")

    body = escape_markdown(
        f"{ticker} — price {price}, 24h vol {volume}, liq {liquidity}, 24h {change}"
    )

    if link:
        return f"• [{body}]({escape_markdown(link)})"
    return f"• {body}"


def join_messages(parts: Sequence[str]) -> str:
    """Join sections with blank lines."""
    return "\n\n".join(part for part in parts if part)


def append_not_financial_advice(message: str) -> str:
    """Ensure the output ends with the NFA footer."""
    footer = f"\n\n_{escape_markdown(NOT_FINANCIAL_ADVICE)}_"
    return message + footer
