# Institutional Crypto Futures Intelligence Platform

A production-grade institutional trading intelligence platform for **Binance USDT Futures**, operating 24/7 with maximum stability, scalability, reliability, and minimum AI usage.

> The platform thinks like an institutional trading desk, not an indicator-based trading bot. AI is only the final decision-maker — mathematics and Smart Money Concepts rule-based filtering find opportunities first.

---

## Highlights

- **Three-stage analysis pipeline** — Stage 1 fast math scanner (300-500 pairs) → Stage 2 Smart Money Concepts rule engine (3-5 setups) → Stage 3 AI validation
- **Multi-timeframe trend engine** — 1H (overall trend) + 15M (pullback/continuation) + 5M (entry timing). LTF never overrides HTF.
- **Smart Money Concepts** — BOS / CHOCH / liquidity sweeps / FVGs / order blocks / institutional flow detection
- **LLM provider layer** — NVIDIA NIM primary, OpenRouter fallback, mock provider for tests. Automatic failover, caching, rate limiting. **AI never sees a coin that didn't pass Stage 1 + Stage 2.**
- **Alert-only by design** — never places live orders. Generates Telegram alerts with entry/SL/TP/RR/AI reasoning.
- **Modular architecture** — every engine is replaceable, testable, and configurable via environment variables.
- **24/7 self-healing** — auto-reconnect WebSocket, retry REST calls, failover AI providers, never stops because one component failed.
- **Enterprise observability** — structured JSON logging, health checks, performance metrics, analytics.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      AnalysisPipeline (24/7 loop)               │
└──────────────────────────────────────────────────────────────────┘
       │
       ├─► Stage 1: Fast Mathematical Scanner  (300-500 → 20-30)
       │     • EMA / VWAP / ATR / RSI / ADX / MACD
       │     • Volume / Pressure / Funding / OI
       │     • Quick confluence estimate
       │
       ├─► Stage 2: Smart Money Rule Engine   (20-30 → 3-5)
       │     • Market Structure (HH/HL/BOS/CHOCH)
       │     • Liquidity (pools / sweeps / FVGs / OBs)
       │     • Smart Money flow + Order Book imbalance
       │     • Full confluence 0-100
       │     • Risk computation (entry/SL/TP/RR)
       │
       ├─► Stage 3: AI Validation              (3-5 → BUY/SELL/HOLD/REJECT)
       │     • LLMProviderLayer (NIM → OpenRouter → Mock)
       │     • Cache (price/confluence change invalidation)
       │     • Rate limit (max N requests per cycle)
       │     • Concurrency control
       │     • Post-AI safety overrides
       │
       ├─► Signal Generation (Type A/B/C/D)
       │
       ├─► Telegram Alert
       │
       └─► Database persistence (signals, AI decisions, metrics)
```

### Modules

| # | Module | Path | Purpose |
|---|--------|------|---------|
| 1 | Project Architecture | `app/`, `app/core/` | DI container, lifespan, errors, logging |
| 2 | Configuration | `app/config/` | Pydantic-settings, env-driven, validated |
| 3 | Environment | `app/config/environment.py` | Secret masking, runtime validation |
| 4 | Database | `app/db/` | SQLAlchemy 2 async models + repositories |
| 5 | Redis Cache | `app/cache/` | Namespaced async client with health check |
| 6 | Binance REST | `app/exchange/binance_rest.py` | All futures endpoints, rate-limited |
| 7 | Binance WebSocket | `app/exchange/binance_ws.py` | Combined streams, sharding, self-healing |
| 8 | Market Data Engine | `app/market/data_engine.py` | In-memory state cache (single source of truth) |
| 9 | Candle Engine | `app/market/candle_engine.py` | Rolling OHLCV buffers + DB persistence |
| 10 | Indicator Engine | `app/market/indicator_engine.py` | EMA/VWAP/ATR/RSI/ADX/MACD/Bollinger/SR (cached) |
| 11 | Market Structure | `app/structure/market_structure.py` | HH/HL/LH/LL, BOS, CHOCH |
| 12 | Trend Engine | `app/structure/trend.py` | Multi-timeframe trend alignment |
| 13 | Liquidity Engine | `app/liquidity/engine.py` | Pools, sweeps, FVGs, order blocks |
| 14 | Smart Money | `app/smart_money/engine.py` | Institutional flow detection |
| 15 | Open Interest | `app/open_interest/engine.py` | OI delta, spikes, divergence |
| 16 | Funding | `app/funding/engine.py` | Regime classification, shifts |
| 17 | Volume | `app/volume/engine.py` | Spike, exhaustion, climax |
| 18 | Buy/Sell Pressure | `app/pressure/engine.py` | Trade tape classification |
| 19 | Confluence | `app/confluence/engine.py` | Weighted score 0-100 |
| 20 | Risk Management | `app/risk/engine.py` | Entry/SL/TP, position sizing, RR validation |
| 21 | Signal Validation | `app/signal/validation.py` | Pre-AI gate, safety rules |
| 22 | AI Validation | `app/ai/` | Provider layer + AI engine |
| 23 | Signal Engine | `app/signal/engine.py` | Type A/B/C/D generation |
| 24 | Telegram | `app/notifier/telegram.py` | Rich alerts, dedup, throttle, retry |
| 25 | REST API | `app/api/app.py` | FastAPI endpoints |
| 26 | Dashboard | (root `/`) | Minimal HTML status page |
| 27 | Monitoring | `app/monitoring/health.py` | Component health checks |
| 28 | Logging | `app/core/logging.py` | Structlog JSON output |
| 29 | Analytics | `app/analytics/engine.py` | Time-windowed aggregates |
| 30 | Performance | `app/metrics/performance.py` | Counters, timers, gauges |
| 31 | Testing | `tests/` | Pytest unit + integration suites |
| 32 | Docker | `Dockerfile` | Multi-stage build |
| 33 | Compose | `docker-compose.yml` | Postgres + Redis + app |
| 34 | Deployment | `deploy/entrypoint.sh` | Migrations + healthcheck |
| 35 | Documentation | `docs/`, this README | Ops + developer docs |

---

## Quick start

### Prerequisites

- Python 3.12+
- PostgreSQL 16+ (or use docker-compose)
- Redis 7+ (or use docker-compose)
- Binance account (testnet recommended)
- NVIDIA NIM API key + OpenRouter API key (both optional — mock provider works for tests)

### Local development

```bash
# 1. Install dependencies
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .  # if you add a pyproject.toml

# 2. Configure environment
cp .env.example .env
# Edit .env with your keys, testnet flag, etc.

# 3. Run tests
pytest tests/

# 4. Start the platform (24/7 loop)
python -m app

# 5. Or start the API server only
uvicorn app.asgi:app --host 0.0.0.0 --port 8080
```

### Docker deployment

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env

# 2. Start the full stack
docker compose up -d

# 3. Check logs
docker compose logs -f app

# 4. (Optional) Start the API server profile
docker compose --profile api up -d api

# 5. Stop
docker compose down
```

---

## Configuration

All configuration is via environment variables (loaded from `.env` if present). Key categories:

| Category | Key variables | Default |
|----------|---------------|---------|
| Runtime | `ENVIRONMENT`, `LOG_LEVEL`, `LOG_FORMAT`, `OFFLINE_MODE` | `development`, `INFO`, `json`, `false` |
| Database | `DATABASE_URL`, `DB_POOL_SIZE` | Postgres local |
| Redis | `REDIS_URL`, `REDIS_NAMESPACE` | Local Redis |
| Binance | `BINANCE_TESTNET`, `BINANCE_API_KEY`, `BINANCE_API_SECRET` | `true`, empty |
| AI | `AI_PROVIDER_ORDER`, `NIM_API_KEY`, `OPENROUTER_API_KEY` | `nim,openrouter,mock` |
| Telegram | `TELEGRAM_ENABLED`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | `false`, empty |
| Scanning | `SCAN_INTERVAL_SEC`, `SCAN_TIMEFRAMES`, `SCAN_STAGE1_TOP_N` | `60`, `1h,15m,5m`, `30` |
| Risk | `RISK_ACCOUNT_BALANCE`, `RISK_MAX_RISK_PER_TRADE_PCT`, `RISK_MIN_RR` | `10000`, `1.0`, `2.0` |
| Confluence | `CONFLUENCE_WEIGHT_*` (must sum to 100) | See `.env.example` |

See [`.env.example`](.env.example) for the full list with defaults and descriptions.

---

## AI Provider Layer

The platform's AI usage is **deliberately minimal**:

1. **Stage 1** scans 300-500 pairs with pure math — **no AI**.
2. **Stage 2** filters 20-30 candidates with Smart Money Concepts rules — **no AI**.
3. **Stage 3** sends only 3-5 premium setups to AI for institutional validation.

### Provider priority

```
NVIDIA NIM → OpenRouter → Mock (for tests)
```

### Caching rules (spec compliance)

AI response is reused if ALL of:
- Trend unchanged
- Market structure unchanged
- Confluence change < 5%
- Price movement < 0.5%
- Previous decision still valid

### Event-driven AI

AI is only called when one of:
- New BOS / CHOCH
- Liquidity sweep
- Funding shift
- OI spike
- Volume spike
- Institutional buying/selling detected
- Trend reversal
- Confluence ≥ 75

### Safety rules

The platform overrides AI decisions when:
- Confluence < 75 → HOLD
- HTF disagrees with direction → HOLD
- Smart money confirmation missing → WATCHLIST
- Risk/Reward < 1:2 → REJECT

---

## Performance targets

- Scan 300-500 Binance Futures pairs in **<5 seconds** (Stage 1)
- Stage 2 deep analysis of 20-30 candidates in **<2 seconds**
- AI validation of 3-5 setups in **<10 seconds** (when cache misses)
- WebSocket latency: <100ms from Binance to local cache update

Achieieved via:
- Async processing throughout (`asyncio`)
- Parallel calculation per symbol (`asyncio.gather` with bounded semaphore)
- Cached indicators (recompute only on candle close)
- In-memory market state (lock-free reads)
- Rate-limited Binance REST with sliding window counter
- Combined WebSocket streams (200 per connection, sharded)

---

## Testing

```bash
# All tests
pytest tests/

# Unit tests only
pytest tests/unit/

# Integration tests only
pytest tests/integration/

# With coverage
pytest --cov=app tests/
```

Current coverage: **72 tests passing** across indicators, structure, trend, liquidity, smart money, OI, funding, volume, pressure, confluence, risk, signal validation, AI provider, rate limiter, config + 3 full pipeline integration tests.

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness probe |
| GET | `/health/ready` | Readiness (checks DB/Redis/Binance) |
| GET | `/api/status` | Platform status |
| GET | `/api/signals` | Recent signals (paginated) |
| GET | `/api/signals/open` | Open signals |
| GET | `/api/market` | Top market states by volume |
| GET | `/api/market/{symbol}` | Single symbol state |
| GET | `/api/metrics` | Recent operational metrics |
| GET | `/api/providers` | AI provider health |
| POST | `/api/scan/trigger` | Manually trigger a scan cycle |
| GET | `/` | Minimal HTML dashboard |
| GET | `/docs` | OpenAPI / Swagger UI |

---

## Operational notes

### 24/7 reliability

- The pipeline loop catches per-cycle exceptions — one bad scan never stops the platform
- WebSocket client auto-reconnects with exponential backoff + jitter
- AI provider layer fails over NIM → OpenRouter → Mock automatically
- Telegram notifier dedups + throttles alerts

### Monitoring

- `GET /health/ready` returns per-component status (Postgres, Redis, Binance REST/WS, AI providers, pipeline cycle)
- `GET /api/metrics` returns last 24h of recorded metrics
- `GET /api/providers` shows per-provider success/error counts
- Structured JSON logs to stdout (ship to ELK / Datadog / Loki as needed)

### Self-healing

| Component | Failure mode | Recovery |
|-----------|--------------|----------|
| Binance WebSocket | Disconnect | Exponential backoff reconnect (max 60s) |
| Binance REST | 5xx / network | Retry with backoff (max 3 attempts) |
| Binance REST | 429 / 418 | Honor Retry-After header |
| AI Provider | Network / 5xx | Retry → failover to next provider → use cache → skip |
| Telegram | 429 rate limit | Honor retry_after |
| PostgreSQL | Connection lost | Pool auto-reconnects (pool_pre_ping) |
| Redis | Connection lost | Operations fail gracefully; cache misses |

---

## Security

- All secrets loaded from environment variables — never hardcoded
- Secrets logged only as masked values (last 4 chars visible)
- Binance API keys passed only as HTTP header (never in URL or body)
- SQL injection prevented via SQLAlchemy parameterized queries (no raw SQL)
- Input validation via Pydantic v2 on every API endpoint
- Binance rate limits respected via sliding window limiter
- Telegram bot token never persisted to DB

---

## Extension points

The architecture is provider-independent and exchange-modular:

- **Add an exchange**: implement `ExchangeClient` protocol in `app/exchange/<name>_rest.py`
- **Add an AI provider**: subclass `LLMProvider` in `app/ai/provider_layer.py`, add to `AI_PROVIDER_ORDER`
- **Add an indicator**: add pure function to `app/market/indicator_engine.py`, wire into `IndicatorEngine.compute_all`
- **Add a signal type**: extend `SignalType` enum in `app/signal/engine.py`
- **Add a notification channel**: subclass `TelegramNotifier` pattern in `app/notifier/`

---

## License

Proprietary. All rights reserved.

---

## Change log

See [CHANGELOG.md](docs/CHANGELOG.md).
