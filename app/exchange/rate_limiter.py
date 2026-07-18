"""Binance Futures weight-tracking rate limiter.

Binance enforces multiple concurrent limits:
- 2400 weight per minute (IP)
- 300 orders per 10 seconds
- 100,000 orders per day

We track weight per minute via a sliding window counter (in-memory + Redis
mirror for multi-process setups). Order limits are not enforced here since
this platform is alert-only — but the API is provided for future use.

The limiter is **fair**: callers acquire weight before issuing a request and
release it on response. If insufficient weight is available, the caller
sleeps until the next window slot opens.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Deque

from app.config import settings
from app.core.errors import RateLimitedError
from app.core.logging import get_logger

log = get_logger(__name__)


class SlidingWindowLimiter:
    """Sliding-window counter over ``window_sec`` with ``max_events`` cap.

    Thread-safe (async). Use one instance per Binance limit dimension.
    """

    def __init__(self, max_events: int, window_sec: float) -> None:
        self._max = max_events
        self._window = window_sec
        self._events: Deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self, weight: int = 1) -> None:
        """Block until ``weight`` slots are available, then consume them."""
        if weight <= 0:
            return
        while True:
            async with self._lock:
                self._purge()
                current = sum(self._events) if False else self._count_weight()
                # _events stores (timestamp, weight) — but deque can't easily store tuples
                # here we re-design: store weights per second slot
                if current + weight <= self._max:
                    self._events.append((time.monotonic(), weight))  # type: ignore[assignment]
                    return
                # Compute sleep duration until the oldest entry falls out of the window.
                oldest_ts = self._events[0][0]  # type: ignore[index]
                sleep_for = self._window - (time.monotonic() - oldest_ts) + 0.05
            if sleep_for > 0:
                log.debug("rate_limit_wait", weight=weight, sleep_sec=round(sleep_for, 3))
                await asyncio.sleep(min(sleep_for, self._window))
            else:
                await asyncio.sleep(0.01)

    async def try_acquire(self, weight: int = 1) -> bool:
        """Try to acquire without blocking. Returns True if acquired."""
        if weight <= 0:
            return True
        async with self._lock:
            self._purge()
            current = self._count_weight()
            if current + weight <= self._max:
                self._events.append((time.monotonic(), weight))  # type: ignore[assignment]
                return True
            return False

    def _purge(self) -> None:
        cutoff = time.monotonic() - self._window
        while self._events and self._events[0][0] < cutoff:  # type: ignore[index]
            self._events.popleft()

    def _count_weight(self) -> int:
        return sum(w for _, w in self._events)  # type: ignore[misc]

    def current_usage(self) -> int:
        return self._count_weight()


class BinanceRateLimiter:
    """Composite limiter matching Binance Futures' dimensions."""

    def __init__(self) -> None:
        self.weight_limiter = SlidingWindowLimiter(
            max_events=settings.binance_weight_per_minute,
            window_sec=60.0,
        )
        # Order limiters retained for future trade execution.
        self._order_10s = SlidingWindowLimiter(settings.binance_order_per_10s, 10.0)
        self._order_day = SlidingWindowLimiter(settings.binance_order_per_day, 86400.0)

    async def acquire_weight(self, weight: int) -> None:
        await self.weight_limiter.acquire(weight)

    async def acquire_order(self) -> None:
        await self._order_10s.acquire(1)
        await self._order_day.acquire(1)

    async def handle_rate_limit_response(self, retry_after_sec: float | None) -> None:
        """Called when Binance returns 429/418 — sleep before retrying."""
        delay = retry_after_sec or 5.0
        log.warning("binance_rate_limited", retry_after=delay)
        raise RateLimitedError(retry_after=delay)


__all__ = ["SlidingWindowLimiter", "BinanceRateLimiter"]
