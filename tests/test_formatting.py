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
    assert "https://dexscreener.com/base/0x123456" in output
    assert "\\/" not in output
