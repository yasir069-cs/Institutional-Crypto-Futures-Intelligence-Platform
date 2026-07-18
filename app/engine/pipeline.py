"""Analysis pipeline — orchestrates the three-stage analysis 24/7 loop.

This is the heart of the platform. Each scan cycle:

1. Reset AI provider cycle counters
2. Stage 1: scan all active symbols (math only)
3. Stage 2: deep analysis on top candidates (Smart Money Concepts)
4. Stage 3: AI validation on 3-5 premium setups
5. Signal validation (pre-AI gate + post-AI safety)
6. Generate final signals (Type A/B/C/D)
7. Send Telegram alerts
8. Store signals in DB
9. Update metrics
10. Sleep until next scan

The loop runs forever. Any per-cycle exception is logged and the loop
continues — the platform must never stop because one scan failed.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from app.ai.provider_layer import LLMProviderLayer
from app.ai.validation_engine import AIValidationEngine, SetupContext
from app.config import settings
from app.core.container import ServiceContainer
from app.core.logging import get_logger
from app.db.models import Signal as SignalModel
from app.db.repositories import SignalRepository, MetricRepository
from app.db.session import get_session
from app.engine.stage1.scanner import Stage1Scanner, Stage1Result
from app.engine.stage2.rule_engine import Stage2RuleEngine, Stage2Result, Stage2Setup
from app.notifier.telegram import TelegramNotifier
from app.signal.engine import Signal, SignalEngine
from app.signal.validation import SignalValidationEngine, SignalVerdict  # noqa: F401

log = get_logger(__name__)


@dataclass
class PipelineResult:
    cycle: int
    started_at: datetime
    duration_ms: int
    stage1: Stage1Result | None
    stage2: Stage2Result | None
    signals: list[Signal]
    ai_calls: int
    ai_cache_hits: int
    error: str | None = None


class AnalysisPipeline:
    """The 24/7 scan loop orchestrator."""

    def __init__(self) -> None:
        self._container: ServiceContainer | None = None
        self._stage1: Stage1Scanner | None = None
        self._stage2: Stage2RuleEngine | None = None
        self._ai: AIValidationEngine | None = None
        self._providers: LLMProviderLayer | None = None
        self._signal_engine = SignalEngine()
        self._validator = SignalValidationEngine()
        self._notifier: TelegramNotifier | None = None
        self._market_data = None
        self._candle_engine = None
        self._rest = None
        self._cycle = 0
        self._seeded_symbols: set[str] = set()  # symbols already backfilled

    @classmethod
    def build(cls, container: ServiceContainer) -> "AnalysisPipeline":
        """Construct the pipeline from registered services."""
        p = cls()
        p._container = container
        return p

    async def _ensure_components(self) -> None:
        """Lazily resolve services from the container."""
        if self._stage1 is not None:
            return
        c = self._container
        if c is None:
            raise RuntimeError("Pipeline has no container")

        market_data = await c.get("market_data")
        candle_engine = await c.get("candle_engine")
        indicator_engine = await c.get("indicator_engine")
        rest = await c.get("binance_rest")
        self._providers = await c.get("llm_providers")
        self._ai = await c.get("AIValidationEngine")
        self._notifier = await c.get("TelegramNotifier")
        self._market_data = market_data
        self._candle_engine = candle_engine
        self._rest = rest

        from app.structure.market_structure import MarketStructureEngine
        from app.structure.trend import TrendEngine
        from app.liquidity.engine import LiquidityEngine
        from app.smart_money.engine import SmartMoneyEngine
        from app.open_interest.engine import OpenInterestEngine
        from app.funding.engine import FundingEngine
        from app.volume.engine import VolumeEngine
        from app.pressure.engine import PressureEngine
        from app.confluence.engine import ConfluenceEngine
        from app.risk.engine import RiskEngine

        structure = MarketStructureEngine()
        trend = TrendEngine(structure)
        liquidity = LiquidityEngine()
        smart_money = SmartMoneyEngine(liquidity)
        oi = OpenInterestEngine()
        funding = FundingEngine()
        volume = VolumeEngine()
        pressure = PressureEngine()
        confluence = ConfluenceEngine()
        risk = RiskEngine()

        self._stage1 = Stage1Scanner(
            market_data=market_data,
            candle_engine=candle_engine,
            indicator_engine=indicator_engine,
            structure_engine=structure,
            trend_engine=trend,
        )
        self._stage2 = Stage2RuleEngine(
            market_data=market_data,
            candle_engine=candle_engine,
            indicator_engine=indicator_engine,
            rest=rest,
            structure_engine=structure,
            trend_engine=trend,
            liquidity_engine=liquidity,
            smart_money_engine=smart_money,
            oi_engine=oi,
            funding_engine=funding,
            volume_engine=volume,
            pressure_engine=pressure,
            confluence_engine=confluence,
            risk_engine=risk,
        )

    async def _seed_market_data(self, market_data) -> list[str]:
        """Cold-start helper: fetch 24h tickers from Binance and populate cache.

        Returns the list of top symbols by 24h volume (limited to scan_max_pairs).
        """
        if self._rest is None:
            return []
        try:
            tickers = await self._rest.ticker_24h_all()
            usdt = [
                t for t in tickers
                if t.symbol.endswith("USDT")
                and t.quote_volume >= settings.stage1_min_volume_usd
                and t.trade_count > 100
            ]
            usdt.sort(key=lambda t: t.quote_volume, reverse=True)
            top = usdt[: settings.scan_max_pairs]
            for t in top:
                await market_data.update_ticker(t)
            log.info(
                "pipeline_market_data_seeded",
                symbols_loaded=len(top),
                total_usdt_perp=len(usdt),
            )
            return [t.symbol for t in top]
        except Exception as exc:  # noqa: BLE001
            log.exception("pipeline_seed_failed", error=str(exc))
            return []

    async def _ensure_candles(self, symbols: list[str]) -> None:
        """Backfill candle history for symbols we haven't seen before.

        Idempotent — only fetches candles for new symbols. Existing candle
        buffers are kept up-to-date by the WebSocket kline stream.
        """
        if self._candle_engine is None:
            return
        htf, mtf, ltf = settings.timeframes
        new_symbols = [s for s in symbols if s not in self._seeded_symbols]
        if not new_symbols:
            return
        # Limit to top 30 new symbols per cycle to avoid hammering Binance
        # during cold start. Remaining symbols get backfilled next cycle.
        batch = new_symbols[:30]
        log.info("pipeline_backfilling_candles", count=len(batch), total_pending=len(new_symbols))
        for symbol in batch:
            try:
                await self._candle_engine.backfill(symbol, [htf, mtf, ltf])
                self._seeded_symbols.add(symbol)
            except Exception:  # noqa: BLE001
                log.exception("pipeline_backfill_failed", symbol=symbol)

    async def run_once(self) -> PipelineResult:
        """Run a single scan cycle. Returns the result for telemetry."""
        await self._ensure_components()
        self._cycle += 1
        started_at = datetime.now(timezone.utc)
        start = time.time()
        cycle = self._cycle

        # Reset AI cycle counters
        if self._providers is not None:
            self._providers.reset_cycle()

        log.info("pipeline_cycle_start", cycle=cycle)
        result = PipelineResult(
            cycle=cycle,
            started_at=started_at,
            duration_ms=0,
            stage1=None,
            stage2=None,
            signals=[],
            ai_calls=0,
            ai_cache_hits=0,
        )

        try:
            # Get active symbols from market data engine. If empty (cold start),
            # seed market data by fetching 24h tickers from Binance.
            market_data = await self._container.get("market_data")  # type: ignore[union-attr]
            top_symbols = [s.symbol for s in market_data.top_volume_symbols(settings.scan_max_pairs)]
            if not top_symbols:
                log.info("pipeline_seeding_market_data")
                top_symbols = await self._seed_market_data(market_data)
            if not top_symbols:
                log.warning("pipeline_no_symbols")
                result.duration_ms = int((time.time() - start) * 1000)
                return result

            # Ensure candle history exists for the top symbols we'll scan.
            # This is a no-op after the first few cycles (cached in _seeded_symbols).
            await self._ensure_candles(top_symbols[:50])  # backfill top 50 only

            # Stage 1
            s1 = await self._stage1.scan(top_symbols)  # type: ignore[union-attr]
            result.stage1 = s1
            await self._record_metric("stage1.duration_ms", s1.duration_ms)
            await self._record_metric("stage1.scanned", s1.total_scanned)
            await self._record_metric("stage1.passed", len(s1.candidates))

            if not s1.candidates:
                log.info("pipeline_no_stage1_candidates", cycle=cycle)
                result.duration_ms = int((time.time() - start) * 1000)
                return result

            # Stage 2
            s2 = await self._stage2.run(s1.candidates)  # type: ignore[union-attr]
            result.stage2 = s2
            await self._record_metric("stage2.duration_ms", s2.duration_ms)
            await self._record_metric("stage2.setups", len(s2.setups))

            if not s2.setups:
                log.info("pipeline_no_stage2_setups", cycle=cycle)
                result.duration_ms = int((time.time() - start) * 1000)
                return result

            # Stage 3: AI validation + signal generation
            for setup in s2.setups:
                try:
                    signal, validation = await self._validate_and_signal(setup)
                    if signal is None:
                        continue

                    # Store signal for audit (always — even rejected)
                    await self._store_signal(signal, validation)

                    # Only send Telegram alert for ACTUALLY ACTIONABLE signals
                    # Rule: validation must be PROCEED (pre-AI gate passed)
                    # AND signal direction must be BUY or SELL
                    # AND confluence must meet stage3 threshold
                    # AND AI must have been called and approved (if validation.can_send_to_ai)
                    should_alert = self._should_send_telegram(signal, validation, ai_called=bool(signal.ai))
                    if should_alert and self._notifier is not None:
                        try:
                            await self._notifier.send_signal(signal)
                            log.info(
                                "pipeline_signal_alerted",
                                symbol=signal.symbol,
                                direction=signal.direction.value,
                                signal_id=signal.id,
                            )
                        except Exception:  # noqa: BLE001
                            log.exception("telegram_send_failed", signal_id=signal.id)
                    elif not should_alert:
                        log.info(
                            "pipeline_signal_skipped_alert",
                            symbol=signal.symbol,
                            direction=signal.direction.value,
                            verdict=validation.verdict.value,
                            confluence=signal.confluence_score,
                            reason="not_actionable",
                        )

                    # Always add to result (for telemetry)
                    result.signals.append(signal)
                except Exception:  # noqa: BLE001
                    log.exception("pipeline_setup_failed", symbol=setup.symbol)

            result.duration_ms = int((time.time() - start) * 1000)
            await self._record_metric("pipeline.duration_ms", result.duration_ms)
            await self._record_metric("pipeline.signals", len(result.signals))

            log.info(
                "pipeline_cycle_complete",
                cycle=cycle,
                duration_ms=result.duration_ms,
                stage1_passed=len(s1.candidates),
                stage2_setups=len(s2.setups),
                signals=len(result.signals),
            )

        except Exception as exc:  # noqa: BLE001
            result.error = str(exc)
            result.duration_ms = int((time.time() - start) * 1000)
            log.exception("pipeline_cycle_failed", cycle=cycle, error=str(exc))

        return result

    async def _validate_and_signal(self, setup: Stage2Setup) -> tuple[Signal | None, "SignalVerdict"]:
        """Run pre-AI validation, AI validation, and generate signal.

        Returns (signal, validation) tuple. Signal is always created (even for
        rejected setups) so we can persist it for audit, but the caller decides
        whether to send the Telegram alert based on the validation verdict.
        """
        # Pre-AI gate
        validation = self._validator.validate(
            confluence=setup.confluence,
            trend=setup.trend,
            smart_money=setup.smart_money,
            risk=setup.risk,
            direction=setup.direction,
        )

        # Only call AI if pre-AI validation allows
        ai_result = None
        if validation.can_send_to_ai and self._ai is not None:
            try:
                ctx = SetupContext(
                    symbol=setup.symbol,
                    direction=setup.direction,
                    price=setup.price,
                    trend=setup.trend,
                    market_structure=setup.market_structure,
                    smart_money=setup.smart_money,
                    confluence=setup.confluence,
                    risk=setup.risk,
                    liquidity_summary=setup.liquidity.summary if hasattr(setup.liquidity, "summary") else "",
                    pressure_summary=setup.pressure.summary,
                    funding_summary=setup.funding.summary,
                    oi_summary=setup.oi.summary,
                    volume_summary=setup.volume.summary,
                    indicators_summary=setup.indicators,
                )
                ai_result = await self._ai.validate(ctx)
            except Exception:  # noqa: BLE001
                log.exception("ai_validation_step_failed", symbol=setup.symbol)

        signal = self._signal_engine.generate(
            symbol=setup.symbol,
            direction=setup.direction,
            trend=setup.trend,
            market_structure=setup.market_structure,
            smart_money=setup.smart_money,
            confluence=setup.confluence,
            risk=setup.risk,
            ai=ai_result,
        )

        # If AI returned a different decision, override signal direction
        if ai_result is not None and ai_result.ai_decision in ("BUY", "SELL", "WATCHLIST", "HOLD", "REJECT"):
            from app.signal.engine import SignalDirection
            try:
                signal.direction = SignalDirection(ai_result.ai_decision)
            except ValueError:
                pass

        return signal, validation

    def _should_send_telegram(
        self,
        signal: Signal,
        validation: "SignalVerdict",
        ai_called: bool,
    ) -> bool:
        """Determine if a signal is actionable enough to alert Telegram.

        Strict institutional rules — prefer no alert over a weak alert.
        A signal is actionable only if ALL of:
        - Pre-AI validation verdict is PROCEED (passed all safety gates)
        - Signal direction is BUY or SELL (not HOLD/WATCHLIST/REJECT)
        - Confluence score ≥ stage3_min_confluence (75 by default)
        - AI was called (validation.can_send_to_ai was True)
        - AI decision (if returned) is BUY or SELL
        """
        from app.signal.engine import SignalDirection
        from app.signal.validation import SignalVerdict as Verdict

        # Direction must be actionable
        if signal.direction not in (SignalDirection.BUY, SignalDirection.SELL):
            return False

        # Pre-AI validation must have passed
        if validation.verdict != Verdict.PROCEED:
            return False

        # Confluence must meet stage3 threshold
        if signal.confluence_score < settings.stage3_min_confluence:
            return False

        # AI must have been called (otherwise validation wouldn't have PROCEED'd, but double-check)
        if not ai_called:
            return False

        # AI decision (if available) must agree
        if signal.ai is not None and signal.ai.ai_decision not in ("BUY", "SELL"):
            return False

        return True

    async def _store_signal(self, signal: Signal, validation: "SignalVerdict | None" = None) -> None:
        """Persist signal to DB (best-effort)."""
        try:
            async with get_session() as session:
                repo = SignalRepository(session)
                model = SignalModel(
                    symbol=signal.symbol,
                    signal_type=signal.signal_type.value,
                    direction=signal.direction.value,
                    entry=signal.entry,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    risk_reward=signal.risk_reward,
                    confidence=signal.confidence,
                    confluence_score=signal.confluence_score,
                    timeframe_htf=signal.trend.htf.timeframe,
                    timeframe_mtf=signal.trend.mtf.timeframe,
                    timeframe_ltf=signal.trend.ltf.timeframe,
                    trend_htf=signal.trend.htf.bias.value,
                    trend_mtf=signal.trend.mtf.bias.value,
                    trend_ltf=signal.trend.ltf.bias.value,
                    market_structure=signal.market_structure.to_dict().__str__()[:1000],
                    smart_money_summary=signal.smart_money.summary,
                    liquidity_summary=signal.metadata.get("liquidity_summary", ""),
                    ai_reasoning=signal.ai.reasoning if signal.ai else "",
                    ai_decision=signal.ai.ai_decision if signal.ai else "",
                    probability=signal.ai.probability if signal.ai else 0.0,
                    trade_quality=signal.ai.trade_quality if signal.ai else "",
                    risk_level=signal.ai.risk_level if signal.ai else "",
                    status="OPEN" if validation and validation.verdict.value == "PROCEED" else "REJECTED",
                    metadata_json=str({**signal.metadata, "validation_verdict": validation.verdict.value if validation else "UNKNOWN", "validation_reasons": validation.reasons if validation else []})[:5000],
                    ai_decision_id=signal.ai.stored_decision_id if signal.ai else None,
                )
                await repo.add(model)
                await session.commit()
        except Exception:  # noqa: BLE001
            log.exception("signal_store_failed", signal_id=signal.id)

    async def _record_metric(self, name: str, value: float | int) -> None:
        try:
            async with get_session() as session:
                repo = MetricRepository(session)
                await repo.record(name, value)
                await session.commit()
        except Exception:  # noqa: BLE001
            log.debug("metric_record_failed", name=name, value=value)

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        """Run scan cycles forever until ``stop`` is set."""
        if stop is None:
            stop = asyncio.Event()
        log.info("pipeline_loop_starting", interval_sec=settings.scan_interval_sec)
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001
                log.exception("pipeline_unexpected_error")
            # Wait for next cycle (or stop signal)
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.scan_interval_sec)
            except asyncio.TimeoutError:
                continue
        log.info("pipeline_loop_stopped")


__all__ = ["AnalysisPipeline", "PipelineResult"]
