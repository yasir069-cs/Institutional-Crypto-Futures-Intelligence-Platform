# Institutional Crypto Futures Intelligence Platform

[![Tests](https://img.shields.io/badge/tests-73%20passing-brightgreen)](tests/)
[![Python](https://img.shields.io/badge/python-3.12-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-proprietary-red)]()

A production-grade institutional trading intelligence platform for **Binance USDT Futures**, operating 24/7 with maximum stability, scalability, reliability, and minimum AI usage.

> The platform thinks like an institutional trading desk, not an indicator-based trading bot. AI is only the final decision-maker — mathematics and Smart Money Concepts rule-based filtering find opportunities first.

---

## 🚀 Quick Start (3 commands)

### Option A: Docker (recommended for production)

```bash
git clone https://github.com/yasir069-cs/Institutional-Crypto-Futures-Intelligence-Platform.git
cd Institutional-Crypto-Futures-Intelligence-Platform
cp .env.example .env
# Edit .env with your API keys (at least OPENROUTER_API_KEY + TELEGRAM_BOT_TOKEN)
docker compose up -d
```

Platform is now live at `http://localhost:8080/`. Telegram alerts will arrive when premium setups are detected.

### Option B: Python directly

```bash
git clone https://github.com/yasir069-cs/Institutional-Crypto-Futures-Intelligence-Platform.git
cd Institutional-Crypto-Futures-Intelligence-Platform
cp .env.example .env
# Edit .env with your API keys
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

### Option C: AWS EC2 one-shot

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the full AWS EC2 setup script.

---

## 📋 What it does

The platform scans **500 Binance USDT Futures pairs** every 60 seconds through a 3-stage institutional pipeline:

```
Stage 1: Fast Math Scanner      (500 pairs → 30 candidates, ~5 sec)
  ↓
Stage 2: Smart Money Rule Engine (30 candidates → 5 setups, ~2 sec)
  ↓
Stage 3: AI Validation          (5 setups → BUY/SELL/HOLD/REJECT)
  ↓
Telegram Alert (only for premium setups: confluence ≥75 + AI approved)
```

### Key features

- **3-stage institutional pipeline** with strict filtering — only premium setups reach AI
- **Multi-timeframe trend engine** — 1H (overall) + 15M (pullback) + 5M (entry timing). LTF never overrides HTF.
- **Smart Money Concepts** — BOS, CHOCH, liquidity sweeps, FVGs, order blocks, institutional flow
- **AI Provider Layer** with failover — OpenRouter → Groq → NIM → Mock (never crashes)
- **Alert-only by design** — never places live orders
- **24/7 self-healing** — auto-reconnect WebSocket, retry REST, failover AI
- **Production Docker setup** — Postgres 16 + Redis 7 + app, all in one command

---

## ⚙️ Configuration

All config is via environment variables (loaded from `.env`). Key ones:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENROUTER_API_KEY` | Recommended* | empty | Free at https://openrouter.ai/ — primary AI provider |
| `TELEGRAM_BOT_TOKEN` | For alerts | empty | From @BotFather |
| `TELEGRAM_CHAT_ID` | For alerts | empty | Your chat ID |
| `GROQ_API_KEY` | Optional | empty | Free at https://console.groq.com (may have regional blocks) |
| `NIM_API_KEY` | Optional | empty | Free at https://build.nvidia.com/ |
| `BINANCE_TESTNET` | No | `true` | Use Binance testnet (public data, no API key needed) |

\* If no AI key is set, platform falls back to Mock provider (works for testing).

See [.env.example](.env.example) for the full list with defaults.

---

## 🎯 Why am I not getting alerts?

The platform sends Telegram alerts **only when ALL of these conditions are met**:

1. ✅ Stage 1 finds candidates (volume > $5M, ATR > 0.5%, trend not neutral)
2. ✅ Stage 2 finds setups (confluence ≥ 70, RR ≥ 1:2, smart money confirms)
3. ✅ Pre-AI validation passes (HTF trend aligned, ADX ≥ 15, smart money not opposing)
4. ✅ AI approves with BUY or SELL (confluence ≥ 75)
5. ✅ AI decision is BUY or SELL (not HOLD/WATCHLIST/REJECT)

In a ranging market, you may go 24+ hours without alerts. This is **by design** — institutional traders don't trade every setup.

### Force test alert

To verify your Telegram setup is working:

```bash
bash scripts/force_test_alert.sh
```

This sends a fake BTCUSDT SELL signal to your Telegram — useful for confirming the alert format works.

### Diagnostics

```bash
bash scripts/diagnose.sh            # Check platform health + activity
sudo journalctl -u crypto-platform -f  # Live logs (systemd)
docker compose logs -f app             # Live logs (Docker)
```

---

## 📊 API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness probe |
| GET | `/health/ready` | Readiness (checks DB/Redis/Binance/AI) |
| GET | `/api/status` | Platform status |
| GET | `/api/signals` | Recent signals |
| GET | `/api/market` | Live market data (top by volume) |
| GET | `/api/market/{symbol}` | Single symbol state |
| GET | `/api/providers` | AI provider health |
| POST | `/api/scan/trigger` | Manually trigger a scan cycle |
| GET | `/` | HTML dashboard |
| GET | `/docs` | OpenAPI / Swagger UI |

---

## 📁 Project Structure

```
.
├── main.py                    # Root entrypoint (--api / --once / --test)
├── app/
│   ├── __main__.py           # `python -m app` entrypoint
│   ├── asgi.py               # uvicorn ASGI app
│   ├── api/                  # FastAPI REST endpoints
│   ├── ai/                   # AI provider layer (OpenRouter/Groq/NIM/Mock)
│   ├── cache/                # Redis client
│   ├── config/               # Pydantic-settings configuration
│   ├── confluence/           # Weighted score 0-100
│   ├── core/                 # DI container, lifespan, logging, errors
│   ├── db/                   # SQLAlchemy 2 async ORM
│   ├── engine/               # 3-stage pipeline (stage1/2/3)
│   ├── exchange/             # Binance REST + WebSocket
│   ├── funding/              # Funding rate analysis
│   ├── indicator (market/)   # EMA/VWAP/ATR/RSI/ADX/MACD/Bollinger
│   ├── liquidity/            # Pools, sweeps, FVGs, order blocks
│   ├── metrics/              # Performance counters
│   ├── monitoring/           # Health checks
│   ├── notifier/             # Telegram alerts
│   ├── open_interest/        # OI delta, divergence
│   ├── pressure/             # Buy/sell pressure
│   ├── risk/                 # Position sizing, RR validation
│   ├── signal/               # Type A/B/C/D generation + validation
│   ├── smart_money/          # Institutional flow detection
│   ├── structure/            # HH/HL/BOS/CHOCH + trend
│   └── volume/               # Spike, exhaustion, climax
├── tests/                    # 73 pytest tests (unit + integration)
├── deploy/entrypoint.sh      # Docker entrypoint (DB migration + startup notify)
├── scripts/                  # diagnose.sh, force_test_alert.sh, fix_platform.sh
├── docs/                     # Architecture, deployment, configuration docs
├── Dockerfile                # Multi-stage production build
├── docker-compose.yml        # Postgres + Redis + app
├── requirements.txt          # Pinned Python deps
├── .env.example              # Template — copy to .env and edit
└── README.md                 # This file
```

---

## 🧪 Testing

```bash
python main.py --test          # Run all 73 tests
pytest tests/unit/ -v          # Unit tests only
pytest tests/integration/ -v   # Integration tests only
```

---

## 🔒 Security

- All secrets loaded from environment variables (never hardcoded)
- `.env` is gitignored — never committed to GitHub
- SQL injection prevented via SQLAlchemy parameterized queries
- Binance API keys passed only as HTTP header
- Input validation on every API endpoint via Pydantic v2

---

## 📚 Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Deployment Guide](docs/DEPLOYMENT.md)
- [Configuration Reference](docs/CONFIGURATION.md)
- [Change Log](docs/CHANGELOG.md)

---

## 📝 License

Proprietary. All rights reserved.
