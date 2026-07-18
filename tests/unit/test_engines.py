"""Unit tests for OI, funding, volume, pressure engines."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.exchange.binance_rest import AggTrade, Candle
from app.funding.engine import FundingEngine
from app.market.data_engine import SymbolState
from app.open_interest.engine import OpenInterestEngine
from app.pressure.engine import PressureEngine
from app.volume.engine import VolumeEngine


def _make_oi_history(values: list[float]) -> list[dict]:
    """Build OI history dicts."""
    now = datetime.now(timezone.utc)
    return [
        {"timestamp": (now - timedelta(minutes=15 * (len(values) - i))).timestamp() * 1000,
         "openInterest": v, "openInterestValue": v * 100}
        for i, v in enumerate(values)
    ]


def test_oi_spike_detected():
    engine = OpenInterestEngine(spike_threshold_pct=5.0)
    # Steady then jump
    history = _make_oi_history([100, 100, 100, 100, 110])
    result = engine.analyze(history, price_now=100, price_1h_ago=100)
    assert result.spike
    assert result.delta_pct_1h > 5


def test_oi_purge_detected():
    engine = OpenInterestEngine(purge_threshold_pct=-5.0)
    history = _make_oi_history([100, 100, 100, 100, 90])
    result = engine.analyze(history, price_now=100, price_1h_ago=100)
    assert result.purge


def test_oi_new_longs_divergence():
    engine = OpenInterestEngine()
    history = _make_oi_history([100, 100, 100, 100, 110])
    result = engine.analyze(history, price_now=105, price_1h_ago=100)
    assert result.divergence == "NEW_LONGS"


def test_funding_extreme_long():
    engine = FundingEngine(extreme_positive_threshold=0.0005)
    history = [
        {"fundingRate": 0.0001, "fundingTime": 1},
        {"fundingRate": 0.0002, "fundingTime": 2},
        {"fundingRate": 0.0008, "fundingTime": 3},
    ]
    result = engine.analyze(history)
    assert result.regime == "EXTREME_LONG"


def test_funding_extreme_short():
    engine = FundingEngine(extreme_negative_threshold=-0.0003)
    history = [
        {"fundingRate": -0.0001, "fundingTime": 1},
        {"fundingRate": -0.0002, "fundingTime": 2},
        {"fundingRate": -0.0006, "fundingTime": 3},
    ]
    result = engine.analyze(history)
    assert result.regime == "EXTREME_SHORT"


def test_funding_shift_detected():
    engine = FundingEngine(shift_threshold=0.0001)
    history = [
        {"fundingRate": 0.0001, "fundingTime": 1},
        {"fundingRate": -0.0005, "fundingTime": 2},  # big shift
    ]
    result = engine.analyze(history)
    assert result.shift
    assert result.shift_direction == "DOWN"


def test_volume_spike_detected(make_candles):
    candles = make_candles(n=50)
    # Add a final candle with 5x volume
    last = candles[-1]
    import dataclasses

    big = dataclasses.replace(last, volume=last.volume * 5)
    candles[-1] = big
    engine = VolumeEngine(spike_threshold=2.0)
    result = engine.analyze(candles)
    assert result.spike_ratio > 2.0


def test_volume_climax_detected(make_candles):
    """A candle with extreme volume + large body should be flagged as climax."""
    import dataclasses

    candles = make_candles(n=100)
    # Inject one extreme candle at the end
    extreme = dataclasses.replace(
        candles[-1],
        volume=candles[-1].volume * 10,
        open=candles[-1].close * 0.98,
        close=candles[-1].close * 1.03,  # large bullish body
    )
    candles[-1] = extreme
    engine = VolumeEngine(climax_volume_percentile=95.0)
    result = engine.analyze(candles)
    assert result.climax
    assert result.climax_direction == "BULLISH"


def test_pressure_buy_dominance(make_candles):
    candles = make_candles(n=50)
    # Inflate taker buy volume to dominate
    import dataclasses

    candles = [dataclasses.replace(c, taker_buy_volume=c.volume * 0.7) for c in candles]
    state = SymbolState(symbol="X")
    state.bid_qty = 10.0
    state.ask_qty = 2.0
    engine = PressureEngine()
    result = engine.analyze(candles=candles, state=state)
    assert result.buy_pct > 0.5
    assert result.net_score > 0


def test_pressure_sell_dominance(make_candles):
    candles = make_candles(n=50)
    import dataclasses

    candles = [dataclasses.replace(c, taker_buy_volume=c.volume * 0.3) for c in candles]
    state = SymbolState(symbol="X")
    state.bid_qty = 2.0
    state.ask_qty = 10.0
    engine = PressureEngine()
    result = engine.analyze(candles=candles, state=state)
    assert result.buy_pct < 0.5
    assert result.net_score < 0
