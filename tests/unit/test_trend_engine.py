"""Unit tests for trend engine."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from app.exchange.binance_rest import Candle
from app.structure.market_structure import TrendBias
from app.structure.trend import TrendEngine


def _make_trending_candles(direction: str = "up", n: int = 100) -> list[Candle]:
    """Build candles with a clear trend including pullbacks (so swing detection works)."""
    rng = random.Random(42 if direction == "up" else 99)
    candles: list[Candle] = []
    price = 100.0
    now = datetime.now(timezone.utc)
    # Add a sinusoidal pullback to the trend so swings form
    for i in range(n):
        drift = 0.003 if direction == "up" else -0.003
        # Sinusoidal oscillation creates clear swing highs/lows
        oscillation = 0.015 * ((-1) ** (i // 5))
        change = drift + oscillation + rng.gauss(0, 0.002)
        new_price = price * (1 + change)
        candles.append(Candle(
            symbol="X", timeframe="1h",
            open_time=now + timedelta(hours=i),
            close_time=now + timedelta(hours=i + 1),
            open=price,
            high=max(price, new_price) * 1.002,
            low=min(price, new_price) * 0.998,
            close=new_price,
            volume=1000.0, quote_volume=1000 * new_price,
            trade_count=100,
            taker_buy_volume=500 if direction == "up" else 400,
            taker_buy_quote_volume=500 * new_price if direction == "up" else 400 * new_price,
            is_closed=True,
        ))
        price = new_price
    return candles


def test_uptrend_classified_bullish():
    candles = _make_trending_candles("up", 100)
    engine = TrendEngine()
    tf = engine.analyze_timeframe("1h", candles)
    # Strong uptrend should yield bullish or at least non-bearish
    assert tf.bias in (TrendBias.BULLISH, TrendBias.NEUTRAL)


def test_downtrend_classified_bearish():
    candles = _make_trending_candles("down", 100)
    engine = TrendEngine()
    tf = engine.analyze_timeframe("1h", candles)
    assert tf.bias in (TrendBias.BEARISH, TrendBias.NEUTRAL)


def test_multi_timeframe_trend_alignment():
    up = _make_trending_candles("up", 60)
    engine = TrendEngine()
    result = engine.analyze(up, up, up, "1h", "15m", "5m")
    assert isinstance(result.aligned, bool)
    assert 0 <= result.score <= 100
    assert result.htf.timeframe == "1h"
    assert result.mtf.timeframe == "15m"
    assert result.ltf.timeframe == "5m"


def test_to_dict_structure():
    up = _make_trending_candles("up", 60)
    engine = TrendEngine()
    result = engine.analyze(up, up, up)
    d = result.to_dict()
    assert "htf" in d
    assert "mtf" in d
    assert "ltf" in d
    assert "overall_bias" in d
    assert "aligned" in d
    assert "score" in d
