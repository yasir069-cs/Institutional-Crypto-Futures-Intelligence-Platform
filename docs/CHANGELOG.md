# Change Log

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-07-18

### Added — Initial production release

**Architecture (Modules 1-3)**
- Project package layout under `app/` with modular sub-packages
- Dependency injection container (`app/core/container.py`) with async factory support
- Application lifespan (`app/core/lifespan.py`) — composition root with graceful shutdown
- Pydantic-settings configuration (`app/config/settings.py`) — all settings env-driven
- Environment management with secret masking and runtime validation
- Structured logging via structlog (JSON in prod, console in dev)
- Comprehensive error hierarchy (`app/core/errors.py`)

**Persistence (Modules 4-5)**
- SQLAlchemy 2.0 async ORM with declarative models for signals, AI decisions, candles, funding rates, OI snapshots, metrics, errors, telegram alerts
- Async session factory with auto-rollback on error
- Generic `BaseRepository` + domain repositories (Symbol, Candle, Signal, etc.)
- PostgreSQL upsert (ON CONFLICT DO UPDATE) for candle bulk writes
- Namespaced async Redis client with health check, JSON helpers, rate-limit primitives

**Binance Integration (Modules 6-7)**
- Async REST client for all Binance USDT Futures public endpoints
- Sliding-window rate limiter (2400 weight/min) + order limiters
- Retry with exponential backoff for 5xx, Retry-After handling for 429/418
- Async WebSocket client with combined streams, sharding (200 streams/conn), exponential backoff reconnect, watchdog for silent connections
- Typed response dataclasses (Ticker24h, Candle, OrderBook, FundingRate, OpenInterest, AggTrade, SymbolInfo)

**Market Layer (Modules 8-10)**
- `MarketDataEngine` — in-memory state cache for ticker, order book, funding, OI, trades, liquidations
- `CandleEngine` — rolling OHLCV buffers per (symbol, timeframe) with DB persistence
- `IndicatorEngine` — EMA, SMA, VWAP, ATR, RSI, ADX, MACD, Bollinger Bands, swing pivots, support/resistance, volume spike ratio — all cached by candle close time

**Analysis Engines (Modules 11-18)**
- `MarketStructureEngine` — swing point detection, HH/HL/LH/LL classification, BOS / CHOCH events
- `TrendEngine` — multi-timeframe trend (HTF/MTF/LTF) with weighted score 0-100, alignment check
- `LiquidityEngine` — liquidity pools, sweeps, FVGs, order blocks, breaker/mitigation blocks
- `SmartMoneyEngine` — institutional buying/selling detection from tape + book + liquidity
- `OpenInterestEngine` — OI delta, spike/purge detection, price/OI divergence (NEW_LONGS / SHORT_COVERING / etc.)
- `FundingEngine` — regime classification (NEUTRAL/BULLISH_HEAT/EXTREME_LONG/etc.), shift detection
- `VolumeEngine` — spike, exhaustion, climax detection
- `PressureEngine` — buy/sell pressure from taker tape + candles + order book

**Pipeline (Modules 19-23)**
- `ConfluenceEngine` — weighted score 0-100 across all engines (weights configurable, must sum to 100)
- `RiskEngine` — entry/SL/TP computation, position sizing, RR validation, intraday/swing SL limits
- `SignalValidationEngine` — pre-AI gate enforcing all safety rules (confluence, RR, HTF agreement, smart money confirmation)
- `LLMProviderLayer` — single AI entry point with provider priority (NIM → OpenRouter → Mock), cache with price/confluence invalidation, per-cycle rate limit, concurrency control
- `AIValidationEngine` — Stage 3 orchestrator: builds structured market context prompt, calls provider layer, applies post-AI safety overrides, persists decisions
- `SignalEngine` — Type A (early smart money), B (bottom), C (top), D (BUY/SELL confirmation)

**Notification (Module 24)**
- `TelegramNotifier` — rich HTML alerts with all signal context, dedup (10-min window), throttle (20/min), exponential backoff retry, DB persistence

**API & Observability (Modules 25-30)**
- FastAPI app with health/readiness, signals, market, metrics, providers, manual scan trigger endpoints
- Minimal HTML dashboard at `/`
- `HealthMonitor` — background task polling all components every 30s
- `AnalyticsEngine` — time-windowed aggregates on signals and AI usage
- `PerformanceMetrics` — in-process counters, timers, gauges, cache hit rates

**Testing (Module 31)**
- 72 unit + integration tests covering indicators, structure, trend, liquidity, smart money, OI, funding, volume, pressure, confluence, risk, signal validation, AI provider, rate limiter, config
- Full pipeline integration test with mock AI provider end-to-end

**Deployment (Modules 32-34)**
- Multi-stage Dockerfile (Python 3.12-slim, tini as PID 1, non-root user)
- docker-compose.yml with Postgres 16 + Redis 7 + app + optional API profile
- Entrypoint script with PostgreSQL/Redis readiness checks + DB migration
- Healthcheck on `/health`

**Documentation (Module 35)**
- README.md with full architecture, quick start, configuration reference, AI provider rules, performance targets, monitoring, security, extension points
- `.env.example` with every setting documented
- This change log

### Security
- No hardcoded secrets anywhere
- All secrets loaded from environment via Pydantic SecretStr
- Binance API keys passed only as HTTP header
- SQL injection prevented via SQLAlchemy parameterized queries
- Telegram bot token never persisted to DB
- Input validation on every API endpoint via Pydantic v2
- Binance rate limits enforced via sliding window counter

### Known limitations
- Alert-only — does not place live orders (by design)
- Single-process pipeline (multi-process requires external coordination for Binance WS)
- Live trade execution stubbed but disabled (would need module 22+ extension)
- Web dashboard is minimal HTML; full UI deferred to a future session
