"""Unit tests for liquidity engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.exchange.binance_rest import Candle
from app.liquidity.engine import LiquidityEngine, LiquidityType, FVGType, OrderBlockType


def _make_candle(i: int, o: float, h: float, l: float, c: float, vol: float = 1000.0) -> Candle:
    now = datetime.now(timezone.utc)
    return Candle(
        symbol="X", timeframe="1h",
        open_time=now + timedelta(hours=i),
        close_time=now + timedelta(hours=i + 1),
        open=o, high=h, low=l, close=c,
        volume=vol, quote_volume=vol * c,
        trade_count=100, taker_buy_volume=vol * 0.5,
        taker_buy_quote_volume=vol * c * 0.5,
        is_closed=True,
    )


def test_empty_candles_returns_empty_result():
    engine = LiquidityEngine()
    result = engine.analyze([])
    assert len(result.pools) == 0
    assert len(result.fvgs) == 0


def test_bullish_fvg_detected():
    # Need ≥10 candles for the engine; the FVG is at the end
    base = [_make_candle(i, 100, 101, 99, 100) for i in range(10)]
    # Now add the FVG sequence: candle 1 high=100, candle 3 low=102
    base += [
        _make_candle(10, 95, 100, 94, 99),
        _make_candle(11, 99, 101, 98, 100),
        _make_candle(12, 102, 105, 102, 104),  # gap up — bullish FVG
    ]
    engine = LiquidityEngine()
    result = engine.analyze(base)
    assert any(f.type == FVGType.BULLISH for f in result.fvgs)


def test_bearish_fvg_detected():
    base = [_make_candle(i, 100, 101, 99, 100) for i in range(10)]
    base += [
        _make_candle(10, 101, 102, 100, 99),
        _make_candle(11, 99, 100, 97, 98),
        _make_candle(12, 96, 98, 95, 95),  # gap down — bearish FVG
    ]
    engine = LiquidityEngine()
    result = engine.analyze(base)
    assert any(f.type == FVGType.BEARISH for f in result.fvgs)


def test_bullish_order_block_detected():
    # Need ≥10 candles; the OB pattern is at the end
    base = [_make_candle(i, 100, 101, 99, 100) for i in range(10)]
    # Add a down candle followed by a big up move
    base += [
        _make_candle(11, 100, 102, 99, 99.5, vol=500),  # down candle (close < open)
        _make_candle(12, 99.5, 105, 99, 104, vol=2000),  # big up move
    ]
    engine = LiquidityEngine(min_ob_displacement_pct=0.5)
    result = engine.analyze(base)
    # The down candle should be marked as a bullish OB
    assert any(ob.type == OrderBlockType.BULLISH for ob in result.order_blocks)


def test_to_dict_structure():
    engine = LiquidityEngine()
    candles = [_make_candle(i, 100, 101, 99, 100) for i in range(20)]
    result = engine.analyze(candles)
    d = result.to_dict()
    assert "pool_count" in d
    assert "sweep_count" in d
    assert "fvg_count" in d
    assert "order_blocks" in d
