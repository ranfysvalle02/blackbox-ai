"""In-process per-client sliding-window rate limiter (abuse prevention).

The limiter is a request-path guard, so it must be cheap and allocation-light.
It keeps a per-key deque of recent hit timestamps and prunes anything older than
the window on access. The event loop is single-threaded, so no locking is needed.

Memory is bounded by the number of *active* clients: idle keys are garbage
collected opportunistically (at most once per window). State is per-process; for
a multi-replica deployment, layer a shared limiter (e.g. Redis) in front - but
this alone already blunts single-source abuse against one instance.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["RateLimitDecision", "RateLimiter", "SlidingWindowRateLimiter"]


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Result of a rate-limit check; ``retry_after_s`` is set only when denied."""

    allowed: bool
    retry_after_s: float = 0.0


@runtime_checkable
class RateLimiter(Protocol):
    """Decides whether a request keyed by ``key`` is allowed right now.

    The relay depends only on this seam, so the in-process limiter below can be
    swapped for a shared/distributed backend (e.g. Redis) with no relay changes.
    Implementations must be cheap and non-blocking (called on the request path).
    """

    def check(self, key: str) -> RateLimitDecision: ...


class SlidingWindowRateLimiter:
    """Allow at most ``max_requests`` per ``window_s`` for each key."""

    def __init__(
        self,
        max_requests: int,
        window_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_requests < 1:
            raise ValueError("max_requests must be >= 1")
        if window_s <= 0:
            raise ValueError("window_s must be > 0")
        self._max = max_requests
        self._window = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._last_gc = clock()

    def check(self, key: str) -> RateLimitDecision:
        """Record a hit for ``key`` and decide whether it is allowed."""
        now = self._clock()
        self._maybe_gc(now)
        cutoff = now - self._window

        bucket = self._hits.get(key)
        if bucket is None:
            bucket = deque()
            self._hits[key] = bucket
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if len(bucket) >= self._max:
            # Oldest hit must age out of the window before capacity frees up.
            retry_after = bucket[0] + self._window - now
            return RateLimitDecision(allowed=False, retry_after_s=max(0.0, retry_after))

        bucket.append(now)
        return RateLimitDecision(allowed=True)

    def _maybe_gc(self, now: float) -> None:
        """Drop keys with no hits inside the current window (bounded memory)."""
        if now - self._last_gc < self._window:
            return
        self._last_gc = now
        cutoff = now - self._window
        stale = [key for key, bucket in self._hits.items() if not bucket or bucket[-1] <= cutoff]
        for key in stale:
            del self._hits[key]
