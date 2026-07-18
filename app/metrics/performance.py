"""Performance metrics — in-process counters and timers.

Lightweight, lock-free-ish counters for hot-path metrics that don't need
the durability of the DB ``metrics`` table. Used for:
- Scan cycle duration (live)
- Cache hit / miss ratio
- Queue depth (AI worker pool)
- Per-symbol scan time

Counters are stored in process memory. For multi-process deployments
they should be periodically flushed to the DB / Prometheus.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Deque

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class TimerSample:
    name: str
    start: float
    end: float

    @property
    def duration_ms(self) -> float:
        return (self.end - self.start) * 1000


class PerformanceMetrics:
    """In-process performance counters and timers."""

    def __init__(self, history_size: int = 1000) -> None:
        self._history_size = history_size
        self._timings: dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=history_size))
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Counters
    # ------------------------------------------------------------------ #
    def inc(self, name: str, by: int = 1) -> None:
        self._counters[name] += by

    def get_counter(self, name: str) -> int:
        return self._counters.get(name, 0)

    # ------------------------------------------------------------------ #
    # Gauges
    # ------------------------------------------------------------------ #
    def set_gauge(self, name: str, value: float) -> None:
        self._gauges[name] = value

    def get_gauge(self, name: str) -> float:
        return self._gauges.get(name, 0.0)

    # ------------------------------------------------------------------ #
    # Timers
    # ------------------------------------------------------------------ #
    def timer(self, name: str) -> "_TimerContext":
        return _TimerContext(self, name)

    def record_timing(self, name: str, duration_ms: float) -> None:
        self._timings[name].append(duration_ms)

    def timing_stats(self, name: str) -> dict[str, float]:
        timings = list(self._timings.get(name, []))
        if not timings:
            return {"count": 0, "avg_ms": 0, "p50_ms": 0, "p95_ms": 0, "max_ms": 0}
        sorted_t = sorted(timings)
        n = len(sorted_t)
        return {
            "count": n,
            "avg_ms": sum(sorted_t) / n,
            "p50_ms": sorted_t[n // 2],
            "p95_ms": sorted_t[min(int(n * 0.95), n - 1)],
            "max_ms": sorted_t[-1],
        }

    # ------------------------------------------------------------------ #
    # Cache stats
    # ------------------------------------------------------------------ #
    def record_cache_hit(self, name: str) -> None:
        self.inc(f"cache.{name}.hits")

    def record_cache_miss(self, name: str) -> None:
        self.inc(f"cache.{name}.misses")

    def cache_hit_rate(self, name: str) -> float:
        hits = self.get_counter(f"cache.{name}.hits")
        misses = self.get_counter(f"cache.{name}.misses")
        total = hits + misses
        return hits / total if total > 0 else 0.0

    # ------------------------------------------------------------------ #
    # Snapshot
    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict[str, object]:
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "timings": {name: self.timing_stats(name) for name in self._timings},
            "cache_hit_rates": {
                name.replace("cache.", "").replace(".hits", ""): self.cache_hit_rate(
                    name.replace("cache.", "").replace(".hits", "")
                )
                for name in list(self._counters)
                if name.startswith("cache.") and name.endswith(".hits")
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


class _TimerContext:
    """Async context manager for timing blocks of code."""

    def __init__(self, metrics: PerformanceMetrics, name: str) -> None:
        self._metrics = metrics
        self._name = name
        self._start = 0.0

    def __enter__(self) -> "_TimerContext":
        self._start = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        duration_ms = (time.time() - self._start) * 1000
        self._metrics.record_timing(self._name, duration_ms)


# Module-level singleton
metrics = PerformanceMetrics()


__all__ = ["PerformanceMetrics", "metrics", "TimerSample"]
