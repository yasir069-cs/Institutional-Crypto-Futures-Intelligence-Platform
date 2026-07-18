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
    # Message formatting — Institutional AI Market Intelligence template
    # ------------------------------------------------------------------ #
    def _format_message(self, signal: Signal) -> str:
        """Format a signal into a Telegram alert using the institutional template."""
        s = signal
        SEP = "━━━━━━━━━━━━━━━━━━━━"

        # Direction → emoji + label
        direction_emoji = {
            SignalDirection.BUY: "🟢 BUY",
            SignalDirection.SELL: "🔴 SELL",
            SignalDirection.WATCHLIST: "🟡 WATCHLIST",
            SignalDirection.HOLD: "⚪ HOLD",
            SignalDirection.REJECT: "⚫ REJECT",
        }

        # Signal Type → institutional label
        type_label = {
            SignalType.TYPE_A: "Early Smart Money Alert",
            SignalType.TYPE_B: "Bottom Detection",
            SignalType.TYPE_C: "Top Detection",
            SignalType.TYPE_D: "Trend Continuation",
        }

        # Priority based on validation verdict + AI decision
        if s.direction == SignalDirection.BUY or s.direction == SignalDirection.SELL:
            priority = "🟢 HIGH"
        elif s.direction == SignalDirection.WATCHLIST:
            priority = "🟡 WATCHLIST"
        elif s.direction == SignalDirection.HOLD:
            priority = "⚪ MEDIUM"
        else:
            priority = "⚫ LOW"

        # Setup Quality: 0-10 scale based on confluence + confidence
        quality_10 = (s.confluence_score / 10.0 + s.confidence * 5) / 2
        quality_10 = min(10.0, max(0.0, quality_10))
        # Stars (10 stars, filled = ●, empty = ○)
        filled = int(round(quality_10))
        stars = "●" * filled + "○" * (10 - filled)

        # AI fields
        ai_confidence_pct = f"{s.ai.confidence*100:.0f}%" if s.ai else "N/A"
        ai_decision = s.ai.ai_decision if s.ai else "Manual"
        ai_reasoning = (
            s.ai.reasoning if s.ai and s.ai.reasoning
            else "Python technicals aligned. No AI verification."
        )
        # Trim AI reasoning to 2 sentences max for compactness
        if s.ai and s.ai.reasoning:
            sentences = ai_reasoning.split(". ")
            if len(sentences) > 2:
                ai_reasoning = ". ".join(sentences[:2]) + "."

        # Trend short labels
        def _trend_short(bias_val: str) -> str:
            if bias_val == "BULLISH": return "BULL"
            if bias_val == "BEARISH": return "BEAR"
            return "NEUTRAL"

        htf_label = _trend_short(s.trend.htf.bias.value)
        mtf_label = _trend_short(s.trend.mtf.bias.value)
        ltf_label = _trend_short(s.trend.ltf.bias.value)

        # EMA alignment
        ema_score = 0.0
        try:
            ema_score = float(s.metadata.get("indicators", {}).get("ema", {}).get("score", 0))
        except Exception:
            pass
        ema_aligned = "Aligned" if abs(ema_score) > 0.3 else "Mixed"

        # VWAP alignment
        vwap_pos = 0.0
        try:
            vwap_pos = float(s.metadata.get("indicators", {}).get("vwap_position", 0))
        except Exception:
            pass
        vwap_aligned = "Aligned" if abs(vwap_pos) > 0.2 else "Neutral"

        # Market structure event
        ms_event = s.market_structure.event.value
        if ms_event == "NONE":
            ms_event = "Stable"
        elif ms_event.startswith("BOS_"):
            ms_event = "BOS"
        elif ms_event.startswith("CHOCH_"):
            ms_event = "CHOCH"

        # Take Profit levels — 3 TP targets based on ATR multiplier
        entry = s.entry
        sl = s.stop_loss
        risk_dist = abs(entry - sl)
        if s.direction == SignalDirection.BUY:
            tp1 = entry + risk_dist * 1.0
            tp2 = entry + risk_dist * 2.0
            tp3 = entry + risk_dist * 3.0
        elif s.direction == SignalDirection.SELL:
            tp1 = entry - risk_dist * 1.0
            tp2 = entry - risk_dist * 2.0
            tp3 = entry - risk_dist * 3.0
        else:
            tp1 = tp2 = tp3 = s.take_profit

        # Risk level
        risk_level = "Medium"
        if s.risk.risk_pct < 1.0:
            risk_level = "Low"
        elif s.risk.risk_pct > 2.0:
            risk_level = "High"

        # RR simplified
        rr_ratio = int(round(s.risk_reward)) if s.risk_reward >= 1 else 1

        # Confluence checklist (which components contributed)
        components = {c.name: c for c in s.confluence.components}
        def _check(name: str) -> str:
            c = components.get(name)
            if c is None:
                return "➖"
            if abs(c.contribution) < 0.1:
                return "➖"
            return "✅" if c.contribution > 0 else "❌"

        # Extract market data from indicators metadata
        ind = s.metadata.get("indicators", {})
        rsi_val = ind.get("rsi", {}).get("value", 0) if isinstance(ind.get("rsi"), dict) else 0
        adx_val = ind.get("adx", {}).get("adx", 0) if isinstance(ind.get("adx"), dict) else 0
        buy_pct = 0.0
        sell_pct = 0.0
        try:
            buy_pct = s.smart_money.institutional_buying * 100
            sell_pct = s.smart_money.institutional_selling * 100
        except Exception:
            pass
        # If smart money values are 0, fall back to pressure
        if buy_pct == 0 and sell_pct == 0:
            try:
                buy_pct = (s.metadata.get("pressure", {}).get("buy_pct", 0.5)) * 100
                sell_pct = 100 - buy_pct
            except Exception:
                pass

        change_24h = s.metadata.get("price_change_pct_24h", 0.0)
        # Funding rate / OI come from signal.metadata (set by pipeline), not indicators
        funding_rate = s.metadata.get("funding_rate", 0.0) if isinstance(s.metadata.get("funding_rate"), (int, float)) else 0.0
        oi_val = s.metadata.get("open_interest", 0.0) if isinstance(s.metadata.get("open_interest"), (int, float)) else 0.0

        # Count rule matches (components with positive contribution)
        rule_matches = sum(1 for c in s.confluence.components if c.contribution > 0.1)
        total_rules = len(s.confluence.components)

        # Symbol emoji
        symbol_emoji = "₿" if s.symbol.startswith("BTC") else \
                        "Ξ" if s.symbol.startswith("ETH") else \
                        "◈" if s.symbol.startswith("BNB") else \
                        "🪙"

        # Build the message
        lines = [
            SEP,
            "🏛 <b>Institutional AI Market Intelligence</b>",
            SEP,
            f"Coin: {symbol_emoji} <b>{s.symbol}</b>",
            f"Direction Bias: {direction_emoji.get(s.direction, s.direction.value)}",
            f"Signal Type: {type_label.get(s.signal_type, s.signal_type.value)}",
            "",
            SEP,
            f"PRIORITY: {priority}",
            f"Setup Quality: {stars} ({quality_10:.1f}/10)",
            f"AI Confidence: {ai_confidence_pct}",
            "",
            SEP,
            "💰 <b>TRADE PLAN</b>",
            f"Entry: <code>{entry}</code>",
            f"Stop Loss: <code>{sl}</code>",
            f"TP1: <code>{tp1:.4f}</code> | TP2: <code>{tp2:.4f}</code> | TP3: <code>{tp3:.4f}</code>",
            f"Risk Reward: 1:{rr_ratio} | Risk: {risk_level}",
            "",
            SEP,
            "📊 <b>MARKET STRUCTURE</b>",
            f"1H Trend: {htf_label} | 15M: {mtf_label} | 5M: {ltf_label}",
            f"EMA21: {ema_aligned}",
            f"VWAP: {vwap_aligned}",
            f"Structure: {ms_event}",
            "",
            SEP,
            "✅ <b>CONFLUENCE SUMMARY</b>",
            f"{_check('trend')} Trend",
            f"{_check('ema')} EMA21",
            f"{_check('vwap')} VWAP",
            f"{_check('rsi')} RSI Behaviour",
            f"{_check('volume')} Volume Spike",
            f"{_check('liquidity')} Liquidity Sweep",
            f"{_check('pressure')} Buy/Sell Pressure",
            f"{_check('bollinger')} Bollinger",
            f"{_check('atr')} ATR",
            f"{_check('market_structure')} Market Structure",
            f"Rule Match: {rule_matches} / {total_rules} Rules",
            "",
            SEP,
            "📈 <b>MARKET DATA</b>",
            f"RSI: {rsi_val:.2f} | ADX: {adx_val:.2f}",
            f"Buy Press: {buy_pct:.1f}% | Sell Press: {sell_pct:.1f}%",
            f"24H Change: {change_24h:+.2f}%",
            f"Funding: {funding_rate*100:.3f}% | OI: {oi_val:.1f}",
            "",
            SEP,
            "🧠 <b>AI ANALYSIS</b>",
            ai_reasoning,
            "",
            SEP,
            "⚠️ <b>TRADE INVALIDATION</b>",
            "Ignore this setup if:",
        ]

        # Dynamic invalidation rules based on direction
        if s.direction == SignalDirection.SELL:
            lines += [
                "• 15M closes above EMA21",
                "• Price reclaims VWAP",
                "• Sell pressure weakens",
                "• Liquidity sweep fails",
            ]
        elif s.direction == SignalDirection.BUY:
            lines += [
                "• 15M closes below EMA21",
                "• Price loses VWAP",
                "• Buy pressure weakens",
                "• Liquidity sweep fails",
            ]
        else:
            lines += [
                "• Setup conditions deteriorate",
                "• Confluence drops below 70",
                "• Smart money flow reverses",
                "• Higher timeframe trend breaks",
            ]

        lines += [
            "",
            SEP,
            "📝 <b>MANUAL CHECKLIST</b>",
            "☐ Support &amp; Resistance",
            "☐ Higher Timeframe Candle Close",
            "☐ Live Volume",
            "☐ BTC Market Direction",
            "☐ High Impact News",
            "☐ Position Size",
            "",
            SEP,
            "⚖️ <b>FINAL VERDICT</b>",
            f"Decision: <b>{s.direction.value}</b>",
            f"Confidence: {s.confidence*100:.0f}% | Quality: {quality_10:.1f}/10",
            "",
            SEP,
            "⚠ <i>AI Market Intelligence Only</i>",
            "<i>This is NOT financial advice.</i>",
            "<i>This alert highlights a potential market opportunity.</i>",
            "",
            "<b>Final Trading Decision:</b>",
            "👤 <b>MANUAL</b>",
            "",
            f"<i>Time: {s.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}</i>",
            f"<i>Signal ID: {s.id}</i>",
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
