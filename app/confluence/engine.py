"""Confluence engine.

Aggregates signals from every analytical engine into a single weighted
score 0-100 representing how many confluence factors agree. Weights are
configured via settings and must sum to 100.

Categories (per spec)
---------------------
**Highest weight**: Market Structure, Trend, Liquidity, Smart Money
**High weight**: EMA, Volume, Buy/Sell Pressure, Open Interest, Funding
**Medium weight**: VWAP, ATR, ADX, Bollinger Bands, Support/Resistance
**Lowest weight**: RSI (supporting only)

Each sub-engine contributes a **directional score** in [-1, +1] (-1 = bearish
confirmation, +1 = bullish confirmation, 0 = neutral). The confluence engine
multiplies each by its weight, sums, normalizes to 0-100, and tags the
dominant direction.

The final score is accompanied by a per-component breakdown so callers can
see which engines contributed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings
from app.funding.engine import FundingResult
from app.liquidity.engine import LiquidityResult
from app.market.indicator_engine import bollinger_bands
from app.open_interest.engine import OIResult
from app.pressure.engine import PressureResult
from app.smart_money.engine import SmartMoneyResult
from app.structure.market_structure import MarketStructureResult, TrendBias
from app.structure.trend import MultiTimeframeTrend
from app.volume.engine import VolumeResult


@dataclass
class ConfluenceComponent:
    name: str
    weight: int
    score: float  # -1..+1
    contribution: float  # weight * score

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "weight": self.weight,
            "score": round(self.score, 3),
            "contribution": round(self.contribution, 3),
        }


@dataclass
class ConfluenceResult:
    score: int  # 0..100
    direction: str  # BULLISH / BEARISH / NEUTRAL
    components: list[ConfluenceComponent] = field(default_factory=list)
    dominant_components: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "direction": self.direction,
            "components": [c.to_dict() for c in self.components],
            "dominant_components": self.dominant_components,
        }


def _bias_to_score(bias: TrendBias) -> float:
    if bias == TrendBias.BULLISH:
        return 1.0
    if bias == TrendBias.BEARISH:
        return -1.0
    return 0.0


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


class ConfluenceEngine:
    """Compute weighted confluence score across all sub-engines."""

    def compute(
        self,
        trend: MultiTimeframeTrend | None = None,
        market_structure: MarketStructureResult | None = None,
        liquidity: LiquidityResult | None = None,
        smart_money: SmartMoneyResult | None = None,
        pressure: PressureResult | None = None,
        oi: OIResult | None = None,
        funding: FundingResult | None = None,
        volume: VolumeResult | None = None,
        indicators: dict | None = None,
    ) -> ConfluenceResult:
        indicators = indicators or {}
        components: list[ConfluenceComponent] = []

        # ------------------------------------------------------------------ #
        # Highest weight
        # ------------------------------------------------------------------ #
        # Market Structure
        ms_score = 0.0
        if market_structure is not None:
            ms_score = _bias_to_score(market_structure.bias) * market_structure.strength
        components.append(ConfluenceComponent(
            name="market_structure",
            weight=settings.confluence_weight_market_structure,
            score=_clamp(ms_score),
            contribution=ms_score * settings.confluence_weight_market_structure,
        ))

        # Trend (overall bias from MTF analysis)
        trend_score = 0.0
        if trend is not None:
            if trend.overall_bias == TrendBias.BULLISH:
                trend_score = trend.score / 100.0
            elif trend.overall_bias == TrendBias.BEARISH:
                trend_score = -trend.score / 100.0
        components.append(ConfluenceComponent(
            name="trend",
            weight=settings.confluence_weight_trend,
            score=_clamp(trend_score),
            contribution=trend_score * settings.confluence_weight_trend,
        ))

        # Liquidity
        liq_score = 0.0
        if liquidity is not None:
            # Bullish OB mitigated + unfilled bullish FVG → bullish
            # Bearish OB mitigated + unfilled bearish FVG → bearish
            bull_signals = 0
            bear_signals = 0
            for ob in liquidity.order_blocks:
                if ob.mitigated:
                    if ob.type.value == "BULLISH":
                        bull_signals += 1
                    else:
                        bear_signals += 1
            for fvg in liquidity.fvgs:
                if not fvg.filled:
                    if fvg.type.value == "BULLISH":
                        bull_signals += 0.5
                    else:
                        bear_signals += 0.5
            for sweep in liquidity.recent_sweeps:
                if sweep.recovered:
                    if sweep.type.value == "BUY_SIDE":
                        bear_signals += 1  # buy-side sweep recovery = bearish reversal
                    else:
                        bull_signals += 1
            net = bull_signals - bear_signals
            liq_score = _clamp(net / 3.0)
        components.append(ConfluenceComponent(
            name="liquidity",
            weight=settings.confluence_weight_liquidity,
            score=_clamp(liq_score),
            contribution=liq_score * settings.confluence_weight_liquidity,
        ))

        # Smart Money
        sm_score = 0.0
        if smart_money is not None:
            sm_score = _clamp(smart_money.net_flow)
        components.append(ConfluenceComponent(
            name="smart_money",
            weight=settings.confluence_weight_smart_money,
            score=sm_score,
            contribution=sm_score * settings.confluence_weight_smart_money,
        ))

        # ------------------------------------------------------------------ #
        # High weight
        # ------------------------------------------------------------------ #
        ema_score = 0.0
        if "ema" in indicators:
            ema_score = _clamp(indicators["ema"].get("score", 0.0))
        components.append(ConfluenceComponent(
            name="ema",
            weight=settings.confluence_weight_ema,
            score=ema_score,
            contribution=ema_score * settings.confluence_weight_ema,
        ))

        vol_score = 0.0
        if volume is not None:
            # Climax bullish + increasing volume = bullish confirmation
            if volume.climax:
                vol_score = 1.0 if volume.climax_direction == "BULLISH" else -1.0
            elif volume.spike_ratio > 1.5:
                # Use candle direction
                vol_score = 0.3 if volume.trend == "INCREASING" else -0.3
            if volume.exhaustion:
                vol_score *= 0.5  # dampen on exhaustion
        components.append(ConfluenceComponent(
            name="volume",
            weight=settings.confluence_weight_volume,
            score=_clamp(vol_score),
            contribution=vol_score * settings.confluence_weight_volume,
        ))

        pr_score = 0.0
        if pressure is not None:
            pr_score = _clamp(pressure.net_score)
        components.append(ConfluenceComponent(
            name="pressure",
            weight=settings.confluence_weight_pressure,
            score=pr_score,
            contribution=pr_score * settings.confluence_weight_pressure,
        ))

        oi_score = 0.0
        if oi is not None:
            # NEW_LONGS = bullish, NEW_SHORTS = bearish, spike = continuation in trend direction
            div = oi.divergence
            if div == "NEW_LONGS":
                oi_score = 1.0
            elif div == "NEW_SHORTS":
                oi_score = -1.0
            elif div == "SHORT_COVERING":
                oi_score = -0.3  # mild bearish (weak rally)
            elif div == "LONG_UNWIND":
                oi_score = 0.3  # mild bullish (weak sell-off)
            if oi.spike:
                # Reinforce the direction
                oi_score = oi_score * 1.2
        components.append(ConfluenceComponent(
            name="open_interest",
            weight=settings.confluence_weight_open_interest,
            score=_clamp(oi_score),
            contribution=oi_score * settings.confluence_weight_open_interest,
        ))

        fund_score = 0.0
        if funding is not None:
            # Contrarian at extremes
            if funding.regime == "EXTREME_LONG":
                fund_score = -0.7
            elif funding.regime == "EXTREME_SHORT":
                fund_score = 0.7
            elif funding.regime == "BULLISH_HEAT":
                fund_score = -0.3
            elif funding.regime == "BEARISH_HEAT":
                fund_score = 0.3
        components.append(ConfluenceComponent(
            name="funding",
            weight=settings.confluence_weight_funding,
            score=_clamp(fund_score),
            contribution=fund_score * settings.confluence_weight_funding,
        ))

        # ------------------------------------------------------------------ #
        # Medium weight
        # ------------------------------------------------------------------ #
        vwap_score = _clamp(indicators.get("vwap_position", 0.0))
        components.append(ConfluenceComponent(
            name="vwap",
            weight=settings.confluence_weight_vwap,
            score=vwap_score,
            contribution=vwap_score * settings.confluence_weight_vwap,
        ))

        atr_score = 0.0
        atr_pct = indicators.get("atr_pct", 0.0)
        # Higher ATR = more volatility — neutral direction, but contributes confidence
        if atr_pct > 1.0:
            atr_score = 0.3  # mild positive (volatility is good for momentum)
        components.append(ConfluenceComponent(
            name="atr",
            weight=settings.confluence_weight_atr,
            score=_clamp(atr_score),
            contribution=atr_score * settings.confluence_weight_atr,
        ))

        adx_score = 0.0
        adx_info = indicators.get("adx", {})
        if isinstance(adx_info, dict):
            adx_val = adx_info.get("adx", 0)
            plus_di = adx_info.get("plus_di", 0)
            minus_di = adx_info.get("minus_di", 0)
            if adx_val > 20:
                adx_score = _clamp((plus_di - minus_di) / 50.0)
        components.append(ConfluenceComponent(
            name="adx",
            weight=settings.confluence_weight_adx,
            score=_clamp(adx_score),
            contribution=adx_score * settings.confluence_weight_adx,
        ))

        bb_score = 0.0
        boll = indicators.get("bollinger") or (bollinger_bands([]) if False else None)
        if isinstance(boll, dict) and boll:
            percent_b = boll.get("percent_b", 0.5)
            # >0.8 = at upper band (overbought), <0.2 = at lower band (oversold)
            if percent_b > 0.8:
                bb_score = -0.3
            elif percent_b < 0.2:
                bb_score = 0.3
            else:
                bb_score = (percent_b - 0.5) * 0.4
        components.append(ConfluenceComponent(
            name="bollinger",
            weight=settings.confluence_weight_bollinger,
            score=_clamp(bb_score),
            contribution=bb_score * settings.confluence_weight_bollinger,
        ))

        sr_score = 0.0
        sr = indicators.get("support_resistance") or {}
        if sr:
            ns = sr.get("nearest_support")
            nr = sr.get("nearest_resistance")
            price = indicators.get("last_price", 0.0)
            if price and ns and nr:
                range_pct = (nr - ns) / price
                if range_pct > 0:
                    # Position within range: 0 = at support (bullish), 1 = at resistance (bearish)
                    pos = (price - ns) / (nr - ns)
                    sr_score = (0.5 - pos) * 2  # +1 at support, -1 at resistance
        components.append(ConfluenceComponent(
            name="support_resistance",
            weight=settings.confluence_weight_support_resistance,
            score=_clamp(sr_score),
            contribution=sr_score * settings.confluence_weight_support_resistance,
        ))

        # ------------------------------------------------------------------ #
        # Lowest weight
        # ------------------------------------------------------------------ #
        rsi_score = 0.0
        rsi_info = indicators.get("rsi") or {}
        if isinstance(rsi_info, dict):
            rsi_val = rsi_info.get("value", 50)
            slope = rsi_info.get("slope", 0)
            # RSI is supporting only — use slope, not absolute level
            rsi_score = _clamp(slope / 30.0)
        components.append(ConfluenceComponent(
            name="rsi",
            weight=settings.confluence_weight_rsi,
            score=_clamp(rsi_score),
            contribution=rsi_score * settings.confluence_weight_rsi,
        ))

        # ------------------------------------------------------------------ #
        # Final scoring
        # ------------------------------------------------------------------ #
        total_weight = sum(c.weight for c in components)
        if total_weight == 0:
            return ConfluenceResult(score=50, direction="NEUTRAL", components=components)

        raw = sum(c.contribution for c in components) / total_weight  # -1..+1
        # Scale to 0..100 with 50 = neutral
        score = int(round(50 + raw * 50))
        score = max(0, min(100, score))

        if raw > 0.15:
            direction = "BULLISH"
        elif raw < -0.15:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"

        # Dominant components: top 3 by absolute contribution
        dominant = sorted(components, key=lambda c: abs(c.contribution), reverse=True)[:3]
        dominant_names = [c.name for c in dominant if abs(c.contribution) > 0.1]

        return ConfluenceResult(
            score=score,
            direction=direction,
            components=components,
            dominant_components=dominant_names,
        )


__all__ = ["ConfluenceEngine", "ConfluenceResult", "ConfluenceComponent"]
