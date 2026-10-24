"""At most N open chat streams per user (Redis when rate limiting is on, in-process otherwise).

Slots expire after STREAM_TTL_S so a stream whose cleanup never ran cannot block a user forever.
"""

import threading
import time
import uuid
from functools import lru_cache
from typing import Protocol

import redis
import structlog

from app.core.config import get_settings
from app.core.errors import RateLimitedError

logger = structlog.get_logger()
STREAM_TTL_S = 600


def too_many_streams(limit: int) -> RateLimitedError:
    """Return the 429 raised when a user already has `limit` streams open."""
    return RateLimitedError(
        f"You already have {limit} answers in progress; wait for one to finish.",
        code="too_many_streams",
        details={"max_concurrent_streams": limit},
        headers={"Retry-After": "5"},
    )


class StreamSlots(Protocol):
    """Acquire and release stream slots per user."""

    def acquire(self, user_id: uuid.UUID, limit: int) -> str:
        """Take a slot or raise RateLimitedError; return a token for release."""
        ...

    def release(self, user_id: uuid.UUID, token: str) -> None:
        """Give a slot back."""
        ...


class MemoryStreamSlots:
    """Process-local slots with expiry."""

    def __init__(self) -> None:
        """Start with no open streams."""
        self._lock = threading.Lock()
        self._open: dict[uuid.UUID, dict[str, float]] = {}

    def acquire(self, user_id: uuid.UUID, limit: int) -> str:
        """Take a slot unless `limit` unexpired slots are open."""
        now = time.monotonic()
        with self._lock:
            slots = {
                t: at for t, at in self._open.get(user_id, {}).items() if now - at < STREAM_TTL_S
            }
            if len(slots) >= limit:
                self._open[user_id] = slots
                raise too_many_streams(limit)
            token = uuid.uuid4().hex
            slots[token] = now
            self._open[user_id] = slots
        return token

    def release(self, user_id: uuid.UUID, token: str) -> None:
        """Drop the slot (idempotent)."""
        with self._lock:
            self._open.get(user_id, {}).pop(token, None)


class RedisStreamSlots:
    """Slots in a Redis sorted set per user (score = start time), shared by API processes."""

    def __init__(self, url: str) -> None:
        """Connect lazily with short timeouts; fall back to memory when Redis is down."""
        self._redis = redis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)
        self._fallback = MemoryStreamSlots()

    def acquire(self, user_id: uuid.UUID, limit: int) -> str:
        """Take a slot atomically enough for a per-user limit (add, then check the count)."""
        key, token, now = f"chatstreams:{user_id}", uuid.uuid4().hex, time.time()
        try:
            pipe = self._redis.pipeline()
            pipe.zremrangebyscore(key, 0, now - STREAM_TTL_S)
            pipe.zadd(key, {token: now})
            pipe.zcard(key)
            pipe.expire(key, STREAM_TTL_S)
            _, _, count, _ = pipe.execute()
            if int(count) > limit:
                self._redis.zrem(key, token)
                raise too_many_streams(limit)
        except redis.RedisError:
            logger.warning("stream_slots_unavailable")
            return "mem:" + self._fallback.acquire(user_id, limit)
        return token

    def release(self, user_id: uuid.UUID, token: str) -> None:
        """Remove the slot; errors are logged, the TTL cleans up."""
        if token.startswith("mem:"):
            self._fallback.release(user_id, token[4:])
            return
        try:
            self._redis.zrem(f"chatstreams:{user_id}", token)
        except redis.RedisError:
            logger.warning("stream_slots_unavailable")


@lru_cache
def get_stream_slots() -> StreamSlots:
    """Return the slot store for this process."""
    settings = get_settings()
    if settings.rate_limit_enabled:
        return RedisStreamSlots(settings.redis_url)
    return MemoryStreamSlots()
