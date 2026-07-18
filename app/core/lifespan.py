"""Application lifecycle management.

Provides a single :func:`lifespan` async context manager compatible with
FastAPI/Starlette and reusable by the standalone CLI runner. It is the
**composition root** — the only place where concrete services are wired
into the :class:`ServiceContainer`.

Responsibilities
----------------
1. Configure logging and structured context.
2. Construct long-lived infrastructure (DB, Redis, Binance clients, etc.).
3. Start background workers (market data engine, scanner loop, AI queue).
4. On shutdown, close everything in reverse order.

The lifespan is idempotent and safe to enter multiple times in tests —
services already constructed are reused.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.core.container import ServiceContainer, container
from app.core.logging import configure_logging, get_logger

log = get_logger(__name__)


async def _register_config(container: ServiceContainer) -> None:
    """Register configuration objects (already loaded via pydantic-settings)."""
    from app.config import settings

    container.register("settings", settings)
    log.info(
        "config_loaded",
        env=settings.environment,
        log_level=settings.log_level,
        binance_testnet=settings.binance_testnet,
    )


async def _register_infrastructure(container: ServiceContainer) -> None:
    """Construct DB, Redis, and exchange clients (deferred to their modules)."""
    # Lazy imports avoid import cycles and keep startup fast.
    from app.db.session import build_db_session_factory, build_engine
    from app.cache.redis_client import build_redis

    engine = build_engine()
    container.register("db_engine", engine)
    container.register("db_session_factory", build_db_session_factory(engine))

    redis = await build_redis()
    container.register("redis", redis)


async def _register_exchange(container: ServiceContainer) -> None:
    """Construct Binance REST and WebSocket clients."""
    from app.exchange.binance_rest import BinanceRestClient
    from app.exchange.binance_ws import BinanceWebSocketClient

    rest = BinanceRestClient()
    container.register("binance_rest", rest)

    ws = BinanceWebSocketClient(rest=rest)
    container.register("binance_ws", ws)


async def _register_market_layer(container: ServiceContainer) -> None:
    """Construct market data + candle + indicator engines."""
    from app.market.data_engine import MarketDataEngine
    from app.market.candle_engine import CandleEngine
    from app.market.indicator_engine import IndicatorEngine

    rest = await container.get("binance_rest")  # type: ignore[arg-type]
    market_data = MarketDataEngine(rest=rest)
    container.register("market_data", market_data)

    candles = CandleEngine(rest=rest, market_data=market_data)
    container.register("candle_engine", candles)

    indicators = IndicatorEngine(candles=candles)
    container.register("indicator_engine", indicators)


async def _register_analysis_layer(container: ServiceContainer) -> None:
    """Construct structure, trend, liquidity, smart-money engines."""
    from app.structure.market_structure import MarketStructureEngine
    from app.structure.trend import TrendEngine
    from app.liquidity.engine import LiquidityEngine
    from app.smart_money.engine import SmartMoneyEngine
    from app.open_interest.engine import OpenInterestEngine
    from app.funding.engine import FundingEngine
    from app.volume.engine import VolumeEngine
    from app.pressure.engine import PressureEngine
    from app.confluence.engine import ConfluenceEngine
    from app.risk.engine import RiskEngine

    container.register_factory(
        MarketStructureEngine,
        lambda c: _async_return(MarketStructureEngine()),
    )
    container.register_factory(
        TrendEngine,
        lambda c: _async_return(TrendEngine()),
    )
    container.register_factory(
        LiquidityEngine,
        lambda c: _async_return(LiquidityEngine()),
    )
    container.register_factory(
        SmartMoneyEngine,
        lambda c: _async_return(SmartMoneyEngine()),
    )
    container.register_factory(
        OpenInterestEngine,
        lambda c: _async_return(OpenInterestEngine()),
    )
    container.register_factory(
        FundingEngine,
        lambda c: _async_return(FundingEngine()),
    )
    container.register_factory(
        VolumeEngine,
        lambda c: _async_return(VolumeEngine()),
    )
    container.register_factory(
        PressureEngine,
        lambda c: _async_return(PressureEngine()),
    )
    container.register_factory(
        ConfluenceEngine,
        lambda c: _async_return(ConfluenceEngine()),
    )
    container.register_factory(
        RiskEngine,
        lambda c: _async_return(RiskEngine()),
    )


async def _register_ai_layer(container: ServiceContainer) -> None:
    from app.ai.provider_layer import LLMProviderLayer
    from app.ai.validation_engine import AIValidationEngine

    providers = LLMProviderLayer()
    container.register("llm_providers", providers)
    container.register_factory(
        "AIValidationEngine",
        lambda c: _async_return(AIValidationEngine(providers=providers)),
    )


async def _register_signal_notifier(container: ServiceContainer) -> None:
    from app.signal.engine import SignalEngine
    from app.signal.validation import SignalValidationEngine
    from app.notifier.telegram import TelegramNotifier

    container.register_factory(
        "SignalValidationEngine",
        lambda c: _async_return(SignalValidationEngine()),
    )
    container.register_factory(
        "SignalEngine",
        lambda c: _async_return(SignalEngine()),
    )
    container.register_factory(
        "TelegramNotifier",
        lambda c: _async_return(TelegramNotifier()),
    )


async def _register_pipeline(container: ServiceContainer) -> None:
    from app.engine.pipeline import AnalysisPipeline

    container.register_factory(
        "AnalysisPipeline",
        lambda c: _async_return(AnalysisPipeline.build(c)),
    )


async def _register_api(container: ServiceContainer) -> None:
    from app.api.app import build_app

    container.register_factory("fastapi_app", lambda c: _async_return(build_app(c)))


async def _async_return(value):
    """Trivial helper to lift a value into an awaitable for factories."""
    return value


async def _startup(container: ServiceContainer) -> None:
    """Construct and register all services in dependency order."""
    configure_logging()
    log.info("lifespan_starting")
    await _register_config(container)
    await _register_infrastructure(container)
    await _register_exchange(container)
    await _register_market_layer(container)
    await _register_analysis_layer(container)
    await _register_ai_layer(container)
    await _register_signal_notifier(container)
    await _register_pipeline(container)
    await _register_api(container)
    log.info("lifespan_ready")


async def _shutdown(container: ServiceContainer) -> None:
    """Tear down all services in reverse order."""
    log.info("lifespan_stopping")
    await container.close()
    log.info("lifespan_stopped")


@asynccontextmanager
async def lifespan(app=None) -> AsyncIterator[ServiceContainer]:
    """Async context manager that starts and stops the whole platform.

    The optional ``app`` parameter is accepted for FastAPI compatibility
    (FastAPI calls ``lifespan(app)``); it is ignored.
    """
    await _startup(container)
    try:
        yield container
    finally:
        await _shutdown(container)


def run_sync() -> None:
    """Synchronous entrypoint: run the platform until Ctrl+C.

    Used by the CLI ``python -m app`` and by the Docker entrypoint.
    """
    import signal

    async def _main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # pragma: no cover - Windows
                pass

        async with lifespan():
            log.info("platform_running")
            # Start the main scan loop in the background.

            pipeline = await container.get("AnalysisPipeline") if container.has("AnalysisPipeline") else None
            if pipeline is not None:
                task = asyncio.create_task(pipeline.run_forever(stop))
                await stop.wait()
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            else:
                await stop.wait()

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:  # pragma: no cover
        pass


__all__ = ["lifespan", "run_sync", "container"]
