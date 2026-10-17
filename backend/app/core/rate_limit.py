"""Fixed-window rate limiting (Redis in production, in-memory for tests)."""

import math
import time
from collections import defaultdict
from functools import lru_cache
from typing import Protocol

import redis
import structlog

from app.core.config import get_settings
from app.core.errors import RateLimitedError

logger = structlog.get_logger()


class RateLimiter(Protocol):
    """Counts hits per key and raises RateLimitedError past the limit."""

    def hit(self, key: str, limit: int, window_seconds: int) -> None:
        """Register one hit for `key`."""
        ...


def _too_many(retry_after: int) -> RateLimitedError:
    return RateLimitedError(
        "Too many requests, try again later",
        details={"retry_after_seconds": retry_after},
        headers={"Retry-After": str(retry_after)},
    )


class NullRateLimiter:
    """Never limits; used when RATE_LIMIT_ENABLED=false."""

    def hit(self, key: str, limit: int, window_seconds: int) -> None:
        """Do nothing."""


class MemoryRateLimiter:
    """Process-local fixed window limiter."""

    def __init__(self) -> None:
        """Create an empty limiter."""
        self._windows: dict[str, tuple[float, int]] = defaultdict(lambda: (0.0, 0))

    def hit(self, key: str, limit: int, window_seconds: int) -> None:
        """Count a hit, raising when the window's limit is exceeded."""
        now = time.monotonic()
        start, count = self._windows[key]
        if now - start >= window_seconds:
            start, count = now, 0
        count += 1
        self._windows[key] = (start, count)
        if count > limit:
            raise _too_many(max(1, math.ceil(window_seconds - (now - start))))


class RedisRateLimiter:
    """Redis fixed window limiter shared by all API processes."""

    def __init__(self, url: str) -> None:
        """Connect lazily with short timeouts."""
        self._redis = redis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)

    def hit(self, key: str, limit: int, window_seconds: int) -> None:
        """Count a hit; fail open (and log) if Redis is unreachable."""
        name = f"ratelimit:{key}"
        try:
            pipe = self._redis.pipeline()
            pipe.incr(name)
            pipe.expire(name, window_seconds, nx=True)
            pipe.ttl(name)
            count, _, ttl = pipe.execute()
        except redis.RedisError:
            logger.warning("rate_limiter_unavailable")
            return
        if int(count) > limit:
            raise _too_many(max(1, int(ttl)))


@lru_cache
def get_rate_limiter() -> RateLimiter:
    """Return the limiter configured for this process."""
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return NullRateLimiter()
    return RedisRateLimiter(settings.redis_url)
