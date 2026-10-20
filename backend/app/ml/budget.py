"""Time and memory budget for heavy ML work; running out degrades the work, never fails it."""

import gc
import os
import time
from collections.abc import Callable

import psutil


def process_rss_mb() -> float:
    """Return the resident memory of this process in megabytes."""
    return float(psutil.Process(os.getpid()).memory_info().rss) / (1024 * 1024)


class Budget:
    """Wall-clock and memory limits shared by the steps of one ML run (forecast, segments).

    `exhausted()` names the limit that was hit ("time" or "memory"), or returns None. The engine
    checks it before each optional model and each additional series, and skips work with a
    warning instead of raising, so a forecast always returns what it managed to compute.
    """

    def __init__(
        self,
        max_seconds: float,
        memory_mb: float,
        rss_provider: Callable[[], float] = process_rss_mb,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Start the clock; `rss_provider` and `clock` can be replaced in tests."""
        self.max_seconds = max_seconds
        self.memory_mb = memory_mb
        self._rss = rss_provider
        self._clock = clock
        self._start = clock()
        self.peak_rss_mb = 0.0

    def elapsed(self) -> float:
        """Seconds since the budget started."""
        return self._clock() - self._start

    def remaining(self) -> float:
        """Seconds left before the time limit."""
        return max(0.0, self.max_seconds - self.elapsed())

    def rss_mb(self) -> float:
        """Return the current process memory in MB (also tracked as a peak)."""
        value = float(self._rss())
        self.peak_rss_mb = max(self.peak_rss_mb, value)
        return value

    def exhausted(self, reserve_fraction: float = 0.0) -> str | None:
        """Return "memory" or "time" when a limit is hit, else None.

        `reserve_fraction` keeps part of the time budget free for work that must still happen
        (a heavy model asks for 0.4 so the cheap steps after it are never starved).
        """
        if self.rss_mb() > self.memory_mb:
            return "memory"
        if self.remaining() <= self.max_seconds * reserve_fraction:
            return "time"
        return None

    def release(self) -> None:
        """Hand memory back between series."""
        gc.collect()


def reason_text(reason: str, limit: "Budget") -> str:
    """Return the plain-language reason a limit was hit."""
    if reason == "memory":
        return f"the memory limit of {limit.memory_mb:.0f} MB was reached"
    return f"the time limit of {limit.max_seconds:.0f} seconds was reached"
