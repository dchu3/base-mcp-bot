import pytest
from app.planner import GeminiPlanner

def _make_planner() -> GeminiPlanner:
    # Bypass __init__ to avoid external dependencies; only formatting helpers are used.
    planner = object.__new__(GeminiPlanner)
    return planner  # type: ignore[return-value]

def test_normalize_honeypot_result_parses_safe_verdict():
    planner = _make_planner()
    payload = {
        "summary": {
            "verdict": "SAFE_TO_TRADE",
            "reason": "No risks found",
            "risks": []
        }
    }
    normalized = planner._normalize_honeypot_result(payload)
    assert normalized is not None
    assert normalized["verdict"] == "SAFE_TO_TRADE"
    assert normalized["reason"] == "No risks found"
    assert "risk" not in normalized

def test_normalize_honeypot_result_parses_do_not_trade_verdict():
    planner = _make_planner()
    payload = {
        "summary": {
            "verdict": "DO_NOT_TRADE",
            "reason": "High risk of honeypot",
            "risks": ["Has a history of being a honeypot"]
        }
    }
    normalized = planner._normalize_honeypot_result(payload)
    assert normalized is not None
    assert normalized["verdict"] == "DO_NOT_TRADE"
    assert normalized["reason"] == "High risk of honeypot"
    assert normalized["risk"] == "Has a history of being a honeypot"

def test_fallback_verdict_from_error_returns_error():
    planner = _make_planner()
    error = Exception("honeypot check failed")
    fallback = planner._fallback_verdict_from_error(error)
    assert fallback is not None
    assert fallback["verdict"] == "ERROR"
    assert fallback["reason"] == "Honeypot check failed"

def test_apply_verdict_to_token_applies_verdict():
    planner = _make_planner()
    token = {"address": "0x123"}
    verdicts = {
        "0x123": {
            "verdict": "DO_NOT_TRADE",
            "reason": "High risk of honeypot",
            "risk": "Has a history of being a honeypot"
        }
    }
    planner._apply_verdict_to_token(token, verdicts)
    assert token["riskVerdict"] == "DO_NOT_TRADE"
    assert token["riskReason"] == "High risk of honeypot"
    assert token["risk"] == "Has a history of being a honeypot"
