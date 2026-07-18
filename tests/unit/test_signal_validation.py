"""Unit tests for signal validation engine."""

from __future__ import annotations

import pytest

from app.confluence.engine import ConfluenceResult
from app.risk.engine import RiskResult, TradeStyle
from app.signal.validation import SignalValidationEngine, SignalVerdict
from app.smart_money.engine import SmartMoneyResult
from app.structure.market_structure import TrendBias
from app.structure.trend import MultiTimeframeTrend, TimeframeTrend


def _make_trend(htf_bias=TrendBias.BULLISH) -> MultiTimeframeTrend:
    htf = TimeframeTrend("1h", htf_bias, 0.8, 0.5, 35.0, "strong")
    mtf = TimeframeTrend("15m", htf_bias, 0.7, 0.4, 25.0, "moderate")
    ltf = TimeframeTrend("5m", htf_bias, 0.6, 0.3, 20.0, "moderate")
    return MultiTimeframeTrend(htf, mtf, ltf, overall_bias=htf_bias, aligned=True, score=80)


def _make_risk(valid=True, rr=2.5) -> RiskResult:
    return RiskResult(
        valid=valid,
        direction="BUY",
        entry=100, stop_loss=98.5, take_profit=103.0,
        risk_pct=1.5, reward_pct=3.0, risk_reward=rr,
        position_size=66.67, position_value=6667,
        risk_amount=100, reward_amount=200,
        trade_style=TradeStyle.INTRADAY,
    )


def test_low_confluence_returns_hold():
    """Confluence between stage2_min (70) and stage3_min (75) → HOLD."""
    engine = SignalValidationEngine()
    result = engine.validate(
        confluence=ConfluenceResult(score=72, direction="BULLISH"),
        trend=_make_trend(),
        smart_money=SmartMoneyResult(net_flow=0.5),
        risk=_make_risk(),
        direction="BUY",
    )
    assert result.verdict == SignalVerdict.HOLD
    assert not result.can_send_to_ai


def test_very_low_confluence_returns_reject():
    """Confluence below stage2_min (70) → REJECT."""
    engine = SignalValidationEngine()
    result = engine.validate(
        confluence=ConfluenceResult(score=50, direction="BULLISH"),
        trend=_make_trend(),
        smart_money=SmartMoneyResult(net_flow=0.5),
        risk=_make_risk(),
        direction="BUY",
    )
    assert result.verdict == SignalVerdict.REJECT
    assert not result.can_send_to_ai


def test_invalid_risk_returns_reject():
    engine = SignalValidationEngine()
    result = engine.validate(
        confluence=ConfluenceResult(score=85, direction="BULLISH"),
        trend=_make_trend(),
        smart_money=SmartMoneyResult(net_flow=0.5),
        risk=_make_risk(valid=False),
        direction="BUY",
    )
    assert result.verdict == SignalVerdict.REJECT


def test_low_rr_returns_reject():
    engine = SignalValidationEngine()
    result = engine.validate(
        confluence=ConfluenceResult(score=85, direction="BULLISH"),
        trend=_make_trend(),
        smart_money=SmartMoneyResult(net_flow=0.5),
        risk=_make_risk(rr=1.5),
        direction="BUY",
    )
    assert result.verdict == SignalVerdict.REJECT


def test_htf_disagrees_returns_hold():
    engine = SignalValidationEngine()
    result = engine.validate(
        confluence=ConfluenceResult(score=85, direction="BULLISH"),
        trend=_make_trend(TrendBias.BEARISH),
        smart_money=SmartMoneyResult(net_flow=0.5),
        risk=_make_risk(),
        direction="BUY",
    )
    assert result.verdict == SignalVerdict.HOLD


def test_smart_money_missing_returns_watchlist():
    engine = SignalValidationEngine()
    result = engine.validate(
        confluence=ConfluenceResult(score=85, direction="BULLISH"),
        trend=_make_trend(TrendBias.BULLISH),
        smart_money=SmartMoneyResult(net_flow=0.0),
        risk=_make_risk(),
        direction="BUY",
    )
    assert result.verdict == SignalVerdict.WATCHLIST


def test_all_checks_pass_proceeds():
    engine = SignalValidationEngine()
    result = engine.validate(
        confluence=ConfluenceResult(score=85, direction="BULLISH"),
        trend=_make_trend(TrendBias.BULLISH),
        smart_money=SmartMoneyResult(net_flow=0.5),
        risk=_make_risk(),
        direction="BUY",
    )
    assert result.verdict == SignalVerdict.PROCEED
    assert result.can_send_to_ai
