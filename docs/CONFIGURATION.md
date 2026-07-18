# Configuration Reference

All configuration is via environment variables, loaded from `.env` if present.
Settings are validated at startup by Pydantic — invalid config fails fast.

## Runtime

| Variable | Default | Description |
|----------|---------|-------------|
| `ENVIRONMENT` | `development` | One of `development`, `staging`, `production` |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `LOG_FORMAT` | `json` | `json` (production) or `console` (dev with colors) |
| `OFFLINE_MODE` | `false` | Skip external connections (DB, Redis, Binance, AI) — for tests |

## Database (PostgreSQL)

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://platform:platform@localhost:5432/platform` | SQLAlchemy async DSN |
| `DB_POOL_SIZE` | `20` | Connection pool size |
| `DB_MAX_OVERFLOW` | `10` | Pool overflow capacity |
| `DB_POOL_TIMEOUT` | `30.0` | Seconds to wait for a connection |
| `DB_POOL_RECYCLE` | `1800` | Recycle connections after N seconds |
| `DB_ECHO` | `false` | Echo SQL to logs (debug only) |

## Redis

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `REDIS_NAMESPACE` | `platform` | Key prefix for all platform keys |
| `REDIS_MAX_CONNECTIONS` | `50` | Max connections in pool |

## Binance Futures

| Variable | Default | Description |
|----------|---------|-------------|
| `BINANCE_TESTNET` | `true` | Use testnet endpoints (recommended for first deploy) |
| `BINANCE_REST_URL` | (auto) | Override REST base URL (else derived from testnet flag) |
| `BINANCE_WS_URL` | (auto) | Override WS base URL |
| `BINANCE_API_KEY` | (empty) | API key (only needed for private endpoints) |
| `BINANCE_API_SECRET` | (empty) | API secret (only needed for signed requests) |
| `BINANCE_RECV_WINDOW_MS` | `5000` | Request timestamp validity window |
| `BINANCE_REQUEST_TIMEOUT` | `10.0` | REST request timeout (seconds) |
| `BINANCE_WEIGHT_PER_MINUTE` | `2400` | Binance weight budget per minute |
| `BINANCE_ORDER_PER_10S` | `300` | Order rate limit (per 10 sec) |
| `BINANCE_ORDER_PER_DAY` | `100000` | Order rate limit (per day) |
| `BINANCE_WS_RECONNECT_DELAY` | `1.0` | Initial WS reconnect backoff (seconds) |
| `BINANCE_WS_RECONNECT_MAX_DELAY` | `60.0` | Max WS reconnect backoff |
| `BINANCE_WS_HEARTBEAT_SEC` | `30` | WS heartbeat interval |
| `BINANCE_WS_MAX_STREAMS_PER_CONN` | `200` | Max streams per WS connection (Binance limit) |

## AI Providers

| Variable | Default | Description |
|----------|---------|-------------|
| `AI_PROVIDER_ORDER` | `nim,openrouter,mock` | Comma-separated provider priority |
| `AI_DEFAULT_MODEL` | `meta/llama-3.1-70b-instruct` | Default model name |
| `NIM_BASE_URL` | `https://integrate.api.nvidia.com/v1` | NVIDIA NIM endpoint |
| `NIM_API_KEY` | (empty) | NVIDIA NIM API key |
| `NIM_MODEL` | `meta/llama-3.1-70b-instruct` | NIM model name |
| `NIM_TIMEOUT` | `30.0` | NIM request timeout |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter endpoint |
| `OPENROUTER_API_KEY` | (empty) | OpenRouter API key |
| `OPENROUTER_MODEL` | `meta-llama/llama-3.1-70b-instruct` | OpenRouter model |
| `OPENROUTER_TIMEOUT` | `30.0` | OpenRouter request timeout |
| `AI_MAX_REQUESTS_PER_CYCLE` | `10` | Hard cap on AI calls per scan cycle |
| `AI_CONCURRENCY` | `3` | Concurrent AI requests |
| `AI_CACHE_TTL_SEC` | `300` | AI response cache TTL (5 min) |
| `AI_TEMPERATURE` | `0.2` | LLM temperature |
| `AI_MAX_TOKENS` | `800` | Max response tokens |
| `AI_SKIP_IF_CONFLUENCE_BELOW` | `75` | Don't call AI below this confluence |
| `AI_SKIP_IF_RR_BELOW` | `2.0` | Don't call AI below this RR |

## Telegram

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | (empty) | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | (empty) | Target chat ID |
| `TELEGRAM_ENABLED` | `false` | Master switch |
| `TELEGRAM_DEDUP_WINDOW_SEC` | `600` | Dedup window (10 min) |
| `TELEGRAM_THROTTLE_PER_MIN` | `20` | Max messages per minute |

## Scanning Pipeline

| Variable | Default | Description |
|----------|---------|-------------|
| `SCAN_INTERVAL_SEC` | `60` | Time between scan cycles |
| `SCAN_MAX_PAIRS` | `500` | Max symbols to scan |
| `SCAN_STAGE1_TOP_N` | `30` | Top N from Stage 1 → Stage 2 |
| `SCAN_STAGE2_TOP_N` | `5` | Top N from Stage 2 → Stage 3 (AI) |
| `SCAN_TIMEFRAMES` | `1h,15m,5m` | HTF, MTF, LTF (must be 3 values) |
| `SCAN_TARGET_SCAN_DURATION_SEC` | `5.0` | Performance target (logged) |
| `STAGE1_MIN_CONFLUENCE` | `50` | Stage 1 confluence filter |
| `STAGE1_MIN_ATR_PCT` | `0.5` | Stage 1 minimum ATR% (volatility floor) |
| `STAGE1_MIN_VOLUME_USD` | `5000000` | Stage 1 minimum 24h USD volume |
| `STAGE2_MIN_CONFLUENCE` | `70` | Stage 2 confluence filter |
| `STAGE2_MIN_RR` | `2.0` | Stage 2 minimum risk:reward |
| `STAGE3_MIN_CONFLUENCE` | `75` | Stage 3 (AI) confluence filter |
| `STAGE3_MIN_RR` | `2.0` | Stage 3 minimum RR |

## Risk Management

| Variable | Default | Description |
|----------|---------|-------------|
| `RISK_ACCOUNT_BALANCE` | `10000` | Account size for position sizing (USDT) |
| `RISK_MAX_RISK_PER_TRADE_PCT` | `1.0` | Max risk per trade (% of account) |
| `RISK_INTRADAY_SL_MAX_PCT` | `3.0` | Max SL for intraday trades |
| `RISK_SWING_SL_MAX_PCT` | `5.0` | Max SL for swing trades |
| `RISK_MIN_RR` | `2.0` | Minimum risk:reward |
| `RISK_ATR_MULTIPLIER_SL` | `1.5` | ATR multiplier for stop-loss |
| `RISK_ATR_MULTIPLIER_TP` | `3.0` | ATR multiplier for take-profit |
| `RISK_MAX_OPEN_POSITIONS` | `5` | Max concurrent positions (advisory) |

## Confluence Weights

These weights determine each engine's contribution to the confluence score.
**Must sum to 100** for correct scoring. The platform warns (but doesn't fail) if not.

**Highest weight** (per spec):
| Variable | Default |
|----------|---------|
| `CONFLUENCE_WEIGHT_MARKET_STRUCTURE` | `20` |
| `CONFLUENCE_WEIGHT_TREND` | `18` |
| `CONFLUENCE_WEIGHT_LIQUIDITY` | `15` |
| `CONFLUENCE_WEIGHT_SMART_MONEY` | `15` |

**High weight**:
| Variable | Default |
|----------|---------|
| `CONFLUENCE_WEIGHT_EMA` | `8` |
| `CONFLUENCE_WEIGHT_VOLUME` | `6` |
| `CONFLUENCE_WEIGHT_PRESSURE` | `5` |
| `CONFLUENCE_WEIGHT_OPEN_INTEREST` | `4` |
| `CONFLUENCE_WEIGHT_FUNDING` | `3` |

**Medium weight**:
| Variable | Default |
|----------|---------|
| `CONFLUENCE_WEIGHT_VWAP` | `2` |
| `CONFLUENCE_WEIGHT_ATR` | `1` |
| `CONFLUENCE_WEIGHT_ADX` | `1` |
| `CONFLUENCE_WEIGHT_BOLLINGER` | `1` |
| `CONFLUENCE_WEIGHT_SUPPORT_RESISTANCE` | `1` |

**Lowest weight** (supporting only):
| Variable | Default |
|----------|---------|
| `CONFLUENCE_WEIGHT_RSI` | `0` |

## Retention (days)

| Variable | Default | Description |
|----------|---------|-------------|
| `RETENTION_MARKET_DATA_DAYS` | `30` | Candle / market data retention |
| `RETENTION_SIGNALS_DAYS` | `365` | Signal retention |
| `RETENTION_AI_DECISIONS_DAYS` | `90` | AI decision retention |
| `RETENTION_LOGS_DAYS` | `30` | Error log retention |
| `RETENTION_METRICS_DAYS` | `30` | Metrics retention |

## API Server

| Variable | Default | Description |
|----------|---------|-------------|
| `API_HOST` | `0.0.0.0` | API bind address |
| `API_PORT` | `8080` | API port |
| `API_WORKERS` | `1` | Uvicorn workers (1 recommended — platform is single-process async) |

---

## Common configurations

### Minimal local dev (no external services)

```env
ENVIRONMENT=development
OFFLINE_MODE=true
LOG_FORMAT=console
LOG_LEVEL=DEBUG
AI_PROVIDER_ORDER=mock
TELEGRAM_ENABLED=false
DATABASE_URL=sqlite+aiosqlite:///./platform.db
```

### Production with mainnet

```env
ENVIRONMENT=production
LOG_FORMAT=json
LOG_LEVEL=INFO
BINANCE_TESTNET=false
AI_PROVIDER_ORDER=nim,openrouter
NIM_API_KEY=nvapi-xxxxxxxx
OPENROUTER_API_KEY=sk-or-xxxxxxxx
AI_MAX_REQUESTS_PER_CYCLE=5
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=123456:ABC-DEF
TELEGRAM_CHAT_ID=-1001234567890
DATABASE_URL=postgresql+asyncpg://platform:strong-pass@db:5432/platform
REDIS_URL=redis://redis:6379/0
```

### Cost-sensitive (minimal AI usage)

```env
AI_MAX_REQUESTS_PER_CYCLE=3
AI_CACHE_TTL_SEC=900
STAGE3_MIN_CONFLUENCE=80
STAGE3_MIN_RR=2.5
SCAN_INTERVAL_SEC=300
```

### High-frequency (more aggressive scanning)

```env
SCAN_INTERVAL_SEC=30
SCAN_MAX_PAIRS=300
STAGE1_MIN_CONFLUENCE=60
STAGE2_MIN_CONFLUENCE=75
AI_MAX_REQUESTS_PER_CYCLE=10
AI_CONCURRENCY=5
```
