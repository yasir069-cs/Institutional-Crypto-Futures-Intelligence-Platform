"""Unit tests for rate limiter."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.exchange.rate_limiter import SlidingWindowLimiter


@pytest.mark.asyncio
async def test_acquire_within_limit_succeeds_immediately():
    limiter = SlidingWindowLimiter(max_events=10, window_sec=1.0)
    start = time.time()
    await limiter.acquire(5)
    elapsed = time.time() - start
    assert elapsed < 0.1


@pytest.mark.asyncio
async def test_acquire_over_limit_blocks():
    limiter = SlidingWindowLimiter(max_events=5, window_sec=1.0)
    await limiter.acquire(5)
    # Next acquire should block until window slides
    start = time.time()
    task = asyncio.create_task(limiter.acquire(1))
    # Give it a tiny bit of time then cancel
    await asyncio.sleep(0.1)
    assert not task.done()
    task.cancel()


@pytest.mark.asyncio
async def test_try_acquire_returns_false_when_full():
    limiter = SlidingWindowLimiter(max_events=2, window_sec=1.0)
    assert await limiter.try_acquire(2)
    assert not await limiter.try_acquire(1)


@pytest.mark.asyncio
async def test_window_purges_old_entries():
    limiter = SlidingWindowLimiter(max_events=5, window_sec=0.2)
    await limiter.acquire(5)
    # Wait for window to slide
    await asyncio.sleep(0.25)
    # Should be able to acquire again
    assert await limiter.try_acquire(5)
