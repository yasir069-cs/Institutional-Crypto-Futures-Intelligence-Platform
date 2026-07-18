"""Funding rate engine.

Classifies the current funding rate regime and detects:
- **Extreme positive**: funding very high → market overcrowded long (bearish contrarian)
- **Extreme negative**: funding very low → market overcrowded short (bullish contrarian)
- **Shift**: funding rate changed direction or magnitude suddenly
- **Heat**: cumulative funding cost over time
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence


@dataclass
class FundingResult:
    current_rate: float = 0.0
    avg_rate_24h: float = 0.0
    regime: str = "NEUTRAL"  # NEUTRAL / BULLISH_HEAT / BEARISH_HEAT / EXTREME_LONG / EXTREME_SHORT
    shift: bool = False
    shift_direction: str = "NONE"  # NONE / UP / DOWN
    annualized_pct: float = 0.0
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "current_rate": self.current_rate,
            "avg_rate_24h": self.avg_rate_24h,
            "regime": self.regime,
            "shift": self.shift,
            "shift_direction": self.shift_direction,
            "annualized_pct": round(self.annualized_pct, 2),
            "summary": self.summary,
        }


class FundingEngine:
    """Analyze funding rate history and classify regime."""

    def __init__(
        self,
        extreme_positive_threshold: float = 0.0005,  # 0.05% per 8h = ~55% APR
        extreme_negative_threshold: float = -0.0003,
        shift_threshold: float = 0.0002,
    ) -> None:
        self._ext_pos = extreme_positive_threshold
        self._ext_neg = extreme_negative_threshold
        self._shift_thr = shift_threshold

    def analyze(self, history: Sequence[dict] | Sequence[float]) -> FundingResult:
        if not history:
            return FundingResult(summary="No funding history.")

        rates: list[float] = []
        times: list[datetime] = []
        for h in history:
            if isinstance(h, dict):
                r = float(h.get("fundingRate", 0))
                t = h.get("fundingTime") or h.get("timestamp")
                if isinstance(t, (int, float)):
                    t = datetime.fromtimestamp(t / 1000, tz=timezone.utc)
                elif isinstance(t, str):
                    try:
                        t = datetime.fromisoformat(t.replace("Z", "+00:00"))
                    except ValueError:
                        t = datetime.now(timezone.utc)
                else:
                    t = datetime.now(timezone.utc)
                rates.append(r)
                times.append(t)
            else:
                rates.append(float(h))

        current = rates[-1]
        # 24h avg: funding is every 8h, so ~3 entries per 24h
        avg_24h = sum(rates[-3:]) / min(3, len(rates)) if rates else 0.0

        # Annualized: rate per 8h × 3 per day × 365
        annualized = current * 3 * 365 * 100  # as percent

        # Regime
        if current > self._ext_pos:
            regime = "EXTREME_LONG"
        elif current < self._ext_neg:
            regime = "EXTREME_SHORT"
        elif current > 0.0001:
            regime = "BULLISH_HEAT"
        elif current < -0.0001:
            regime = "BEARISH_HEAT"
        else:
            regime = "NEUTRAL"

        # Shift detection: compare last to previous
        shift = False
        shift_dir = "NONE"
        if len(rates) >= 2:
            delta = current - rates[-2]
            if abs(delta) >= self._shift_thr:
                shift = True
                shift_dir = "UP" if delta > 0 else "DOWN"

        summary = self._build_summary(current, regime, shift, shift_dir, annualized)
        return FundingResult(
            current_rate=current,
            avg_rate_24h=avg_24h,
            regime=regime,
            shift=shift,
            shift_direction=shift_dir,
            annualized_pct=annualized,
            summary=summary,
        )

    def _build_summary(
        self, rate: float, regime: str, shift: bool, shift_dir: str, annualized: float
    ) -> str:
        parts = [f"Funding {rate*100:.4f}%/8h ({annualized:.1f}% APR)", regime]
        if shift:
            parts.append(f"shift {shift_dir}")
        return "; ".join(parts)


__all__ = ["FundingEngine", "FundingResult"]
