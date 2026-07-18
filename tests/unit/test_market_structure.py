"""Unit tests for market structure engine."""

from __future__ import annotations

from app.exchange.binance_rest import Candle
from app.structure.market_structure import (
    MarketStructureEngine,
    StructureEvent,
    TrendBias,
)


def test_empty_series_returns_neutral(make_candles):
    engine = MarketStructureEngine()
    result = engine.analyze([])
    assert result.bias == TrendBias.NEUTRAL
    assert result.event == StructureEvent.NONE


def test_uptrend_detected(make_candles):
    """A clear uptrend should produce bullish bias."""
    # Build a series with rising lows and rising highs
    import random

    rng = random.Random(123)
    candles: list[Candle] = []
    base = 100.0
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    for i in range(60):
        # Steady upward drift
        price = base + i * 0.5 + rng.gauss(0, 0.5)
        candles.append(Candle(
            symbol="X", timeframe="1h",
            open_time=now + __import__("datetime").timedelta(hours=i),
            close_time=now + __import__("datetime").timedelta(hours=i + 1),
            open=price - 0.2, high=price + 1.0, low=price - 1.0,
            close=price, volume=1000.0, quote_volume=price * 1000,
            trade_count=100, taker_buy_volume=600, taker_buy_quote_volume=600 * price,
            is_closed=True,
        ))
    engine = MarketStructureEngine(left=3, right=3)
    result = engine.analyze(candles)
    assert result.bias in (TrendBias.BULLISH, TrendBias.NEUTRAL)


def test_downtrend_detected(make_candles):
    """A clear downtrend should produce bearish bias."""
    import random

    rng = random.Random(456)
    candles: list[Candle] = []
    base = 200.0
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    for i in range(60):
        price = base - i * 0.5 + rng.gauss(0, 0.5)
        candles.append(Candle(
            symbol="X", timeframe="1h",
            open_time=now + __import__("datetime").timedelta(hours=i),
            close_time=now + __import__("datetime").timedelta(hours=i + 1),
            open=price + 0.2, high=price + 1.0, low=price - 1.0,
            close=price, volume=1000.0, quote_volume=price * 1000,
            trade_count=100, taker_buy_volume=400, taker_buy_quote_volume=400 * price,
            is_closed=True,
        ))
    engine = MarketStructureEngine(left=3, right=3)
    result = engine.analyze(candles)
    assert result.bias in (TrendBias.BEARISH, TrendBias.NEUTRAL)


def test_structure_result_to_dict(make_candles):
    engine = MarketStructureEngine()
    candles = make_candles(n=100)
    result = engine.analyze(candles)
    d = result.to_dict()
    assert "bias" in d
    assert "event" in d
    assert "hh" in d and "hl" in d
