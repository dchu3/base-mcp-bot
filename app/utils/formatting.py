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
        summary += f" \\({escape_markdown(amount)}\\)"
    if explorer:
        summary += f" — [{escape_markdown(tx_hash[:8] + '…')}]({escape_markdown(explorer)})"
    else:
        summary += f" — `{escape_markdown(tx_hash)}`"
    return summary


def format_token_summary(entry: Mapping[str, str]) -> str:
    """Render a Dexscreener token snapshot as a compact card."""
    ticker = entry.get("symbol") or "TOKEN"
    name = entry.get("name")
    price = entry.get("price") or "?"
    volume = entry.get("volume24h") or "?"
    liquidity = entry.get("liquidity") or "?"
    change = entry.get("change24h") or "?"
    fdv = entry.get("fdv")
    link = entry.get("url")
    change_pct = _parse_percentage(change)
    signal_tag = _classify_change(change_pct)

    title = f"*{escape_markdown(ticker)}*"
    if name and name != ticker:
        title += f" \\({escape_markdown(name)}\\)"
    if signal_tag:
        title += f" · {escape_markdown(signal_tag)}"

    price_line = f"Price: {escape_markdown(price)}"
    if change and change != "?":
        price_line += f" \\(24h {escape_markdown(change)}\\)"

    metrics: List[str] = []
    if volume and volume != "?":
        metrics.append(f"Vol {escape_markdown(volume)}")
    if liquidity and liquidity != "?":
        metrics.append(f"Liq {escape_markdown(liquidity)}")
    if fdv and fdv != "?":
        metrics.append(f"FDV {escape_markdown(fdv)}")
    metrics_line = " · ".join(metrics)

    lines: List[str] = [title, price_line]
    if metrics_line:
        lines.append(metrics_line)
    if link:
        safe_link = escape_markdown_url(link)
        lines.append(f"[View on Dexscreener]({safe_link})")

    return "\n".join(line for line in lines if line)


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
    trimmed = message.strip()
    if not trimmed:
        return escape_markdown(NOT_FINANCIAL_ADVICE)
    footer = f"\n\n{escape_markdown(NOT_FINANCIAL_ADVICE)}"
    return trimmed + footer
