"""Telegram notification engine.

Sends richly formatted alerts to Telegram when signals are generated.
Implements:
- **Dedup**: same setup within ``dedup_window_sec`` → skip
- **Throttle**: max ``throttle_per_min`` alerts per minute
- **Retry**: exponential backoff on Telegram API errors
- **Persistence**: every alert logged to DB for audit
- **Rich formatting**: monospace block with all signal context per spec
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime, timezone

import aiohttp

from app.config import settings
from app.core.logging import get_logger
from app.db.models import TelegramAlert
from app.db.repositories import TelegramAlertRepository
from app.db.session import get_session
from app.signal.engine import Signal, SignalDirection, SignalType

log = get_logger(__name__)


class TelegramNotifier:
    """Send Telegram alerts for generated signals."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._recent_send_times: list[float] = []  # for throttle

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15.0)
            )
        return self._session

    async def aclose(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    async def send_signal(self, signal: Signal) -> bool:
        """Send a Telegram alert for ``signal``. Returns True on success."""
        if not settings.telegram_enabled:
            log.debug("telegram_disabled_skip", signal_id=signal.id)
            return False
        if not settings.telegram_bot_token.get_secret_value():
            log.warning("telegram_no_token_skip", signal_id=signal.id)
            return False
        if not signal.is_actionable:
            log.debug("telegram_skip_non_actionable", signal_id=signal.id, direction=signal.direction.value)
            return False

        dedup_key = self._dedup_key(signal)
        if await self._already_sent(dedup_key):
            log.info("telegram_dedup_skip", signal_id=signal.id, dedup_key=dedup_key)
            return False

        if not self._throttle_allow():
            log.warning("telegram_throttle_skip", signal_id=signal.id)
            return False

        message = self._format_message(signal)
        success = await self._send_with_retry(message)

        await self._persist_alert(signal, message, dedup_key, success)
        return success

    async def send_text(self, text: str) -> bool:
        """Send a raw text alert (e.g. startup / error notifications)."""
        if not settings.telegram_enabled:
            return False
        return await self._send_with_retry(text)

    # ------------------------------------------------------------------ #
    # Dedup & throttle
    # ------------------------------------------------------------------ #
    def _dedup_key(self, signal: Signal) -> str:
        """Build a dedup key from symbol + direction + signal type."""
        raw = f"{signal.symbol}:{signal.direction.value}:{signal.signal_type.value}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    async def _already_sent(self, dedup_key: str) -> bool:
        try:
            async with get_session() as session:
                repo = TelegramAlertRepository(session)
                return await repo.recent_dedup(dedup_key, settings.telegram_dedup_window_sec)
        except Exception:  # noqa: BLE001
            log.exception("telegram_dedup_check_failed")
            return False  # don't block on DB error

    def _throttle_allow(self) -> bool:
        """Return True if we haven't exceeded the per-minute throttle."""
        now = time.time()
        # Drop entries older than 60s
        self._recent_send_times = [t for t in self._recent_send_times if now - t < 60]
        if len(self._recent_send_times) >= settings.telegram_throttle_per_min:
            return False
        self._recent_send_times.append(now)
        return True

    # ------------------------------------------------------------------ #
    # Message formatting
    # ------------------------------------------------------------------ #
    def _format_message(self, signal: Signal) -> str:
        """Format a signal into a rich Telegram message."""
        direction_emoji = {
            SignalDirection.BUY: "🟢 BUY",
            SignalDirection.SELL: "🔴 SELL",
            SignalDirection.WATCHLIST: "🟡 WATCHLIST",
            SignalDirection.HOLD: "⚪ HOLD",
            SignalDirection.REJECT: "⚫ REJECT",
        }
        type_label = {
            SignalType.TYPE_A: "TYPE A — Early Smart Money Alert",
            SignalType.TYPE_B: "TYPE B — Bottom Detection",
            SignalType.TYPE_C: "TYPE C — Top Detection",
            SignalType.TYPE_D: "TYPE D — BUY/SELL Confirmation",
        }
        s = signal
        ai_reasoning = s.ai.reasoning if s.ai and s.ai.ai_decision else "N/A (no AI validation)"
        ai_decision = s.ai.ai_decision if s.ai else "N/A"
        ai_confidence = f"{s.ai.confidence*100:.0f}%" if s.ai else "N/A"
        provider = s.ai.provider if s.ai else "N/A"

        lines = [
            f"<b>{direction_emoji.get(s.direction, s.direction.value)}  {s.symbol}</b>",
            f"<i>{type_label.get(s.signal_type, s.signal_type.value)}</i>",
            "",
            f"<b>Entry:</b> <code>{s.entry}</code>",
            f"<b>Stop Loss:</b> <code>{s.stop_loss}</code>  ({s.risk.risk_pct:.2f}%)",
            f"<b>Take Profit:</b> <code>{s.take_profit}</code>  ({s.risk.reward_pct:.2f}%)",
            f"<b>Risk/Reward:</b> 1:{s.risk_reward:.2f}",
            f"<b>Position Size:</b> <code>{s.risk.position_size:.4f}</code> ({s.risk.position_value:.0f} USDT)",
            "",
            f"<b>Confluence:</b> {s.confluence_score}/100  ({s.confluence.direction})",
            f"<b>Confidence:</b> {s.confidence*100:.0f}%",
            "",
            f"<b>Trend HTF ({s.trend.htf.timeframe}):</b> {s.trend.htf.bias.value} (ADX {s.trend.htf.adx:.0f})",
            f"<b>Trend MTF ({s.trend.mtf.timeframe}):</b> {s.trend.mtf.bias.value} (ADX {s.trend.mtf.adx:.0f})",
            f"<b>Trend LTF ({s.trend.ltf.timeframe}):</b> {s.trend.ltf.bias.value} (ADX {s.trend.ltf.adx:.0f})",
            f"<b>Aligned:</b> {'YES' if s.trend.aligned else 'NO'} (score {s.trend.score})",
            "",
            f"<b>Market Structure:</b> {s.market_structure.bias.value} (event: {s.market_structure.event.value})",
            f"<b>Smart Money:</b> {s.smart_money.summary}",
            f"<b>Liquidity:</b> {s.metadata.get('liquidity_summary', 'N/A')}",
            "",
            f"<b>AI Decision:</b> {ai_decision}  ({ai_confidence} via {provider})",
            f"<b>AI Reasoning:</b> {ai_reasoning}",
            "",
            f"<b>Time:</b> {s.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"<b>Signal ID:</b> <code>{s.id}</code>",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #
    async def _send_with_retry(self, message: str, retry: int = 3) -> bool:
        """Send to Telegram with exponential backoff."""
        token = settings.telegram_bot_token.get_secret_value()
        chat_id = settings.telegram_chat_id
        url = f"https://api.telegram.org/bot{token}/sendMessage"

        last_exc: Exception | None = None
        for attempt in range(retry):
            try:
                session = await self._get_session()
                payload = {
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("ok"):
                            return True
                        log.warning("telegram_api_not_ok", response=str(data)[:200])
                    elif resp.status == 429:
                        # Rate limited — wait the specified time
                        retry_after = (await resp.json()).get("parameters", {}).get("retry_after", 5)
                        log.warning("telegram_rate_limited", retry_after=retry_after)
                        await asyncio.sleep(float(retry_after))
                        continue
                    else:
                        body = await resp.text()
                        log.warning("telegram_http_error", status=resp.status, body=body[:200])
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                log.warning("telegram_network_error", attempt=attempt, error=str(exc))
                await asyncio.sleep(min(2**attempt, 8))
                continue

        log.error("telegram_send_failed_after_retries", error=str(last_exc) if last_exc else "unknown")
        return False

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    async def _persist_alert(
        self,
        signal: Signal,
        message: str,
        dedup_key: str,
        success: bool,
        error: str = "",
    ) -> None:
        try:
            async with get_session() as session:
                repo = TelegramAlertRepository(session)
                alert = TelegramAlert(
                    signal_id=None,  # signal not persisted yet at this point
                    chat_id=settings.telegram_chat_id,
                    message=message,
                    sent_at=datetime.now(timezone.utc) if success else None,
                    success=success,
                    error=error,
                    dedup_key=dedup_key,
                )
                await repo.add(alert)
                await session.commit()
        except Exception:  # noqa: BLE001
            log.exception("telegram_alert_persist_failed")


__all__ = ["TelegramNotifier"]
