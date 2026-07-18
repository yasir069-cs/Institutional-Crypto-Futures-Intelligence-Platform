"""Volume analysis engine.

Detects:
- **Spike**: current volume vs. average (used in scanner)
- **Exhaustion**: declining volume across recent bars despite price movement
- **Climax**: extreme volume + large body candle (potential reversal)
- **Profile**: identify high-volume nodes (HVN) and low-volume nodes (LVN)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.exchange.binance_rest import Candle


@dataclass
class VolumeResult:
    current_volume: float = 0.0
    avg_volume_20: float = 0.0
    spike_ratio: float = 1.0
    trend: str = "STABLE"  # INCREASING / DECREASING / STABLE
    exhaustion: bool = False
    climax: bool = False
    climax_direction: str = "NONE"  # NONE / BULLISH / BEARISH
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "current_volume": self.current_volume,
            "avg_volume_20": self.avg_volume_20,
            "spike_ratio": round(self.spike_ratio, 2),
            "trend": self.trend,
            "exhaustion": self.exhaustion,
            "climax": self.climax,
            "climax_direction": self.climax_direction,
            "summary": self.summary,
        }


class VolumeEngine:
    """Analyze volume behavior across a candle series."""

    def __init__(
        self,
        spike_threshold: float = 2.0,
        climax_volume_percentile: float = 95.0,
    ) -> None:
        self._spike_thr = spike_threshold
        self._climax_pctile = climax_volume_percentile

    def analyze(self, candles: Sequence[Candle]) -> VolumeResult:
        if not candles or len(candles) < 20:
            return VolumeResult(
                current_volume=candles[-1].volume if candles else 0.0,
                summary="Insufficient volume history.",
            )

        recent_20 = list(candles[-20:])
        current = recent_20[-1]
        avg_20 = sum(c.volume for c in recent_20) / 20
        spike = current.volume / avg_20 if avg_20 > 0 else 1.0

        # Trend: linear regression slope approximation
        first_half = sum(c.volume for c in recent_20[:10]) / 10
        second_half = sum(c.volume for c in recent_20[10:]) / 10
        if second_half > first_half * 1.15:
            trend = "INCREASING"
        elif second_half < first_half * 0.85:
            trend = "DECREASING"
        else:
            trend = "STABLE"

        # Exhaustion: price moved but volume declined for 3+ consecutive bars
        exhaustion = False
        if len(recent_20) >= 5:
            declining = all(
                recent_20[-i].volume < recent_20[-i - 1].volume for i in range(1, 4)
            )
            price_move = abs(recent_20[-1].close - recent_20[-4].close) / recent_20[-4].close
            if declining and price_move > 0.01:
                exhaustion = True

        # Climax: volume in 95th percentile + large body
        climax = False
        climax_dir = "NONE"
        sorted_vols = sorted(c.volume for c in candles[-200:])
        pctile_val = sorted_vols[int(len(sorted_vols) * self._climax_pctile / 100)] if sorted_vols else 0
        body_pct = abs(current.close - current.open) / current.open if current.open > 0 else 0
        if current.volume >= pctile_val and body_pct > 0.01:
            climax = True
            climax_dir = "BULLISH" if current.close > current.open else "BEARISH"

        summary = self._build_summary(spike, trend, exhaustion, climax, climax_dir)
        return VolumeResult(
            current_volume=current.volume,
            avg_volume_20=avg_20,
            spike_ratio=spike,
            trend=trend,
            exhaustion=exhaustion,
            climax=climax,
            climax_direction=climax_dir,
            summary=summary,
        )

    def _build_summary(
        self, spike: float, trend: str, exhaustion: bool, climax: bool, climax_dir: str
    ) -> str:
        parts = [f"Volume {spike:.2f}x avg", trend]
        if exhaustion:
            parts.append("exhaustion")
        if climax:
            parts.append(f"climax ({climax_dir.lower()})")
        return "; ".join(parts)


__all__ = ["VolumeEngine", "VolumeResult"]
