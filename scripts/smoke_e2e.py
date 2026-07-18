"""End-to-end smoke test: verify the full app constructs and serves requests."""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Set test env
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("OFFLINE_MODE", "true")
os.environ.setdefault("BINANCE_TESTNET", "true")
os.environ.setdefault("AI_PROVIDER_ORDER", "mock")
os.environ.setdefault("TELEGRAM_ENABLED", "false")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")


async def main() -> None:
    from app.core.container import container
    from app.core.lifespan import _startup, _shutdown

    print("=== Starting lifespan ===")
    await _startup(container)
    print("Lifespan started OK")

    # Verify key services are registered
    from app.exchange.binance_rest import BinanceRestClient
    from app.exchange.binance_ws import BinanceWebSocketClient
    from app.market.data_engine import MarketDataEngine
    from app.market.candle_engine import CandleEngine
    from app.market.indicator_engine import IndicatorEngine
    from app.ai.provider_layer import LLMProviderLayer
    from app.ai.validation_engine import AIValidationEngine
    from app.notifier.telegram import TelegramNotifier
    from app.engine.pipeline import AnalysisPipeline

    settings = container.try_get("settings")
    print(f"  settings: env={settings.environment}, testnet={settings.binance_testnet}")

    rest = container.try_get("binance_rest")
    print(f"  binance_rest: {type(rest).__name__}, weight_limit={rest.weight_limit}")

    ws = container.try_get("binance_ws")
    print(f"  binance_ws: {type(ws).__name__}, alive={ws.alive_connections}")

    md = container.try_get("market_data")
    print(f"  market_data: {type(md).__name__}, symbols_tracked={len(md.known_symbols())}")

    providers = container.try_get("llm_providers")
    print(f"  llm_providers: healthy={providers.healthy_providers()}")

    pipeline = await container.get("AnalysisPipeline")
    print(f"  pipeline: {type(pipeline).__name__}")

    # Test the FastAPI app
    from app.api.app import build_app
    app = build_app(container)
    print(f"  fastapi_app: routes={len(app.routes)}")

    # Verify some endpoints exist
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    expected = {"/health", "/health/ready", "/api/status", "/api/signals", "/api/market", "/api/providers"}
    missing = expected - paths
    assert not missing, f"Missing endpoints: {missing}"
    print(f"  all_expected_endpoints_present: True")

    print("\n=== Running one pipeline cycle (will fail gracefully without Binance) ===")
    result = await pipeline.run_once()
    print(f"  cycle={result.cycle}, duration={result.duration_ms}ms, error={result.error}")

    print("\n=== Shutting down ===")
    await _shutdown(container)
    print("Shutdown OK")

    print("\n=== SMOKE TEST PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
