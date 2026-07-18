"""Unit tests for risk engine."""

from __future__ import annotations

from app.risk.engine import RiskEngine, TradeStyle


def test_valid_buy_setup():
    engine = RiskEngine(account_balance=10_000, max_risk_pct=1.0, atr_sl_mult=1.5, atr_tp_mult=3.0)
    result = engine.compute(direction="BUY", entry=100.0, atr=1.0)
    assert result.valid
    assert result.direction == "BUY"
    # SL = 100 - 1.5 = 98.5
    assert abs(result.stop_loss - 98.5) < 1e-6
    # TP = 100 + 3.0 = 103.0
    assert abs(result.take_profit - 103.0) < 1e-6
    # RR = 3 / 1.5 = 2.0
    assert abs(result.risk_reward - 2.0) < 1e-6
    # Risk amount = 1% of 10000 = 100
    assert abs(result.risk_amount - 100.0) < 1e-6
    # Position size = 100 / 1.5 = 66.67
    assert abs(result.position_size - 100 / 1.5) < 1e-3


def test_valid_sell_setup():
    engine = RiskEngine(account_balance=10_000)
    result = engine.compute(direction="SELL", entry=100.0, atr=1.0)
    assert result.valid
    assert result.stop_loss > 100  # above entry for SELL
    assert result.take_profit < 100  # below entry for SELL


def test_reject_invalid_direction():
    engine = RiskEngine()
    result = engine.compute(direction="SIDEWAYS", entry=100.0, atr=1.0)
    assert not result.valid
    assert "direction" in result.rejection_reason.lower()


def test_reject_excessive_sl_intraday():
    # 5% SL on intraday (max 3%) → reject
    engine = RiskEngine(intraday_sl_max_pct=3.0, atr_sl_mult=10.0)  # 10x ATR = 10% SL
    result = engine.compute(direction="BUY", entry=100.0, atr=1.0)
    assert not result.valid
    assert "exceeds" in result.rejection_reason.lower()


def test_reject_low_rr():
    # RR 1:1 → reject
    engine = RiskEngine(min_rr=2.0, atr_sl_mult=1.5, atr_tp_mult=1.5)
    result = engine.compute(direction="BUY", entry=100.0, atr=1.0)
    assert not result.valid
    assert "rr" in result.rejection_reason.lower() or "below" in result.rejection_reason.lower()


def test_sl_on_wrong_side_rejected():
    engine = RiskEngine()
    result = engine.compute(direction="BUY", entry=100.0, atr=1.0, stop_loss_override=105.0)
    assert not result.valid


def test_to_dict_structure():
    engine = RiskEngine()
    result = engine.compute(direction="BUY", entry=100.0, atr=1.0)
    d = result.to_dict()
    assert "valid" in d
    assert "entry" in d
    assert "stop_loss" in d
    assert "take_profit" in d
    assert "risk_reward" in d
