"""FastAPI application factory.

Endpoints
---------
- ``GET /health``            — liveness probe (always 200)
- ``GET /health/ready``      — readiness probe (checks deps)
- ``GET /api/status``        — overall platform status
- ``GET /api/signals``       — recent signals (paginated)
- ``GET /api/signals/{id}``  — single signal detail
- ``GET /api/market``        — live market state for top symbols
- ``GET /api/market/{sym}``  — single symbol state
- ``GET /api/metrics``       — recent operational metrics
- ``GET /api/providers``     — AI provider health
- ``POST /api/scan/trigger`` — manually trigger a scan (dev only)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.core.container import ServiceContainer
from app.core.logging import get_logger
from app.db.session import get_session
from app.db.repositories import MetricRepository, SignalRepository

log = get_logger(__name__)


def build_app(container: ServiceContainer | None = None) -> FastAPI:
    """Construct the FastAPI app. ``container`` is optional for testing."""
    app = FastAPI(
        title="Institutional Crypto Futures Intelligence Platform",
        version="1.0.0",
        description="Production-grade Binance USDT Futures intelligence & signal platform",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Store container in app state for endpoints to access
    app.state.container = container

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #
    @app.get("/health", tags=["health"])
    async def health() -> dict[str, Any]:
        """Liveness probe — always 200 if process is alive."""
        return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

    @app.get("/health/ready", tags=["health"])
    async def readiness() -> dict[str, Any]:
        """Readiness probe — checks DB, Redis, Binance connectivity."""
        checks: dict[str, bool] = {}
        if container is not None:
            # DB
            try:
                from app.db.session import check_db_connection

                checks["database"] = await check_db_connection()
            except Exception:  # noqa: BLE001
                checks["database"] = False
            # Redis
            try:
                from app.cache.redis_client import redis_health

                checks["redis"] = await redis_health()
            except Exception:  # noqa: BLE001
                checks["redis"] = False
            # Binance REST
            try:
                rest = container.try_get("binance_rest")
                checks["binance_rest"] = rest is not None
            except Exception:  # noqa: BLE001
                checks["binance_rest"] = False
            # Binance WS
            try:
                ws = container.try_get("binance_ws")
                checks["binance_ws"] = ws is not None and ws.alive_connections > 0  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                checks["binance_ws"] = False
        ready = all(checks.values()) if checks else False
        return {"ready": ready, "checks": checks}

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    @app.get("/api/status", tags=["status"])
    async def status() -> dict[str, Any]:
        """Overall platform status: config, runtime, components."""
        from app.config import settings

        components: dict[str, Any] = {}
        if container is not None:
            pipeline = container.try_get("AnalysisPipeline")
            if pipeline is not None:
                components["pipeline_cycle"] = getattr(pipeline, "_cycle", 0)
            providers = container.try_get("llm_providers")
            if providers is not None:
                components["providers"] = providers.provider_stats()  # type: ignore[union-attr]
            ws = container.try_get("binance_ws")
            if ws is not None:
                components["ws_connections"] = {
                    "alive": ws.alive_connections,  # type: ignore[union-attr]
                    "total": ws.total_connections,  # type: ignore[union-attr]
                    "status": ws.status,  # type: ignore[union-attr]
                }
            market_data = container.try_get("market_data")
            if market_data is not None:
                components["symbols_tracked"] = len(market_data.known_symbols())  # type: ignore[union-attr]

        return {
            "version": "1.0.0",
            "environment": settings.environment,
            "binance_testnet": settings.binance_testnet,
            "ai_provider_order": settings.ai_provider_list,
            "components": components,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------ #
    # Signals
    # ------------------------------------------------------------------ #
    @app.get("/api/signals", tags=["signals"])
    async def list_signals(
        limit: int = Query(50, ge=1, le=500),
        symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        try:
            async with get_session() as session:
                repo = SignalRepository(session)
                signals = await repo.recent(limit=limit, symbol=symbol)
                return [_signal_to_dict(s) for s in signals]
        except Exception as exc:  # noqa: BLE001
            log.exception("api_list_signals_failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/signals/open", tags=["signals"])
    async def open_signals() -> list[dict[str, Any]]:
        try:
            async with get_session() as session:
                repo = SignalRepository(session)
                signals = await repo.open_signals()
                return [_signal_to_dict(s) for s in signals]
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # ------------------------------------------------------------------ #
    # Market
    # ------------------------------------------------------------------ #
    @app.get("/api/market", tags=["market"])
    async def market_top(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
        if container is None:
            raise HTTPException(status_code=503, detail="Container not initialized")
        market_data = container.try_get("market_data")
        if market_data is None:
            raise HTTPException(status_code=503, detail="Market data engine not ready")
        states = market_data.top_volume_symbols(limit)  # type: ignore[union-attr]
        return [_state_to_dict(s) for s in states]

    @app.get("/api/market/{symbol}", tags=["market"])
    async def market_symbol(symbol: str) -> dict[str, Any]:
        if container is None:
            raise HTTPException(status_code=503, detail="Container not initialized")
        market_data = container.try_get("market_data")
        if market_data is None:
            raise HTTPException(status_code=503, detail="Market data engine not ready")
        state = market_data.get(symbol.upper())  # type: ignore[union-attr]
        if state is None:
            raise HTTPException(status_code=404, detail=f"Symbol {symbol} not tracked")
        return _state_to_dict(state)

    # ------------------------------------------------------------------ #
    # Metrics
    # ------------------------------------------------------------------ #
    @app.get("/api/metrics", tags=["metrics"])
    async def recent_metrics(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        try:
            async with get_session() as session:
                repo = MetricRepository(session)
                since = datetime.now(timezone.utc) - timedelta(hours=24)
                metrics = await repo.series(name="", since=since, limit=limit)
                return [
                    {
                        "name": m.name,
                        "value_float": m.value_float,
                        "value_int": m.value_int,
                        "tags": m.tags,
                        "timestamp": m.timestamp.isoformat(),
                    }
                    for m in metrics
                ]
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # ------------------------------------------------------------------ #
    # AI providers
    # ------------------------------------------------------------------ #
    @app.get("/api/providers", tags=["ai"])
    async def provider_health() -> dict[str, Any]:
        if container is None:
            raise HTTPException(status_code=503, detail="Container not initialized")
        providers = container.try_get("llm_providers")
        if providers is None:
            raise HTTPException(status_code=503, detail="AI providers not initialized")
        return {
            "stats": providers.provider_stats(),  # type: ignore[union-attr]
            "healthy": providers.healthy_providers(),  # type: ignore[union-attr]
        }

    # ------------------------------------------------------------------ #
    # Manual scan trigger (dev only)
    # ------------------------------------------------------------------ #
    @app.post("/api/scan/trigger", tags=["scan"])
    async def trigger_scan() -> dict[str, Any]:
        if container is None:
            raise HTTPException(status_code=503, detail="Container not initialized")
        pipeline = container.try_get("AnalysisPipeline")
        if pipeline is None:
            raise HTTPException(status_code=503, detail="Pipeline not initialized")
        result = await pipeline.run_once()  # type: ignore[union-attr]
        return {
            "cycle": result.cycle,
            "duration_ms": result.duration_ms,
            "stage1_passed": len(result.stage1.candidates) if result.stage1 else 0,
            "stage2_setups": len(result.stage2.setups) if result.stage2 else 0,
            "signals": len(result.signals),
            "error": result.error,
        }

    # ------------------------------------------------------------------ #
    # Root (simple HTML status page — minimal dashboard)
    # ------------------------------------------------------------------ #
    @app.get("/", response_class=HTMLResponse, tags=["dashboard"])
    async def dashboard() -> str:
        return _simple_dashboard_html()

    return app


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _signal_to_dict(s: Any) -> dict[str, Any]:
    return {
        "id": s.id,
        "symbol": s.symbol,
        "signal_type": s.signal_type,
        "direction": s.direction,
        "entry": s.entry,
        "stop_loss": s.stop_loss,
        "take_profit": s.take_profit,
        "risk_reward": s.risk_reward,
        "confidence": s.confidence,
        "confluence_score": s.confluence_score,
        "trend_htf": s.trend_htf,
        "trend_mtf": s.trend_mtf,
        "trend_ltf": s.trend_ltf,
        "ai_decision": s.ai_decision,
        "status": s.status,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def _state_to_dict(s: Any) -> dict[str, Any]:
    return {
        "symbol": s.symbol,
        "last_price": s.last_price,
        "volume_24h": s.volume_24h,
        "quote_volume_24h": s.quote_volume_24h,
        "price_change_pct_24h": s.price_change_pct_24h,
        "funding_rate": s.funding_rate,
        "open_interest": s.open_interest,
        "bid_price": s.bid_price,
        "ask_price": s.ask_price,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def _simple_dashboard_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Crypto Futures Intelligence Platform</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0b0e14; color: #e1e4e8; margin: 0; padding: 24px; }
    h1 { color: #58a6ff; margin-top: 0; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
    .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
    .card h2 { font-size: 14px; color: #8b949e; margin: 0 0 8px 0; text-transform: uppercase; letter-spacing: 0.5px; }
    .big { font-size: 28px; font-weight: 600; }
    .ok { color: #3fb950; } .warn { color: #d29922; } .err { color: #f85149; }
    a { color: #58a6ff; } .muted { color: #8b949e; font-size: 12px; }
    ul { padding-left: 18px; }
    code { background: #21262d; padding: 2px 6px; border-radius: 3px; }
  </style>
</head>
<body>
  <h1>Institutional Crypto Futures Intelligence Platform</h1>
  <p class="muted">Minimal status dashboard — full UI in module 26.</p>
  <div class="grid">
    <div class="card">
      <h2>Health</h2>
      <div><a href="/health/ready">Readiness probe →</a></div>
      <div class="muted" style="margin-top:8px"><a href="/docs">API docs</a> · <a href="/api/status">Status JSON</a></div>
    </div>
    <div class="card">
      <h2>Recent signals</h2>
      <div class="big"><a href="/api/signals?limit=20">/api/signals</a></div>
      <div class="muted">Open positions: <a href="/api/signals/open">/api/signals/open</a></div>
    </div>
    <div class="card">
      <h2>Market data</h2>
      <div class="big"><a href="/api/market?limit=20">/api/market</a></div>
      <div class="muted">Top symbols by 24h volume</div>
    </div>
    <div class="card">
      <h2>AI providers</h2>
      <div class="big"><a href="/api/providers">/api/providers</a></div>
      <div class="muted">NVIDIA NIM → OpenRouter → Mock</div>
    </div>
    <div class="card">
      <h2>Metrics</h2>
      <div><a href="/api/metrics">/api/metrics</a></div>
      <div class="muted">Scan duration, cache hits, queue depth</div>
    </div>
    <div class="card">
      <h2>Manual scan</h2>
      <div><code>POST /api/scan/trigger</code></div>
      <div class="muted" style="margin-top:8px">Triggers one cycle immediately</div>
    </div>
  </div>
</body>
</html>
"""


__all__ = ["build_app"]
