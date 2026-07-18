"""Run all unit tests without pytest (uses unittest-style assertions)."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Set test env
import os

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("OFFLINE_MODE", "true")
os.environ.setdefault("BINANCE_TESTNET", "true")
os.environ.setdefault("AI_PROVIDER_ORDER", "mock")
os.environ.setdefault("TELEGRAM_ENABLED", "false")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

import asyncio

# Import all test modules
from tests.unit import (
    test_config,
    test_indicators,
    test_market_structure,
    test_risk_engine,
    test_confluence,
    test_ai_provider,
    test_signal_validation,
    test_rate_limiter,
    test_liquidity,
    test_engines,
    test_trend_engine,
)


def run_module(mod) -> tuple[int, int, list[str]]:
    """Run all test_* functions in module. Returns (passed, failed, errors)."""
    passed = 0
    failed = 0
    errors = []
    for name in dir(mod):
        if not name.startswith("test_"):
            continue
        fn = getattr(mod, name)
        if not callable(fn):
            continue
        try:
            result = fn()
            # Check if it's a coroutine (async test)
            if asyncio.iscoroutine(result):
                asyncio.get_event_loop().run_until_complete(result)
            passed += 1
            print(f"  PASS  {mod.__name__}.{name}")
        except Exception:
            failed += 1
            errors.append(f"{mod.__name__}.{name}")
            print(f"  FAIL  {mod.__name__}.{name}")
            traceback.print_exc()
    return passed, failed, errors


def main() -> int:
    modules = [
        test_config,
        test_indicators,
        test_market_structure,
        test_risk_engine,
        test_confluence,
        test_ai_provider,
        test_signal_validation,
        test_rate_limiter,
        test_liquidity,
        test_engines,
        test_trend_engine,
    ]
    total_pass = 0
    total_fail = 0
    all_errors: list[str] = []
    for mod in modules:
        print(f"\n=== {mod.__name__} ===")
        p, f, e = run_module(mod)
        total_pass += p
        total_fail += f
        all_errors.extend(e)

    print(f"\n{'='*60}")
    print(f"TOTAL: {total_pass} passed, {total_fail} failed")
    print(f"{'='*60}")
    if all_errors:
        print("Failed tests:")
        for e in all_errors:
            print(f"  - {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
