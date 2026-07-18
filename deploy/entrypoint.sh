#!/usr/bin/env bash
# ============================================================
# Container entrypoint — runs DB migrations then starts the app
# ============================================================
set -euo pipefail

echo "[entrypoint] Starting Institutional Crypto Futures Intelligence Platform..."
echo "[entrypoint] Environment: ${ENVIRONMENT:-development}"
echo "[entrypoint] Binance testnet: ${BINANCE_TESTNET:-true}"

# Wait for PostgreSQL if DATABASE_URL points to it
if [[ "${DATABASE_URL:-}" == postgresql* ]]; then
    echo "[entrypoint] Waiting for PostgreSQL..."
    until python -c "
import asyncio, asyncpg, os, sys
async def main():
    try:
        url = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://')
        conn = await asyncpg.connect(url)
        await conn.close()
        sys.exit(0)
    except Exception as e:
        print(f'  postgres not ready: {e}', file=sys.stderr)
        sys.exit(1)
asyncio.run(main())
" 2>/dev/null; do
        sleep 2
    done
    echo "[entrypoint] PostgreSQL is ready."
fi

# Wait for Redis
if [[ -n "${REDIS_URL:-}" ]]; then
    echo "[entrypoint] Waiting for Redis..."
    until python -c "
import asyncio, redis.asyncio as redis, os, sys
async def main():
    try:
        client = redis.from_url(os.environ['REDIS_URL'])
        await client.ping()
        await client.aclose()
        sys.exit(0)
    except Exception as e:
        print(f'  redis not ready: {e}', file=sys.stderr)
        sys.exit(1)
asyncio.run(main())
" 2>/dev/null; do
        sleep 2
    done
    echo "[entrypoint] Redis is ready."
fi

# Run DB migrations (create tables if they don't exist)
echo "[entrypoint] Running database migrations..."
python -c "
import asyncio
from app.db.base import Base
from app.db.session import build_engine
from app.db import models  # noqa: F401 — register all models

async def migrate():
    engine = build_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print('[migration] tables created/verified')

asyncio.run(migrate())
" || echo "[entrypoint] WARNING: migration failed — continuing anyway"

# Start the platform
echo "[entrypoint] Launching platform..."
exec "$@"
