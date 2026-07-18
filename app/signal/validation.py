"""Signal validation engine — pre-AI gate enforcing all safety rules.

The signal validation engine runs *before* AI validation and enforces
hard institutional rules from the spec. If any rule fails, the candidate
becomes ``REJECT`` or ``HOLD``/``WATCHLIST`` rather than being sent to AI.

Rules
-----
- Confluence < 75 → HOLD (do not progress to AI)
- HTF disagrees with direction → HOLD
- Smart Money confirmation missing → WATCHLIST
- RR < 1:2 → REJECT
- SL > intraday/swing limit → REJECT
- Conflicting signals (multiple timeframes disagree) → HOLD
- Stop Loss within limits → otherwise REJECT
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.config import settings
from app.confluence.engine import ConfluenceResult
from app.risk.engine import RiskResult
from app.smart_money.engine import SmartMoneyResult
from app.structure.market_structure import TrendBias
from app.structure.trend import MultiTimeframeTrend


class SignalVerdict(str, Enum):
    REJECT = "REJECT"
    HOLD = "HOLD"
    WATCHLIST = "WATCHLIST"
    PROCEED = "PROCEED"  # safe to send to AI


@dataclass
class ValidationResult:
    verdict: SignalVerdict
    reasons: list[str]
    can_send_to_ai: bool

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "reasons": self.reasons,
            "can_send_to_ai": self.can_send_to_ai,
        }


class SignalValidationEngine:
    """Pre-AI gate: enforce institutional safety rules."""

    def validate(
        self,
        confluence: ConfluenceResult,
        trend: MultiTimeframeTrend,
        smart_money: SmartMoneyResult,
        risk: RiskResult,
        direction: str,  # BUY / SELL
    ) -> ValidationResult:
        reasons: list[str] = []

        # 1. Confluence threshold
        if confluence.score < settings.stage3_min_confluence:
            if confluence.score < settings.stage2_min_confluence:
                return ValidationResult(
                    verdict=SignalVerdict.REJECT,
                    reasons=[f"Confluence {confluence.score} below stage2 minimum {settings.stage2_min_confluence}"],
                    can_send_to_ai=False,
                )
            reasons.append(f"Confluence {confluence.score} below stage3 minimum {settings.stage3_min_confluence}")
            return ValidationResult(verdict=SignalVerdict.HOLD, reasons=reasons, can_send_to_ai=False)

        # 2. Risk validity
        if not risk.valid:
            return ValidationResult(
                verdict=SignalVerdict.REJECT,
                reasons=[f"Risk invalid: {risk.rejection_reason}"],
                can_send_to_ai=False,
            )

        # 3. RR threshold
        if risk.risk_reward < settings.ai_skip_if_rr_below:
            return ValidationResult(
                verdict=SignalVerdict.REJECT,
                reasons=[f"RR {risk.risk_reward:.2f} below minimum {settings.ai_skip_if_rr_below}"],
                can_send_to_ai=False,
            )

        # 4. HTF agreement
        htf_agrees = (
            (direction == "BUY" and trend.htf.bias == TrendBias.BULLISH)
            or (direction == "SELL" and trend.htf.bias == TrendBias.BEARISH)
        )
        if not htf_agrees:
            if trend.htf.bias == TrendBias.NEUTRAL:
                reasons.append(f"HTF neutral (need clear {direction} bias)")
                return ValidationResult(verdict=SignalVerdict.HOLD, reasons=reasons, can_send_to_ai=False)
            reasons.append(f"HTF {trend.htf.bias.value} disagrees with {direction}")
            return ValidationResult(verdict=SignalVerdict.HOLD, reasons=reasons, can_send_to_ai=False)

        # 5. Smart money confirmation
        sm_confirms = (
            (direction == "BUY" and smart_money.net_flow > 0.15)
            or (direction == "SELL" and smart_money.net_flow < -0.15)
        )
        if not sm_confirms:
            reasons.append(f"Smart money not confirming {direction} (flow {smart_money.net_flow:+.2f})")
            return ValidationResult(verdict=SignalVerdict.WATCHLIST, reasons=reasons, can_send_to_ai=False)

        # 6. Multi-TF alignment (LTF cannot override HTF, but should not oppose)
        ltf_opposes = (
            (direction == "BUY" and trend.ltf.bias == TrendBias.BEARISH)
            or (direction == "SELL" and trend.ltf.bias == TrendBias.BULLISH)
        )
        if ltf_opposes:
            reasons.append(f"LTF {trend.ltf.bias.value} opposes {direction} (timing off)")
            return ValidationResult(verdict=SignalVerdict.HOLD, reasons=reasons, can_send_to_ai=False)

        # All checks passed — proceed to AI
        reasons.append("All pre-AI validations passed")
        return ValidationResult(verdict=SignalVerdict.PROCEED, reasons=reasons, can_send_to_ai=True)


__all__ = ["SignalValidationEngine", "ValidationResult", "SignalVerdict"]
