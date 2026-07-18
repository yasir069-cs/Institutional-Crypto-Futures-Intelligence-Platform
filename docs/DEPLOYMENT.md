# Deployment Guide

## Option 1: Docker Compose (recommended)

The simplest production deployment. Brings up PostgreSQL, Redis, and the platform together.

### Prerequisites

- Docker 24+ with Compose v2
- A Binance account (testnet recommended for first deploy)
- (Optional) NVIDIA NIM API key
- (Optional) OpenRouter API key
- (Optional) Telegram bot token + chat ID

### Steps

```bash
# 1. Clone and configure
git clone <your-repo-url> platform
cd platform
cp .env.example .env

# 2. Edit .env with your secrets
#    At minimum:
#      - BINANCE_TESTNET=true (start with testnet)
#      - NIM_API_KEY=...
#      - OPENROUTER_API_KEY=...
#      - TELEGRAM_ENABLED=true
#      - TELEGRAM_BOT_TOKEN=...
#      - TELEGRAM_CHAT_ID=...
#      - POSTGRES_PASSWORD=<strong-password>

# 3. Start the stack
docker compose up -d

# 4. Check status
docker compose ps
docker compose logs -f app

# 5. Verify health
curl http://localhost:8080/health/ready
# Should return {"ready": true, "checks": {...}}

# 6. (Optional) Start the API server profile
docker compose --profile api up -d api
curl http://localhost:8081/docs
```

### Updating

```bash
git pull
docker compose build app
docker compose up -d
```

### Backups

PostgreSQL data is in a named volume `postgres_data`. To back up:

```bash
docker compose exec postgres pg_dump -U platform platform > backup_$(date +%Y%m%d).sql
```

To restore:

```bash
cat backup_YYYYMMDD.sql | docker compose exec -T postgres psql -U platform platform
```

---

## Option 2: Bare metal / VM

### Prerequisites

- Python 3.12+
- PostgreSQL 16+
- Redis 7+
- systemd (for service management)

### Steps

```bash
# 1. Install system packages (Debian/Ubuntu)
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip postgresql-16 redis-server git

# 2. Create platform user
sudo useradd -r -s /bin/bash -d /opt/platform -m platform

# 3. Clone repo
sudo -u platform git clone <your-repo-url> /opt/platform
cd /opt/platform

# 4. Create venv and install deps
sudo -u platform python3.12 -m venv .venv
sudo -u platform .venv/bin/pip install -r requirements.txt

# 5. Configure environment
sudo -u platform cp .env.example .env
sudo -u platform nano .env  # edit secrets

# 6. Set up PostgreSQL
sudo -u postgres createuser platform
sudo -u postgres psql -c "ALTER USER platform WITH PASSWORD 'strong-password';"
sudo -u postgres createdb -O platform platform

# 7. Set up Redis (ensure it's running)
sudo systemctl enable --now redis-server

# 8. Create systemd service
sudo tee /etc/systemd/system/platform.service << 'EOF'
[Unit]
Description=Institutional Crypto Futures Intelligence Platform
After=network.target postgresql.service redis-server.service
Wants=postgresql.service redis-server.service

[Service]
Type=simple
User=platform
Group=platform
WorkingDirectory=/opt/platform
EnvironmentFile=/opt/platform/.env
ExecStart=/opt/platform/.venv/bin/python -m app
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=platform

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now platform

# 9. Check status
sudo systemctl status platform
sudo journalctl -u platform -f
```

---

## Health checks

The platform exposes two health endpoints:

- `GET /health` — liveness probe (always 200 if process is up)
- `GET /health/ready` — readiness probe (checks DB, Redis, Binance REST, Binance WS, AI providers)

### Docker Compose healthcheck

Already configured in `docker-compose.yml`:

```yaml
healthcheck:
  test: ["CMD", "curl", "-fsS", "http://localhost:8080/health"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 60s
```

### Kubernetes readiness/liveness

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8080
  initialDelaySeconds: 30
  periodSeconds: 30
readinessProbe:
  httpGet:
    path: /health/ready
    port: 8080
  initialDelaySeconds: 60
  periodSeconds: 30
  failureThreshold: 3
```

---

## Logs

Logs are JSON formatted to stdout. To follow:

```bash
# Docker
docker compose logs -f app

# systemd
sudo journalctl -u platform -f -o json
```

Sample log entry:

```json
{
  "event": "pipeline_cycle_complete",
  "cycle": 42,
  "duration_ms": 1842,
  "stage1_passed": 23,
  "stage2_setups": 4,
  "signals": 2,
  "timestamp": "2026-07-18T12:00:00Z",
  "level": "info",
  "logger": "app.engine.pipeline"
}
```

### Shipping logs to ELK / Datadog / Loki

The JSON output is ready for any log aggregator. For Docker:

```yaml
# docker-compose.yml addition
services:
  app:
    logging:
      driver: "json-file"
      options:
        max-size: "100m"
        max-file: "10"
```

Or use Filebeat / Promtail to tail container logs.

---

## Monitoring

### Built-in monitoring

- `GET /api/status` — overall platform status
- `GET /api/metrics` — last 24h of recorded metrics (scan duration, AI calls, cache hits, etc.)
- `GET /api/providers` — AI provider health and stats

### Prometheus (optional)

To expose Prometheus metrics, add a `/metrics` endpoint in `app/api/app.py` and use `prometheus-client`:

```python
from prometheus_client import generate_latest, Counter, Histogram

scan_duration = Histogram('platform_scan_duration_seconds', 'Scan cycle duration')
signals_total = Counter('platform_signals_total', 'Total signals', ['direction', 'type'])

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type="text/plain")
```

---

## Scaling

### Vertical

Increase `SCAN_MAX_PAIRS`, `AI_CONCURRENCY`, `DB_POOL_SIZE`, `REDIS_MAX_CONNECTIONS` as needed. A single instance handles 500 pairs comfortably on a 2-vCPU / 4GB VM.

### Horizontal

Run multiple instances behind a load balancer. Each instance scans independently — signals are deduplicated by the Telegram notifier (dedup key based on symbol+direction+type, 10-min window).

For shared state across instances:
- Use Redis for the AI response cache (already namespaced)
- Use Redis for the per-cycle AI rate limiter (currently in-process; would need a Redis-backed token bucket for multi-instance)
- Each instance maintains its own market data cache (acceptable — they all consume the same Binance WS streams)

---

## Troubleshooting

### Platform won't start

1. Check `.env` is present and `BINANCE_TESTNET` is set
2. Check Postgres / Redis are reachable: `docker compose ps`
3. Check logs: `docker compose logs app`
4. Try `OFFLINE_MODE=true` to skip all external connections

### No signals generated

1. Check Stage 1 is finding candidates: `GET /api/status` → `pipeline_cycle` should increment
2. Check `SCAN_MAX_PAIRS` and `STAGE1_MIN_VOLUME_USD` — values may be too strict
3. Check `STAGE1_MIN_CONFLUENCE` — lower it temporarily to see if any candidates pass
4. Verify candles are being received: `GET /api/market?limit=10` — should return non-zero prices

### AI not being called

1. Check `AI_PROVIDER_ORDER` includes `nim` or `openrouter` (not just `mock`)
2. Check `NIM_API_KEY` or `OPENROUTER_API_KEY` is set
3. Check `GET /api/providers` — should show healthy providers
4. Verify signal validation is allowing AI: lower `STAGE3_MIN_CONFLUENCE` temporarily
5. Check `AI_MAX_REQUESTS_PER_CYCLE` — may be too low

### Telegram alerts not arriving

1. Check `TELEGRAM_ENABLED=true`
2. Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are correct
3. Check `GET /api/status` — `providers` should show stats
4. Check logs for `telegram_dedup_skip` or `telegram_throttle_skip`
5. Send a test message: `curl -X POST http://localhost:8080/api/scan/trigger` to force a scan

### Binance WebSocket disconnects

1. Check `BINANCE_WS_MAX_STREAMS_PER_CONN` — 200 is the Binance limit
2. Check network connectivity to `stream.binancefuture.com` (testnet) or `fstream.binance.com` (mainnet)
3. Check logs for `ws_reconnect_scheduled` — backoff should reset after a healthy message
4. If using a firewall, ensure outbound 443 is allowed

---

## Production checklist

Before going live:

- [ ] `ENVIRONMENT=production` set in `.env`
- [ ] `BINANCE_TESTNET=false` (only when ready for real market data — but still no order placement)
- [ ] `LOG_FORMAT=json` for log aggregation
- [ ] `LOG_LEVEL=INFO` (not DEBUG)
- [ ] `POSTGRES_PASSWORD` is a strong, unique value
- [ ] `NIM_API_KEY` and `OPENROUTER_API_KEY` are valid
- [ ] `TELEGRAM_ENABLED=true` with valid token + chat ID
- [ ] `RISK_ACCOUNT_BALANCE` matches your actual account size (for sizing math)
- [ ] `SCAN_INTERVAL_SEC` ≥ 30 (avoid hitting Binance rate limits)
- [ ] `AI_MAX_REQUESTS_PER_CYCLE` ≤ 10 (cost control)
- [ ] Volume backups configured
- [ ] Health check endpoint monitored externally
- [ ] Log aggregation set up
- [ ] Restart policy = `unless-stopped` (already default in compose)
