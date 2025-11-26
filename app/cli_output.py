"""CLI output formatting for terminal display.

Provides formatters for plain text, JSON, and rich terminal output.
Unlike Telegram formatters, these have no message length restrictions.
"""

from __future__ import annotations

import json
import sys
from enum import Enum
from typing import Any, Dict, List, Optional

from app.planner_types import PlannerResult


class OutputFormat(Enum):
    """Supported output formats."""

    TEXT = "text"
    JSON = "json"
    RICH = "rich"


class CLIOutput:
    """Unified output handler for CLI."""

    def __init__(
        self,
        format: OutputFormat = OutputFormat.TEXT,
        verbose: bool = False,
        stream: Any = None,
    ) -> None:
        self.format = format
        self.verbose = verbose
        self.stream = stream or sys.stdout
        self._rich_console: Optional[Any] = None

        # Try to initialize rich if requested
        if format == OutputFormat.RICH:
            try:
                from rich.console import Console

                self._rich_console = Console()
            except ImportError:
                # Fall back to text if rich not installed
                self.format = OutputFormat.TEXT
                self.warning(
                    "'rich' library not installed, falling back to text output"
                )

    def result(self, result: PlannerResult) -> None:
        """Output a planner result."""
        if self.format == OutputFormat.JSON:
            self._json_result(result)
        elif self.format == OutputFormat.RICH:
            self._rich_result(result)
        else:
            self._text_result(result)

    def _text_result(self, result: PlannerResult) -> None:
        """Plain text output."""
        # Strip Markdown escapes for plain text
        message = self._strip_markdown(result.message)
        print(message, file=self.stream)

        if result.tokens and self.verbose:
            print("\n--- Token Context ---", file=self.stream)
            for token in result.tokens:
                print(
                    f"  {token.get('symbol', '?')}: {token.get('address', '?')}",
                    file=self.stream,
                )

    def _json_result(self, result: PlannerResult) -> None:
        """JSON output for scripting."""
        output = {
            "message": self._strip_markdown(result.message),
            "tokens": result.tokens,
        }
        print(json.dumps(output, indent=2), file=self.stream)

    def _rich_result(self, result: PlannerResult) -> None:
        """Rich terminal output with colors and panels."""
        if not self._rich_console:
            self._text_result(result)
            return

        from rich.panel import Panel
        from rich.table import Table

        # Convert message (strip Telegram Markdown escapes but keep structure)
        message = self._strip_markdown(result.message)

        # Display main result in a panel
        self._rich_console.print(Panel(message, title="Result", border_style="green"))

        # Display tokens in a table if present
        if result.tokens:
            table = Table(title="Discovered Tokens")
            table.add_column("Symbol", style="cyan")
            table.add_column("Address", style="dim")
            table.add_column("Chain", style="magenta")

            for token in result.tokens:
                table.add_row(
                    token.get("symbol", "?"),
                    (
                        token.get("address", "?")[:20] + "..."
                        if len(token.get("address", "")) > 20
                        else token.get("address", "?")
                    ),
                    token.get("chainId", "base"),
                )

            self._rich_console.print(table)

    def status(self, message: str) -> None:
        """Output a status message."""
        if self.format == OutputFormat.JSON:
            return  # Suppress status in JSON mode

        if self.format == OutputFormat.RICH and self._rich_console:
            self._rich_console.print(f"[dim]⏳ {message}[/dim]")
        else:
            print(f"⏳ {message}", file=self.stream)

    def info(self, message: str) -> None:
        """Output an info message."""
        if self.format == OutputFormat.JSON:
            return

        if self.format == OutputFormat.RICH and self._rich_console:
            self._rich_console.print(f"[blue]ℹ️  {message}[/blue]")
        else:
            print(f"ℹ️  {message}", file=self.stream)

    def warning(self, message: str) -> None:
        """Output a warning message."""
        if self.format == OutputFormat.JSON:
            print(json.dumps({"warning": message}), file=sys.stderr)
            return

        if self.format == OutputFormat.RICH and self._rich_console:
            self._rich_console.print(f"[yellow]⚠️  {message}[/yellow]")
        else:
            print(f"⚠️  {message}", file=sys.stderr)

    def error(self, message: str) -> None:
        """Output an error message."""
        if self.format == OutputFormat.JSON:
            print(json.dumps({"error": message}), file=sys.stderr)
            return

        if self.format == OutputFormat.RICH and self._rich_console:
            self._rich_console.print(f"[red]❌ {message}[/red]")
        else:
            print(f"❌ {message}", file=sys.stderr)

    def debug(self, message: str, data: Any = None) -> None:
        """Output debug information (only in verbose mode)."""
        if not self.verbose:
            return

        if self.format == OutputFormat.JSON:
            output = {"debug": message}
            if data is not None:
                output["data"] = data
            print(json.dumps(output), file=sys.stderr)
            return

        if self.format == OutputFormat.RICH and self._rich_console:
            self._rich_console.print(f"[dim]🔍 {message}[/dim]")
            if data is not None:
                self._rich_console.print(data)
        else:
            print(f"🔍 {message}", file=sys.stderr)
            if data is not None:
                print(f"   {data}", file=sys.stderr)

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Remove Telegram MarkdownV2 escapes for plain text output."""
        if not text:
            return ""

        # Remove common Markdown escape sequences
        result = text

        # Remove backslash escapes (Telegram MarkdownV2)
        escape_chars = r"\_*[]()~`>#+-=|{}.!"
        for char in escape_chars:
            result = result.replace(f"\\{char}", char)

        # Convert Markdown links to plain text: [text](url) -> text (url)
        import re

        result = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", result)

        return result


def format_token_plain(token: Dict[str, Any]) -> str:
    """Format a single token for plain text output."""
    lines = []

    symbol = token.get("symbol") or token.get("baseSymbol") or "TOKEN"
    name = token.get("name", "")

    header = f"📊 {symbol}"
    if name and name != symbol:
        header += f" ({name})"
    lines.append(header)

    # Price info
    price = token.get("price") or token.get("priceUsd")
    if price:
        price_line = f"   Price: ${price}"
        change = token.get("change24h") or token.get("priceChange24h")
        if change:
            price_line += f"  |  24h: {change}%"
        lines.append(price_line)

    # Volume and liquidity
    volume = token.get("volume24h")
    liquidity = token.get("liquidity")
    if volume or liquidity:
        metrics = []
        if volume:
            metrics.append(f"Volume: ${_format_number(volume)}")
        if liquidity:
            metrics.append(f"Liquidity: ${_format_number(liquidity)}")
        lines.append(f"   {' | '.join(metrics)}")

    # Safety verdict
    verdict = token.get("riskVerdict")
    if verdict:
        verdict_emoji = {
            "SAFE_TO_TRADE": "✅",
            "CAUTION": "⚠️",
            "DO_NOT_TRADE": "🚫",
        }.get(verdict, "❓")
        lines.append(f"   Safety: {verdict_emoji} {verdict}")
        reason = token.get("riskReason")
        if reason:
            lines.append(f"   Reason: {reason}")

    # Link
    url = token.get("url")
    if url:
        lines.append(f"   🔗 {url}")

    return "\n".join(lines)


def format_tokens_plain(tokens: List[Dict[str, Any]], max_tokens: int = 50) -> str:
    """Format a list of tokens for plain text output."""
    if not tokens:
        return "No tokens found."

    lines = []
    for i, token in enumerate(tokens[:max_tokens], 1):
        lines.append(f"\n{i}. {format_token_plain(token)}")

    if len(tokens) > max_tokens:
        lines.append(f"\n... and {len(tokens) - max_tokens} more tokens")

    return "\n".join(lines)


def _format_number(value: Any) -> str:
    """Format a number with K/M/B suffixes."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)

    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.2f}B"
    elif num >= 1_000_000:
        return f"{num / 1_000_000:.2f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.2f}K"
    else:
        return f"{num:.2f}"
