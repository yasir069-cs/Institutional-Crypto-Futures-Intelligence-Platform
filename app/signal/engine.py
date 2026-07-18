"""Signal engine — final signal generation (Type A/B/C/D)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from app.ai.validation_engine import AIValidationResult
from app.confluence.engine import ConfluenceResult
from app.risk.engine import RiskResult
from app.smart_money.engine import SmartMoneyResult
from app.structure.market_structure import MarketStructureResult, StructureEvent
from app.structure.trend import MultiTimeframeTrend


class SignalType(str, Enum):
    """Per spec."""

    TYPE_A = "A"  # Early Smart Money Alert
    TYPE_B = "B"  # Bottom Detection
    TYPE_C = "C"  # Top Detection
    TYPE_D = "D"  # BUY / SELL Confirmation


class SignalDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    WATCHLIST = "WATCHLIST"
    HOLD = "HOLD"
    REJECT = "REJECT"


@dataclass
class Signal:
    id: str
    signal_type: SignalType
    direction: SignalDirection
    symbol: str
    entry: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    confidence: float
    confluence_score: int
    trend: MultiTimeframeTrend
    market_structure: MarketStructureResult
    smart_money: SmartMoneyResult
    confluence: ConfluenceResult
    risk: RiskResult
    ai: AIValidationResult | None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.direction in (SignalDirection.BUY, SignalDirection.SELL)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "signal_type": self.signal_type.value,
            "direction": self.direction.value,
            "symbol": self.symbol,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "risk_reward": round(self.risk_reward, 3),
            "confidence": round(self.confidence, 3),
            "confluence_score": self.confluence_score,
            "trend": self.trend.to_dict(),
            "market_structure": self.market_structure.to_dict(),
            "smart_money": self.smart_money.to_dict(),
            "confluence": self.confluence.to_dict(),
            "risk": self.risk.to_dict(),
            "ai": self.ai.to_dict() if self.ai else None,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }


class SignalEngine:
    """Generate signals based on AI validation result and setup context."""

    def generate(
        self,
        symbol: str,
        direction: str,
        trend: MultiTimeframeTrend,
        market_structure: MarketStructureResult,
        smart_money: SmartMoneyResult,
        confluence: ConfluenceResult,
        risk: RiskResult,
        ai: AIValidationResult | None,
    ) -> Signal:
        """Generate a signal from validated components."""
        sig_direction = SignalDirection(direction.upper())
        signal_type = self._classify_signal_type(market_structure, smart_money, ai, sig_direction)

        confidence = self._compute_confidence(confluence, smart_money, ai)
        sid = f"{symbol}-{signal_type.value}-{int(datetime.now(timezone.utc).timestamp())}"

        return Signal(
            id=sid,
            signal_type=signal_type,
            direction=sig_direction,
            symbol=symbol,
            entry=risk.entry,
            stop_loss=risk.stop_loss,
            take_profit=risk.take_profit,
            risk_reward=risk.risk_reward,
            confidence=confidence,
            confluence_score=confluence.score,
            trend=trend,
            market_structure=market_structure,
            smart_money=smart_money,
            confluence=confluence,
            risk=risk,
            ai=ai,
            metadata={
                "trend_aligned": trend.aligned,
                "market_structure_event": market_structure.event.value,
                "smart_money_signals": smart_money.signals,
            },
        )

    # ------------------------------------------------------------------ #
    # Signal type classification (per spec)
    # ------------------------------------------------------------------ #
    def _classify_signal_type(
        self,
        ms: MarketStructureResult,
        sm: SmartMoneyResult,
        ai: AIValidationResult | None,
        direction: SignalDirection,
    ) -> SignalType:
        """Classify as Type A/B/C/D per spec.

        - TYPE A: Early Smart Money Alert (CHOCH + smart money flow shift)
        - TYPE B: Bottom Detection (sell-side sweep recovery + bullish flow)
        - TYPE C: Top Detection (buy-side sweep recovery + bearish flow)
        - TYPE D: BUY/SELL Confirmation (BOS + aligned trend)
        """
        # Use the smart money signals list to detect sweep recoveries
        sm_signals = sm.signals or []
        has_buy_sweep_recovery = "buy_side_sweep_recovery" in sm_signals
        has_sell_sweep_recovery = "sell_side_sweep_recovery" in sm_signals

        # CHOCH = reversal event → Type A
        if ms.event in (StructureEvent.CHOCH_BULL, StructureEvent.CHOCH_BEAR):
            return SignalType.TYPE_A

        # Type B: Bottom Detection
        if has_buy_sweep_recovery and direction == SignalDirection.BUY:
            return SignalType.TYPE_B

        # Type C: Top Detection
        if has_sell_sweep_recovery and direction == SignalDirection.SELL:
            return SignalType.TYPE_C

        # Type D: BUY/SELL Confirmation (default for BOS / continuation)
        return SignalType.TYPE_D

    # ------------------------------------------------------------------ #
    # Confidence computation
    # ------------------------------------------------------------------ #
    def _compute_confidence(
        self,
        confluence: ConfluenceResult,
        smart_money: SmartMoneyResult,
        ai: AIValidationResult | None,
    ) -> float:
        """Blend confluence, smart money, and AI confidence."""
        # Base: confluence score normalized to 0..1
        base = confluence.score / 100.0
        # Adjust by smart money conviction
        sm_factor = abs(smart_money.net_flow) * 0.2
        # If AI validated, blend in AI confidence
        if ai is not None and ai.ai_decision in ("BUY", "SELL"):
            ai_factor = ai.confidence * 0.4
            return min(1.0, base * 0.4 + sm_factor * 0.2 + ai_factor)
        return min(1.0, base * 0.7 + sm_factor * 0.3)


__all__ = ["SignalEngine", "Signal", "SignalType", "SignalDirection"]
