"""Smart money engine — institutional flow detection.

Combines signals from multiple sources to detect institutional activity:

- **Volume footprint**: large taker buy/sell imbalance on key candles
- **Order book imbalance**: bid/ask stack skew
- **Liquidity sweep + recovery**: classic institutional stop hunt
- **Open interest surge + price direction**: new money entering
- **Funding rate divergence**: contrarian signal at extremes
- **Aggressive tape reading**: clustered large trades in one direction

Output is a single score and summary that the confluence engine weights
heavily.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from app.exchange.binance_rest import Candle
from app.liquidity.engine import LiquidityResult, LiquidityEngine
from app.market.data_engine import SymbolState
from app.market.indicator_engine import volume_spike_ratio


@dataclass
class SmartMoneyResult:
    institutional_buying: float = 0.0  # 0..1
    institutional_selling: float = 0.0  # 0..1
    net_flow: float = 0.0  # -1..+1
    summary: str = ""
    signals: list[str] = field(default_factory=list)
    score: float = 0.0  # -100..+100

    def to_dict(self) -> dict:
        return {
            "institutional_buying": round(self.institutional_buying, 3),
            "institutional_selling": round(self.institutional_selling, 3),
            "net_flow": round(self.net_flow, 3),
            "score": round(self.score, 1),
            "signals": self.signals,
            "summary": self.summary,
        }


class SmartMoneyEngine:
    """Detect institutional flow from candle tape + market state + liquidity."""

    def __init__(self, liquidity_engine: LiquidityEngine | None = None) -> None:
        self._liquidity = liquidity_engine or LiquidityEngine()

    def analyze(
        self,
        candles: Sequence[Candle],
        state: SymbolState | None = None,
        liquidity: LiquidityResult | None = None,
    ) -> SmartMoneyResult:
        if not candles or len(candles) < 10:
            return SmartMoneyResult()

        result = SmartMoneyResult()
        signals: list[str] = []

        # 1. Taker buy/sell imbalance over recent window
        recent = candles[-20:]
        taker_buy = sum(c.taker_buy_volume for c in recent)
        total = sum(c.volume for c in recent)
        if total > 0:
            buy_pct = taker_buy / total
            # >0.55 = buy pressure, <0.45 = sell pressure
            if buy_pct > 0.55:
                result.institutional_buying = min(1.0, (buy_pct - 0.5) * 4)
                signals.append(f"taker_buy_dominance_{buy_pct:.2f}")
            elif buy_pct < 0.45:
                result.institutional_selling = min(1.0, (0.5 - buy_pct) * 4)
                signals.append(f"taker_sell_dominance_{buy_pct:.2f}")

        # 2. Volume spike + directional close
        spike = volume_spike_ratio(candles)
        if spike > 2.0:
            last = candles[-1]
            prev = candles[-2]
            if last.close > prev.close:
                result.institutional_buying = min(1.0, result.institutional_buying + 0.2)
                signals.append(f"volume_spike_bull_{spike:.1f}x")
            else:
                result.institutional_selling = min(1.0, result.institutional_selling + 0.2)
                signals.append(f"volume_spike_bear_{spike:.1f}x")

        # 3. Liquidity sweep recovery (strong institutional reversal signal)
        if liquidity is None:
            liquidity = self._liquidity.analyze(candles)
        for sweep in liquidity.recent_sweeps:
            if sweep.recovered:
                if sweep.type.value == "BUY_SIDE":
                    result.institutional_selling = min(1.0, result.institutional_selling + 0.3)
                    signals.append("sell_side_sweep_recovery")
                else:
                    result.institutional_buying = min(1.0, result.institutional_buying + 0.3)
                    signals.append("buy_side_sweep_recovery")

        # 4. Order block mitigation
        for ob in liquidity.order_blocks:
            if ob.mitigated:
                if ob.type.value == "BULLISH":
                    result.institutional_buying = min(1.0, result.institutional_buying + 0.15)
                    signals.append("bullish_ob_mitigated")
                else:
                    result.institutional_selling = min(1.0, result.institutional_selling + 0.15)
                    signals.append("bearish_ob_mitigated")

        # 5. Order book imbalance (live state)
        if state is not None:
            bid_q = state.bid_qty
            ask_q = state.ask_qty
            total_q = bid_q + ask_q
            if total_q > 0:
                imb = (bid_q - ask_q) / total_q
                if imb > 0.3:
                    result.institutional_buying = min(1.0, result.institutional_buying + 0.15)
                    signals.append(f"book_imbalance_bull_{imb:.2f}")
                elif imb < -0.3:
                    result.institutional_selling = min(1.0, result.institutional_selling + 0.15)
                    signals.append(f"book_imbalance_bear_{imb:.2f}")

        # Net flow score
        result.net_flow = result.institutional_buying - result.institutional_selling
        result.score = result.net_flow * 100
        result.signals = signals
        result.summary = self._build_summary(result)
        return result

    def _build_summary(self, r: SmartMoneyResult) -> str:
        if r.score > 30:
            tone = "Strong institutional buying"
        elif r.score > 10:
            tone = "Mild institutional buying"
        elif r.score < -30:
            tone = "Strong institutional selling"
        elif r.score < -10:
            tone = "Mild institutional selling"
        else:
            tone = "Balanced flow / no clear institutional activity"
        return f"{tone} (score {r.score:+.0f}, signals: {', '.join(r.signals[:3]) or 'none'})"


__all__ = ["SmartMoneyEngine", "SmartMoneyResult"]
