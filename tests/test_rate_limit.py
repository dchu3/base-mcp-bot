from app.utils.rate_limit import RateLimiter


def test_rate_limiter_allows_within_limit():
    limiter = RateLimiter(limit_per_minute=2)
    assert limiter.allow(1)
    assert limiter.allow(1)
    assert not limiter.allow(1)


def test_rate_limiter_expires(monkeypatch):
    current = 0.0

    def fake_time():
        return current

    monkeypatch.setattr("app.utils.rate_limit.time.time", fake_time)

    limiter = RateLimiter(limit_per_minute=1)
    assert limiter.allow(2)

    current = 61.0
    assert limiter.allow(2)
