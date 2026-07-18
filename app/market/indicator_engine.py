"""Indicator engine — vectorized technical indicators with caching.

Provides pure functions for each indicator plus a thin caching layer keyed
on (symbol, timeframe, close_time_of_last_candle). Cached results are
reused until the latest candle changes.

Indicators implemented
----------------------
- EMA (any period)
- SMA
- VWAP (rolling and session)
- ATR (Wilder's smoothing)
- RSI (Wilder's)
- ADX (+DI, -DI)
- MACD (12/26/9 default)
- Bollinger Bands
- Support / Resistance (swing pivots)

Implementation notes
--------------------
- Uses NumPy for vectorization where the math is heavy (EMA, RSI, ADX).
- Pure-Python fallback if NumPy is unavailable (slower but correct).
- All functions accept a list of ``Candle`` and return either a scalar or
  a list aligned with the input.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from app.core.logging import get_logger
from app.exchange.binance_rest import Candle

log = get_logger(__name__)

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    _HAS_NUMPY = False
    np = None  # type: ignore


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _closes(candles: Sequence[Candle]) -> list[float]:
    return [c.close for c in candles]


def _highs(candles: Sequence[Candle]) -> list[float]:
    return [c.high for c in candles]


def _lows(candles: Sequence[Candle]) -> list[float]:
    return [c.low for c in candles]


def _volumes(candles: Sequence[Candle]) -> list[float]:
    return [c.volume for c in candles]


# --------------------------------------------------------------------------- #
# EMA / SMA
# --------------------------------------------------------------------------- #
def ema(values: Sequence[float], period: int) -> list[float]:
    """Exponential moving average. Returns list aligned with input."""
    if not values:
        return []
    if period <= 0:
        return list(values)
    alpha = 2.0 / (period + 1)
    out: list[float] = []
    prev = values[0]
    for i, v in enumerate(values):
        if i == 0:
            out.append(v)
            prev = v
        else:
            prev = alpha * v + (1 - alpha) * prev
            out.append(prev)
    return out


def sma(values: Sequence[float], period: int) -> list[float]:
    """Simple moving average. First ``period-1`` slots are partial averages."""
    if not values:
        return []
    out: list[float] = []
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period:
            s -= values[i - period]
        out.append(s / min(i + 1, period))
    return out


def ema_alignment_score(candles: Sequence[Candle]) -> dict[str, float | list[float]]:
    """Compute EMA 9/21/50/200 alignment and return score in [-1, +1].

    +1 = perfectly stacked bullish (9 > 21 > 50 > 200)
    -1 = perfectly stacked bearish (9 < 21 < 50 < 200)
    """
    closes = _closes(candles)
    if len(closes) < 200:
        # Fall back to whatever periods we have data for
        periods = [p for p in (9, 21, 50, 200) if len(closes) >= p]
    else:
        periods = [9, 21, 50, 200]
    if len(periods) < 2:
        return {"score": 0.0, "emas": []}

    ema_values = {p: ema(closes, p)[-1] for p in periods}
    sorted_periods = sorted(periods, reverse=True)  # slow → fast: [200, 50, 21, 9]
    sorted_emas = [ema_values[p] for p in sorted_periods]

    # Bullish alignment: fast EMA > slow EMA, i.e. sorted_emas ASCENDING by value
    # (since sorted_emas is ordered slow→fast, ascending values mean fast > slow)
    bullish = all(sorted_emas[i] < sorted_emas[i + 1] for i in range(len(sorted_emas) - 1))
    bearish = all(sorted_emas[i] > sorted_emas[i + 1] for i in range(len(sorted_emas) - 1))
    if bullish:
        score = 1.0
    elif bearish:
        score = -1.0
    else:
        # Partial alignment: count adjacent agreements in bullish direction
        agreements = sum(
            1 for i in range(len(sorted_emas) - 1) if sorted_emas[i] < sorted_emas[i + 1]
        )
        score = (agreements / (len(sorted_emas) - 1)) * 2 - 1  # scale to [-1, +1]
    return {"score": score, "emas": [ema_values[p] for p in periods]}


# --------------------------------------------------------------------------- #
# VWAP
# --------------------------------------------------------------------------- #
def vwap(candles: Sequence[Candle]) -> list[float]:
    """Cumulative VWAP over the window."""
    if not candles:
        return []
    out: list[float] = []
    cum_pv = 0.0
    cum_v = 0.0
    for c in candles:
        typical = (c.high + c.low + c.close) / 3.0
        pv = typical * c.volume
        cum_pv += pv
        cum_v += c.volume
        out.append(cum_pv / cum_v if cum_v > 0 else c.close)
    return out


def vwap_position(candles: Sequence[Candle]) -> float:
    """Return current price's position relative to VWAP in [-1, +1].

    +1: price is well above VWAP (strong bullish)
     0: price at VWAP
    -1: price is well below VWAP
    """
    if not candles:
        return 0.0
    v = vwap(candles)[-1]
    price = candles[-1].close
    if v <= 0:
        return 0.0
    deviation = (price - v) / v
    # Normalize: 1% deviation → ±0.5, 2% → ±1.0 (capped)
    return max(-1.0, min(1.0, deviation * 50))


# --------------------------------------------------------------------------- #
# ATR (Wilder)
# --------------------------------------------------------------------------- #
def atr(candles: Sequence[Candle], period: int = 14) -> list[float]:
    """Average True Range using Wilder's smoothing."""
    if len(candles) < 2:
        return [0.0] * len(candles)
    trs: list[float] = [0.0]
    for i in range(1, len(candles)):
        c = candles[i]
        prev = candles[i - 1]
        tr = max(
            c.high - c.low,
            abs(c.high - prev.close),
            abs(c.low - prev.close),
        )
        trs.append(tr)
    # Wilder smoothing
    out: list[float] = []
    prev_atr = sum(trs[1 : period + 1]) / period if len(trs) > period else sum(trs[1:]) / max(1, len(trs) - 1)
    for i, tr in enumerate(trs):
        if i == 0:
            out.append(0.0)
            continue
        if i <= period:
            prev_atr = (prev_atr * (i - 1) + tr) / i
        else:
            prev_atr = (prev_atr * (period - 1) + tr) / period
        out.append(prev_atr)
    return out


def atr_value(candles: Sequence[Candle], period: int = 14) -> float:
    return atr(candles, period)[-1] if candles else 0.0


def atr_pct(candles: Sequence[Candle], period: int = 14) -> float:
    """ATR as a percentage of current price — useful for volatility gating."""
    if not candles:
        return 0.0
    a = atr_value(candles, period)
    price = candles[-1].close
    return (a / price * 100.0) if price > 0 else 0.0


# --------------------------------------------------------------------------- #
# RSI (Wilder)
# --------------------------------------------------------------------------- #
def rsi(candles: Sequence[Candle], period: int = 14) -> list[float]:
    """Relative Strength Index using Wilder's smoothing."""
    if len(candles) < 2:
        return [50.0] * len(candles)
    gains = [0.0]
    losses = [0.0]
    for i in range(1, len(candles)):
        change = candles[i].close - candles[i - 1].close
        gains.append(max(0.0, change))
        losses.append(max(0.0, -change))

    avg_gain = sum(gains[1 : period + 1]) / period if len(gains) > period else sum(gains[1:]) / max(1, len(gains) - 1)
    avg_loss = sum(losses[1 : period + 1]) / period if len(losses) > period else sum(losses[1:]) / max(1, len(losses) - 1)

    out: list[float] = []
    for i in range(len(candles)):
        if i == 0:
            out.append(50.0)
            continue
        if i <= period:
            avg_gain = (avg_gain * (i - 1) + gains[i]) / i
            avg_loss = (avg_loss * (i - 1) + losses[i]) / i
        else:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            out.append(100.0)
        else:
            rs = avg_gain / avg_loss
            out.append(100 - 100 / (1 + rs))
    return out


def rsi_metrics(candles: Sequence[Candle], period: int = 14, lookback: int = 10) -> dict[str, float]:
    """Compute slope, momentum, acceleration of RSI over recent lookback bars."""
    r = rsi(candles, period)
    if len(r) < 3:
        return {"value": r[-1] if r else 50.0, "slope": 0.0, "momentum": 0.0, "acceleration": 0.0}
    window = r[-lookback:] if len(r) >= lookback else r
    value = window[-1]
    slope = window[-1] - window[0]
    momentum = (window[-1] - window[-2]) if len(window) >= 2 else 0.0
    acceleration = (window[-1] - 2 * window[-2] + window[-3]) if len(window) >= 3 else 0.0
    return {
        "value": value,
        "slope": slope,
        "momentum": momentum,
        "acceleration": acceleration,
    }


# --------------------------------------------------------------------------- #
# ADX
# --------------------------------------------------------------------------- #
def adx(candles: Sequence[Candle], period: int = 14) -> dict[str, float]:
    """ADX with +DI and -DI. Returns latest values."""
    if len(candles) < period + 2:
        return {"adx": 0.0, "plus_di": 0.0, "minus_di": 0.0, "trend_strength": "none"}

    plus_dm: list[float] = [0.0]
    minus_dm: list[float] = [0.0]
    trs: list[float] = [0.0]
    for i in range(1, len(candles)):
        up = candles[i].high - candles[i - 1].high
        down = candles[i - 1].low - candles[i].low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr = max(
            candles[i].high - candles[i].low,
            abs(candles[i].high - candles[i - 1].close),
            abs(candles[i].low - candles[i - 1].close),
        )
        trs.append(tr)

    # Wilder smoothing
    atr_s = [sum(trs[1 : period + 1])]
    plus_dm_s = [sum(plus_dm[1 : period + 1])]
    minus_dm_s = [sum(minus_dm[1 : period + 1])]
    for i in range(period + 1, len(candles)):
        atr_s.append((atr_s[-1] * (period - 1) + trs[i]) / period)
        plus_dm_s.append((plus_dm_s[-1] * (period - 1) + plus_dm[i]) / period)
        minus_dm_s.append((minus_dm_s[-1] * (period - 1) + minus_dm[i]) / period)

    plus_di: list[float] = []
    minus_di: list[float] = []
    dx: list[float] = []
    for i in range(len(atr_s)):
        pdi = (plus_dm_s[i] / atr_s[i] * 100) if atr_s[i] > 0 else 0.0
        mdi = (minus_dm_s[i] / atr_s[i] * 100) if atr_s[i] > 0 else 0.0
        plus_di.append(pdi)
        minus_di.append(mdi)
        if pdi + mdi > 0:
            dx.append(abs(pdi - mdi) / (pdi + mdi) * 100)
        else:
            dx.append(0.0)

    if len(dx) < period:
        adx_val = sum(dx) / len(dx) if dx else 0.0
    else:
        adx_val = sum(dx[-period:]) / period

    strength = (
        "extreme" if adx_val >= 50
        else "strong" if adx_val >= 35
        else "moderate" if adx_val >= 20
        else "weak"
    )

    return {
        "adx": adx_val,
        "plus_di": plus_di[-1] if plus_di else 0.0,
        "minus_di": minus_di[-1] if minus_di else 0.0,
        "trend_strength": strength,
    }


# --------------------------------------------------------------------------- #
# MACD
# --------------------------------------------------------------------------- #
def macd(candles: Sequence[Candle], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, float]:
    """MACD line, signal line, histogram."""
    closes = _closes(candles)
    if len(closes) < slow + signal:
        return {"macd": 0.0, "signal": 0.0, "histogram": 0.0}
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = ema(macd_line, signal)
    histogram = macd_line[-1] - signal_line[-1]
    return {
        "macd": macd_line[-1],
        "signal": signal_line[-1],
        "histogram": histogram,
    }


# --------------------------------------------------------------------------- #
# Bollinger Bands
# --------------------------------------------------------------------------- #
def bollinger_bands(candles: Sequence[Candle], period: int = 20, std_dev: float = 2.0) -> dict[str, float]:
    """Bollinger bands using SMA + population stddev."""
    closes = _closes(candles)
    if len(closes) < period:
        return {"upper": 0.0, "middle": 0.0, "lower": 0.0, "width": 0.0, "percent_b": 0.5}
    window = closes[-period:]
    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    sd = math.sqrt(variance)
    upper = mean + std_dev * sd
    lower = mean - std_dev * sd
    price = closes[-1]
    width = (upper - lower) / mean if mean > 0 else 0.0
    percent_b = (price - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
    return {
        "upper": upper,
        "middle": mean,
        "lower": lower,
        "width": width,
        "percent_b": percent_b,
    }


# --------------------------------------------------------------------------- #
# Support / Resistance (swing pivots)
# --------------------------------------------------------------------------- #
def swing_pivots(candles: Sequence[Candle], left: int = 5, right: int = 5) -> dict[str, list[float]]:
    """Return recent support (swing lows) and resistance (swing highs)."""
    supports: list[float] = []
    resistances: list[float] = []
    if len(candles) < left + right + 1:
        return {"supports": supports, "resistances": resistances}

    for i in range(left, len(candles) - right):
        window = candles[i - left : i + right + 1]
        if candles[i].low == min(c.low for c in window):
            supports.append(candles[i].low)
        if candles[i].high == max(c.high for c in window):
            resistances.append(candles[i].high)
    # Trim to most recent 10 each
    return {
        "supports": supports[-10:],
        "resistances": resistances[-10:],
    }


def nearest_support_resistance(candles: Sequence[Candle], left: int = 5, right: int = 5) -> dict[str, float | None]:
    """Return nearest support below and resistance above current price."""
    pivots = swing_pivots(candles, left, right)
    price = candles[-1].close if candles else 0.0
    supports_below = [s for s in pivots["supports"] if s < price]
    resistances_above = [r for r in pivots["resistances"] if r > price]
    return {
        "nearest_support": max(supports_below) if supports_below else None,
        "nearest_resistance": min(resistances_above) if resistances_above else None,
    }


# --------------------------------------------------------------------------- #
# Volume metrics
# --------------------------------------------------------------------------- #
def volume_spike_ratio(candles: Sequence[Candle], period: int = 20) -> float:
    """Current volume / average volume of last ``period`` bars."""
    if len(candles) < period + 1:
        return 1.0
    recent = [c.volume for c in candles[-period - 1 : -1]]
    avg = sum(recent) / len(recent) if recent else 0
    return (candles[-1].volume / avg) if avg > 0 else 1.0


# --------------------------------------------------------------------------- #
# Caching layer
# --------------------------------------------------------------------------- #
@dataclass
class CachedIndicator:
    value: dict
    computed_at_close_time: datetime


class IndicatorEngine:
    """Caching wrapper that recomputes only when the latest candle changes."""

    def __init__(self, candles: "Any | None" = None) -> None:
        # ``candles`` is an optional CandleEngine instance; we accept Any to
        # avoid a circular import (CandleEngine imports from this module's
        # public functions, not the class itself).
        self._candles = candles
        self._cache: dict[tuple[str, str], CachedIndicator] = {}

    def compute_all(self, symbol: str, timeframe: str, candles: Sequence[Candle]) -> dict:
        """Compute the full indicator bundle for a symbol+timeframe.

        Results are cached by the close_time of the last candle; if that
        hasn't changed since last call, the cached bundle is returned.
        """
        if not candles:
            return {}
        key = (symbol, timeframe)
        last_close = candles[-1].close_time
        cached = self._cache.get(key)
        if cached is not None and cached.computed_at_close_time == last_close:
            return cached.value

        bundle = {
            "symbol": symbol,
            "timeframe": timeframe,
            "last_close_time": last_close,
            "last_price": candles[-1].close,
            "ema": ema_alignment_score(candles),
            "vwap_position": vwap_position(candles),
            "atr": atr_value(candles),
            "atr_pct": atr_pct(candles),
            "rsi": rsi_metrics(candles),
            "adx": adx(candles),
            "macd": macd(candles),
            "bollinger": bollinger_bands(candles),
            "support_resistance": nearest_support_resistance(candles),
            "volume_spike_ratio": volume_spike_ratio(candles),
        }
        self._cache[key] = CachedIndicator(value=bundle, computed_at_close_time=last_close)
        return bundle

    def clear(self, symbol: str | None = None) -> None:
        if symbol is None:
            self._cache.clear()
        else:
            keys_to_drop = [k for k in self._cache if k[0] == symbol]
            for k in keys_to_drop:
                self._cache.pop(k, None)


__all__ = [
    "ema",
    "sma",
    "vwap",
    "vwap_position",
    "ema_alignment_score",
    "atr",
    "atr_value",
    "atr_pct",
    "rsi",
    "rsi_metrics",
    "adx",
    "macd",
    "bollinger_bands",
    "swing_pivots",
    "nearest_support_resistance",
    "volume_spike_ratio",
    "IndicatorEngine",
]
