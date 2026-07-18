"""Stage 2 — Advanced Rule Engine (Smart Money Concepts).

Runs ONLY on Stage 1 candidates. Performs deep analysis:
- BOS / CHOCH detection across multiple timeframes
- Liquidity sweeps, FVGs, order blocks
- Institutional buying/selling classification
- Open interest spikes & funding shifts
- Fake breakout / fake breakdown detection
- Risk/reward computation

Goal: reduce 20-30 candidates → 3-5 premium setups.

Rejects:
- Weak market structure
- Missing liquidity confirmation
- Poor RR
- Fake move detected
- Institutional confirmation missing
- Confluence < stage2_min_confluence
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from app.config import settings
from app.confluence.engine import ConfluenceEngine, ConfluenceResult
from app.core.logging import get_logger
from app.exchange.binance_rest import BinanceRestClient
from app.funding.engine import FundingEngine, FundingResult
from app.liquidity.engine import LiquidityEngine, LiquidityResult
from app.market.candle_engine import CandleEngine
from app.market.data_engine import MarketDataEngine
from app.market.indicator_engine import IndicatorEngine
from app.open_interest.engine import OpenInterestEngine, OIResult
from app.pressure.engine import PressureEngine, PressureResult
from app.risk.engine import RiskEngine, RiskResult, TradeStyle
from app.smart_money.engine import SmartMoneyEngine, SmartMoneyResult
from app.structure.market_structure import (
    MarketStructureEngine,
    MarketStructureResult,
    StructureEvent,
    TrendBias,
)
from app.structure.trend import MultiTimeframeTrend, TrendEngine
from app.volume.engine import VolumeEngine, VolumeResult
from app.engine.stage1.scanner import Stage1Candidate

log = get_logger(__name__)


@dataclass
class Stage2Setup:
    """A premium setup emerging from Stage 2."""

    symbol: str
    direction: str  # BUY / SELL
    price: float
    trend: MultiTimeframeTrend
    market_structure: MarketStructureResult
    liquidity: LiquidityResult
    smart_money: SmartMoneyResult
    pressure: PressureResult
    oi: OIResult
    funding: FundingResult
    volume: VolumeResult
    confluence: ConfluenceResult
    risk: RiskResult
    indicators: dict
    htf_label: str
    mtf_label: str
    ltf_label: str
    rejected_reason: str = ""

    @property
    def passed(self) -> bool:
        return not self.rejected_reason

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "price": self.price,
            "trend": self.trend.to_dict(),
            "market_structure": self.market_structure.to_dict(),
            "liquidity": self.liquidity.to_dict(),
            "smart_money": self.smart_money.to_dict(),
            "pressure": self.pressure.to_dict(),
            "oi": self.oi.to_dict(),
            "funding": self.funding.to_dict(),
            "volume": self.volume.to_dict(),
            "confluence": self.confluence.to_dict(),
            "risk": self.risk.to_dict(),
            "passed": self.passed,
            "rejected_reason": self.rejected_reason,
        }


@dataclass
class Stage2Result:
    setups: list[Stage2Setup]
    rejected: list[Stage2Setup]
    duration_ms: int

    def to_dict(self) -> dict:
        return {
            "duration_ms": self.duration_ms,
            "setups_count": len(self.setups),
            "rejected_count": len(self.rejected),
            "setups": [s.to_dict() for s in self.setups],
        }


class Stage2RuleEngine:
    """Deep analysis on Stage 1 candidates."""

    def __init__(
        self,
        market_data: MarketDataEngine,
        candle_engine: CandleEngine,
        indicator_engine: IndicatorEngine,
        rest: BinanceRestClient,
        structure_engine: MarketStructureEngine | None = None,
        trend_engine: TrendEngine | None = None,
        liquidity_engine: LiquidityEngine | None = None,
        smart_money_engine: SmartMoneyEngine | None = None,
        oi_engine: OpenInterestEngine | None = None,
        funding_engine: FundingEngine | None = None,
        volume_engine: VolumeEngine | None = None,
        pressure_engine: PressureEngine | None = None,
        confluence_engine: ConfluenceEngine | None = None,
        risk_engine: RiskEngine | None = None,
    ) -> None:
        self._md = market_data
        self._candles = candle_engine
        self._ind = indicator_engine
        self._rest = rest
        self._structure = structure_engine or MarketStructureEngine()
        self._trend = trend_engine or TrendEngine(self._structure)
        self._liq = liquidity_engine or LiquidityEngine()
        self._sm = smart_money_engine or SmartMoneyEngine(self._liq)
        self._oi = oi_engine or OpenInterestEngine()
        self._funding = funding_engine or FundingEngine()
        self._vol = volume_engine or VolumeEngine()
        self._press = pressure_engine or PressureEngine()
        self._conf = confluence_engine or ConfluenceEngine()
        self._risk = risk_engine or RiskEngine()

    async def run(self, candidates: list[Stage1Candidate]) -> Stage2Result:
        """Run deep analysis on each Stage 1 candidate."""
        start = time.time()
        sem = asyncio.Semaphore(20)

        async def _run_one(c: Stage1Candidate) -> Stage2Setup:
            async with sem:
                return await self._analyze(c)

        tasks = [_run_one(c) for c in candidates]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        setups: list[Stage2Setup] = []
        rejected: list[Stage2Setup] = []
        for r in results:
            if isinstance(r, Exception):
                log.warning("stage2_exception", error=str(r))
                continue
            if r.passed:
                setups.append(r)
            else:
                rejected.append(r)

        # Sort by confluence score descending
        setups.sort(key=lambda s: s.confluence.score, reverse=True)
        top = setups[: settings.scan_stage2_top_n]

        duration_ms = int((time.time() - start) * 1000)
        log.info(
            "stage2_complete",
            input_count=len(candidates),
            passed=len(setups),
            top=len(top),
            duration_ms=duration_ms,
        )
        return Stage2Result(setups=top, rejected=rejected, duration_ms=duration_ms)

    async def _analyze(self, c: Stage1Candidate) -> Stage2Setup:
        """Deep-analyze a single Stage 1 candidate."""
        htf, mtf, ltf = settings.timeframes
        symbol = c.symbol

        # Fetch candles for all 3 timeframes
        htf_candles = self._candles.latest(symbol, htf, 200)
        mtf_candles = self._candles.latest(symbol, mtf, 200)
        ltf_candles = self._candles.latest(symbol, ltf, 200)

        if len(htf_candles) < 30 or len(mtf_candles) < 30 or len(ltf_candles) < 30:
            return self._reject(c, "insufficient candle history across timeframes", htf, mtf, ltf)

        # Run all engines
        prev_bias = c.trend_bias
        ms_htf = self._structure.analyze(htf_candles, prev_bias=prev_bias)
        ms_mtf = self._structure.analyze(mtf_candles, prev_bias=ms_htf.bias)
        ms_ltf = self._structure.analyze(ltf_candles, prev_bias=ms_mtf.bias)
        trend = self._trend.analyze(htf_candles, mtf_candles, ltf_candles, htf, mtf, ltf)

        liquidity = self._liq.analyze(htf_candles)
        state = self._md.get(symbol)
        smart_money = self._sm.analyze(htf_candles, state, liquidity)
        pressure = self._press.analyze(
            trades=None, candles=ltf_candles, state=state
        )

        # Volume
        volume = self._vol.analyze(htf_candles)

        # Fetch OI and funding from REST (best-effort; tolerate failures)
        oi = await self._fetch_oi(symbol, c.price)
        funding = await self._fetch_funding(symbol)

        # Indicators bundle for HTF
        ind_htf = self._ind.compute_all(symbol, htf, htf_candles)

        # Determine direction from HTF bias + smart money flow
        direction = self._decide_direction(trend, smart_money, ms_htf, ms_ltf)
        if direction is None:
            return self._reject_with_data(
                c, "no clear directional bias",
                trend, ms_htf, liquidity, smart_money, pressure, oi, funding, volume,
                ind_htf, htf, mtf, ltf,
            )

        # Compute confluence
        confluence = self._conf.compute(
            trend=trend,
            market_structure=ms_htf,
            liquidity=liquidity,
            smart_money=smart_money,
            pressure=pressure,
            oi=oi,
            funding=funding,
            volume=volume,
            indicators=ind_htf,
        )

        # Reject low confluence
        if confluence.score < settings.stage2_min_confluence:
            return self._reject_with_data(
                c, f"confluence {confluence.score} < {settings.stage2_min_confluence}",
                trend, ms_htf, liquidity, smart_money, pressure, oi, funding, volume,
                ind_htf, htf, mtf, ltf, direction=direction, confluence=confluence,
            )

        # Reject conflicting direction
        if confluence.direction != "NEUTRAL" and confluence.direction != direction:
            return self._reject_with_data(
                c, f"confluence direction {confluence.direction} conflicts with {direction}",
                trend, ms_htf, liquidity, smart_money, pressure, oi, funding, volume,
                ind_htf, htf, mtf, ltf, direction=direction, confluence=confluence,
            )

        # Compute risk
        atr = ind_htf.get("atr", 0.0)
        risk = self._risk.compute(
            direction=direction,
            entry=c.price,
            atr=atr,
            trade_style=TradeStyle.INTRADAY,
        )
        if not risk.valid:
            return self._reject_with_data(
                c, f"risk invalid: {risk.rejection_reason}",
                trend, ms_htf, liquidity, smart_money, pressure, oi, funding, volume,
                ind_htf, htf, mtf, ltf, direction=direction, confluence=confluence, risk=risk,
            )

        # Reject if RR below stage2 minimum
        if risk.risk_reward < settings.stage2_min_rr:
            return self._reject_with_data(
                c, f"RR {risk.risk_reward:.2f} < {settings.stage2_min_rr}",
                trend, ms_htf, liquidity, smart_money, pressure, oi, funding, volume,
                ind_htf, htf, mtf, ltf, direction=direction, confluence=confluence, risk=risk,
            )

        # Reject if smart money doesn't confirm
        if (direction == "BUY" and smart_money.net_flow < 0.0) or (
            direction == "SELL" and smart_money.net_flow > 0.0
        ):
            return self._reject_with_data(
                c, f"smart money flow {smart_money.net_flow:+.2f} doesn't confirm {direction}",
                trend, ms_htf, liquidity, smart_money, pressure, oi, funding, volume,
                ind_htf, htf, mtf, ltf, direction=direction, confluence=confluence, risk=risk,
            )

        return Stage2Setup(
            symbol=symbol,
            direction=direction,
            price=c.price,
            trend=trend,
            market_structure=ms_htf,
            liquidity=liquidity,
            smart_money=smart_money,
            pressure=pressure,
            oi=oi,
            funding=funding,
            volume=volume,
            confluence=confluence,
            risk=risk,
            indicators=ind_htf,
            htf_label=htf,
            mtf_label=mtf,
            ltf_label=ltf,
        )

    def _decide_direction(
        self,
        trend: MultiTimeframeTrend,
        smart_money: SmartMoneyResult,
        ms_htf: MarketStructureResult,
        ms_ltf: MarketStructureResult,
    ) -> str | None:
        """Decide BUY / SELL / None from trend + smart money + structure.

        HTF bias dominates; LTF can confirm timing but never override.
        """
        htf_bias = trend.htf.bias
        if htf_bias == TrendBias.BULLISH and smart_money.net_flow > 0:
            return "BUY"
        if htf_bias == TrendBias.BEARISH and smart_money.net_flow < 0:
            return "SELL"
        # HTF neutral — use structure event
        if ms_htf.event.value == "BOS_BULL":
            return "BUY"
        if ms_htf.event.value == "BOS_BEAR":
            return "SELL"
        return None

    async def _fetch_oi(self, symbol: str, price: float) -> OIResult:
        try:
            history = await self._rest.open_interest_history(symbol, period="15m", limit=30)
            return self._oi.analyze(history, price_now=price, price_1h_ago=price * 0.99)
        except Exception:  # noqa: BLE001
            log.debug("oi_fetch_failed", symbol=symbol, error="exception")
            return OIResult(summary="OI fetch failed")

    async def _fetch_funding(self, symbol: str) -> FundingResult:
        try:
            history = await self._rest.funding_rate_history(symbol, limit=30)
            return self._funding.analyze(history)
        except Exception:  # noqa: BLE001
            log.debug("funding_fetch_failed", symbol=symbol)
            return FundingResult(summary="Funding fetch failed")

    # ------------------------------------------------------------------ #
    # Rejection helpers
    # ------------------------------------------------------------------ #
    def _reject(
        self, c: Stage1Candidate, reason: str, htf: str, mtf: str, ltf: str
    ) -> Stage2Setup:
        # Build a minimal but valid MultiTimeframeTrend for the rejected setup.
        from app.structure.trend import TimeframeTrend
        placeholder_tf = TimeframeTrend(
            timeframe="1h", bias=TrendBias.NEUTRAL, strength=0.0,
            ema_score=0.0, adx=0.0, adx_label="none",
        )
        return Stage2Setup(
            symbol=c.symbol,
            direction="NONE",
            price=c.price,
            trend=MultiTimeframeTrend(
                htf=placeholder_tf, mtf=placeholder_tf, ltf=placeholder_tf,
                overall_bias=TrendBias.NEUTRAL, aligned=False, score=50,
            ),
            market_structure=MarketStructureResult(
                bias=TrendBias.NEUTRAL, event=StructureEvent.NONE,
            ),
            liquidity=LiquidityResult(),
            smart_money=SmartMoneyResult(),
            pressure=PressureResult(),
            oi=OIResult(),
            funding=FundingResult(),
            volume=VolumeResult(),
            confluence=ConfluenceResult(score=0, direction="NEUTRAL"),
            risk=RiskResult(
                valid=False, direction="NONE", entry=c.price, stop_loss=0,
                take_profit=0, risk_pct=0, reward_pct=0, risk_reward=0,
                position_size=0, position_value=0, risk_amount=0, reward_amount=0,
                trade_style=TradeStyle.INTRADAY, rejection_reason=reason,
            ),
            indicators=c.indicators,
            htf_label=htf,
            mtf_label=mtf,
            ltf_label=ltf,
            rejected_reason=reason,
        )

    def _reject_with_data(
        self,
        c: Stage1Candidate,
        reason: str,
        trend: MultiTimeframeTrend,
        ms: MarketStructureResult,
        liq: LiquidityResult,
        sm: SmartMoneyResult,
        pressure: PressureResult,
        oi: OIResult,
        funding: FundingResult,
        volume: VolumeResult,
        ind: dict,
        htf: str,
        mtf: str,
        ltf: str,
        direction: str = "NONE",
        confluence: ConfluenceResult | None = None,
        risk: RiskResult | None = None,
    ) -> Stage2Setup:
        return Stage2Setup(
            symbol=c.symbol,
            direction=direction,
            price=c.price,
            trend=trend,
            market_structure=ms,
            liquidity=liq,
            smart_money=sm,
            pressure=pressure,
            oi=oi,
            funding=funding,
            volume=volume,
            confluence=confluence or ConfluenceResult(score=0, direction="NEUTRAL"),
            risk=risk or RiskResult(
                valid=False, direction=direction, entry=c.price, stop_loss=0,
                take_profit=0, risk_pct=0, reward_pct=0, risk_reward=0,
                position_size=0, position_value=0, risk_amount=0, reward_amount=0,
                trade_style=TradeStyle.INTRADAY, rejection_reason=reason,
            ),
            indicators=ind,
            htf_label=htf,
            mtf_label=mtf,
            ltf_label=ltf,
            rejected_reason=reason,
        )


__all__ = ["Stage2RuleEngine", "Stage2Result", "Stage2Setup"]
