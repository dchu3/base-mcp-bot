from app.utils.formatting import (
    append_not_financial_advice,
    escape_markdown,
    format_token_summary,
    format_transaction,
)


def test_format_transaction_basic():
    tx = {
        "method": "swapExactTokensForTokens",
        "amount": "0.5 ETH",
        "timestamp": "12:00",
        "hash": "0xabc12345",
        "explorer_url": "https://example/tx/0xabc12345",
    }
    output = format_transaction(tx)
    assert "swapExactTokensForTokens" in output
    assert escape_markdown("0.5 ETH") in output
    assert escape_markdown("12:00") in output


def test_format_transaction_handles_numeric_fields():
    tx = {
        "method": "swap",
        "amount": 123,
        "timestamp": 1700000000,
        "hash": "0x123",
    }
    output = format_transaction(tx)
    assert escape_markdown("1700000000") in output
    assert escape_markdown("123") in output


def test_append_not_financial_advice():
    output = append_not_financial_advice("Some message")
    assert "not financial advice" in output.lower()


def test_format_token_summary_preserves_url():
    entry = {
        "symbol": "USDC",
        "price": "1.00",
        "volume24h": "1000000",
        "liquidity": "500000",
        "change24h": "0.2%",
        "url": "https://dexscreener.com/base/0x123456",
    }
    output = format_token_summary(entry)
    assert output.startswith("*USDC*")
    assert "[View on Dexscreener](https://dexscreener.com/base/0x123456)" in output
    assert "Price:" in output
    assert "Vol" in output


def test_format_token_summary_highlights_alerts():
    entry = {
        "symbol": "ABC",
        "price": "$2.50",
        "volume24h": "12345",
        "liquidity": "6789",
        "change24h": "18.2%",
        "url": "https://dexscreener.com/base/0xabc",
    }
    output = format_token_summary(entry)
    assert "*ABC*" in output
    assert "ALERT" in output
    assert escape_markdown("18.2%") in output


def test_format_token_summary_includes_honeypot_verdict():
    entry = {
        "symbol": "XYZ",
        "price": "$0.01",
        "volume24h": "1000",
        "liquidity": "500",
        "change24h": "1.2%",
        "riskVerdict": "CAUTION",
        "riskReason": "High sell tax",
    }
    output = format_token_summary(entry)
    assert "CAUTION" in output
    assert "High sell tax" in output
