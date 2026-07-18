"""Analytics engine — compute aggregate stats over stored signals & AI calls.

Provides time-windowed aggregates used by the dashboard and ops team:
- Signal counts by direction, by symbol, by signal_type
- AI usage: provider breakdown, cache hit rate, latency percentiles
- Scan performance: avg/p95 duration, stage1/stage2 throughput
- Hit rate: closed signals grouped by outcome (TODO when outcome tracking added)
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models import AIDecision, Metric, Signal
from app.db.session import get_session

log = get_logger(__name__)


@dataclass
class AnalyticsReport:
    window_hours: int
    generated_at: datetime
    signals_total: int = 0
    signals_by_direction: dict[str, int] = field(default_factory=dict)
    signals_by_type: dict[str, int] = field(default_factory=dict)
    signals_by_symbol: dict[str, int] = field(default_factory=dict)
    ai_calls: int = 0
    ai_cached: int = 0
    ai_cache_hit_rate: float = 0.0
    ai_by_provider: dict[str, int] = field(default_factory=dict)
    ai_by_decision: dict[str, int] = field(default_factory=dict)
    ai_avg_latency_ms: float = 0.0
    ai_p95_latency_ms: float = 0.0
    scan_avg_duration_ms: float = 0.0
    scan_p95_duration_ms: float = 0.0
    stage1_avg_passed: float = 0.0
    stage2_avg_setups: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_hours": self.window_hours,
            "generated_at": self.generated_at.isoformat(),
            "signals_total": self.signals_total,
            "signals_by_direction": self.signals_by_direction,
            "signals_by_type": self.signals_by_type,
            "signals_by_symbol": self.signals_by_symbol,
            "ai_calls": self.ai_calls,
            "ai_cached": self.ai_cached,
            "ai_cache_hit_rate": round(self.ai_cache_hit_rate, 3),
            "ai_by_provider": self.ai_by_provider,
            "ai_by_decision": self.ai_by_decision,
            "ai_avg_latency_ms": round(self.ai_avg_latency_ms, 1),
            "ai_p95_latency_ms": round(self.ai_p95_latency_ms, 1),
            "scan_avg_duration_ms": round(self.scan_avg_duration_ms, 1),
            "scan_p95_duration_ms": round(self.scan_p95_duration_ms, 1),
            "stage1_avg_passed": round(self.stage1_avg_passed, 1),
            "stage2_avg_setups": round(self.stage2_avg_setups, 1),
        }


class AnalyticsEngine:
    """Compute time-windowed analytics from the database."""

    async def report(self, window_hours: int = 24) -> AnalyticsReport:
        """Generate an analytics report for the last ``window_hours``."""
        since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        report = AnalyticsReport(
            window_hours=window_hours,
            generated_at=datetime.now(timezone.utc),
        )

        try:
            async with get_session() as session:
                # Signals
                stmt = select(Signal).where(Signal.created_at >= since)
                result = await session.execute(stmt)
                signals = list(result.scalars().all())
                report.signals_total = len(signals)
                report.signals_by_direction = dict(Counter(s.direction for s in signals))
                report.signals_by_type = dict(Counter(s.signal_type for s in signals))
                by_sym = Counter(s.symbol for s in signals)
                report.signals_by_symbol = dict(by_sym.most_common(20))

                # AI decisions
                stmt = select(AIDecision).where(AIDecision.created_at >= since)
                result = await session.execute(stmt)
                ai = list(result.scalars().all())
                report.ai_calls = len(ai)
                report.ai_cached = sum(1 for a in ai if a.cached)
                if ai:
                    report.ai_cache_hit_rate = report.ai_cached / len(ai)
                report.ai_by_provider = dict(Counter(a.provider for a in ai))
                report.ai_by_decision = dict(Counter(a.decision for a in ai))
                latencies = sorted(a.latency_ms for a in ai if a.latency_ms > 0)
                if latencies:
                    report.ai_avg_latency_ms = sum(latencies) / len(latencies)
                    p95_idx = int(len(latencies) * 0.95)
                    report.ai_p95_latency_ms = latencies[min(p95_idx, len(latencies) - 1)]

                # Metrics
                stmt = select(Metric).where(Metric.timestamp >= since)
                result = await session.execute(stmt)
                metrics = list(result.scalars().all())

                durations = [m.value_float for m in metrics if m.name == "pipeline.duration_ms"]
                if durations:
                    report.scan_avg_duration_ms = sum(durations) / len(durations)
                    durations_sorted = sorted(durations)
                    p95_idx = int(len(durations_sorted) * 0.95)
                    report.scan_p95_duration_ms = durations_sorted[min(p95_idx, len(durations_sorted) - 1)]

                s1_passed = [m.value_int for m in metrics if m.name == "stage1.passed"]
                if s1_passed:
                    report.stage1_avg_passed = sum(s1_passed) / len(s1_passed)
                s2_setups = [m.value_int for m in metrics if m.name == "stage2.setups"]
                if s2_setups:
                    report.stage2_avg_setups = sum(s2_setups) / len(s2_setups)

        except Exception:  # noqa: BLE001
            log.exception("analytics_report_failed")

        return report


__all__ = ["AnalyticsEngine", "AnalyticsReport"]
