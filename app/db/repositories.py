"""Domain repositories for each ORM model.

These thin wrappers expose intent-revealing methods to the rest of the
application. Domain code never imports SQLAlchemy directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AIDecision,
    Candle,
    ErrorLog,
    FundingRate,
    Metric,
    OpenInterestSnapshot,
    Signal,
    Symbol,
    TelegramAlert,
)
from app.db.repository import BaseRepository


class SymbolRepository(BaseRepository[Symbol]):
    model = Symbol

    async def upsert(self, symbol: Symbol) -> Symbol:
        """Insert or update a symbol row by ``symbol`` name."""
        stmt = pg_insert(Symbol).values(
            symbol=symbol.symbol,
            base_asset=symbol.base_asset,
            quote_asset=symbol.quote_asset,
            contract_type=symbol.contract_type,
            price_precision=symbol.price_precision,
            quantity_precision=symbol.quantity_precision,
            tick_size=symbol.tick_size,
            step_size=symbol.step_size,
            min_notional=symbol.min_notional,
            is_active=symbol.is_active,
            last_seen=symbol.last_seen,
        )
        update_cols = {
            "price_precision": stmt.excluded.price_precision,
            "quantity_precision": stmt.excluded.quantity_precision,
            "tick_size": stmt.excluded.tick_size,
            "step_size": stmt.excluded.step_size,
            "min_notional": stmt.excluded.min_notional,
            "is_active": stmt.excluded.is_active,
            "last_seen": stmt.excluded.last_seen,
        }
        stmt = stmt.on_conflict_do_update(index_elements=["symbol"], set_=update_cols).returning(Symbol)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one()

    async def active_symbols(self, limit: int = 500) -> list[Symbol]:
        stmt = select(Symbol).where(Symbol.is_active.is_(True)).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class CandleRepository(BaseRepository[Candle]):
    model = Candle

    async def bulk_upsert(self, candles: list[Candle]) -> int:
        """Insert candles, replacing duplicates by (symbol, tf, open_time)."""
        if not candles:
            return 0
        rows = [
            {
                "symbol": c.symbol,
                "timeframe": c.timeframe,
                "open_time": c.open_time,
                "close_time": c.close_time,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "quote_volume": c.quote_volume,
                "trade_count": c.trade_count,
                "taker_buy_volume": c.taker_buy_volume,
                "taker_buy_quote_volume": c.taker_buy_quote_volume,
                "is_closed": c.is_closed,
            }
            for c in candles
        ]
        stmt = pg_insert(Candle).values(rows)
        update_cols = {
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "volume": stmt.excluded.volume,
            "quote_volume": stmt.excluded.quote_volume,
            "trade_count": stmt.excluded.trade_count,
            "taker_buy_volume": stmt.excluded.taker_buy_volume,
            "taker_buy_quote_volume": stmt.excluded.taker_buy_quote_volume,
            "is_closed": stmt.excluded.is_closed,
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "timeframe", "open_time"], set_=update_cols
        )
        await self.session.execute(stmt)
        await self.session.flush()
        return len(candles)

    async def latest(self, symbol: str, timeframe: str, limit: int = 500) -> list[Candle]:
        stmt = (
            select(Candle)
            .where(Candle.symbol == symbol, Candle.timeframe == timeframe)
            .order_by(Candle.open_time.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        # Reverse so oldest first, which is what indicator math expects.
        rows = list(result.scalars().all())
        rows.reverse()
        return rows

    async def latest_close_time(self, symbol: str, timeframe: str) -> datetime | None:
        stmt = (
            select(Candle.close_time)
            .where(Candle.symbol == symbol, Candle.timeframe == timeframe)
            .order_by(Candle.close_time.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class FundingRateRepository(BaseRepository[FundingRate]):
    model = FundingRate

    async def latest(self, symbol: str, limit: int = 100) -> list[FundingRate]:
        stmt = (
            select(FundingRate)
            .where(FundingRate.symbol == symbol)
            .order_by(FundingRate.funding_time.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class OpenInterestRepository(BaseRepository[OpenInterestSnapshot]):
    model = OpenInterestSnapshot

    async def latest(self, symbol: str, limit: int = 100) -> list[OpenInterestSnapshot]:
        stmt = (
            select(OpenInterestSnapshot)
            .where(OpenInterestSnapshot.symbol == symbol)
            .order_by(OpenInterestSnapshot.timestamp.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class SignalRepository(BaseRepository[Signal]):
    model = Signal

    async def recent(self, limit: int = 50, symbol: str | None = None) -> list[Signal]:
        stmt = select(Signal).order_by(Signal.created_at.desc()).limit(limit)
        if symbol:
            stmt = stmt.where(Signal.symbol == symbol)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def open_signals(self) -> list[Signal]:
        stmt = select(Signal).where(Signal.status == "OPEN")
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def since(self, since: datetime, symbol: str | None = None) -> list[Signal]:
        stmt = select(Signal).where(Signal.created_at >= since)
        if symbol:
            stmt = stmt.where(Signal.symbol == symbol)
        stmt = stmt.order_by(Signal.created_at.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class AIDecisionRepository(BaseRepository[AIDecision]):
    model = AIDecision


class TelegramAlertRepository(BaseRepository[TelegramAlert]):
    model = TelegramAlert

    async def recent_dedup(self, dedup_key: str, since_seconds: int) -> bool:
        """Return True if ``dedup_key`` was sent within the dedup window."""
        cutoff = datetime.utcnow() - timedelta(seconds=since_seconds)
        stmt = (
            select(TelegramAlert.id)
            .where(
                TelegramAlert.dedup_key == dedup_key,
                TelegramAlert.created_at >= cutoff,
                TelegramAlert.success.is_(True),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None


class MetricRepository(BaseRepository[Metric]):
    model = Metric

    async def record(self, name: str, value: float | int | None = None, tags: dict[str, Any] | None = None) -> None:
        import json

        m = Metric(
            name=name,
            value_float=float(value) if isinstance(value, float) else 0.0,
            value_int=int(value) if isinstance(value, int) and not isinstance(value, bool) else 0,
            tags=json.dumps(tags or {}),
        )
        self.session.add(m)
        await self.session.flush()

    async def series(self, name: str, since: datetime, limit: int = 1000) -> list[Metric]:
        stmt = (
            select(Metric)
            .where(Metric.name == name, Metric.timestamp >= since)
            .order_by(Metric.timestamp.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class ErrorLogRepository(BaseRepository[ErrorLog]):
    model = ErrorLog

    async def record(self, code: str, message: str, module: str = "", context: dict[str, Any] | None = None) -> None:
        import json

        e = ErrorLog(code=code, message=message, module=module, context=json.dumps(context or {}))
        self.session.add(e)
        await self.session.flush()


__all__ = [
    "SymbolRepository",
    "CandleRepository",
    "FundingRateRepository",
    "OpenInterestRepository",
    "SignalRepository",
    "AIDecisionRepository",
    "TelegramAlertRepository",
    "MetricRepository",
    "ErrorLogRepository",
]
