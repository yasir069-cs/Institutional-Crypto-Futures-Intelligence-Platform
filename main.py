#!/usr/bin/env python3
"""
============================================================
Institutional Crypto Futures Intelligence Platform
============================================================
Root entrypoint — supports two ways to start:

    python main.py           # starts the 24/7 pipeline loop
    python main.py --api     # starts the FastAPI server (uvicorn)
    python main.py --once    # runs one scan cycle and exits
    python main.py --test    # runs the test suite

For production:
    docker compose up -d
    OR
    python main.py
============================================================
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Ensure project root is on PYTHONPATH (so `import app` works)
ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _validate_env() -> None:
    """Validate critical environment variables before startup.

    Fail fast with clear error messages instead of crashing later.
    """
    # Try to load .env if dotenv available
    try:
        from dotenv import load_dotenv
        env_path = ROOT / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass  # python-dotenv not installed — rely on OS env

    # Required for platform to operate
    required = {
        "ENVIRONMENT": "development",
        "DATABASE_URL": "sqlite+aiosqlite:///./platform.db",
        "REDIS_URL": "redis://localhost:6379/0",
        "BINANCE_TESTNET": "true",
    }
    for key, default in required.items():
        if not os.environ.get(key):
            os.environ[key] = default

    # Telegram (warn if missing, don't fail — platform runs without alerts)
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        print("⚠️  WARNING: TELEGRAM_BOT_TOKEN not set — alerts will be disabled.")
        os.environ["TELEGRAM_ENABLED"] = "false"

    # AI providers (warn if no AI key, fall back to mock)
    ai_keys = ["GROQ_API_KEY", "NIM_API_KEY", "OPENROUTER_API_KEY"]
    if not any(os.environ.get(k) for k in ai_keys):
        print("⚠️  WARNING: No AI provider key set — falling back to Mock provider.")
        os.environ["AI_PROVIDER_ORDER"] = "mock"

    print(f"✅ Environment validated: {os.environ.get('ENVIRONMENT', 'development')} mode")


def run_pipeline() -> int:
    """Start the 24/7 pipeline loop."""
    _validate_env()
    from app.core.lifespan import run_sync
    print("🚀 Starting Institutional Crypto Futures Intelligence Platform...")
    print("   Press Ctrl+C to stop.")
    print()
    run_sync()
    return 0


def run_api(host: str = "0.0.0.0", port: int = 8080) -> int:
    """Start the FastAPI server using uvicorn."""
    _validate_env()
    try:
        import uvicorn
    except ImportError:
        print("❌ uvicorn not installed. Install with: pip install uvicorn[standard]")
        return 1
    print(f"🚀 Starting FastAPI server on http://{host}:{port}")
    uvicorn.run("app.asgi:app", host=host, port=port, reload=False)
    return 0


def run_once() -> int:
    """Run one scan cycle and exit (useful for testing)."""
    _validate_env()
    from app.core.container import container
    from app.core.lifespan import _startup, _shutdown

    async def _run():
        await _startup(container)
        try:
            pipeline = await container.get("AnalysisPipeline")
            result = await pipeline.run_once()
            print(f"\n📊 Cycle {result.cycle} complete:")
            print(f"   Duration: {result.duration_ms}ms")
            print(f"   Error: {result.error or 'None'}")
            if result.stage1:
                print(f"   Stage 1: scanned={result.stage1.total_scanned}, "
                      f"passed={len(result.stage1.candidates)}")
            if result.stage2:
                print(f"   Stage 2: setups={len(result.stage2.setups)}")
            print(f"   Signals generated: {len(result.signals)}")
            return 0
        finally:
            await _shutdown(container)

    return asyncio.run(_run())


def run_tests() -> int:
    """Run the pytest test suite."""
    import subprocess
    print("🧪 Running test suite...")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=ROOT,
    )
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Institutional Crypto Futures Intelligence Platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py              # Start 24/7 pipeline (production)
  python main.py --api        # Start API server only
  python main.py --once       # Run one scan cycle
  python main.py --test       # Run tests
  docker compose up -d        # Production deployment
        """,
    )
    parser.add_argument("--api", action="store_true", help="Start API server only")
    parser.add_argument("--once", action="store_true", help="Run one scan cycle and exit")
    parser.add_argument("--test", action="store_true", help="Run test suite")
    parser.add_argument("--host", default="0.0.0.0", help="API host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="API port (default: 8080)")
    args = parser.parse_args()

    if args.test:
        return run_tests()
    if args.api:
        return run_api(args.host, args.port)
    if args.once:
        return run_once()
    return run_pipeline()


if __name__ == "__main__":
    sys.exit(main())
