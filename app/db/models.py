"""ORM models for all persisted entities.

Tables
------
- ``symbols``             — tracked Binance futures symbols
- ``candles``             — OHLCV (partitioned conceptually by symbol+tf)
- ``funding_rates``       — funding rate history
- ``open_interest_snapshots`` — OI snapshots
- ``signals``             — generated signals (Type A/B/C/D)
- ``ai_decisions``        — AI validation responses
- ``telegram_alerts``     — outbound alert log
- ``metrics``             — operational metrics (scan duration, etc.)
- ``errors``              — error log (structured)

All time-series tables use composite indexes on (symbol, timestamp) so
range queries for backfilling and analysis are fast.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, utcnow


# --------------------------------------------------------------------------- #
# Symbols
# --------------------------------------------------------------------------- #
class Symbol(Base, TimestampMixin):
    __tablename__ = "symbols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    base_asset: Mapped[str] = mapped_column(String(16), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(8), nullable=False, default="USDT")
    contract_type: Mapped[str] = mapped_column(String(16), default="PERPETUAL")
    price_precision: Mapped[int] = mapped_column(Integer, default=2)
    quantity_precision: Mapped[int] = mapped_column(Integer, default=3)
    tick_size: Mapped[float] = mapped_column(Float, default=0.01)
    step_size: Mapped[float] = mapped_column(Float, default=0.001)
    min_notional: Mapped[float] = mapped_column(Float, default=5.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_seen: Mapped[datetime] = mapped_column(
        nullable=True, default=None
    )


# --------------------------------------------------------------------------- #
# Candles (OHLCV)
# --------------------------------------------------------------------------- #
class Candle(Base):
    __tablename__ = "candles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    open_time: Mapped[datetime] = mapped_column(nullable=False)
    close_time: Mapped[datetime] = mapped_column(nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    quote_volume: Mapped[float] = mapped_column(Float, nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    taker_buy_volume: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    taker_buy_quote_volume: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "open_time", name="uq_candles_symbol_tf_opentime"),
        Index("ix_candles_symbol_tf_opentime", "symbol", "timeframe", "open_time"),
    )


# --------------------------------------------------------------------------- #
# Funding rates
# --------------------------------------------------------------------------- #
class FundingRate(Base):
    __tablename__ = "funding_rates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    funding_time: Mapped[datetime] = mapped_column(nullable=False)
    funding_rate: Mapped[float] = mapped_column(Float, nullable=False)
    mark_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    __table_args__ = (
        UniqueConstraint("symbol", "funding_time", name="uq_funding_symbol_time"),
        Index("ix_funding_symbol_time", "symbol", "funding_time"),
    )


# --------------------------------------------------------------------------- #
# Open interest snapshots
# --------------------------------------------------------------------------- #
class OpenInterestSnapshot(Base):
    __tablename__ = "open_interest_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(nullable=False)
    open_interest: Mapped[float] = mapped_column(Float, nullable=False)
    open_interest_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    __table_args__ = (
        UniqueConstraint("symbol", "timestamp", name="uq_oi_symbol_time"),
        Index("ix_oi_symbol_time", "symbol", "timestamp"),
    )


# --------------------------------------------------------------------------- #
# Signals
# --------------------------------------------------------------------------- #
class Signal(Base, TimestampMixin):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    signal_type: Mapped[str] = mapped_column(String(2), nullable=False)  # A/B/C/D
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # BUY/SELL/WATCHLIST/HOLD/REJECT
    entry: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    risk_reward: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confluence_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timeframe_htf: Mapped[str] = mapped_column(String(8), nullable=False)
    timeframe_mtf: Mapped[str] = mapped_column(String(8), nullable=False)
    timeframe_ltf: Mapped[str] = mapped_column(String(8), nullable=False)
    trend_htf: Mapped[str] = mapped_column(String(16), default="NEUTRAL")
    trend_mtf: Mapped[str] = mapped_column(String(16), default="NEUTRAL")
    trend_ltf: Mapped[str] = mapped_column(String(16), default="NEUTRAL")
    market_structure: Mapped[str] = mapped_column(Text, default="")
    smart_money_summary: Mapped[str] = mapped_column(Text, default="")
    liquidity_summary: Mapped[str] = mapped_column(Text, default="")
    ai_reasoning: Mapped[str] = mapped_column(Text, default="")
    ai_decision: Mapped[str] = mapped_column(String(16), default="")
    probability: Mapped[float] = mapped_column(Float, default=0.0)
    trade_quality: Mapped[str] = mapped_column(String(32), default="")
    risk_level: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="OPEN", index=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    ai_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_decisions.id", ondelete="SET NULL"), nullable=True
    )

    ai_decision_ref = relationship("AIDecision", backref="signals", foreign_keys=[ai_decision_id])


# --------------------------------------------------------------------------- #
# AI decisions
# --------------------------------------------------------------------------- #
class AIDecision(Base, TimestampMixin):
    __tablename__ = "ai_decisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)  # BUY/SELL/WATCHLIST/HOLD/REJECT
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    probability: Mapped[float] = mapped_column(Float, default=0.0)
    trade_quality: Mapped[str] = mapped_column(String(32), default="")
    risk_level: Mapped[str] = mapped_column(String(32), default="")
    reasoning: Mapped[str] = mapped_column(Text, default="")
    request_payload: Mapped[str] = mapped_column(Text, default="")
    response_raw: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")


# --------------------------------------------------------------------------- #
# Telegram alerts
# --------------------------------------------------------------------------- #
class TelegramAlert(Base, TimestampMixin):
    __tablename__ = "telegram_alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    signal_id: Mapped[int | None] = mapped_column(
        ForeignKey("signals.id", ondelete="SET NULL"), nullable=True
    )
    chat_id: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(nullable=True, default=None)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    dedup_key: Mapped[str] = mapped_column(String(128), default="", index=True)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
class Metric(Base):
    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(nullable=False, default=utcnow, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    value_float: Mapped[float] = mapped_column(Float, default=0.0)
    value_int: Mapped[int] = mapped_column(BigInteger, default=0)
    tags: Mapped[str] = mapped_column(Text, default="{}")  # JSON

    __table_args__ = (Index("ix_metrics_name_ts", "name", "timestamp"),)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class ErrorLog(Base):
    __tablename__ = "error_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(nullable=False, default=utcnow, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    module: Mapped[str] = mapped_column(String(64), default="")
    context: Mapped[str] = mapped_column(Text, default="{}")  # JSON


__all__ = [
    "Symbol",
    "Candle",
    "FundingRate",
    "OpenInterestSnapshot",
    "Signal",
    "AIDecision",
    "TelegramAlert",
    "Metric",
    "ErrorLog",
]
