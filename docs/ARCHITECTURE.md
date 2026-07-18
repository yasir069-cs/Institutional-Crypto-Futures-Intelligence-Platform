# Architecture

## High-level design

The platform follows **Clean Architecture** with strict dependency direction:

```
Domain engines (pure business logic)
        ↑
Application services (pipeline, validation)
        ↑
Infrastructure (DB, Redis, Binance, AI providers, Telegram)
        ↑
Composition root (lifespan.py — wires everything)
        ↑
Entry points (CLI __main__, ASGI app)
```

Domain engines (indicators, structure, trend, liquidity, smart money, etc.) have **zero infrastructure dependencies** — they accept dataclasses / primitives and return dataclasses. This makes them trivially testable.

Infrastructure adapters (Binance REST/WS, PostgreSQL, Redis, AI providers, Telegram) implement interfaces defined by the domain. They can be swapped without touching business logic.

## Service container

The `ServiceContainer` in `app/core/container.py` is a lightweight async DI container. Services are registered as either:

1. **Instances** — pre-built objects (e.g. `settings`)
2. **Async factories** — lazy constructors called on first `get()`

The lifespan (`app/core/lifespan.py`) is the only place that registers services. Domain modules receive dependencies via constructor parameters — they never import the container directly. This keeps modules testable (just pass mocks) and prevents hidden coupling.

## Data flow

### Live market data path

```
Binance WebSocket
      ↓
BinanceWebSocketClient._dispatch(stream, data)
      ↓
MarketDataEngine.update_*_from_ws(data)  ← in-memory cache
      ↓
CandleEngine.on_kline_ws(data)  ← rolling buffer + DB persist
      ↓
IndicatorEngine cache invalidated (on close_time change)
```

### Scan cycle path

```
AnalysisPipeline.run_once()
      ↓
1. Reset AI cycle counters
2. Stage1Scanner.scan(symbols)
      ├─ Parallel (sem=50) per symbol
      ├─ Read MarketDataEngine state (lock-free)
      ├─ Compute indicators (cached)
      ├─ Quick confluence estimate
      └─ Filter by stage1 thresholds → 20-30 candidates
3. Stage2RuleEngine.run(candidates)
      ├─ Parallel (sem=20) per candidate
      ├─ Fetch HTF/MTF/LTF candles from CandleEngine
      ├─ Run all engines (structure, trend, liquidity, smart money, OI, funding, volume, pressure)
      ├─ Compute full confluence
      ├─ Decide direction (HTF bias + smart money flow)
      ├─ Compute risk (entry/SL/TP/RR)
      └─ Filter by stage2 thresholds → 3-5 setups
4. For each setup:
      ├─ SignalValidationEngine.validate()  ← pre-AI gate
      ├─ If can_send_to_ai:
      │   └─ AIValidationEngine.validate(setup)
      │       ├─ Build prompt (institutional context)
      │       ├─ LLMProviderLayer.validate(messages, snapshot)
      │       │   ├─ Check cache (digest + price/confluence invalidation)
      │       │   ├─ Enforce per-cycle rate limit
      │       │   ├─ Acquire concurrency slot (sem=3)
      │       │   ├─ For each healthy provider in order:
      │       │   │   └─ Try with retry (3 attempts, exp backoff)
      │       │   └─ Cache response
      │       ├─ Apply post-AI safety overrides
      │       └─ Persist AIDecision
      ├─ SignalEngine.generate()  ← Type A/B/C/D
      ├─ Persist Signal to DB
      └─ TelegramNotifier.send_signal()
5. Record metrics
6. Sleep until next cycle
```

## Concurrency model

- **Single event loop** — the platform is single-process async (no threads for I/O)
- **Per-symbol locks** in `MarketDataEngine` for atomic state updates; reads are lock-free (eventual consistency acceptable for scanner)
- **Bounded semaphores** to limit concurrent REST/scan work:
  - Stage 1: 50 concurrent symbol scans
  - Stage 2: 20 concurrent candidate analyses
  - AI provider layer: 3 concurrent AI requests
- **Per-cycle AI rate limit**: max 10 requests per scan cycle (configurable)
- **Redis semaphore** for distributed rate limiting (when running multi-process)

## Performance

### Indicator caching

`IndicatorEngine` caches computed bundles per (symbol, timeframe) keyed on the close_time of the last candle. Recomputation happens only when a new candle arrives. For 500 symbols × 3 timeframes × 13 indicators, this turns ~20,000 calculations per scan into ~20.

### Market state as single source of truth

`MarketDataEngine` holds the latest snapshot for every symbol. Stage 1 reads from this in-memory cache instead of issuing REST calls per symbol. WebSocket updates keep the cache fresh. This is what enables sub-5-second scanning of 500 pairs.

### WebSocket sharding

Binance allows up to 200 streams per WebSocket connection. The `BinanceWebSocketClient` shards subscriptions into groups of 200 and manages each as an independent connection with its own reconnect logic.

### Database write batching

Closed candles are upserted via PostgreSQL's `INSERT ... ON CONFLICT DO UPDATE` in a single statement per batch, avoiding per-row round trips.

## Reliability

### Self-healing

| Component | Failure detection | Recovery |
|-----------|-------------------|----------|
| Binance WebSocket | Watchdog: no message for 3× heartbeat | Force reconnect |
| Binance WebSocket | Connection closed | Exponential backoff (1s → 60s) with jitter |
| Binance REST | 5xx / network | Retry with exp backoff (max 3) |
| Binance REST | 429 / 418 | Honor Retry-After header |
| AI Provider | Network / 5xx / parse error | Retry (3x) → failover to next provider → use cache → skip |
| AI Provider | 3 consecutive failures | Marked unhealthy, skipped until recovery |
| PostgreSQL | Connection lost | Pool auto-reconnects (pool_pre_ping=True) |
| Redis | Connection lost | Operations fail gracefully; cache misses |
| Telegram | 429 | Honor retry_after |
| Pipeline | Per-cycle exception | Logged, cycle skipped, loop continues |

### Never stops

The platform's prime directive: **never stop because one component failed**. Every long-running loop catches exceptions and continues. Background tasks (WebSocket, health monitor, pipeline) are independent — one crashing doesn't take down the others.

## Security boundaries

- **Secrets**: loaded from env via Pydantic `SecretStr`; never logged raw (only masked)
- **Binance API keys**: passed only as `X-MBX-APIKEY` HTTP header; HMAC signatures never persisted
- **SQL injection**: 100% parameterized via SQLAlchemy ORM; no raw SQL anywhere
- **Input validation**: every API endpoint uses Pydantic models with strict types
- **Rate limits**: Binance weight tracked via sliding window; AI per-cycle limit enforced
- **Telegram bot token**: stored only in memory; never written to DB

## Extension points

### Add a new exchange

1. Create `app/exchange/<name>_rest.py` implementing the same interface as `BinanceRestClient`
2. Create `app/exchange/<name>_ws.py` implementing the same interface as `BinanceWebSocketClient`
3. Add an `EXCHANGE` env var and switch in `app/core/lifespan.py`
4. Domain engines remain untouched — they consume the same dataclasses

### Add a new AI provider

1. Subclass `LLMProvider` (or `OpenAICompatibleProvider` for OpenAI-compatible APIs) in `app/ai/provider_layer.py`
2. Add the provider name to `AI_PROVIDER_ORDER` env var
3. Add provider config keys (`<NAME>_API_KEY`, `<NAME>_BASE_URL`, etc.) to `Settings`
4. Register the provider in `LLMProviderLayer.__init__`

No changes needed in `AIValidationEngine` or any domain code.

### Add a new indicator

1. Add a pure function to `app/market/indicator_engine.py`
2. Add it to the `IndicatorEngine.compute_all` bundle
3. Optionally wire it into `ConfluenceEngine.compute` with a configurable weight

### Add a new notification channel

1. Create `app/notifier/<channel>.py` with a class following the `TelegramNotifier` pattern
2. Register in `app/core/lifespan.py`
3. Call from `AnalysisPipeline` alongside (or instead of) Telegram

## Trade-offs

- **Single-process**: simpler ops, no distributed coordination needed. For horizontal scaling, run multiple instances behind a load balancer — each will scan independently (idempotent; signals deduplicated by Telegram notifier).
- **Alert-only**: no order placement. Eliminates a huge class of risk (bad fills, API misuse, account liquidation). Live trading can be added later behind a feature flag.
- **Sync DB writes for closed candles**: candles close every 5min (LTF) max, so write throughput isn't a bottleneck. For higher-frequency strategies, consider TimescaleDB.
- **In-memory market state**: lost on restart. Acceptable — full state rebuilds from Binance REST in <30 seconds.
