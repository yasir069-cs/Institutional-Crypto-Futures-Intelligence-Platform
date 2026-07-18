"""Async engine and session factory.

Provides a single shared engine (process-wide) and a session factory that
modules use via :func:`get_session` async context manager. All sessions are
async; commits and rollbacks are explicit.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.core.errors import DatabaseError
from app.core.logging import get_logger

log = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def build_engine() -> AsyncEngine:
    """Construct the process-wide async engine.

    Connection pool parameters are configurable via settings. The engine is
    lazily created on first use and cached.
    """
    global _engine
    if _engine is not None:
        return _engine

    try:
        # SQLite (used for tests/dev) doesn't accept pool params
        is_sqlite = settings.database_url.startswith("sqlite")
        if is_sqlite:
            _engine = create_async_engine(
                settings.database_url,
                echo=settings.db_echo,
                future=True,
            )
        else:
            _engine = create_async_engine(
                settings.database_url,
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                pool_timeout=settings.db_pool_timeout,
                pool_recycle=settings.db_pool_recycle,
                pool_pre_ping=True,
                echo=settings.db_echo,
                future=True,
            )
        log.info("db_engine_created", url=_safe_url(settings.database_url))
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError(f"Failed to create engine: {exc}", context={"url": _safe_url(settings.database_url)}) from exc
    return _engine


def build_db_session_factory(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    """Construct a session factory bound to ``engine`` (or the global one)."""
    global _session_factory
    if _session_factory is not None and engine is None:
        return _session_factory
    engine = engine or build_engine()
    _session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Async context manager yielding a session; auto-rollback on error.

    Usage::

        async with get_session() as session:
            repo = SignalRepository(session)
            await repo.add(signal)
            await session.commit()
    """
    factory = build_db_session_factory()
    session = factory()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def check_db_connection() -> bool:
    """Return True if the database accepts a SELECT 1; False otherwise."""
    try:
        async with get_session() as session:
            from sqlalchemy import text

            await session.execute(text("SELECT 1"))
            return True
    except Exception as exc:  # noqa: BLE001
        log.warning("db_health_check_failed", error=str(exc))
        return False


def _safe_url(url: str) -> str:
    """Mask password in a DSN for safe logging."""
    if "@" not in url:
        return url
    head, tail = url.rsplit("@", 1)
    if ":" in head:
        scheme_user, _pw = head.rsplit(":", 1)
        return f"{scheme_user}:***@{tail}"
    return f"***@{tail}"


async def dispose_engine() -> None:
    """Dispose the global engine — used during shutdown and tests."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        log.info("db_engine_disposed")
    _engine = None
    _session_factory = None


__all__ = [
    "build_engine",
    "build_db_session_factory",
    "get_session",
    "check_db_connection",
    "dispose_engine",
]
