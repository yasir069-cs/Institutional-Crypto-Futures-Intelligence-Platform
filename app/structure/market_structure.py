"""Market structure engines — HH/HL/LH/LL, BOS, CHOCH.

This module analyzes the swing pivots produced by the indicator engine to
classify the market structure: is price making higher highs/higher lows
(uptrend) or lower highs/lower lows (downtrend)? It also detects two key
Smart Money Concepts events:

- **BOS (Break of Structure)**: continuation signal. Price closes beyond
  the prior swing high (bullish BOS) or low (bearish BOS) in the direction
  of the existing trend.

- **CHOCH (Change of Character)**: reversal signal. Price breaks a swing
  point in the *opposite* direction of the prevailing trend, indicating
  the trend may be exhausted.

The output is a dataclass that downstream engines (Trend, Smart Money,
Confluence) consume to weight their decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Sequence

from app.exchange.binance_rest import Candle


class TrendBias(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class StructureEvent(str, Enum):
    NONE = "NONE"
    BOS_BULL = "BOS_BULL"
    BOS_BEAR = "BOS_BEAR"
    CHOCH_BULL = "CHOCH_BULL"  # bearish → bullish reversal
    CHOCH_BEAR = "CHOCH_BEAR"  # bullish → bearish reversal


@dataclass
class SwingPoint:
    index: int
    price: float
    time: datetime
    type: str  # "high" or "low"


@dataclass
class MarketStructureResult:
    bias: TrendBias
    event: StructureEvent
    swings: list[SwingPoint] = field(default_factory=list)
    last_high: SwingPoint | None = None
    last_low: SwingPoint | None = None
    hh_count: int = 0
    hl_count: int = 0
    lh_count: int = 0
    ll_count: int = 0
    broken_level: float | None = None
    strength: float = 0.0  # 0..1

    def to_dict(self) -> dict:
        return {
            "bias": self.bias.value,
            "event": self.event.value,
            "hh": self.hh_count,
            "hl": self.hl_count,
            "lh": self.lh_count,
            "ll": self.ll_count,
            "broken_level": self.broken_level,
            "strength": round(self.strength, 3),
            "last_high": self.last_high.price if self.last_high else None,
            "last_low": self.last_low.price if self.last_low else None,
        }


class MarketStructureEngine:
    """Detect swing points, classify bias, and detect BOS / CHOCH events."""

    def __init__(self, left: int = 5, right: int = 5) -> None:
        self._left = left
        self._right = right

    def analyze(self, candles: Sequence[Candle], prev_bias: TrendBias = TrendBias.NEUTRAL) -> MarketStructureResult:
        """Analyze the candle series and return structure classification.

        ``prev_bias`` is the prior bias — used to distinguish BOS (continuation)
        from CHOCH (reversal).
        """
        if len(candles) < self._left + self._right + 1:
            return MarketStructureResult(bias=TrendBias.NEUTRAL, event=StructureEvent.NONE)

        swings = self._find_swings(candles)
        if len(swings) < 4:
            return MarketStructureResult(bias=TrendBias.NEUTRAL, event=StructureEvent.NONE)

        # Classify HH/HL/LH/LL over the recent swing sequence
        highs = [s for s in swings if s.type == "high"]
        lows = [s for s in swings if s.type == "low"]

        hh = hl = lh = ll = 0
        for i in range(1, len(highs)):
            if highs[i].price > highs[i - 1].price:
                hh += 1
            else:
                lh += 1
        for i in range(1, len(lows)):
            if lows[i].price > lows[i - 1].price:
                hl += 1
            else:
                ll += 1

        # Determine bias
        bullish_score = hh + hl
        bearish_score = lh + ll
        if bullish_score > bearish_score:
            bias = TrendBias.BULLISH
        elif bearish_score > bullish_score:
            bias = TrendBias.BEARISH
        else:
            bias = TrendBias.NEUTRAL

        # Strength: ratio of dominant side
        total = bullish_score + bearish_score
        if total > 0:
            strength = max(bullish_score, bearish_score) / total
        else:
            strength = 0.0

        # Detect BOS / CHOCH
        event, broken_level = self._detect_event(candles, highs, lows, bias, prev_bias)

        return MarketStructureResult(
            bias=bias,
            event=event,
            swings=swings[-10:],
            last_high=highs[-1] if highs else None,
            last_low=lows[-1] if lows else None,
            hh_count=hh,
            hl_count=hl,
            lh_count=lh,
            ll_count=ll,
            broken_level=broken_level,
            strength=strength,
        )

    def _find_swings(self, candles: Sequence[Candle]) -> list[SwingPoint]:
        swings: list[SwingPoint] = []
        for i in range(self._left, len(candles) - self._right):
            window = candles[i - self._left : i + self._right + 1]
            if candles[i].low == min(c.low for c in window):
                swings.append(SwingPoint(i, candles[i].low, candles[i].open_time, "low"))
            if candles[i].high == max(c.high for c in window):
                swings.append(SwingPoint(i, candles[i].high, candles[i].open_time, "high"))
        swings.sort(key=lambda s: s.index)
        return swings

    def _detect_event(
        self,
        candles: Sequence[Candle],
        highs: list[SwingPoint],
        lows: list[SwingPoint],
        bias: TrendBias,
        prev_bias: TrendBias,
    ) -> tuple[StructureEvent, float | None]:
        if not highs or not lows or not candles:
            return StructureEvent.NONE, None
        last_candle = candles[-1]

        # The most recent prior swing high/low (excluding the last one itself)
        prev_high = highs[-2] if len(highs) >= 2 else None
        prev_low = lows[-2] if len(lows) >= 2 else None

        if prev_high and last_candle.close > prev_high.price:
            # Broke above prior swing high
            if bias == TrendBias.BULLISH:
                return StructureEvent.BOS_BULL, prev_high.price
            else:
                # Was bearish/neutral → reversal up
                if prev_bias == TrendBias.BEARISH:
                    return StructureEvent.CHOCH_BULL, prev_high.price
                return StructureEvent.BOS_BULL, prev_high.price

        if prev_low and last_candle.close < prev_low.price:
            if bias == TrendBias.BEARISH:
                return StructureEvent.BOS_BEAR, prev_low.price
            else:
                if prev_bias == TrendBias.BULLISH:
                    return StructureEvent.CHOCH_BEAR, prev_low.price
                return StructureEvent.BOS_BEAR, prev_low.price

        return StructureEvent.NONE, None


__all__ = [
    "TrendBias",
    "StructureEvent",
    "SwingPoint",
    "MarketStructureResult",
    "MarketStructureEngine",
]
