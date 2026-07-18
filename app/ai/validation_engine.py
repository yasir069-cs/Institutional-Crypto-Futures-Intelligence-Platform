"""AI validation engine — orchestrates the Stage 3 AI validation step.

Receives premium setups (3-5 per scan) from Stage 2 and:
1. Constructs a structured market context prompt
2. Sends it through the :class:`LLMProviderLayer`
3. Applies AI safety rules (confluence < 75 → HOLD, etc.)
4. Stores the AI decision in the database
5. Returns a final :class:`AIDecision` for the signal engine

The AI is ONLY the final decision-maker. It never scans markets, computes
indicators, or filters candidates — all of that is done before AI is called.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.ai.provider_layer import LLMProviderLayer, ProviderResponse
from app.config import settings
from app.confluence.engine import ConfluenceResult
from app.core.logging import get_logger
from app.db.models import AIDecision as AIDecisionModel
from app.db.session import get_session
from app.db.repositories import AIDecisionRepository
from app.risk.engine import RiskResult
from app.smart_money.engine import SmartMoneyResult
from app.structure.market_structure import MarketStructureResult
from app.structure.trend import MultiTimeframeTrend

log = get_logger(__name__)


@dataclass
class SetupContext:
    """Container for all data passed to AI validation."""

    symbol: str
    direction: str  # BUY / SELL
    price: float
    trend: MultiTimeframeTrend
    market_structure: MarketStructureResult
    smart_money: SmartMoneyResult
    confluence: ConfluenceResult
    risk: RiskResult
    liquidity_summary: str = ""
    pressure_summary: str = ""
    funding_summary: str = ""
    oi_summary: str = ""
    volume_summary: str = ""
    indicators_summary: dict = field(default_factory=dict)

    def to_market_snapshot(self) -> dict[str, Any]:
        """Snapshot used for cache key + invalidation."""
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "price": self.price,
            "confluence_score": self.confluence.score,
            "trend": {"overall_bias": self.trend.overall_bias.value},
            "market_structure": {"bias": self.market_structure.bias.value},
            "smart_money": {"net_flow": self.smart_money.net_flow},
        }


@dataclass
class AIValidationResult:
    setup_id: str
    symbol: str
    direction: str
    ai_decision: str  # BUY / SELL / WATCHLIST / HOLD / REJECT
    confidence: float
    probability: float
    trade_quality: str
    risk_level: str
    reasoning: str
    provider: str
    model: str
    latency_ms: int
    cached: bool
    safety_overrides: list[str] = field(default_factory=list)
    stored_decision_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "setup_id": self.setup_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "ai_decision": self.ai_decision,
            "confidence": round(self.confidence, 3),
            "probability": round(self.probability, 3),
            "trade_quality": self.trade_quality,
            "risk_level": self.risk_level,
            "reasoning": self.reasoning,
            "provider": self.provider,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "cached": self.cached,
            "safety_overrides": self.safety_overrides,
            "stored_decision_id": self.stored_decision_id,
        }


class AIValidationEngine:
    """Stage 3 AI validator — the only place AI is invoked."""

    SYSTEM_PROMPT = (
        "You are the senior decision-maker on an institutional crypto futures trading desk. "
        "Your job is to validate ONLY the highest-quality setups that have already passed "
        "mathematical scanning (Stage 1) and rule-based Smart Money filtering (Stage 2). "
        "You must behave like an experienced institutional trader: "
        "approve only exceptional opportunities, reject mediocre trades, and prioritize "
        "precision over quantity.\n\n"
        "OUTPUT REQUIREMENTS (STRICT):\n"
        "Respond with a single JSON object and nothing else. Schema:\n"
        "{\n"
        "  \"decision\": \"BUY\" | \"SELL\" | \"WATCHLIST\" | \"HOLD\" | \"REJECT\",\n"
        "  \"confidence\": 0.0-1.0,\n"
        "  \"probability\": 0.0-1.0,\n"
        "  \"trade_quality\": \"A\" | \"B\" | \"C\",\n"
        "  \"risk_level\": \"LOW\" | \"MEDIUM\" | \"HIGH\",\n"
        "  \"reasoning\": \"one-paragraph institutional reasoning\"\n"
        "}\n\n"
        "SAFETY RULES (MANDATORY):\n"
        "- If confluence < 75 → HOLD\n"
        "- If higher timeframe disagrees with direction → HOLD\n"
        "- If smart money confirmation missing → WATCHLIST\n"
        "- If risk/reward < 1:2 → REJECT\n"
        "- Prefer REJECT over mediocre trades.\n"
    )

    def __init__(self, providers: LLMProviderLayer) -> None:
        self._providers = providers

    async def validate(self, setup: SetupContext) -> AIValidationResult:
        """Run AI validation on a single premium setup."""
        # Build messages
        user_prompt = self._build_user_prompt(setup)
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        setup_id = f"{setup.symbol}-{setup.direction}-{int(datetime.now(timezone.utc).timestamp())}"
        snapshot = setup.to_market_snapshot()

        # Call provider layer
        try:
            response: ProviderResponse = await self._providers.validate(
                setup_id=setup_id,
                market_context=snapshot,
                messages=messages,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("ai_validation_failed", setup_id=setup_id, error=str(exc))
            # Graceful degradation — return HOLD instead of crashing
            return AIValidationResult(
                setup_id=setup_id,
                symbol=setup.symbol,
                direction=setup.direction,
                ai_decision="HOLD",
                confidence=0.0,
                probability=0.0,
                trade_quality="C",
                risk_level="HIGH",
                reasoning=f"AI validation failed: {exc}. Defaulting to HOLD per safety rules.",
                provider="none",
                model="none",
                latency_ms=0,
                cached=False,
                safety_overrides=["ai_failure_default_hold"],
            )

        # Apply post-AI safety overrides
        final_decision, overrides = self._apply_safety_rules(response, setup)

        # Persist to DB
        decision_id = await self._store_decision(setup, response, user_prompt, final_decision)

        return AIValidationResult(
            setup_id=setup_id,
            symbol=setup.symbol,
            direction=setup.direction,
            ai_decision=final_decision,
            confidence=response.confidence,
            probability=response.probability,
            trade_quality=response.trade_quality,
            risk_level=response.risk_level,
            reasoning=response.reasoning,
            provider=response.provider,
            model=response.model,
            latency_ms=response.latency_ms,
            cached=response.cached,
            safety_overrides=overrides,
            stored_decision_id=decision_id,
        )

    # ------------------------------------------------------------------ #
    # Prompt construction
    # ------------------------------------------------------------------ #
    def _build_user_prompt(self, setup: SetupContext) -> str:
        """Build a structured market context prompt for the AI."""
        t = setup.trend
        ms = setup.market_structure
        sm = setup.smart_money
        c = setup.confluence
        r = setup.risk

        prompt = f"""VALIDATE THIS PREMIUM SETUP — Institutional Crypto Futures

SYMBOL: {setup.symbol}
PROPOSED DIRECTION: {setup.direction}
CURRENT PRICE: {setup.price}

=== MULTI-TIMEFRAME TREND ===
HTF ({t.htf.timeframe}): {t.htf.bias.value} (strength {t.htf.strength:.2f}, ADX {t.htf.adx:.1f} {t.htf.adx_label})
MTF ({t.mtf.timeframe}): {t.mtf.bias.value} (strength {t.mtf.strength:.2f}, ADX {t.mtf.adx:.1f} {t.mtf.adx_label})
LTF ({t.ltf.timeframe}): {t.ltf.bias.value} (strength {t.ltf.strength:.2f}, ADX {t.ltf.adx:.1f} {t.ltf.adx_label})
Aligned: {t.aligned}
Trend Score: {t.score}/100

=== MARKET STRUCTURE ===
Bias: {ms.bias.value} (strength {ms.strength:.2f})
Recent event: {ms.event.value}
HH: {ms.hh_count}, HL: {ms.hl_count}, LH: {ms.lh_count}, LL: {ms.ll_count}
Last swing high: {ms.last_high.price if ms.last_high else 'N/A'}
Last swing low: {ms.last_low.price if ms.last_low else 'N/A'}
Broken level: {ms.broken_level if ms.broken_level else 'N/A'}

=== SMART MONEY ===
{sm.summary}
Institutional buying: {sm.institutional_buying:.2f}
Institutional selling: {sm.institutional_selling:.2f}
Net flow: {sm.net_flow:+.2f}

=== LIQUIDITY ===
{setup.liquidity_summary or 'N/A'}

=== BUY/SELL PRESSURE ===
{setup.pressure_summary or 'N/A'}

=== FUNDING ===
{setup.funding_summary or 'N/A'}

=== OPEN INTEREST ===
{setup.oi_summary or 'N/A'}

=== VOLUME ===
{setup.volume_summary or 'N/A'}

=== CONFLUENCE ===
Overall score: {c.score}/100
Direction: {c.direction}
Dominant components: {', '.join(c.dominant_components) if c.dominant_components else 'none'}

=== RISK PARAMETERS ===
Entry: {r.entry}
Stop loss: {r.stop_loss} ({r.risk_pct:.2f}% risk)
Take profit: {r.take_profit} ({r.reward_pct:.2f}% reward)
Risk/Reward: 1:{r.risk_reward:.2f}
Position size: {r.position_size} ({r.position_value} USDT notional)
Trade style: {r.trade_style.value}

=== INDICATORS SNAPSHOT ===
{json.dumps(setup.indicators_summary, indent=2, default=str)}

Based on the above institutional context, validate or reject this setup.
Return ONLY the JSON object per the schema in your instructions.
"""
        return prompt

    # ------------------------------------------------------------------ #
    # Post-AI safety rules
    # ------------------------------------------------------------------ #
    def _apply_safety_rules(
        self, response: ProviderResponse, setup: SetupContext
    ) -> tuple[str, list[str]]:
        """Apply hard safety overrides on top of the AI decision."""
        decision = response.decision
        overrides: list[str] = []

        # Confluence < 75 → HOLD (per spec)
        if setup.confluence.score < settings.ai_skip_if_confluence_below and decision in ("BUY", "SELL"):
            overrides.append(
                f"Confluence {setup.confluence.score} < {settings.ai_skip_if_confluence_below} → HOLD"
            )
            decision = "HOLD"

        # Higher TF disagrees → HOLD
        from app.structure.market_structure import TrendBias

        htf = setup.trend.htf.bias
        if decision == "BUY" and htf == TrendBias.BEARISH:
            overrides.append("HTF bearish vs BUY → HOLD")
            decision = "HOLD"
        elif decision == "SELL" and htf == TrendBias.BULLISH:
            overrides.append("HTF bullish vs SELL → HOLD")
            decision = "HOLD"

        # Smart money missing → WATCHLIST
        if decision in ("BUY", "SELL"):
            sm = setup.smart_money
            if (decision == "BUY" and sm.net_flow < 0.15) or (decision == "SELL" and sm.net_flow > -0.15):
                overrides.append(f"Smart money not confirming (flow {sm.net_flow:+.2f}) → WATCHLIST")
                decision = "WATCHLIST"

        # RR < 1:2 → REJECT
        if decision in ("BUY", "SELL") and setup.risk.risk_reward < settings.ai_skip_if_rr_below:
            overrides.append(f"RR {setup.risk.risk_reward:.2f} < {settings.ai_skip_if_rr_below} → REJECT")
            decision = "REJECT"

        return decision, overrides

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    async def _store_decision(
        self,
        setup: SetupContext,
        response: ProviderResponse,
        request_payload: str,
        final_decision: str,
    ) -> int | None:
        """Persist the AI decision to the database. Best-effort."""
        try:
            async with get_session() as session:
                repo = AIDecisionRepository(session)
                model = AIDecisionModel(
                    symbol=setup.symbol,
                    provider=response.provider,
                    model=response.model,
                    decision=final_decision,
                    confidence=response.confidence,
                    probability=response.probability,
                    trade_quality=response.trade_quality,
                    risk_level=response.risk_level,
                    reasoning=response.reasoning,
                    request_payload=request_payload[:10000],
                    response_raw=response.raw[:10000],
                    latency_ms=response.latency_ms,
                    cached=response.cached,
                    error="",
                )
                await repo.add(model)
                await session.commit()
                return model.id
        except Exception:  # noqa: BLE001
            log.exception("ai_decision_store_failed", symbol=setup.symbol)
            return None


__all__ = ["AIValidationEngine", "SetupContext", "AIValidationResult"]
