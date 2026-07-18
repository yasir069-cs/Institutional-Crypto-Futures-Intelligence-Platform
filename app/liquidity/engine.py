"""Liquidity engine — pools, sweeps, FVGs, order blocks.

Implements Smart Money Concepts liquidity analysis:

- **Liquidity pools**: equal highs/lows where stops cluster (retail traders
  place stops just beyond swing points).
- **Liquidity sweeps**: price wicks beyond a prior swing point then closes
  back — institutional stop hunt followed by reversal.
- **Fair Value Gaps (FVG)**: 3-candle imbalance where the gap between
  candle 1's high and candle 3's low (bullish) or candle 1's low and
  candle 3's high (bearish) leaves an unfilled gap.
- **Order blocks**: last down-candle before a strong up-move (bullish OB)
  or last up-candle before a strong down-move (bearish OB).
- **Breaker blocks**: an order block that failed and now acts as resistance.
- **Mitigation blocks**: prior supply/demand zone being revisited.

These are heavyweight analytical objects — Stage 2 computes them only for
candidates that passed Stage 1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Sequence

from app.exchange.binance_rest import Candle


class LiquidityType(str, Enum):
    BUY_SIDE = "BUY_SIDE"  # liquidity above swing highs (stops of shorts)
    SELL_SIDE = "SELL_SIDE"  # liquidity below swing lows (stops of longs)


class FVGType(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class OrderBlockType(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


@dataclass
class LiquidityPool:
    price: float
    type: LiquidityType
    touches: int
    last_touch_time: datetime
    swept: bool = False
    sweep_time: datetime | None = None


@dataclass
class LiquiditySweep:
    price: float
    type: LiquidityType
    time: datetime
    wick_pct: float  # how far past the pool price went
    recovered: bool  # did close back inside?


@dataclass
class FairValueGap:
    start_time: datetime
    end_time: datetime
    upper: float
    lower: float
    type: FVGType
    filled: bool = False


@dataclass
class OrderBlock:
    start_time: datetime
    end_time: datetime
    high: float
    low: float
    type: OrderBlockType
    mitigated: bool = False
    strength: float = 0.0


@dataclass
class LiquidityResult:
    pools: list[LiquidityPool] = field(default_factory=list)
    recent_sweeps: list[LiquiditySweep] = field(default_factory=list)
    fvgs: list[FairValueGap] = field(default_factory=list)
    order_blocks: list[OrderBlock] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pool_count": len(self.pools),
            "active_pools": sum(1 for p in self.pools if not p.swept),
            "sweep_count": len(self.recent_sweeps),
            "recent_sweep": self.recent_sweeps[-1].type.value if self.recent_sweeps else None,
            "fvg_count": len(self.fvgs),
            "unfilled_fvgs": sum(1 for f in self.fvgs if not f.filled),
            "order_blocks": len(self.order_blocks),
            "active_obs": [
                {
                    "type": ob.type.value,
                    "high": ob.high,
                    "low": ob.low,
                    "mitigated": ob.mitigated,
                    "strength": round(ob.strength, 3),
                }
                for ob in self.order_blocks[-3:]
            ],
        }


class LiquidityEngine:
    """Detect liquidity pools, sweeps, FVGs and order blocks from candle series."""

    def __init__(
        self,
        equal_highs_tolerance_pct: float = 0.05,
        min_ob_displacement_pct: float = 0.5,
    ) -> None:
        self._eh_tol = equal_highs_tolerance_pct / 100.0
        self._min_displacement = min_ob_displacement_pct / 100.0

    def analyze(self, candles: Sequence[Candle], lookback: int = 100) -> LiquidityResult:
        if len(candles) < 10:
            return LiquidityResult()

        window = candles[-lookback:] if len(candles) > lookback else list(candles)
        pools = self._find_pools(window)
        sweeps = self._find_sweeps(window, pools)
        fvgs = self._find_fvgs(window)
        obs = self._find_order_blocks(window)

        return LiquidityResult(
            pools=pools,
            recent_sweeps=sweeps,
            fvgs=fvgs,
            order_blocks=obs,
        )

    # ------------------------------------------------------------------ #
    # Liquidity pools (equal highs / lows)
    # ------------------------------------------------------------------ #
    def _find_pools(self, candles: Sequence[Candle]) -> list[LiquidityPool]:
        pools: list[LiquidityPool] = []
        # Find swing highs and lows
        highs = []
        lows = []
        for i in range(2, len(candles) - 2):
            if candles[i].high == max(c.high for c in candles[i - 2 : i + 3]):
                highs.append((i, candles[i].high, candles[i].open_time))
            if candles[i].low == min(c.low for c in candles[i - 2 : i + 3]):
                lows.append((i, candles[i].low, candles[i].open_time))

        # Cluster equal highs
        for i, (idx, price, t) in enumerate(highs):
            touches = 1
            for j in range(i + 1, len(highs)):
                if abs(highs[j][1] - price) / price < self._eh_tol:
                    touches += 1
                    t = max(t, highs[j][2])
            if touches >= 2:
                pools.append(LiquidityPool(
                    price=price, type=LiquidityType.BUY_SIDE, touches=touches, last_touch_time=t
                ))

        for i, (idx, price, t) in enumerate(lows):
            touches = 1
            for j in range(i + 1, len(lows)):
                if abs(lows[j][1] - price) / price < self._eh_tol:
                    touches += 1
                    t = max(t, lows[j][2])
            if touches >= 2:
                pools.append(LiquidityPool(
                    price=price, type=LiquidityType.SELL_SIDE, touches=touches, last_touch_time=t
                ))

        # Deduplicate pools within tolerance
        deduped: list[LiquidityPool] = []
        for p in pools:
            if any(abs(p.price - q.price) / p.price < self._eh_tol and p.type == q.type for q in deduped):
                continue
            deduped.append(p)
        return deduped

    # ------------------------------------------------------------------ #
    # Liquidity sweeps (stop hunts)
    # ------------------------------------------------------------------ #
    def _find_sweeps(self, candles: Sequence[Candle], pools: list[LiquidityPool]) -> list[LiquiditySweep]:
        sweeps: list[LiquiditySweep] = []
        if not candles or not pools:
            return sweeps
        last_candle = candles[-1]
        for pool in pools:
            if pool.swept:
                continue
            if pool.type == LiquidityType.BUY_SIDE and last_candle.high > pool.price:
                # Did the body close back below?
                recovered = last_candle.close < pool.price
                wick_pct = (last_candle.high - pool.price) / pool.price * 100
                sweeps.append(LiquiditySweep(
                    price=pool.price,
                    type=LiquidityType.BUY_SIDE,
                    time=last_candle.open_time,
                    wick_pct=wick_pct,
                    recovered=recovered,
                ))
                pool.swept = True
                pool.sweep_time = last_candle.open_time
            elif pool.type == LiquidityType.SELL_SIDE and last_candle.low < pool.price:
                recovered = last_candle.close > pool.price
                wick_pct = (pool.price - last_candle.low) / pool.price * 100
                sweeps.append(LiquiditySweep(
                    price=pool.price,
                    type=LiquidityType.SELL_SIDE,
                    time=last_candle.open_time,
                    wick_pct=wick_pct,
                    recovered=recovered,
                ))
                pool.swept = True
                pool.sweep_time = last_candle.open_time
        return sweeps

    # ------------------------------------------------------------------ #
    # Fair Value Gaps
    # ------------------------------------------------------------------ #
    def _find_fvgs(self, candles: Sequence[Candle]) -> list[FairValueGap]:
        fvgs: list[FairValueGap] = []
        for i in range(2, len(candles)):
            c1, c3 = candles[i - 2], candles[i]
            # Bullish FVG: c3.low > c1.high
            if c3.low > c1.high:
                fvgs.append(FairValueGap(
                    start_time=c1.open_time,
                    end_time=c3.open_time,
                    upper=c3.low,
                    lower=c1.high,
                    type=FVGType.BULLISH,
                ))
            # Bearish FVG: c3.high < c1.low
            elif c3.high < c1.low:
                fvgs.append(FairValueGap(
                    start_time=c1.open_time,
                    end_time=c3.open_time,
                    upper=c1.low,
                    lower=c3.high,
                    type=FVGType.BEARISH,
                ))

        # Mark filled FVGs
        if not candles:
            return fvgs
        last_price = candles[-1].close
        for fvg in fvgs:
            if fvg.type == FVGType.BULLISH and last_price < fvg.lower:
                fvg.filled = True
            elif fvg.type == FVGType.BEARISH and last_price > fvg.upper:
                fvg.filled = True
        return fvgs[-20:]  # last 20

    # ------------------------------------------------------------------ #
    # Order blocks
    # ------------------------------------------------------------------ #
    def _find_order_blocks(self, candles: Sequence[Candle]) -> list[OrderBlock]:
        obs: list[OrderBlock] = []
        for i in range(1, len(candles) - 1):
            prev, curr, nxt = candles[i - 1], candles[i], candles[i + 1]
            displacement = (nxt.close - curr.close) / curr.close if curr.close > 0 else 0
            if abs(displacement) < self._min_displacement:
                continue

            if displacement > 0 and curr.close < curr.open:
                # Bullish OB: last down candle before big up move
                obs.append(OrderBlock(
                    start_time=curr.open_time,
                    end_time=curr.close_time,
                    high=curr.high,
                    low=curr.low,
                    type=OrderBlockType.BULLISH,
                    strength=abs(displacement),
                ))
            elif displacement < 0 and curr.close > curr.open:
                # Bearish OB: last up candle before big down move
                obs.append(OrderBlock(
                    start_time=curr.open_time,
                    end_time=curr.close_time,
                    high=curr.high,
                    low=curr.low,
                    type=OrderBlockType.BEARISH,
                    strength=abs(displacement),
                ))

        # Mark mitigation: price returned to OB zone after creation
        if not candles:
            return obs
        last_price = candles[-1].close
        for ob in obs:
            if ob.low <= last_price <= ob.high:
                ob.mitigated = True
        return obs[-10:]


__all__ = [
    "LiquidityEngine",
    "LiquidityResult",
    "LiquidityPool",
    "LiquiditySweep",
    "FairValueGap",
    "OrderBlock",
    "LiquidityType",
    "FVGType",
    "OrderBlockType",
]
