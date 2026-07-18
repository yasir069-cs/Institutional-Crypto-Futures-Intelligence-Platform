"""Risk management engine.

Computes entry, stop-loss, take-profit, position size and validates the
risk/reward ratio for any candidate setup. Implements the spec rules:

- **Intraday SL ≤ 3%** (configurable)
- **Swing SL ≤ 5%** (configurable)
- **Min RR ≥ 1:2**
- **Max risk per trade = 1%** of account (configurable)
- **ATR-based SL** = ATR × multiplier (default 1.5)
- **ATR-based TP** = ATR × multiplier (default 3.0)

If a setup violates hard limits, ``valid=False`` and the rejection reason
is set. The signal engine must not generate a BUY/SELL signal from an
invalid risk result.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.config import settings


class TradeStyle(str, Enum):
    INTRADAY = "INTRADAY"
    SWING = "SWING"


@dataclass
class RiskResult:
    valid: bool
    direction: str  # BUY / SELL
    entry: float
    stop_loss: float
    take_profit: float
    risk_pct: float  # price distance % (entry → SL)
    reward_pct: float
    risk_reward: float
    position_size: float  # in base asset
    position_value: float  # in quote currency
    risk_amount: float  # in quote currency
    reward_amount: float
    trade_style: TradeStyle
    rejection_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "direction": self.direction,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "risk_pct": round(self.risk_pct, 3),
            "reward_pct": round(self.reward_pct, 3),
            "risk_reward": round(self.risk_reward, 3),
            "position_size": round(self.position_size, 6),
            "position_value": round(self.position_value, 2),
            "risk_amount": round(self.risk_amount, 2),
            "reward_amount": round(self.reward_amount, 2),
            "trade_style": self.trade_style.value,
            "rejection_reason": self.rejection_reason,
        }


class RiskEngine:
    """Compute risk parameters for a candidate setup."""

    def __init__(
        self,
        account_balance: float | None = None,
        max_risk_pct: float | None = None,
        intraday_sl_max_pct: float | None = None,
        swing_sl_max_pct: float | None = None,
        min_rr: float | None = None,
        atr_sl_mult: float | None = None,
        atr_tp_mult: float | None = None,
    ) -> None:
        self._account = account_balance if account_balance is not None else settings.risk_account_balance
        self._max_risk_pct = max_risk_pct if max_risk_pct is not None else settings.risk_max_risk_per_trade_pct
        self._intraday_max = intraday_sl_max_pct if intraday_sl_max_pct is not None else settings.risk_intraday_sl_max_pct
        self._swing_max = swing_sl_max_pct if swing_sl_max_pct is not None else settings.risk_swing_sl_max_pct
        self._min_rr = min_rr if min_rr is not None else settings.risk_min_rr
        self._atr_sl = atr_sl_mult if atr_sl_mult is not None else settings.risk_atr_multiplier_sl
        self._atr_tp = atr_tp_mult if atr_tp_mult is not None else settings.risk_atr_multiplier_tp

    def compute(
        self,
        direction: str,  # BUY or SELL
        entry: float,
        atr: float,
        trade_style: TradeStyle = TradeStyle.INTRADAY,
        stop_loss_override: float | None = None,
        take_profit_override: float | None = None,
    ) -> RiskResult:
        """Compute risk parameters. Returns ``valid=False`` if hard limits breached."""
        direction = direction.upper()
        if direction not in ("BUY", "SELL"):
            return self._reject(direction, entry, 0.0, 0.0, 0.0, trade_style, "Invalid direction")
        if entry <= 0:
            return self._reject(direction, entry, 0.0, 0.0, 0.0, trade_style, "Invalid entry price")
        if atr < 0:
            return self._reject(direction, entry, 0.0, 0.0, 0.0, trade_style, "Invalid ATR")

        # Determine SL/TP
        if stop_loss_override is not None and stop_loss_override > 0:
            sl = stop_loss_override
        else:
            sl_offset = atr * self._atr_sl if atr > 0 else entry * 0.01
            sl = entry - sl_offset if direction == "BUY" else entry + sl_offset

        if take_profit_override is not None and take_profit_override > 0:
            tp = take_profit_override
        else:
            tp_offset = atr * self._atr_tp if atr > 0 else entry * 0.02
            tp = entry + tp_offset if direction == "BUY" else entry - tp_offset

        # Validate SL is on the correct side
        if direction == "BUY" and sl >= entry:
            return self._reject(direction, entry, sl, tp, atr, trade_style, "SL must be below entry for BUY")
        if direction == "SELL" and sl <= entry:
            return self._reject(direction, entry, sl, tp, atr, trade_style, "SL must be above entry for SELL")
        if direction == "BUY" and tp <= entry:
            return self._reject(direction, entry, sl, tp, atr, trade_style, "TP must be above entry for BUY")
        if direction == "SELL" and tp >= entry:
            return self._reject(direction, entry, sl, tp, atr, trade_style, "TP must be above entry for SELL")

        # Compute risk %, reward %, RR
        risk_dist = abs(entry - sl)
        reward_dist = abs(tp - entry)
        risk_pct = risk_dist / entry * 100
        reward_pct = reward_dist / entry * 100
        rr = reward_dist / risk_dist if risk_dist > 0 else 0.0

        # Validate SL limits
        sl_max = self._intraday_max if trade_style == TradeStyle.INTRADAY else self._swing_max
        if risk_pct > sl_max:
            return self._reject(
                direction, entry, sl, tp, atr, trade_style,
                f"SL {risk_pct:.2f}% exceeds {trade_style.value} limit {sl_max}%",
            )

        # Validate RR
        if rr < self._min_rr:
            return self._reject(
                direction, entry, sl, tp, atr, trade_style,
                f"RR {rr:.2f} below minimum {self._min_rr}",
            )

        # Position sizing: risk_amount = account * max_risk_pct
        risk_amount = self._account * (self._max_risk_pct / 100.0)
        # position_size (base asset) = risk_amount / risk_dist
        position_size = risk_amount / risk_dist if risk_dist > 0 else 0.0
        position_value = position_size * entry
        reward_amount = position_size * reward_dist

        return RiskResult(
            valid=True,
            direction=direction,
            entry=entry,
            stop_loss=sl,
            take_profit=tp,
            risk_pct=risk_pct,
            reward_pct=reward_pct,
            risk_reward=rr,
            position_size=position_size,
            position_value=position_value,
            risk_amount=risk_amount,
            reward_amount=reward_amount,
            trade_style=trade_style,
        )

    def _reject(
        self,
        direction: str,
        entry: float,
        sl: float,
        tp: float,
        atr: float,
        style: TradeStyle,
        reason: str,
    ) -> RiskResult:
        return RiskResult(
            valid=False,
            direction=direction,
            entry=entry,
            stop_loss=sl,
            take_profit=tp,
            risk_pct=0.0,
            reward_pct=0.0,
            risk_reward=0.0,
            position_size=0.0,
            position_value=0.0,
            risk_amount=0.0,
            reward_amount=0.0,
            trade_style=style,
            rejection_reason=reason,
        )


__all__ = ["RiskEngine", "RiskResult", "TradeStyle"]
