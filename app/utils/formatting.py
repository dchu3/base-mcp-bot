"""Helpers for Telegram-safe Markdown formatting."""

from __future__ import annotations

import html
from typing import Iterable, List, Mapping, Sequence

NOT_FINANCIAL_ADVICE = "DYOR, not financial advice"

# Movement thresholds for highlighting Dexscreener rows (percentage points).
SIGNAL_STRONG_THRESHOLD = 15.0
SIGNAL_WATCH_THRESHOLD = 5.0


def escape_markdown(text: str) -> str:
    """Escape Telegram MarkdownV2 control characters."""
    if text is None:
        text = ""
    if not isinstance(text, str):
        text = str(text)
    special_chars = r"_*[]()~`>#+-=|{}.!\\"
    return "".join(f"\\{char}" if char in special_chars else char for char in text)


def escape_markdown_url(url: str) -> str:
    """Escape Telegram MarkdownV2-sensitive characters inside link URLs."""
    if not url:
        return ""
    return url.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


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
    change_pct = _parse_percentage(change)
    signal_tag = _classify_change(change_pct)

    headline_parts: List[str] = []
    if signal_tag:
        headline_parts.append(f"[{signal_tag}]")
    headline_parts.append(f"{ticker} — price {price}")
    if change and change != "?":
        headline_parts.append(f"24h {change}")
    headline_text = " ".join(headline_parts)

    signal_bits: List[str] = []
    if volume and volume != "?":
        signal_bits.append(f"vol {volume}")
    if liquidity and liquidity != "?":
        signal_bits.append(f"liq {liquidity}")
    if change and change != "?":
        signal_bits.append(f"move {change}")
    signal_line = "Signals: " + ", ".join(signal_bits) if signal_bits else "Signals unavailable"

    escaped_headline = escape_markdown(headline_text)
    escaped_signals = escape_markdown(signal_line)

    if link:
        safe_link = escape_markdown_url(link)
        headline = f"• [{escaped_headline}]({safe_link})"
    else:
        headline = f"• {escaped_headline}"

    return f"{headline}\n  {escaped_signals}"


def _parse_percentage(value: str | None) -> float | None:
    """Convert percent strings like '12.3%' to a float."""
    if not value or value == "?":
        return None
    trimmed = value.strip().replace("%", "")
    if not trimmed:
        return None
    try:
        return float(trimmed)
    except ValueError:
        return None


def _classify_change(change_pct: float | None) -> str | None:
    """Classify percentage change into alert tiers."""
    if change_pct is None:
        return None
    if change_pct >= SIGNAL_STRONG_THRESHOLD:
        return "ALERT"
    if change_pct <= -SIGNAL_STRONG_THRESHOLD:
        return "RISK"
    if abs(change_pct) >= SIGNAL_WATCH_THRESHOLD:
        return "WATCH"
    return None


def join_messages(parts: Sequence[str]) -> str:
    """Join sections with blank lines."""
    return "\n\n".join(part for part in parts if part)


def append_not_financial_advice(message: str) -> str:
    """Ensure the output ends with the NFA footer."""
    footer = f"\n\n{escape_markdown(NOT_FINANCIAL_ADVICE)}"
    return message + footer
