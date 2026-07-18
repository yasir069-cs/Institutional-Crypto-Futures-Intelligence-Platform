"""Unit tests for the indicator engine."""

from __future__ import annotations

from app.market.indicator_engine import (
    atr,
    atr_value,
    bollinger_bands,
    ema,
    ema_alignment_score,
    macd,
    nearest_support_resistance,
    rsi,
    rsi_metrics,
    sma,
    vwap,
    vwap_position,
    volume_spike_ratio,
)


def test_ema_returns_aligned_list(make_candles):
    candles = make_candles(n=100)
    closes = [c.close for c in candles]
    out = ema(closes, 21)
    assert len(out) == len(closes)
    # First value equals input
    assert out[0] == closes[0]
    # Subsequent values are bounded by min/max of input
    assert min(closes) - 1 <= out[-1] <= max(closes) + 1


def test_sma_first_value(make_candles):
    candles = make_candles(n=50)
    closes = [c.close for c in candles]
    out = sma(closes, 20)
    assert len(out) == len(closes)
    # First SMA value equals input
    assert out[0] == closes[0]
    # At index 19, SMA should be average of first 20 values
    expected = sum(closes[:20]) / 20
    assert abs(out[19] - expected) < 1e-6


def test_vwap_within_price_range(make_candles):
    candles = make_candles(n=50)
    out = vwap(candles)
    assert len(out) == len(candles)
    # VWAP should be within range of high/low across the series
    overall_low = min(c.low for c in candles)
    overall_high = max(c.high for c in candles)
    assert overall_low - 1 <= out[-1] <= overall_high + 1


def test_vwap_position_bounded(make_candles):
    candles = make_candles(n=100)
    pos = vwap_position(candles)
    assert -1.0 <= pos <= 1.0


def test_atr_positive(make_candles):
    candles = make_candles(n=100)
    out = atr(candles, 14)
    assert len(out) == len(candles)
    # ATR should be positive for volatile series
    assert out[-1] > 0
    assert atr_value(candles) > 0


def test_rsi_bounded(make_candles):
    candles = make_candles(n=100)
    out = rsi(candles, 14)
    assert len(out) == len(candles)
    # RSI must be in [0, 100]
    for v in out:
        assert 0 <= v <= 100


def test_rsi_metrics_structure(make_candles):
    candles = make_candles(n=100)
    m = rsi_metrics(candles, lookback=10)
    assert "value" in m
    assert "slope" in m
    assert "momentum" in m
    assert "acceleration" in m
    assert 0 <= m["value"] <= 100


def test_ema_alignment_score_bounds(make_candles):
    candles = make_candles(n=250)
    result = ema_alignment_score(candles)
    assert -1.0 <= result["score"] <= 1.0


def test_macd_returns_dict(make_candles):
    candles = make_candles(n=100)
    result = macd(candles)
    assert "macd" in result
    assert "signal" in result
    assert "histogram" in result


def test_bollinger_bands(make_candles):
    candles = make_candles(n=50)
    bb = bollinger_bands(candles)
    assert bb["upper"] > bb["middle"] > bb["lower"]
    assert 0 <= bb["percent_b"] <= 1.0  # may be slightly outside if price extreme


def test_support_resistance(make_candles):
    candles = make_candles(n=100)
    sr = nearest_support_resistance(candles)
    # At least one should exist for a varied series
    if sr["nearest_support"] and sr["nearest_resistance"]:
        assert sr["nearest_support"] < sr["nearest_resistance"]


def test_volume_spike_ratio(make_candles):
    candles = make_candles(n=50)
    ratio = volume_spike_ratio(candles, period=20)
    assert ratio > 0


def test_indicator_engine_cache(make_candles):
    from app.market.indicator_engine import IndicatorEngine

    engine = IndicatorEngine()
    candles = make_candles(n=100)
    bundle1 = engine.compute_all("TESTUSDT", "1h", candles)
    bundle2 = engine.compute_all("TESTUSDT", "1h", candles)
    # Cached: should be the same dict object (or at least equal)
    assert bundle1["last_close_time"] == bundle2["last_close_time"]
    assert bundle1["last_price"] == bundle2["last_price"]
