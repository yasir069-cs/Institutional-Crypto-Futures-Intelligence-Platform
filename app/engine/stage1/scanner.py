"""Stage 1 — Fast Mathematical Scanner.

Scans ALL active Binance USDT Futures pairs using only mathematical
calculations. NO AI. NO LLM. NO reasoning.

Goal: reduce 300-500 coins → 20-30 high-potential candidates in <5 seconds.

Per-pair calculations:
- EMA alignment
- VWAP position
- ATR %
- Trend direction & strength (from HTF candles)
- Volume spike
- Buy/sell pressure
- Confluence score (lightweight)

Rejection rules:
- Weak trend (HTF NEUTRAL)
- Low volume (< min_volume_usd)
- Low confluence (< stage1_min_confluence)
- Sideways market (low ATR %)
- Poor risk reward
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.core.logging import get_logger
from app.exchange.binance_rest import Candle
from app.market.candle_engine import CandleEngine
from app.market.data_engine import MarketDataEngine
from app.market.indicator_engine import IndicatorEngine
from app.structure.market_structure import MarketStructureEngine, TrendBias
from app.structure.trend import TrendEngine

log = get_logger(__name__)


@dataclass
class Stage1Candidate:
    """Output of Stage 1 for a single symbol."""

    symbol: str
    price: float
    volume_usd: float
    atr_pct: float
    trend_bias: TrendBias
    trend_strength: float
    ema_score: float
    vwap_position: float
    confluence_score: int
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
            "price": self.price,
            "volume_usd": self.volume_usd,
            "atr_pct": round(self.atr_pct, 3),
            "trend_bias": self.trend_bias.value,
            "trend_strength": round(self.trend_strength, 3),
            "ema_score": round(self.ema_score, 3),
            "vwap_position": round(self.vwap_position, 3),
            "confluence_score": self.confluence_score,
            "passed": self.passed,
            "rejected_reason": self.rejected_reason,
        }


@dataclass
class Stage1Result:
    candidates: list[Stage1Candidate]
    rejected: list[Stage1Candidate]
    duration_ms: int
    total_scanned: int

    def to_dict(self) -> dict:
        return {
            "duration_ms": self.duration_ms,
            "total_scanned": self.total_scanned,
            "passed": len(self.candidates),
            "rejected": len(self.rejected),
            "top_candidates": [c.to_dict() for c in self.candidates[:settings.scan_stage1_top_n]],
        }


class Stage1Scanner:
    """Fast mathematical scanner running every minute."""

    def __init__(
        self,
        market_data: MarketDataEngine,
        candle_engine: CandleEngine,
        indicator_engine: IndicatorEngine,
        structure_engine: MarketStructureEngine | None = None,
        trend_engine: TrendEngine | None = None,
    ) -> None:
        self._market_data = market_data
        self._candles = candle_engine
        self._indicators = indicator_engine
        self._structure = structure_engine or MarketStructureEngine()
        self._trend = trend_engine or TrendEngine(self._structure)

    async def scan(self, symbols: list[str]) -> Stage1Result:
        """Scan all symbols in parallel; return top candidates."""
        start = time.time()
        htf, mtf, ltf = settings.timeframes

        # Concurrency-limited parallel scan
        sem = asyncio.Semaphore(50)  # bound concurrent CPU work

        async def _scan_one(symbol: str) -> Stage1Candidate:
            async with sem:
                return await self._scan_symbol(symbol, htf, mtf, ltf)

        tasks = [_scan_one(s) for s in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        candidates: list[Stage1Candidate] = []
        rejected: list[Stage1Candidate] = []
        for r in results:
            if isinstance(r, Exception):
                log.warning("stage1_scan_exception", error=str(r))
                continue
            if r.passed:
                candidates.append(r)
            else:
                rejected.append(r)

        # Sort by confluence score descending, take top N
        candidates.sort(key=lambda c: c.confluence_score, reverse=True)
        top = candidates[: settings.scan_stage1_top_n]

        duration_ms = int((time.time() - start) * 1000)
        log.info(
            "stage1_complete",
            scanned=len(symbols),
            passed=len(candidates),
            top=len(top),
            duration_ms=duration_ms,
        )
        return Stage1Result(
            candidates=top,
            rejected=rejected,
            duration_ms=duration_ms,
            total_scanned=len(symbols),
        )

    async def _scan_symbol(self, symbol: str, htf: str, mtf: str, ltf: str) -> Stage1Candidate:
        """Run lightweight math scan on a single symbol."""
        # Get state from cache (lock-free read)
        state = self._market_data.get(symbol)
        price = state.last_price if state else 0.0
        volume_usd = state.quote_volume_24h if state else 0.0

        # Reject early on insufficient volume
        if volume_usd < settings.stage1_min_volume_usd:
            return Stage1Candidate(
                symbol=symbol,
                price=price,
                volume_usd=volume_usd,
                atr_pct=0.0,
                trend_bias=TrendBias.NEUTRAL,
                trend_strength=0.0,
                ema_score=0.0,
                vwap_position=0.0,
                confluence_score=0,
                indicators={},
                htf_label=htf,
                mtf_label=mtf,
                ltf_label=ltf,
                rejected_reason=f"volume {volume_usd:.0f} < {settings.stage1_min_volume_usd}",
            )

        # Get HTF candles
        htf_candles = self._candles.latest(symbol, htf, 200)
        if len(htf_candles) < 50:
            return Stage1Candidate(
                symbol=symbol,
                price=price,
                volume_usd=volume_usd,
                atr_pct=0.0,
                trend_bias=TrendBias.NEUTRAL,
                trend_strength=0.0,
                ema_score=0.0,
                vwap_position=0.0,
                confluence_score=0,
                indicators={},
                htf_label=htf,
                mtf_label=mtf,
                ltf_label=ltf,
                rejected_reason="insufficient HTF history",
            )

        # Compute indicators (cached by close_time)
        ind = self._indicators.compute_all(symbol, htf, htf_candles)

        # Compute HTF trend quickly
        ms = self._structure.analyze(htf_candles)
        htf_trend = self._trend.analyze_timeframe(htf, htf_candles)

        # Reject sideways / weak trend
        if htf_trend.bias == TrendBias.NEUTRAL:
            return Stage1Candidate(
                symbol=symbol,
                price=price,
                volume_usd=volume_usd,
                atr_pct=ind["atr_pct"],
                trend_bias=TrendBias.NEUTRAL,
                trend_strength=htf_trend.strength,
                ema_score=ind["ema"]["score"],
                vwap_position=ind["vwap_position"],
                confluence_score=0,
                indicators=ind,
                htf_label=htf,
                mtf_label=mtf,
                ltf_label=ltf,
                rejected_reason="HTF trend NEUTRAL",
            )

        # Reject low ATR (sideways)
        if ind["atr_pct"] < settings.stage1_min_atr_pct:
            return Stage1Candidate(
                symbol=symbol,
                price=price,
                volume_usd=volume_usd,
                atr_pct=ind["atr_pct"],
                trend_bias=htf_trend.bias,
                trend_strength=htf_trend.strength,
                ema_score=ind["ema"]["score"],
                vwap_position=ind["vwap_position"],
                confluence_score=0,
                indicators=ind,
                htf_label=htf,
                mtf_label=mtf,
                ltf_label=ltf,
                rejected_reason=f"ATR% {ind['atr_pct']:.2f} < {settings.stage1_min_atr_pct}",
            )

        # Lightweight confluence estimate (full confluence computed in Stage 2)
        confluence = self._quick_confluence(htf_trend, ind, ms)

        if confluence < settings.stage1_min_confluence:
            return Stage1Candidate(
                symbol=symbol,
                price=price,
                volume_usd=volume_usd,
                atr_pct=ind["atr_pct"],
                trend_bias=htf_trend.bias,
                trend_strength=htf_trend.strength,
                ema_score=ind["ema"]["score"],
                vwap_position=ind["vwap_position"],
                confluence_score=confluence,
                indicators=ind,
                htf_label=htf,
                mtf_label=mtf,
                ltf_label=ltf,
                rejected_reason=f"confluence {confluence} < {settings.stage1_min_confluence}",
            )

        return Stage1Candidate(
            symbol=symbol,
            price=price,
            volume_usd=volume_usd,
            atr_pct=ind["atr_pct"],
            trend_bias=htf_trend.bias,
            trend_strength=htf_trend.strength,
            ema_score=ind["ema"]["score"],
            vwap_position=ind["vwap_position"],
            confluence_score=confluence,
            indicators=ind,
            htf_label=htf,
            mtf_label=mtf,
            ltf_label=ltf,
        )

    def _quick_confluence(self, htf_trend, ind, ms) -> int:
        """Lightweight confluence estimate for Stage 1.

        Combines: trend strength (40%), EMA alignment (30%), market structure (30%).
        """
        trend_score = htf_trend.strength * 40
        ema_score = abs(ind["ema"]["score"]) * 30
        ms_score = ms.strength * 30
        return int(trend_score + ema_score + ms_score)


__all__ = ["Stage1Scanner", "Stage1Result", "Stage1Candidate"]
