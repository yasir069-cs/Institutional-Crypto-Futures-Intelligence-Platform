"""Shared pytest fixtures."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Ensure project root is on path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Set safe test defaults BEFORE importing app code
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("OFFLINE_MODE", "true")
os.environ.setdefault("BINANCE_TESTNET", "true")
os.environ.setdefault("AI_PROVIDER_ORDER", "mock")
os.environ.setdefault("TELEGRAM_ENABLED", "false")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture(scope="session")
def event_loop():
    """Single event loop for the whole test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def make_candles():
    """Factory fixture for generating synthetic candle series."""
    from app.exchange.binance_rest import Candle

    def _factory(
        n: int = 200,
        start_price: float = 100.0,
        drift: float = 0.001,
        volatility: float = 0.01,
        interval_sec: int = 3600,
        base_volume: float = 1000.0,
    ) -> list[Candle]:
        import random

        rng = random.Random(42)
        candles: list[Candle] = []
        now = datetime.now(timezone.utc) - timedelta(seconds=interval_sec * n)
        price = start_price
        for i in range(n):
            change = drift + rng.gauss(0, volatility)
            open_ = price
            close = price * (1 + change)
            high = max(open_, close) * (1 + abs(rng.gauss(0, volatility * 0.3)))
            low = min(open_, close) * (1 - abs(rng.gauss(0, volatility * 0.3)))
            volume = base_volume * (1 + abs(rng.gauss(0, 0.3)))
            open_time = now + timedelta(seconds=interval_sec * i)
            close_time = open_time + timedelta(seconds=interval_sec)
            candles.append(Candle(
                symbol="TESTUSDT",
                timeframe="1h",
                open_time=open_time,
                close_time=close_time,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                quote_volume=volume * close,
                trade_count=int(volume / 10),
                taker_buy_volume=volume * rng.uniform(0.4, 0.6),
                taker_buy_quote_volume=volume * close * 0.5,
                is_closed=True,
            ))
            price = close
        return candles

    return _factory


@pytest.fixture
def settings_override(monkeypatch):
    """Fixture to override settings values within a test."""
    from app.config import settings

    def _apply(**overrides):
        for k, v in overrides.items():
            monkeypatch.setattr(settings, k, v, raising=False)

    return _apply
