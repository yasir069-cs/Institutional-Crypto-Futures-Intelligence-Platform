"""Unified in-memory market state cache.

Holds the **latest** snapshot of every tracked symbol's:
- ticker (last price, 24h vol)
- order book (top N levels)
- funding rate / mark price
- open interest
- recent trades (rolling window)
- liquidation feed (rolling window)

The cache is the **single source of truth** for live data. Stage 1 scanner
reads from here instead of hitting the REST API per-pair, which is what
makes sub-5-second scanning of 500 pairs possible.

Design notes
------------
- All updates are O(1) — overwrite the latest slot.
- Trade and liquidation windows use bounded deques.
- Concurrency: per-symbol asyncio.Lock for atomic updates, but reads are
  lock-free (we accept eventual consistency for scanner reads).
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque

from app.core.logging import get_logger
from app.exchange.binance_rest import (
    AggTrade,
    FundingRate,
    OpenInterest,
    OrderBook,
    Ticker24h,
)

log = get_logger(__name__)

TRADE_WINDOW = 500  # rolling window of recent trades per symbol
LIQ_WINDOW = 100


@dataclass
class SymbolState:
    """Per-symbol live market state."""

    symbol: str
    # Ticker
    last_price: float = 0.0
    price_change_pct_24h: float = 0.0
    volume_24h: float = 0.0
    quote_volume_24h: float = 0.0
    high_24h: float = 0.0
    low_24h: float = 0.0
    trade_count_24h: int = 0
    last_ticker_update: datetime | None = None

    # Order book (top of book aggregated)
    bid_price: float = 0.0
    bid_qty: float = 0.0
    ask_price: float = 0.0
    ask_qty: float = 0.0
    book_depth_bids: list[tuple[float, float]] = field(default_factory=list)
    book_depth_asks: list[tuple[float, float]] = field(default_factory=list)
    last_book_update: datetime | None = None

    # Funding / mark
    mark_price: float = 0.0
    funding_rate: float = 0.0
    next_funding_time: datetime | None = None
    last_funding_update: datetime | None = None

    # Open interest
    open_interest: float = 0.0
    open_interest_value: float = 0.0
    last_oi_update: datetime | None = None

    # Trades (rolling)
    recent_trades: Deque[AggTrade] = field(default_factory=lambda: deque(maxlen=TRADE_WINDOW))

    # Liquidations (rolling)
    recent_liquidations: Deque[dict] = field(default_factory=lambda: deque(maxlen=LIQ_WINDOW))

    # Bookkeeping
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MarketDataEngine:
    """In-memory cache of live market state for all tracked symbols."""

    def __init__(self, rest: "Any | None" = None) -> None:
        # ``rest`` is an optional BinanceRestClient. Accept Any to avoid a
        # circular import (BinanceRestClient imports dataclasses from this
        # module's package).
        self._rest = rest
        self._states: dict[str, SymbolState] = {}
        self._lock_per_symbol: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._subscribers: list = []  # type: ignore[var-annotated]

    # ------------------------------------------------------------------ #
    # Symbol management
    # ------------------------------------------------------------------ #
    def ensure_symbol(self, symbol: str) -> SymbolState:
        state = self._states.get(symbol)
        if state is None:
            state = SymbolState(symbol=symbol)
            self._states[symbol] = state
            self._lock_per_symbol[symbol] = asyncio.Lock()
        return state

    def known_symbols(self) -> list[str]:
        return list(self._states.keys())

    def get(self, symbol: str) -> SymbolState | None:
        return self._states.get(symbol)

    def all_states(self) -> list[SymbolState]:
        return list(self._states.values())

    # ------------------------------------------------------------------ #
    # Updates (called from WS callbacks)
    # ------------------------------------------------------------------ #
    async def update_ticker(self, ticker: Ticker24h) -> None:
        state = self.ensure_symbol(ticker.symbol)
        async with self._lock_per_symbol[ticker.symbol]:
            state.last_price = ticker.last_price
            state.price_change_pct_24h = ticker.price_change_pct
            state.volume_24h = ticker.volume
            state.quote_volume_24h = ticker.quote_volume
            state.high_24h = ticker.high
            state.low_24h = ticker.low
            state.trade_count_24h = ticker.trade_count
            state.last_ticker_update = datetime.now(timezone.utc)
            state.updated_at = state.last_ticker_update

    async def update_ticker_from_ws(self, data: dict) -> None:
        """Update ticker from a ``@ticker`` WS payload."""
        symbol = data.get("s", "")
        if not symbol:
            return
        state = self.ensure_symbol(symbol)
        async with self._lock_per_symbol[symbol]:
            state.last_price = float(data.get("c", state.last_price))
            state.price_change_pct_24h = float(data.get("P", state.price_change_pct_24h))
            state.volume_24h = float(data.get("v", state.volume_24h))
            state.quote_volume_24h = float(data.get("q", state.quote_volume_24h))
            state.high_24h = float(data.get("h", state.high_24h))
            state.low_24h = float(data.get("l", state.low_24h))
            state.trade_count_24h = int(data.get("n", state.trade_count_24h))
            state.last_ticker_update = datetime.now(timezone.utc)
            state.updated_at = state.last_ticker_update

    async def update_book(self, book: OrderBook) -> None:
        state = self.ensure_symbol(book.symbol)
        async with self._lock_per_symbol[book.symbol]:
            if book.bids:
                state.bid_price = book.bids[0].price
                state.bid_qty = book.bids[0].quantity
                state.book_depth_bids = [(b.price, b.quantity) for b in book.bids[:20]]
            if book.asks:
                state.ask_price = book.asks[0].price
                state.ask_qty = book.asks[0].quantity
                state.book_depth_asks = [(a.price, a.quantity) for a in book.asks[:20]]
            state.last_book_update = datetime.now(timezone.utc)
            state.updated_at = state.last_book_update

    async def update_book_from_ws(self, data: dict) -> None:
        """Update book from a ``@depth20@100ms`` WS payload."""
        symbol = data.get("s", "")
        if not symbol:
            return
        state = self.ensure_symbol(symbol)
        async with self._lock_per_symbol[symbol]:
            bids = data.get("b", []) or data.get("bids", [])
            asks = data.get("a", []) or data.get("asks", [])
            if bids:
                state.bid_price = float(bids[0][0])
                state.bid_qty = float(bids[0][1])
                state.book_depth_bids = [(float(b[0]), float(b[1])) for b in bids[:20]]
            if asks:
                state.ask_price = float(asks[0][0])
                state.ask_qty = float(asks[0][1])
                state.book_depth_asks = [(float(a[0]), float(a[1])) for a in asks[:20]]
            state.last_book_update = datetime.now(timezone.utc)
            state.updated_at = state.last_book_update

    async def update_funding(self, fr: FundingRate) -> None:
        state = self.ensure_symbol(fr.symbol)
        async with self._lock_per_symbol[fr.symbol]:
            state.mark_price = fr.mark_price
            state.funding_rate = fr.funding_rate
            state.next_funding_time = fr.next_funding_time
            state.last_funding_update = datetime.now(timezone.utc)
            state.updated_at = state.last_funding_update

    async def update_funding_from_ws(self, data: dict) -> None:
        """Update from ``@markPrice@1s`` WS payload (includes funding)."""
        symbol = data.get("s", "")
        if not symbol:
            return
        state = self.ensure_symbol(symbol)
        async with self._lock_per_symbol[symbol]:
            state.mark_price = float(data.get("p", state.mark_price))
            state.funding_rate = float(data.get("r", state.funding_rate))
            nft = data.get("T")
            state.next_funding_time = (
                datetime.fromtimestamp(int(nft) / 1000, tz=timezone.utc) if nft else None
            )
            state.last_funding_update = datetime.now(timezone.utc)
            state.updated_at = state.last_funding_update

    async def update_open_interest(self, oi: OpenInterest) -> None:
        state = self.ensure_symbol(oi.symbol)
        async with self._lock_per_symbol[oi.symbol]:
            state.open_interest = oi.open_interest
            state.open_interest_value = oi.open_interest_value
            state.last_oi_update = datetime.now(timezone.utc)
            state.updated_at = state.last_oi_update

    async def update_open_interest_from_ws(self, data: dict) -> None:
        symbol = data.get("symbol") or data.get("s", "")
        if not symbol:
            return
        state = self.ensure_symbol(symbol)
        async with self._lock_per_symbol[symbol]:
            state.open_interest = float(data.get("openInterest", state.open_interest))
            state.last_oi_update = datetime.now(timezone.utc)
            state.updated_at = state.last_oi_update

    async def add_trade(self, trade: AggTrade) -> None:
        state = self.ensure_symbol(trade.symbol)
        async with self._lock_per_symbol[trade.symbol]:
            state.recent_trades.append(trade)
            state.last_price = trade.price
            state.updated_at = datetime.now(timezone.utc)

    async def add_trade_from_ws(self, data: dict) -> None:
        symbol = data.get("s", "")
        if not symbol:
            return
        try:
            trade = AggTrade(
                symbol=symbol,
                price=float(data["p"]),
                quantity=float(data["q"]),
                is_buyer_maker=bool(data["m"]),
                timestamp=datetime.fromtimestamp(int(data["T"]) / 1000, tz=timezone.utc),
            )
        except (KeyError, ValueError):
            return
        await self.add_trade(trade)

    async def add_liquidation_from_ws(self, data: dict) -> None:
        """Add a liquidation event from ``!forceOrder@arr`` or per-symbol stream."""
        try:
            o = data.get("o", data)
            symbol = o.get("s", "")
            if not symbol:
                return
            state = self.ensure_symbol(symbol)
            liq = {
                "symbol": symbol,
                "side": o.get("S", ""),
                "price": float(o.get("p", 0)),
                "qty": float(o.get("q", 0)),
                "value": float(o.get("p", 0)) * float(o.get("q", 0)),
                "time": datetime.fromtimestamp(int(o.get("T", 0)) / 1000, tz=timezone.utc),
            }
            async with self._lock_per_symbol[symbol]:
                state.recent_liquidations.append(liq)
                state.updated_at = datetime.now(timezone.utc)
        except Exception:  # noqa: BLE001
            log.exception("liquidation_parse_failed", data=str(data)[:200])

    # ------------------------------------------------------------------ #
    # Snapshot helpers (lock-free reads for scanner)
    # ------------------------------------------------------------------ #
    def snapshot(self, symbol: str) -> SymbolState | None:
        """Return current state (lock-free; may be slightly stale)."""
        return self._states.get(symbol)

    def top_volume_symbols(self, limit: int = 500) -> list[SymbolState]:
        """Return top symbols by 24h quote volume (used for scan candidate list)."""
        return sorted(
            self._states.values(),
            key=lambda s: s.quote_volume_24h,
            reverse=True,
        )[:limit]

    # ------------------------------------------------------------------ #
    # Subscription (pub/sub for future UI / WS broadcast)
    # ------------------------------------------------------------------ #
    def subscribe(self, callback) -> None:  # type: ignore[no-untyped-def]
        self._subscribers.append(callback)

    async def _notify(self, symbol: str, event: str) -> None:
        for cb in self._subscribers:
            try:
                await cb(symbol, event)
            except Exception:  # noqa: BLE001
                log.exception("market_data_subscriber_failed")

    async def aclose(self) -> None:
        self._states.clear()
        self._lock_per_symbol.clear()
        self._subscribers.clear()


__all__ = ["MarketDataEngine", "SymbolState"]
