"""Multi-timeframe trend engine.

Combines market structure + EMA alignment + ADX across HTF / MTF / LTF to
produce a single trend direction and strength score. Implements the rule
"the 5M timeframe MUST NEVER override the 1H trend unless exceptional
confirmation exists" — LTF can only *align* or *diverge*, never override.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.exchange.binance_rest import Candle
from app.market.indicator_engine import (
    IndicatorEngine,
    adx,
    ema_alignment_score,
)
from app.structure.market_structure import MarketStructureEngine, TrendBias


@dataclass
class TimeframeTrend:
    timeframe: str
    bias: TrendBias
    strength: float  # 0..1
    ema_score: float  # -1..+1
    adx: float
    adx_label: str

    def to_dict(self) -> dict:
        return {
            "timeframe": self.timeframe,
            "bias": self.bias.value,
            "strength": round(self.strength, 3),
            "ema_score": round(self.ema_score, 3),
            "adx": round(self.adx, 1),
            "adx_label": self.adx_label,
        }


@dataclass
class MultiTimeframeTrend:
    htf: TimeframeTrend
    mtf: TimeframeTrend
    ltf: TimeframeTrend
    overall_bias: TrendBias
    aligned: bool
    score: int  # 0..100

    def to_dict(self) -> dict:
        return {
            "htf": self.htf.to_dict(),
            "mtf": self.mtf.to_dict(),
            "ltf": self.ltf.to_dict(),
            "overall_bias": self.overall_bias.value,
            "aligned": self.aligned,
            "score": self.score,
        }


class TrendEngine:
    """Combine structure + indicators across timeframes into a trend verdict."""

    def __init__(self, structure_engine: MarketStructureEngine | None = None) -> None:
        self._structure = structure_engine or MarketStructureEngine()

    def analyze_timeframe(self, timeframe: str, candles: Sequence[Candle]) -> TimeframeTrend:
        if not candles or len(candles) < 30:
            return TimeframeTrend(
                timeframe=timeframe,
                bias=TrendBias.NEUTRAL,
                strength=0.0,
                ema_score=0.0,
                adx=0.0,
                adx_label="insufficient_data",
            )

        structure = self._structure.analyze(candles)
        ema_info = ema_alignment_score(candles)
        adx_info = adx(candles)

        # Combine structure bias + EMA alignment into one bias
        # Structure has priority; EMA only breaks ties.
        if structure.bias != TrendBias.NEUTRAL:
            bias = structure.bias
        elif ema_info["score"] > 0.2:  # type: ignore[operator]
            bias = TrendBias.BULLISH
        elif ema_info["score"] < -0.2:  # type: ignore[operator]
            bias = TrendBias.BEARISH
        else:
            bias = TrendBias.NEUTRAL

        # Strength: blend structure strength + ADX normalized + |EMA score|
        adx_norm = min(1.0, adx_info["adx"] / 50.0)
        ema_strength = abs(ema_info["score"])  # type: ignore[operator]
        strength = (structure.strength * 0.4 + adx_norm * 0.4 + ema_strength * 0.2)

        return TimeframeTrend(
            timeframe=timeframe,
            bias=bias,
            strength=min(1.0, strength),
            ema_score=ema_info["score"],  # type: ignore[operator]
            adx=adx_info["adx"],
            adx_label=adx_info["trend_strength"],
        )

    def analyze(
        self,
        htf_candles: Sequence[Candle],
        mtf_candles: Sequence[Candle],
        ltf_candles: Sequence[Candle],
        htf_label: str = "1h",
        mtf_label: str = "15m",
        ltf_label: str = "5m",
    ) -> MultiTimeframeTrend:
        htf = self.analyze_timeframe(htf_label, htf_candles)
        mtf = self.analyze_timeframe(mtf_label, mtf_candles)
        ltf = self.analyze_timeframe(ltf_label, ltf_candles)

        # Overall bias is dominated by HTF. HTF must be non-NEUTRAL for a strong call.
        if htf.bias != TrendBias.NEUTRAL:
            overall_bias = htf.bias
        elif mtf.bias != TrendBias.NEUTRAL:
            overall_bias = mtf.bias
        else:
            overall_bias = ltf.bias

        # Alignment: all three agree (NEUTRAL counts as agreement with anything)
        non_neutral = [t for t in (htf, mtf, ltf) if t.bias != TrendBias.NEUTRAL]
        aligned = len(non_neutral) >= 2 and all(t.bias == non_neutral[0].bias for t in non_neutral)

        # Score 0..100: weighted combination
        # HTF weighted highest (50%), MTF (30%), LTF (20%)
        def bias_to_num(b: TrendBias) -> int:
            return 1 if b == TrendBias.BULLISH else -1 if b == TrendBias.BEARISH else 0

        raw = (
            bias_to_num(htf.bias) * htf.strength * 50
            + bias_to_num(mtf.bias) * mtf.strength * 30
            + bias_to_num(ltf.bias) * ltf.strength * 20
        )
        # Convert to 0..100 with 50 = neutral
        score = int(max(0, min(100, 50 + raw)))

        return MultiTimeframeTrend(
            htf=htf,
            mtf=mtf,
            ltf=ltf,
            overall_bias=overall_bias,
            aligned=aligned,
            score=score,
        )


__all__ = ["TrendEngine", "TimeframeTrend", "MultiTimeframeTrend"]
