"""Chat stream plumbing: per-user slots and the heartbeat that keeps an idle stream open.

At most N open chat streams per user (Redis when rate limiting is on, in-process otherwise). Slots
expire after STREAM_TTL_S so a stream whose cleanup never ran cannot block a user forever.
"""

import contextvars
import queue
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import redis
import structlog

from app.core.config import get_settings
from app.core.errors import RateLimitedError

logger = structlog.get_logger()
STREAM_TTL_S = 600
HEARTBEAT_S = 15.0  # well inside the Next.js proxy's 30 s idle timeout
HEARTBEAT_FRAME = ": keep-alive\n\n"  # an SSE comment: it holds proxies open, clients skip it


@dataclass(frozen=True)
class _Failed:
    """The stream raised; the error is passed on to the response side."""

    error: Exception


def with_heartbeat(frames: Iterator[str], interval: float | None = None) -> Iterator[str]:
    """Yield `frames`, adding a heartbeat whenever none arrives for `interval` seconds.

    The agent runs synchronously, so a slow tool or model call blocks the stream between frames.
    It therefore runs in its own thread and hands frames over a queue, which leaves the response
    side free to send heartbeats whatever the cause of the silence. Closing this generator (the
    client went away) stops the source at its next frame; the source is then closed in the thread
    it ran in, so its database session and cleanup stay on one thread.
    """
    wait = interval if interval is not None else HEARTBEAT_S
    out: queue.Queue[str | _Failed | None] = queue.Queue()
    stop = threading.Event()

    def pump() -> None:
        try:
            for frame in frames:
                out.put(frame)
                if stop.is_set():
                    break
        except Exception as exc:  # noqa: BLE001 - raised again on the response side
            out.put(_Failed(exc))
        finally:
            try:
                getattr(frames, "close", lambda: None)()
            finally:
                out.put(None)

    context = contextvars.copy_context()  # keeps the request's log fields
    threading.Thread(target=context.run, args=(pump,), name="chat-stream", daemon=True).start()
    try:
        while True:
            try:
                item = out.get(timeout=wait)
            except queue.Empty:
                yield HEARTBEAT_FRAME
                continue
            if item is None:
                return
            if isinstance(item, _Failed):
                raise item.error
            yield item
    finally:
        stop.set()


def too_many_streams(limit: int) -> RateLimitedError:
    """Return the 429 raised when a user already has `limit` streams open."""
    return RateLimitedError(
        f"You already have {limit} answers in progress; wait for one to finish.",
        code="too_many_streams",
        details={"max_concurrent_streams": limit},
        headers={"Retry-After": "5"},
    )


def acquire_turn(
    slots: "StreamSlots", user_id: uuid.UUID, conversation_id: uuid.UUID, limit: int
) -> Callable[[], None]:
    """Take a user slot and the conversation's only slot; return the function that frees both.

    One turn at a time per conversation: rows are numbered when a turn starts, so a second turn
    running alongside it (Stop, then a quick new question) would interleave with it.
    """
    user_token = slots.acquire(user_id, limit)
    try:
        turn_token = slots.acquire(conversation_id, 1)
    except RateLimitedError as exc:
        slots.release(user_id, user_token)
        raise RateLimitedError(
            "This conversation is still finishing an answer; try again in a moment.",
            code="conversation_busy",
            headers={"Retry-After": "2"},
        ) from exc

    def release() -> None:
        slots.release(conversation_id, turn_token)
        slots.release(user_id, user_token)

    return release


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
