"""Buy/sell pressure engine.

Classifies trades as aggressive-buy or aggressive-sell based on the
``is_buyer_maker`` flag in Binance aggTrades (a taker BUY is a trade where
``is_buyer_maker == false``; taker SELL when ``is_buyer_maker == true``).

Combines:
- Recent trade tape (last N trades from MarketDataEngine)
- Taker buy volume from candle history
- Order book top-of-book imbalance

Output: a net pressure score in [-1, +1] and a confidence value.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Sequence

from app.exchange.binance_rest import AggTrade, Candle
from app.market.data_engine import SymbolState


@dataclass
class PressureResult:
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    buy_pct: float = 0.5
    net_score: float = 0.0  # -1..+1
    confidence: float = 0.0  # 0..1
    book_imbalance: float = 0.0  # -1..+1
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "buy_volume": self.buy_volume,
            "sell_volume": self.sell_volume,
            "buy_pct": round(self.buy_pct, 3),
            "net_score": round(self.net_score, 3),
            "confidence": round(self.confidence, 3),
            "book_imbalance": round(self.book_imbalance, 3),
            "summary": self.summary,
        }


class PressureEngine:
    """Compute net buy/sell pressure from trade tape and candles."""

    def __init__(self, min_trades: int = 30, lookback_candles: int = 20) -> None:
        self._min_trades = min_trades
        self._lookback = lookback_candles

    def analyze(
        self,
        trades: Sequence[AggTrade] | None = None,
        candles: Sequence[Candle] | None = None,
        state: SymbolState | None = None,
    ) -> PressureResult:
        # 1. Trade tape classification
        buy_vol_tape = 0.0
        sell_vol_tape = 0.0
        if trades:
            recent = list(trades)[-200:]
            for t in recent:
                if t.is_buyer_maker:
                    sell_vol_tape += t.quantity
                else:
                    buy_vol_tape += t.quantity

        # 2. Taker buy/sell from candles (more reliable for low-trade symbols)
        buy_vol_candles = 0.0
        sell_vol_candles = 0.0
        if candles:
            recent_c = list(candles)[-self._lookback:]
            for c in recent_c:
                buy_vol_candles += c.taker_buy_volume
                sell_vol_candles += c.volume - c.taker_buy_volume

        # Combine: prefer candles (more samples), supplement with tape
        buy_vol = buy_vol_candles + buy_vol_tape * 0.1
        sell_vol = sell_vol_candles + sell_vol_tape * 0.1
        total = buy_vol + sell_vol
        buy_pct = buy_vol / total if total > 0 else 0.5
        net = (buy_pct - 0.5) * 2  # scale to -1..+1

        # Confidence based on sample size
        confidence = min(1.0, total / 100_000) if total > 0 else 0.0

        # Order book imbalance
        book_imb = 0.0
        if state is not None:
            bid_q = state.bid_qty
            ask_q = state.ask_qty
            if bid_q + ask_q > 0:
                book_imb = (bid_q - ask_q) / (bid_q + ask_q)

        # Aggregate score: 70% tape/candle pressure, 30% book imbalance
        net_score = net * 0.7 + book_imb * 0.3
        net_score = max(-1.0, min(1.0, net_score))

        summary = self._build_summary(buy_pct, net_score, book_imb)
        return PressureResult(
            buy_volume=buy_vol,
            sell_volume=sell_vol,
            buy_pct=buy_pct,
            net_score=net_score,
            confidence=confidence,
            book_imbalance=book_imb,
            summary=summary,
        )

    def _build_summary(self, buy_pct: float, net_score: float, book_imb: float) -> str:
        if net_score > 0.3:
            tone = "Strong buy pressure"
        elif net_score > 0.1:
            tone = "Mild buy pressure"
        elif net_score < -0.3:
            tone = "Strong sell pressure"
        elif net_score < -0.1:
            tone = "Mild sell pressure"
        else:
            tone = "Balanced pressure"
        return (
            f"{tone} (buy {buy_pct*100:.1f}%, net {net_score:+.2f}, "
            f"book {book_imb:+.2f})"
        )


__all__ = ["PressureEngine", "PressureResult"]
