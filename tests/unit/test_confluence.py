"""Unit tests for confluence engine."""

from __future__ import annotations

from datetime import datetime, timezone

from app.confluence.engine import ConfluenceEngine
from app.funding.engine import FundingResult
from app.liquidity.engine import LiquidityResult, LiquidityType, LiquidityPool, LiquiditySweep, FairValueGap, FVGType, OrderBlock, OrderBlockType
from app.market.data_engine import SymbolState
from app.open_interest.engine import OIResult
from app.pressure.engine import PressureResult
from app.smart_money.engine import SmartMoneyResult
from app.structure.market_structure import MarketStructureResult, StructureEvent, TrendBias
from app.structure.trend import MultiTimeframeTrend, TimeframeTrend
from app.volume.engine import VolumeResult


def _make_trend(bias: TrendBias = TrendBias.BULLISH) -> MultiTimeframeTrend:
    htf = TimeframeTrend("1h", bias, 0.8, 0.5, 35.0, "strong")
    mtf = TimeframeTrend("15m", bias, 0.7, 0.4, 25.0, "moderate")
    ltf = TimeframeTrend("5m", bias, 0.6, 0.3, 20.0, "moderate")
    return MultiTimeframeTrend(htf, mtf, ltf, overall_bias=bias, aligned=True, score=80)


def _make_market_structure(bias: TrendBias = TrendBias.BULLISH) -> MarketStructureResult:
    return MarketStructureResult(
        bias=bias,
        event=StructureEvent.BOS_BULL,
        hh_count=3, hl_count=2, lh_count=1, ll_count=0,
        strength=0.75,
    )


def test_neutral_inputs_yield_mid_score():
    engine = ConfluenceEngine()
    result = engine.compute()
    # All inputs None → score should be ~50 (neutral)
    assert 40 <= result.score <= 60
    assert result.direction == "NEUTRAL"


def test_all_bullish_inputs_yield_high_score():
    engine = ConfluenceEngine()
    sm = SmartMoneyResult(institutional_buying=0.8, institutional_selling=0.1, net_flow=0.7, score=70, summary="strong buy")
    pr = PressureResult(buy_pct=0.65, net_score=0.5)
    oi = OIResult(divergence="NEW_LONGS", spike=True)
    fund = FundingResult(regime="BEARISH_HEAT", current_rate=-0.0001)
    vol = VolumeResult(spike_ratio=2.5, climax=True, climax_direction="BULLISH")
    liq = LiquidityResult(
        pools=[],
        recent_sweeps=[LiquiditySweep(price=100, type=LiquidityType.SELL_SIDE, time=datetime.now(timezone.utc), wick_pct=0.5, recovered=True)],
        fvgs=[FairValueGap(start_time=datetime.now(timezone.utc), end_time=datetime.now(timezone.utc), upper=101, lower=100, type=FVGType.BULLISH)],
        order_blocks=[OrderBlock(start_time=datetime.now(timezone.utc), end_time=datetime.now(timezone.utc), high=100, low=99, type=OrderBlockType.BULLISH, mitigated=True)],
    )
    result = engine.compute(
        trend=_make_trend(TrendBias.BULLISH),
        market_structure=_make_market_structure(TrendBias.BULLISH),
        liquidity=liq,
        smart_money=sm,
        pressure=pr,
        oi=oi,
        funding=fund,
        volume=vol,
        indicators={
            "ema": {"score": 0.8},
            "vwap_position": 0.5,
            "atr_pct": 1.5,
            "adx": {"adx": 30, "plus_di": 35, "minus_di": 15},
            "bollinger": {"percent_b": 0.7},
            "support_resistance": {"nearest_support": 95, "nearest_resistance": 110},
            "last_price": 100,
            "rsi": {"value": 60, "slope": 5},
        },
    )
    assert result.score > 70
    assert result.direction == "BULLISH"


def test_all_bearish_inputs_yield_low_score():
    engine = ConfluenceEngine()
    sm = SmartMoneyResult(institutional_buying=0.1, institutional_selling=0.8, net_flow=-0.7, score=-70, summary="strong sell")
    pr = PressureResult(buy_pct=0.35, net_score=-0.5)
    oi = OIResult(divergence="NEW_SHORTS", spike=True)
    fund = FundingResult(regime="BULLISH_HEAT", current_rate=0.0001)
    vol = VolumeResult(spike_ratio=2.5, climax=True, climax_direction="BEARISH")
    liq = LiquidityResult(
        pools=[],
        recent_sweeps=[LiquiditySweep(price=100, type=LiquidityType.BUY_SIDE, time=datetime.now(timezone.utc), wick_pct=0.5, recovered=True)],
        fvgs=[FairValueGap(start_time=datetime.now(timezone.utc), end_time=datetime.now(timezone.utc), upper=100, lower=99, type=FVGType.BEARISH)],
        order_blocks=[OrderBlock(start_time=datetime.now(timezone.utc), end_time=datetime.now(timezone.utc), high=101, low=100, type=OrderBlockType.BEARISH, mitigated=True)],
    )
    result = engine.compute(
        trend=_make_trend(TrendBias.BEARISH),
        market_structure=_make_market_structure(TrendBias.BEARISH),
        liquidity=liq,
        smart_money=sm,
        pressure=pr,
        oi=oi,
        funding=fund,
        volume=vol,
        indicators={
            "ema": {"score": -0.8},
            "vwap_position": -0.5,
            "atr_pct": 1.5,
            "adx": {"adx": 30, "plus_di": 15, "minus_di": 35},
            "bollinger": {"percent_b": 0.3},
            "support_resistance": {"nearest_support": 95, "nearest_resistance": 110},
            "last_price": 100,
            "rsi": {"value": 40, "slope": -5},
        },
    )
    assert result.score < 30
    assert result.direction == "BEARISH"


def test_components_returned():
    engine = ConfluenceEngine()
    result = engine.compute()
    assert len(result.components) > 0
    assert all(hasattr(c, "name") for c in result.components)
