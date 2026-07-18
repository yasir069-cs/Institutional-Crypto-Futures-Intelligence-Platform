"""Async Binance USDT Futures REST client.

Covers all public market-data endpoints the platform needs. Private
endpoints (account, order) are stubbed for future use but disabled by
default (alert-only platform).

Every request goes through the rate limiter and has:
- Timeout protection
- Retry with exponential backoff for 5xx / network errors
- Structured logging with weight consumption
- Conversion to typed dataclasses where possible

Endpoint weights (Binance Futures docs as of 2024):
- /fapi/v1/exchangeInfo:           1
- /fapi/v1/klines:                 based on limit (1 for ≤100, 2 for ≤500, 5 for ≤1000, 10 for >1000)
- /fapi/v1/ticker/24hr:            40 (all), 1 (single)
- /fapi/v1/ticker/price:           2 (all), 1 (single)
- /fapi/v1/depth:                  2-20 based on limit
- /fapi/v1/fundingRate:            1
- /fapi/v1/openInterest:           1
- /futures/data/openInterestHist:  1
- /futures/data/topLongShortPositionRatio: 1
- /futures/data/takerlongshortRatio: 1
- /fapi/v1/aggTrades:              20
- /fapi/v1/markPrice:              1
"""

from __future__ import annotations

import asyncio
import hmac
import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import aiohttp

from app.config import settings
from app.core.errors import ExchangeError
from app.core.logging import get_logger
from app.exchange.rate_limiter import BinanceRateLimiter

log = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Response dataclasses
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Ticker24h:
    symbol: str
    last_price: float
    price_change_pct: float
    volume: float
    quote_volume: float
    high: float
    low: float
    trade_count: int

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "Ticker24h":
        return cls(
            symbol=str(d["symbol"]),
            last_price=float(d["lastPrice"]),
            price_change_pct=float(d["priceChangePercent"]),
            volume=float(d["volume"]),
            quote_volume=float(d["quoteVolume"]),
            high=float(d["highPrice"]),
            low=float(d["lowPrice"]),
            trade_count=int(d["count"]),
        )


@dataclass(frozen=True)
class Candle:
    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trade_count: int
    taker_buy_volume: float
    taker_buy_quote_volume: float
    is_closed: bool

    @classmethod
    def from_api(
        cls, symbol: str, timeframe: str, raw: list[Any], closed: bool = True
    ) -> "Candle":
        return cls(
            symbol=symbol,
            timeframe=timeframe,
            open_time=datetime.fromtimestamp(int(raw[0]) / 1000, tz=timezone.utc),
            close_time=datetime.fromtimestamp(int(raw[6]) / 1000, tz=timezone.utc),
            open=float(raw[1]),
            high=float(raw[2]),
            low=float(raw[3]),
            close=float(raw[4]),
            volume=float(raw[5]),
            quote_volume=float(raw[7]),
            trade_count=int(raw[8]),
            taker_buy_volume=float(raw[9]),
            taker_buy_quote_volume=float(raw[10]),
            is_closed=closed,
        )


@dataclass(frozen=True)
class OrderBookEntry:
    price: float
    quantity: float


@dataclass(frozen=True)
class OrderBook:
    symbol: str
    last_update_id: int
    bids: list[OrderBookEntry] = field(default_factory=list)
    asks: list[OrderBookEntry] = field(default_factory=list)

    @classmethod
    def from_api(cls, symbol: str, d: dict[str, Any]) -> "OrderBook":
        return cls(
            symbol=symbol,
            last_update_id=int(d["lastUpdateId"]),
            bids=[OrderBookEntry(float(b[0]), float(b[1])) for b in d["bids"]],
            asks=[OrderBookEntry(float(a[0]), float(a[1])) for a in d["asks"]],
        )


@dataclass(frozen=True)
class FundingRate:
    symbol: str
    mark_price: float
    funding_rate: float
    next_funding_time: datetime

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "FundingRate":
        return cls(
            symbol=str(d["symbol"]),
            mark_price=float(d["markPrice"]),
            funding_rate=float(d["lastFundingRate"]),
            next_funding_time=datetime.fromtimestamp(
                int(d["nextFundingTime"]) / 1000, tz=timezone.utc
            ),
        )


@dataclass(frozen=True)
class OpenInterest:
    symbol: str
    open_interest: float
    open_interest_value: float
    timestamp: datetime

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "OpenInterest":
        ts = d.get("time") or d.get("timestamp")
        return cls(
            symbol=str(d["symbol"]),
            open_interest=float(d["openInterest"]),
            open_interest_value=float(d.get("openInterestValue", 0.0)),
            timestamp=datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc) if ts else datetime.now(timezone.utc),
        )


@dataclass(frozen=True)
class AggTrade:
    symbol: str
    price: float
    quantity: float
    is_buyer_maker: bool
    timestamp: datetime

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "AggTrade":
        return cls(
            symbol=str(d["s"]),
            price=float(d["p"]),
            quantity=float(d["q"]),
            is_buyer_maker=bool(d["m"]),
            timestamp=datetime.fromtimestamp(int(d["T"]) / 1000, tz=timezone.utc),
        )


@dataclass(frozen=True)
class SymbolInfo:
    symbol: str
    base_asset: str
    quote_asset: str
    contract_type: str
    price_precision: int
    quantity_precision: int
    tick_size: float
    step_size: float
    min_notional: float
    status: str

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "SymbolInfo":
        filters = {f["filterType"]: f for f in d.get("filters", [])}
        price_filter = filters.get("PRICE_FILTER", {})
        lot_size = filters.get("LOT_SIZE", {})
        min_notional_filter = filters.get("MIN_NOTIONAL", {})
        return cls(
            symbol=str(d["symbol"]),
            base_asset=str(d.get("baseAsset", "")),
            quote_asset=str(d.get("quoteAsset", "")),
            contract_type=str(d.get("contractType", "PERPETUAL")),
            price_precision=int(d.get("pricePrecision", 2)),
            quantity_precision=int(d.get("quantityPrecision", 3)),
            tick_size=float(price_filter.get("tickSize", 0.01)),
            step_size=float(lot_size.get("stepSize", 0.001)),
            min_notional=float(min_notional_filter.get("notional", 5.0)),
            status=str(d.get("status", "TRADING")),
        )


# --------------------------------------------------------------------------- #
# REST client
# --------------------------------------------------------------------------- #
class BinanceRestClient:
    """Async client for Binance USDT Futures REST API.

    The client is alert-only by design — order endpoints raise
    ``NotImplementedError`` to prevent accidental live trading.
    """

    BASE_PATH = ""  # settings.binance_rest_base_url already includes /fapi

    def __init__(self) -> None:
        self._base_url = settings.binance_rest_base_url
        self._session: aiohttp.ClientSession | None = None
        self._limiter = BinanceRateLimiter()
        self._weight_used_header = 0

    # ------------------------------------------------------------------ #
    # Session lifecycle
    # ------------------------------------------------------------------ #
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=settings.binance_request_timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def aclose(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    # ------------------------------------------------------------------ #
    # Core request
    # ------------------------------------------------------------------ #
    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        weight: int = 1,
        signed: bool = False,
        retry: int = 3,
    ) -> Any:
        """Issue a rate-limited request with retries."""
        await self._limiter.acquire_weight(weight)

        url = f"{self._base_url}{path}"
        params = dict(params or {})

        if signed:
            params.update(self._sign(params))

        last_exc: Exception | None = None
        for attempt in range(1, retry + 1):
            try:
                session = await self._get_session()
                headers = {"X-MBX-APIKEY": settings.binance_api_key.get_secret_value()}
                async with session.request(method, url, params=params, headers=headers) as resp:
                    # Track weight consumption
                    self._weight_used_header = int(resp.headers.get("X-MBX-USED-WEIGHT-1M", 0) or 0)

                    if resp.status == 429 or resp.status == 418:
                        retry_after = float(resp.headers.get("Retry-After", 5))
                        await self._limiter.handle_rate_limit_response(retry_after)
                        await asyncio.sleep(retry_after)
                        continue

                    if 500 <= resp.status < 600:
                        # Server error — retry with backoff
                        body = await resp.text()
                        log.warning(
                            "binance_5xx",
                            status=resp.status,
                            path=path,
                            body=body[:200],
                            attempt=attempt,
                        )
                        await asyncio.sleep(min(2**attempt, 10))
                        last_exc = ExchangeError(
                            f"Binance {resp.status} for {path}",
                            code="exchange.http_5xx",
                            context={"status": resp.status, "body": body[:500]},
                        )
                        continue

                    if resp.status >= 400:
                        body = await resp.text()
                        raise ExchangeError(
                            f"Binance {resp.status} for {path}: {body[:300]}",
                            code="exchange.http_4xx",
                            context={"status": resp.status, "path": path, "body": body[:500]},
                        )

                    return await resp.json()

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                log.warning("binance_network_error", path=path, attempt=attempt, error=str(exc))
                await asyncio.sleep(min(2**attempt, 10))

        raise ExchangeError(
            f"Binance request failed after {retry} attempts: {path}",
            code="exchange.retry_exhausted",
            context={"path": path, "last_error": str(last_exc) if last_exc else ""},
        ) from last_exc

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        secret = settings.binance_api_secret.get_secret_value()
        if not secret:
            raise ExchangeError("Signed request requires API secret", code="exchange.no_secret")
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = settings.binance_recv_window_ms
        query = urlencode(params)
        signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = signature
        return params

    # ------------------------------------------------------------------ #
    # Public endpoints
    # ------------------------------------------------------------------ #
    async def exchange_info(self) -> list[SymbolInfo]:
        data = await self._request("GET", "/v1/exchangeInfo", weight=1)
        return [SymbolInfo.from_api(s) for s in data.get("symbols", [])]

    async def ticker_24h_all(self) -> list[Ticker24h]:
        data = await self._request("GET", "/v1/ticker/24hr", weight=40)
        return [Ticker24h.from_api(d) for d in data]

    async def ticker_24h(self, symbol: str) -> Ticker24h:
        data = await self._request("GET", "/v1/ticker/24hr", params={"symbol": symbol}, weight=1)
        return Ticker24h.from_api(data)

    async def ticker_price_all(self) -> dict[str, float]:
        data = await self._request("GET", "/v1/ticker/price", weight=2)
        return {d["symbol"]: float(d["price"]) for d in data}

    async def ticker_price(self, symbol: str) -> float:
        data = await self._request("GET", "/v1/ticker/price", params={"symbol": symbol}, weight=1)
        return float(data["price"])

    async def klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Candle]:
        # Weight per Binance docs: 1 for ≤100, 2 for ≤500, 5 for ≤1000, 10 for >1000
        weight = 1 if limit <= 100 else 2 if limit <= 500 else 5 if limit <= 1000 else 10
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time:
            params["startTime"] = int(start_time.timestamp() * 1000)
        if end_time:
            params["endTime"] = int(end_time.timestamp() * 1000)
        data = await self._request("GET", "/v1/klines", params=params, weight=weight)
        return [Candle.from_api(symbol, interval, row) for row in data]

    async def depth(self, symbol: str, limit: int = 100) -> OrderBook:
        weight = 2 if limit <= 100 else 5 if limit <= 500 else 10 if limit <= 1000 else 20
        data = await self._request(
            "GET", "/v1/depth", params={"symbol": symbol, "limit": limit}, weight=weight
        )
        return OrderBook.from_api(symbol, data)

    async def funding_rate_history(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            "/v1/fundingRate",
            params={"symbol": symbol, "limit": limit},
            weight=1,
        )
        return data if isinstance(data, list) else []

    async def mark_price(self, symbol: str | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        return await self._request("GET", "/v1/premiumIndex", params=params, weight=1)

    async def open_interest(self, symbol: str) -> OpenInterest:
        data = await self._request("GET", "/v1/openInterest", params={"symbol": symbol}, weight=1)
        return OpenInterest.from_api(data)

    async def open_interest_history(self, symbol: str, period: str = "15m", limit: int = 30) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            "/futures/data/openInterestHist",
            params={"symbol": symbol, "period": period, "limit": limit},
            weight=1,
        )
        return data if isinstance(data, list) else []

    async def top_long_short_account_ratio(self, symbol: str, period: str = "15m", limit: int = 30) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            "/futures/data/topLongShortAccountRatio",
            params={"symbol": symbol, "period": period, "limit": limit},
            weight=1,
        )
        return data if isinstance(data, list) else []

    async def taker_buy_sell_volume(self, symbol: str, period: str = "15m", limit: int = 30) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            "/futures/data/takerlongshortRatio",
            params={"symbol": symbol, "period": period, "limit": limit},
            weight=1,
        )
        return data if isinstance(data, list) else []

    async def agg_trades(self, symbol: str, limit: int = 100) -> list[AggTrade]:
        data = await self._request(
            "GET", "/v1/aggTrades", params={"symbol": symbol, "limit": limit}, weight=20
        )
        return [AggTrade.from_api(d) for d in data]

    async def liquidation_orders(self, symbol: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if symbol:
            params["symbol"] = symbol
        data = await self._request("GET", "/v1/allForceOrders", params=params, weight=20)
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------ #
    # Properties for monitoring
    # ------------------------------------------------------------------ #
    @property
    def weight_used(self) -> int:
        return self._weight_used_header

    @property
    def weight_limit(self) -> int:
        return settings.binance_weight_per_minute


__all__ = [
    "BinanceRestClient",
    "Ticker24h",
    "Candle",
    "OrderBook",
    "OrderBookEntry",
    "FundingRate",
    "OpenInterest",
    "AggTrade",
    "SymbolInfo",
]
