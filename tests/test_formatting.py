from app.utils.formatting import append_not_financial_advice, format_transaction


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
    assert "0.5 ETH" in output
    assert "12:00" in output


def test_append_not_financial_advice():
    output = append_not_financial_advice("Some message")
    assert "not financial advice" in output.lower()
