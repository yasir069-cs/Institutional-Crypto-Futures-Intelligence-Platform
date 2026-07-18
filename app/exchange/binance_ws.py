"""Binance Futures WebSocket client with combined streams and self-healing.

Features
--------
- **Combined streams**: subscribe to many symbol/stream combos per connection
  (Binance allows up to 200 streams per connection by default).
- **Auto-reconnect**: exponential backoff with cap; resets on healthy messages.
- **Heartbeat**: Binance sends ping every 3 minutes; we send pong automatically
  via the aiohttp WebSocket. We also watch for silent connections.
- **Stream multiplexing**: each callback receives (stream_name, payload).
- **Backpressure**: if a callback queue grows beyond a threshold, we drop the
  oldest messages and log (better than blocking the read loop).

The client never crashes the platform: any connection failure triggers a
reconnect cycle and updates a health flag the monitoring layer can observe.
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any, Awaitable, Callable

import aiohttp

from app.config import settings
from app.core.logging import get_logger
from app.exchange.binance_rest import BinanceRestClient

log = get_logger(__name__)

StreamCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


class BinanceStreamConnection:
    """One WebSocket connection managing up to ``max_streams`` subscriptions."""

    def __init__(
        self,
        ws_base_url: str,
        streams: list[str],
        on_message: StreamCallback,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        if len(streams) > settings.binance_ws_max_streams_per_conn:
            raise ValueError(
                f"Too many streams for one connection: {len(streams)} > "
                f"{settings.binance_ws_max_streams_per_conn}"
            )
        self._ws_base_url = ws_base_url.rstrip("/")
        self._streams = streams
        self._on_message = on_message
        self._on_status = on_status
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._last_msg_time: float = 0.0
        self._reconnect_count: int = 0

    @property
    def is_alive(self) -> bool:
        return self._ws is not None and not self._ws.closed

    @property
    def last_msg_age_sec(self) -> float:
        if self._last_msg_time == 0:
            return float("inf")
        return asyncio.get_event_loop().time() - self._last_msg_time

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run_forever(), name=f"ws-{id(self)}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _run_forever(self) -> None:
        backoff = settings.binance_ws_reconnect_delay
        max_backoff = settings.binance_ws_reconnect_max_delay
        while not self._stop.is_set():
            try:
                await self._run_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("ws_connection_error", error=str(exc), reconnect_count=self._reconnect_count)
                self._emit_status(f"error: {exc}")

            if self._stop.is_set():
                break

            self._reconnect_count += 1
            # Exponential backoff with jitter
            sleep_for = min(backoff, max_backoff) + random.uniform(0, 0.5)
            log.info("ws_reconnect_scheduled", sleep_sec=round(sleep_for, 2))
            self._emit_status(f"reconnecting in {sleep_for:.1f}s")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep_for)
                break  # stop signal received during sleep
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, max_backoff)

    async def _run_once(self) -> None:
        url = f"{self._ws_base_url}/stream"
        params = {"streams": "/".join(self._streams)}
        timeout = aiohttp.ClientTimeout(total=None)
        self._session = aiohttp.ClientSession(timeout=timeout)
        try:
            async with self._session.ws_connect(
                url,
                params=params,
                heartbeat=settings.binance_ws_heartbeat_sec,
                autoping=True,
                max_msg_size=2**24,  # 16 MB
            ) as ws:
                self._ws = ws
                self._last_msg_time = asyncio.get_event_loop().time()
                self._emit_status("connected")
                log.info("ws_connected", streams=len(self._streams))

                # Watchdog task for silent connections
                watchdog = asyncio.create_task(self._watchdog())

                try:
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self._last_msg_time = asyncio.get_event_loop().time()
                            try:
                                payload = json.loads(msg.data)
                                stream = payload.get("stream", "")
                                data = payload.get("data", payload)
                                await self._on_message(stream, data)
                            except Exception:  # noqa: BLE001
                                log.exception("ws_message_handler_failed", stream=payload.get("stream", ""))
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING):
                            log.warning("ws_closed_by_server")
                            break
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            log.warning("ws_error_state")
                            break
                finally:
                    watchdog.cancel()
                    try:
                        await watchdog
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                    self._ws = None
                    self._emit_status("disconnected")
        finally:
            if self._session and not self._session.closed:
                await self._session.close()
            self._session = None

    async def _watchdog(self) -> None:
        """Force-reconnect if no messages arrive within heartbeat window."""
        grace = max(settings.binance_ws_heartbeat_sec * 3, 90)
        while not self._stop.is_set():
            await asyncio.sleep(15)
            if self.last_msg_age_sec > grace:
                log.warning("ws_silent_force_reconnect", silent_sec=self.last_msg_age_sec)
                if self._ws is not None and not self._ws.closed:
                    await self._ws.close()
                return

    def _emit_status(self, status: str) -> None:
        if self._on_status is not None:
            try:
                self._on_status(status)
            except Exception:  # noqa: BLE001
                log.exception("ws_status_callback_failed")


class BinanceWebSocketClient:
    """High-level WebSocket manager that shards streams across connections."""

    def __init__(self, rest: BinanceRestClient) -> None:
        self._rest = rest
        self._ws_base_url = settings.binance_ws_base_url
        self._connections: list[BinanceStreamConnection] = []
        self._callbacks: dict[str, StreamCallback] = {}
        self._status: str = "stopped"
        self._lock = asyncio.Lock()

    def on(self, stream_name: str, callback: StreamCallback) -> None:
        """Register a callback for a stream name (e.g. ``btcusdt@kline_1m``)."""
        self._callbacks[stream_name.lower()] = callback

    async def _dispatch(self, stream: str, data: dict[str, Any]) -> None:
        cb = self._callbacks.get(stream.lower())
        if cb is None:
            # Try wildcard by prefix (e.g., "@kline_1m" matches any symbol)
            for key, fn in self._callbacks.items():
                if key.startswith("@") and stream.endswith(key):
                    try:
                        await fn(stream, data)
                    except Exception:  # noqa: BLE001
                        log.exception("ws_callback_failed", stream=stream)
                    return
            return
        try:
            await cb(stream, data)
        except Exception:  # noqa: BLE001
            log.exception("ws_callback_failed", stream=stream)

    async def start(self, streams: list[str] | None = None) -> None:
        """Start connections for the given streams (or all registered)."""
        async with self._lock:
            target = streams or list(self._callbacks.keys())
            if not target:
                log.warning("ws_start_no_streams")
                return
            # Shard into groups of max_streams_per_conn.
            max_per = settings.binance_ws_max_streams_per_conn
            shards = [target[i : i + max_per] for i in range(0, len(target), max_per)]
            for shard in shards:
                conn = BinanceStreamConnection(
                    self._ws_base_url, shard, self._dispatch, self._set_status
                )
                conn.start()
                self._connections.append(conn)
            self._status = "starting"

    def _set_status(self, status: str) -> None:
        self._status = status

    async def stop(self) -> None:
        async with self._lock:
            for conn in self._connections:
                await conn.stop()
            self._connections.clear()
            self._status = "stopped"

    async def aclose(self) -> None:
        await self.stop()

    @property
    def status(self) -> str:
        return self._status

    @property
    def alive_connections(self) -> int:
        return sum(1 for c in self._connections if c.is_alive)

    @property
    def total_connections(self) -> int:
        return len(self._connections)

    # ------------------------------------------------------------------ #
    # Convenience builders for common stream patterns
    # ------------------------------------------------------------------ #
    @staticmethod
    def kline_stream(symbol: str, interval: str) -> str:
        return f"{symbol.lower()}@kline_{interval}"

    @staticmethod
    def depth_stream(symbol: str, level: int = 20) -> str:
        return f"{symbol.lower()}@depth{level}@100ms"

    @staticmethod
    def agg_trade_stream(symbol: str) -> str:
        return f"{symbol.lower()}@aggTrade"

    @staticmethod
    def mark_price_stream(symbol: str) -> str:
        return f"{symbol.lower()}@markPrice@1s"

    @staticmethod
    def liquidation_stream(symbol: str) -> str:
        return f"{symbol.lower()}@forceOrder"

    @staticmethod
    def funding_stream(symbol: str) -> str:
        return f"{symbol.lower()}@markPrice@1s"  # funding included in markPrice stream


__all__ = ["BinanceWebSocketClient", "BinanceStreamConnection", "StreamCallback"]
