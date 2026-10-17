import pytest
import redis
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.errors import RateLimitedError
from app.core.rate_limit import (
    MemoryRateLimiter,
    NullRateLimiter,
    RedisRateLimiter,
    get_rate_limiter,
)
from app.main import app
from tests.helpers import API, PASSWORD, login, register


def test_sixth_login_attempt_in_a_minute_is_429(client: TestClient) -> None:
    register(client, "rl@example.com")
    limiter = MemoryRateLimiter()
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    for _ in range(5):
        assert login(client, "rl@example.com", "wrong-password-1").status_code == 401
    blocked = login(client, "rl@example.com", PASSWORD)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1
    # A different email from the same IP has its own budget.
    assert login(client, "other@example.com").status_code == 401


def test_register_is_rate_limited_per_ip(client: TestClient) -> None:
    limiter = MemoryRateLimiter()
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    for i in range(5):
        register(client, f"r{i}@example.com")
    response = client.post(
        f"{API}/auth/register",
        json={
            "company_name": "X",
            "email": "r9@example.com",
            "password": PASSWORD,
            "full_name": "R N",
        },
    )
    assert response.status_code == 429


def test_memory_limiter_window_resets(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("app.core.rate_limit.time.monotonic", lambda: now[0])
    limiter = MemoryRateLimiter()
    for _ in range(3):
        limiter.hit("k", 3, 60)
    with pytest.raises(RateLimitedError):
        limiter.hit("k", 3, 60)
    now[0] += 61
    limiter.hit("k", 3, 60)


def test_null_limiter_never_limits() -> None:
    limiter = NullRateLimiter()
    for _ in range(100):
        limiter.hit("k", 1, 60)


def test_factory_honours_setting() -> None:
    get_rate_limiter.cache_clear()
    assert isinstance(get_rate_limiter(), NullRateLimiter)
    get_rate_limiter.cache_clear()


def test_redis_limiter_counts_and_fails_open() -> None:
    limiter = RedisRateLimiter(get_settings().redis_url)
    try:
        limiter._redis.ping()
    except redis.RedisError:
        pytest.skip("redis not available")
    key = "test-key-redis-limiter"
    limiter._redis.delete(f"ratelimit:{key}")
    for _ in range(2):
        limiter.hit(key, 2, 30)
    with pytest.raises(RateLimitedError) as excinfo:
        limiter.hit(key, 2, 30)
    assert excinfo.value.headers["Retry-After"]
    limiter._redis.delete(f"ratelimit:{key}")
    # An unreachable Redis must not take authentication down.
    RedisRateLimiter("redis://127.0.0.1:1/0").hit("k", 1, 60)
