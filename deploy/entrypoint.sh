#!/usr/bin/env bash
# ============================================================
# Container entrypoint — runs DB migrations then starts the app
# ============================================================
set -euo pipefail

echo "[entrypoint] Starting Institutional Crypto Futures Intelligence Platform..."
echo "[entrypoint] Environment: ${ENVIRONMENT:-development}"
echo "[entrypoint] Binance testnet: ${BINANCE_TESTNET:-true}"
echo "[entrypoint] AI providers: ${AI_PROVIDER_ORDER:-mock}"

# Validate critical env vars
if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] && [ "${TELEGRAM_ENABLED:-false}" = "true" ]; then
    echo "[entrypoint] WARNING: TELEGRAM_ENABLED=true but TELEGRAM_BOT_TOKEN is empty."
    echo "[entrypoint]   Telegram alerts will be disabled."
    export TELEGRAM_ENABLED=false
fi

# Wait for PostgreSQL if DATABASE_URL points to it
if [[ "${DATABASE_URL:-}" == postgresql* ]]; then
    echo "[entrypoint] Waiting for PostgreSQL..."
    for i in $(seq 1 30); do
        if python -c "
import asyncio, asyncpg, os, sys
async def main():
    try:
        url = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://')
        conn = await asyncpg.connect(url)
        await conn.close()
        sys.exit(0)
    except Exception as e:
        sys.exit(1)
asyncio.run(main())
" 2>/dev/null; then
            echo "[entrypoint] PostgreSQL is ready."
            break
        fi
        echo "  [$i/30] Waiting for PostgreSQL..."
        sleep 2
    done
fi

# Wait for Redis (best-effort, don't fail if unavailable)
if [[ -n "${REDIS_URL:-}" ]]; then
    echo "[entrypoint] Checking Redis..."
    for i in $(seq 1 10); do
        if python -c "
import asyncio, redis.asyncio as redis, os, sys
async def main():
    try:
        client = redis.from_url(os.environ['REDIS_URL'])
        await client.ping()
        await client.aclose()
        sys.exit(0)
    except Exception as e:
        sys.exit(1)
asyncio.run(main())
" 2>/dev/null; then
            echo "[entrypoint] Redis is ready."
            break
        fi
        echo "  [$i/10] Waiting for Redis..."
        sleep 2
    done
fi

# Run DB migrations (create tables if they don't exist)
echo "[entrypoint] Running database migrations..."
python -c "
import asyncio
from app.db.base import Base
from app.db.session import build_engine, dispose_engine
from app.db import models  # noqa: F401 — register all models

async def migrate():
    engine = build_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print('[migration] tables created/verified')

asyncio.run(migrate())
" || echo "[entrypoint] WARNING: migration failed — continuing anyway (tables may already exist)"

# Send startup Telegram notification (best-effort)
if [ "${TELEGRAM_ENABLED:-false}" = "true" ] && [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
    python -c "
import asyncio, os
import aiohttp

async def notify():
    token = os.environ['TELEGRAM_BOT_TOKEN']
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not chat_id:
        return
    msg = '🚀 <b>Platform Started</b>\n\nInstitutional Crypto Futures Intelligence Platform is now running 24/7.\n\nAlerts will arrive when premium setups are detected (confluence ≥ 75 + AI approved).'
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f'https://api.telegram.org/bot{token}/sendMessage',
                json={'chat_id': chat_id, 'text': msg, 'parse_mode': 'HTML', 'disable_web_page_preview': True},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    print('[entrypoint] Startup notification sent to Telegram')
        except Exception as e:
            print(f'[entrypoint] Telegram notify failed: {e}')

asyncio.run(notify())
" 2>/dev/null || true
fi

echo "[entrypoint] Launching platform..."
exec "$@"
