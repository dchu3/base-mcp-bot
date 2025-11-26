"""Tests for CLI output formatting."""

import io
import json

from app.cli_output import (
    CLIOutput,
    OutputFormat,
    format_token_plain,
    format_tokens_plain,
    _format_number,
)
from app.planner_types import PlannerResult


class TestCLIOutput:
    """Tests for CLIOutput class."""

    def test_text_output_basic(self):
        """Test basic text output."""
        stream = io.StringIO()
        output = CLIOutput(format=OutputFormat.TEXT, stream=stream)
        result = PlannerResult(message="Hello world", tokens=[])

        output.result(result)

        assert "Hello world" in stream.getvalue()

    def test_text_output_strips_markdown(self):
        """Test that markdown escapes are stripped."""
        stream = io.StringIO()
        output = CLIOutput(format=OutputFormat.TEXT, stream=stream)
        # Use raw string - this is how Telegram markdown actually looks
        result = PlannerResult(message=r"Price: \$100\.00", tokens=[])

        output.result(result)

        assert "Price: $100.00" in stream.getvalue()

    def test_text_output_verbose_shows_tokens(self):
        """Test verbose mode shows token context."""
        stream = io.StringIO()
        output = CLIOutput(format=OutputFormat.TEXT, verbose=True, stream=stream)
        result = PlannerResult(
            message="Found tokens",
            tokens=[{"symbol": "PEPE", "address": "0x123"}],
        )

        output.result(result)

        content = stream.getvalue()
        assert "Token Context" in content
        assert "PEPE" in content

    def test_json_output_format(self):
        """Test JSON output format."""
        stream = io.StringIO()
        output = CLIOutput(format=OutputFormat.JSON, stream=stream)
        result = PlannerResult(
            message="Test message",
            tokens=[{"symbol": "TEST", "address": "0xabc"}],
        )

        output.result(result)

        data = json.loads(stream.getvalue())
        assert data["message"] == "Test message"
        assert len(data["tokens"]) == 1
        assert data["tokens"][0]["symbol"] == "TEST"

    def test_json_output_strips_markdown(self):
        """Test JSON output also strips markdown."""
        stream = io.StringIO()
        output = CLIOutput(format=OutputFormat.JSON, stream=stream)
        result = PlannerResult(message=r"Price: \$50", tokens=[])

        output.result(result)

        data = json.loads(stream.getvalue())
        assert data["message"] == "Price: $50"

    def test_status_suppressed_in_json_mode(self):
        """Test that status messages are suppressed in JSON mode."""
        stream = io.StringIO()
        output = CLIOutput(format=OutputFormat.JSON, stream=stream)

        output.status("Loading...")

        assert stream.getvalue() == ""

    def test_info_suppressed_in_json_mode(self):
        """Test that info messages are suppressed in JSON mode."""
        stream = io.StringIO()
        output = CLIOutput(format=OutputFormat.JSON, stream=stream)

        output.info("Some info")

        assert stream.getvalue() == ""

    def test_status_shown_in_text_mode(self):
        """Test status messages appear in text mode."""
        stream = io.StringIO()
        output = CLIOutput(format=OutputFormat.TEXT, stream=stream)

        output.status("Loading...")

        assert "Loading" in stream.getvalue()

    def test_info_shown_in_text_mode(self):
        """Test info messages appear in text mode."""
        stream = io.StringIO()
        output = CLIOutput(format=OutputFormat.TEXT, stream=stream)

        output.info("Information")

        assert "Information" in stream.getvalue()

    def test_rich_fallback_when_not_installed(self, monkeypatch):
        """Test fallback to text when rich is not installed."""
        # Simulate rich not being installed
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "rich.console" or name.startswith("rich"):
                raise ImportError("No module named 'rich'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        stream = io.StringIO()
        output = CLIOutput(format=OutputFormat.RICH, stream=stream)

        # Should have fallen back to TEXT
        assert output.format == OutputFormat.TEXT

    def test_debug_only_in_verbose_mode(self, capsys):
        """Test debug messages only appear in verbose mode."""
        # Non-verbose - debug should not appear
        stream = io.StringIO()
        output = CLIOutput(format=OutputFormat.TEXT, verbose=False, stream=stream)
        output.debug("Debug info")
        captured = capsys.readouterr()
        assert "Debug" not in captured.err

        # Verbose - debug should appear on stderr
        output = CLIOutput(format=OutputFormat.TEXT, verbose=True, stream=stream)
        output.debug("Debug info")
        captured = capsys.readouterr()
        assert "Debug" in captured.err


class TestStripMarkdown:
    """Tests for markdown stripping."""

    def test_strips_backslash_escapes(self):
        """Test removal of backslash escapes."""
        text = r"Price: \$100 \- discount"
        result = CLIOutput._strip_markdown(text)
        assert result == "Price: $100 - discount"

    def test_converts_markdown_links(self):
        """Test conversion of markdown links to plain text."""
        text = "Check [Dexscreener](https://dex.com/token)"
        result = CLIOutput._strip_markdown(text)
        assert result == "Check Dexscreener (https://dex.com/token)"

    def test_handles_empty_string(self):
        """Test handling of empty string."""
        assert CLIOutput._strip_markdown("") == ""

    def test_handles_none_like_empty(self):
        """Test handling of None-like values."""
        assert CLIOutput._strip_markdown("") == ""

    def test_multiple_escapes(self):
        """Test multiple escape sequences."""
        text = r"Token \*PEPE\* at \$0\.001"
        result = CLIOutput._strip_markdown(text)
        assert result == "Token *PEPE* at $0.001"


class TestFormatTokenPlain:
    """Tests for token formatting."""

    def test_basic_token(self):
        """Test formatting a basic token."""
        token = {"symbol": "PEPE", "price": "0.001"}
        result = format_token_plain(token)

        assert "PEPE" in result
        assert "$0.001" in result

    def test_token_with_name(self):
        """Test token with name different from symbol."""
        token = {"symbol": "PEPE", "name": "Pepe Token", "price": "0.001"}
        result = format_token_plain(token)

        assert "PEPE" in result
        assert "Pepe Token" in result

    def test_token_with_price_change(self):
        """Test token with 24h price change."""
        token = {"symbol": "TEST", "price": "1.00", "change24h": "+15.5"}
        result = format_token_plain(token)

        assert "24h: +15.5%" in result

    def test_token_with_volume_and_liquidity(self):
        """Test token with volume and liquidity."""
        token = {
            "symbol": "TEST",
            "volume24h": "1000000",
            "liquidity": "500000",
        }
        result = format_token_plain(token)

        assert "Volume" in result
        assert "Liquidity" in result

    def test_token_with_safety_verdict(self):
        """Test token with safety verdict."""
        token = {
            "symbol": "TEST",
            "riskVerdict": "SAFE_TO_TRADE",
            "riskReason": "No issues found",
        }
        result = format_token_plain(token)

        assert "✅" in result
        assert "SAFE_TO_TRADE" in result
        assert "No issues found" in result

    def test_token_with_caution_verdict(self):
        """Test token with caution verdict."""
        token = {"symbol": "TEST", "riskVerdict": "CAUTION"}
        result = format_token_plain(token)

        assert "⚠️" in result

    def test_token_with_url(self):
        """Test token with URL."""
        token = {"symbol": "TEST", "url": "https://dexscreener.com/base/0x123"}
        result = format_token_plain(token)

        assert "🔗" in result
        assert "https://dexscreener.com" in result


class TestFormatTokensPlain:
    """Tests for token list formatting."""

    def test_empty_list(self):
        """Test formatting empty token list."""
        result = format_tokens_plain([])
        assert result == "No tokens found."

    def test_single_token(self):
        """Test formatting single token."""
        tokens = [{"symbol": "PEPE", "price": "0.001"}]
        result = format_tokens_plain(tokens)

        assert "1." in result
        assert "PEPE" in result

    def test_multiple_tokens(self):
        """Test formatting multiple tokens."""
        tokens = [
            {"symbol": "PEPE", "price": "0.001"},
            {"symbol": "DOGE", "price": "0.08"},
        ]
        result = format_tokens_plain(tokens)

        assert "1." in result
        assert "2." in result
        assert "PEPE" in result
        assert "DOGE" in result

    def test_max_tokens_limit(self):
        """Test max_tokens limit."""
        tokens = [{"symbol": f"TOKEN{i}"} for i in range(10)]
        result = format_tokens_plain(tokens, max_tokens=3)

        assert "TOKEN0" in result
        assert "TOKEN2" in result
        assert "TOKEN3" not in result
        assert "7 more tokens" in result


class TestFormatNumber:
    """Tests for number formatting."""

    def test_billions(self):
        """Test formatting billions."""
        assert _format_number(1_500_000_000) == "1.50B"

    def test_millions(self):
        """Test formatting millions."""
        assert _format_number(2_500_000) == "2.50M"

    def test_thousands(self):
        """Test formatting thousands."""
        assert _format_number(1_500) == "1.50K"

    def test_small_numbers(self):
        """Test formatting small numbers."""
        assert _format_number(123.45) == "123.45"

    def test_string_input(self):
        """Test formatting string input."""
        assert _format_number("1000000") == "1.00M"

    def test_invalid_input(self):
        """Test handling invalid input."""
        assert _format_number("invalid") == "invalid"
