"""Smoke test: verify all modules import cleanly."""

import sys
import traceback


def test_imports():
    modules_to_test = [
        "app",
        "app.config",
        "app.config.settings",
        "app.config.environment",
        "app.core.errors",
        "app.core.logging",
        "app.core.container",
        "app.core.lifespan",
        "app.db.base",
        "app.db.models",
        "app.db.session",
        "app.db.repository",
        "app.db.repositories",
        "app.cache.redis_client",
        "app.exchange.rate_limiter",
        "app.exchange.binance_rest",
        "app.exchange.binance_ws",
        "app.market.data_engine",
        "app.market.candle_engine",
        "app.market.indicator_engine",
        "app.structure.market_structure",
        "app.structure.trend",
        "app.liquidity.engine",
        "app.smart_money.engine",
        "app.open_interest.engine",
        "app.funding.engine",
        "app.volume.engine",
        "app.pressure.engine",
        "app.confluence.engine",
        "app.risk.engine",
        "app.signal.validation",
        "app.signal.engine",
        "app.ai.provider_layer",
        "app.ai.validation_engine",
        "app.notifier.telegram",
        "app.engine.stage1.scanner",
        "app.engine.stage2.rule_engine",
        "app.engine.pipeline",
        "app.api.app",
        "app.monitoring.health",
        "app.analytics.engine",
        "app.metrics.performance",
    ]
    failures = []
    for mod in modules_to_test:
        try:
            __import__(mod)
            print(f"OK   {mod}")
        except Exception as exc:
            failures.append((mod, exc))
            print(f"FAIL {mod}: {exc}")
            traceback.print_exc()
    if failures:
        print(f"\n{len(failures)} module(s) failed to import")
        sys.exit(1)
    else:
        print(f"\nAll {len(modules_to_test)} modules imported successfully.")


if __name__ == "__main__":
    test_imports()
