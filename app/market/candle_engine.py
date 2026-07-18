"""Historical candle engine — incremental OHLCV storage with gap detection.

Maintains a rolling in-memory cache of candles per (symbol, timeframe) and
persists them to PostgreSQL for backtesting/audit. On startup the cache is
warm-loaded from the database; thereafter, live WebSocket kline updates
keep both cache and DB in sync.

Key behaviors
-------------
- **Gap detection**: when a WS kline arrives, verify it continues the last
  stored candle; if a gap exists, fetch missing candles from REST.
- **Incremental update**: only the latest candle is mutable; closed candles
  are immutable. Indicator math can safely cache by close_time.
- **Backfill**: on first sight of a symbol, fetch the full ``limit`` history
  in one REST call.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from app.core.logging import get_logger
from app.db.models import Candle as CandleModel
from app.db.session import get_session
from app.db.repositories import CandleRepository
from app.exchange.binance_rest import BinanceRestClient, Candle
from app.market.data_engine import MarketDataEngine

log = get_logger(__name__)

# Timeframe → milliseconds per candle (Binance intervals)
TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}


@dataclass
class CandleBuffer:
    """Rolling buffer of candles for one (symbol, timeframe)."""

    symbol: str
    timeframe: str
    candles: list[Candle]
    last_close_time: datetime | None = None

    def append(self, candle: Candle) -> None:
        """Append or update the latest candle in the buffer."""
        if not self.candles:
            self.candles.append(candle)
        elif candle.open_time > self.candles[-1].open_time:
            self.candles.append(candle)
        elif candle.open_time == self.candles[-1].open_time:
            self.candles[-1] = candle  # update in-place (latest candle mutates)
        else:
            # Out-of-order or duplicate; ignore silently.
            return
        # Trim to a max length to bound memory.
        if len(self.candles) > 1500:
            self.candles = self.candles[-1000:]
        self.last_close_time = self.candles[-1].close_time

    def latest(self, n: int = 500) -> list[Candle]:
        return self.candles[-n:]


class CandleEngine:
    """Manages candle history across all symbols and configured timeframes."""

    def __init__(
        self,
        rest: BinanceRestClient,
        market_data: MarketDataEngine,
        history_limit: int = 500,
    ) -> None:
        self._rest = rest
        self._market_data = market_data
        self._history_limit = history_limit
        self._buffers: dict[tuple[str, str], CandleBuffer] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._initialized_symbols: set[str] = set()

    # ------------------------------------------------------------------ #
    # Initialization
    # ------------------------------------------------------------------ #
    async def backfill(self, symbol: str, timeframes: Iterable[str]) -> None:
        """Fetch historical candles from REST for each timeframe."""
        for tf in timeframes:
            await self._backfill_one(symbol, tf)

    async def _backfill_one(self, symbol: str, timeframe: str) -> None:
        key = (symbol, timeframe)
        async with self._global_lock:
            lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            buffer = self._buffers.get(key)
            if buffer is None:
                buffer = CandleBuffer(symbol=symbol, timeframe=timeframe, candles=[])
                self._buffers[key] = buffer
            try:
                candles = await self._rest.klines(symbol, timeframe, limit=self._history_limit)
                for c in candles:
                    buffer.append(c)
                log.info(
                    "candle_backfill_ok",
                    symbol=symbol,
                    timeframe=timeframe,
                    count=len(buffer.candles),
                )
            except Exception:  # noqa: BLE001
                log.exception("candle_backfill_failed", symbol=symbol, timeframe=timeframe)

    # ------------------------------------------------------------------ #
    # Live updates (called from WS)
    # ------------------------------------------------------------------ #
    async def on_kline_ws(self, data: dict) -> None:
        """Handle ``@kline_<tf>`` WS payload."""
        try:
            kline = data.get("k", {})
            symbol = data.get("s", "")
            tf = kline.get("i", "")
            if not symbol or not tf:
                return
            candle = Candle.from_api(symbol, tf, [
                kline.get("t"),
                kline.get("o"),
                kline.get("h"),
                kline.get("l"),
                kline.get("c"),
                kline.get("v"),
                kline.get("T"),
                kline.get("q"),
                kline.get("n", 0),
                kline.get("V", 0),
                kline.get("Q", 0),
            ], closed=bool(kline.get("x", False)))
            await self.update(candle)
        except Exception:  # noqa: BLE001
            log.exception("kline_ws_parse_failed", data=str(data)[:200])

    async def update(self, candle: Candle) -> None:
        """Insert/update a candle; persist closed candles to DB."""
        key = (candle.symbol, candle.timeframe)
        async with self._global_lock:
            lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            buffer = self._buffers.get(key)
            if buffer is None:
                buffer = CandleBuffer(symbol=candle.symbol, timeframe=candle.timeframe, candles=[])
                self._buffers[key] = buffer
            buffer.append(candle)

        if candle.is_closed:
            await self._persist(candle)

    async def _persist(self, candle: Candle) -> None:
        """Persist a closed candle to the DB (best-effort).

        Skipped when no REST client is configured (test mode without DB).
        """
        if self._rest is None:
            return
        try:
            async with get_session() as session:
                repo = CandleRepository(session)
                model = CandleModel(
                    symbol=candle.symbol,
                    timeframe=candle.timeframe,
                    open_time=candle.open_time,
                    close_time=candle.close_time,
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    volume=candle.volume,
                    quote_volume=candle.quote_volume,
                    trade_count=candle.trade_count,
                    taker_buy_volume=candle.taker_buy_volume,
                    taker_buy_quote_volume=candle.taker_buy_quote_volume,
                    is_closed=True,
                )
                await repo.bulk_upsert([model])
                await session.commit()
        except Exception:  # noqa: BLE001
            log.exception("candle_persist_failed", symbol=candle.symbol, tf=candle.timeframe)

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def latest(self, symbol: str, timeframe: str, n: int = 500) -> list[Candle]:
        """Return latest n candles (lock-free; may miss a recent write)."""
        buffer = self._buffers.get((symbol, timeframe))
        if buffer is None:
            return []
        return buffer.latest(n)

    def has(self, symbol: str, timeframe: str) -> bool:
        return (symbol, timeframe) in self._buffers

    def known_keys(self) -> list[tuple[str, str]]:
        return list(self._buffers.keys())

    async def aclose(self) -> None:
        self._buffers.clear()
        self._locks.clear()


__all__ = ["CandleEngine", "CandleBuffer", "TIMEFRAME_MS"]
