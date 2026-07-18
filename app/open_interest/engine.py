"""Open interest engine.

Tracks OI changes over time and detects:
- **Spike**: OI increase > N% in short window = new positions being opened
- **Purge**: OI drop > N% = mass liquidations / position closing
- **Divergence**: price up + OI down (short covering) or price down + OI up (new shorts)

OI data comes from Binance's ``/futures/data/openInterestHist`` endpoint
(15-minute snapshots). Live updates from the ``@openInterest`` WS stream
keep the latest value fresh.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from app.market.data_engine import SymbolState


@dataclass
class OISnapshot:
    timestamp: datetime
    open_interest: float
    open_interest_value: float


@dataclass
class OIResult:
    current_oi: float = 0.0
    current_oi_value: float = 0.0
    delta_pct_1h: float = 0.0  # % change over last hour
    delta_pct_4h: float = 0.0
    spike: bool = False
    purge: bool = False
    divergence: str = "NONE"  # NONE / SHORT_COVERING / NEW_SHORTS / NEW_LONGS / LONG_UNWIND
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "current_oi": self.current_oi,
            "current_oi_value": self.current_oi_value,
            "delta_pct_1h": round(self.delta_pct_1h, 3),
            "delta_pct_4h": round(self.delta_pct_4h, 3),
            "spike": self.spike,
            "purge": self.purge,
            "divergence": self.divergence,
            "summary": self.summary,
        }


class OpenInterestEngine:
    """Compute OI deltas, spikes, and price/OI divergences."""

    def __init__(
        self,
        spike_threshold_pct: float = 5.0,
        purge_threshold_pct: float = -5.0,
    ) -> None:
        self._spike_thr = spike_threshold_pct
        self._purge_thr = purge_threshold_pct

    def analyze(
        self,
        history: Sequence[OISnapshot] | Sequence[dict],
        current_state: SymbolState | None = None,
        price_now: float = 0.0,
        price_1h_ago: float = 0.0,
    ) -> OIResult:
        if not history:
            # Fall back to live state only
            if current_state is not None:
                return OIResult(
                    current_oi=current_state.open_interest,
                    current_oi_value=current_state.open_interest_value,
                    summary="No OI history available; using live snapshot only.",
                )
            return OIResult(summary="No OI data available.")

        # Normalize to OISnapshot
        snapshots = self._normalize(history)
        if len(snapshots) < 2:
            return OIResult(current_oi=snapshots[-1].open_interest if snapshots else 0.0)

        current = snapshots[-1]
        # 1h ago = ~4 snapshots at 15m cadence
        idx_1h = max(0, len(snapshots) - 5)
        idx_4h = max(0, len(snapshots) - 17)
        delta_1h = self._delta_pct(snapshots[idx_1h].open_interest, current.open_interest)
        delta_4h = self._delta_pct(snapshots[idx_4h].open_interest, current.open_interest)

        spike = delta_1h > self._spike_thr
        purge = delta_1h < self._purge_thr

        # Price/OI divergence
        divergence = "NONE"
        if price_now > 0 and price_1h_ago > 0:
            price_change_pct = (price_now - price_1h_ago) / price_1h_ago * 100
            if price_change_pct > 0.5 and delta_1h > self._spike_thr:
                divergence = "NEW_LONGS"  # price up + OI up = new longs
            elif price_change_pct > 0.5 and delta_1h < -1.0:
                divergence = "SHORT_COVERING"  # price up + OI down
            elif price_change_pct < -0.5 and delta_1h > self._spike_thr:
                divergence = "NEW_SHORTS"  # price down + OI up
            elif price_change_pct < -0.5 and delta_1h < -1.0:
                divergence = "LONG_UNWIND"  # price down + OI down

        summary = self._build_summary(delta_1h, divergence, spike, purge)
        return OIResult(
            current_oi=current.open_interest,
            current_oi_value=current.open_interest_value,
            delta_pct_1h=delta_1h,
            delta_pct_4h=delta_4h,
            spike=spike,
            purge=purge,
            divergence=divergence,
            summary=summary,
        )

    def _normalize(self, history: Sequence) -> list[OISnapshot]:
        out: list[OISnapshot] = []
        for h in history:
            if isinstance(h, OISnapshot):
                out.append(h)
            elif isinstance(h, dict):
                ts = h.get("timestamp") or h.get("time")
                if isinstance(ts, str):
                    try:
                        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except ValueError:
                        ts = datetime.now(timezone.utc)
                elif isinstance(ts, (int, float)):
                    ts = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                elif ts is None:
                    ts = datetime.now(timezone.utc)
                out.append(OISnapshot(
                    timestamp=ts,
                    open_interest=float(h.get("openInterest", h.get("sumOpenInterest", 0))),
                    open_interest_value=float(h.get("openInterestValue", h.get("sumOpenInterestValue", 0))),
                ))
        return out

    def _delta_pct(self, old: float, new: float) -> float:
        if old <= 0:
            return 0.0
        return (new - old) / old * 100

    def _build_summary(self, delta: float, divergence: str, spike: bool, purge: bool) -> str:
        parts: list[str] = []
        if spike:
            parts.append(f"OI spike +{delta:.2f}%")
        elif purge:
            parts.append(f"OI purge {delta:.2f}%")
        else:
            parts.append(f"OI delta {delta:+.2f}%")
        if divergence != "NONE":
            parts.append(divergence.replace("_", " ").lower())
        return "; ".join(parts)


__all__ = ["OpenInterestEngine", "OIResult", "OISnapshot"]
