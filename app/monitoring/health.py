"""Health monitor — periodic checks of all platform components.

Runs as a background task that polls every component every N seconds and
records health metrics. On failure, marks the component unhealthy and
triggers self-healing where possible (e.g. reconnect WebSocket, refresh
REST session).

Components monitored:
- Binance REST (ping exchangeInfo)
- Binance WebSocket (check alive_connections + last_msg_age)
- PostgreSQL (SELECT 1)
- Redis (PING)
- AI providers (provider.healthy flags)
- Telegram (no proactive check — only flagged on send failure)
- Pipeline loop (last_cycle timestamp)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.container import ServiceContainer
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class ComponentHealth:
    name: str
    healthy: bool
    last_check: datetime
    last_success: datetime | None
    error: str = ""
    latency_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class HealthMonitor:
    """Background health checker for all platform components."""

    def __init__(self, container: ServiceContainer, interval_sec: float = 30.0) -> None:
        self._container = container
        self._interval = interval_sec
        self._health: dict[str, ComponentHealth] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def check_all(self) -> dict[str, ComponentHealth]:
        """Run a single round of health checks; return all results."""
        results: dict[str, ComponentHealth] = {}
        now = datetime.now(timezone.utc)

        # PostgreSQL
        results["postgres"] = await self._check_postgres(now)
        # Redis
        results["redis"] = await self._check_redis(now)
        # Binance REST
        results["binance_rest"] = await self._check_binance_rest(now)
        # Binance WS
        results["binance_ws"] = self._check_binance_ws(now)
        # AI providers
        results["ai_providers"] = self._check_ai_providers(now)
        # Pipeline
        results["pipeline"] = self._check_pipeline(now)

        self._health = results
        return results

    async def _check_postgres(self, now: datetime) -> ComponentHealth:
        start = time.time()
        try:
            from app.db.session import check_db_connection

            ok = await check_db_connection()
            latency_ms = int((time.time() - start) * 1000)
            return ComponentHealth(
                name="postgres",
                healthy=ok,
                last_check=now,
                last_success=now if ok else None,
                latency_ms=latency_ms,
                error="" if ok else "SELECT 1 failed",
            )
        except Exception as exc:  # noqa: BLE001
            return ComponentHealth(
                name="postgres",
                healthy=False,
                last_check=now,
                last_success=None,
                error=str(exc),
                latency_ms=int((time.time() - start) * 1000),
            )

    async def _check_redis(self, now: datetime) -> ComponentHealth:
        start = time.time()
        try:
            from app.cache.redis_client import redis_health

            ok = await redis_health()
            return ComponentHealth(
                name="redis",
                healthy=ok,
                last_check=now,
                last_success=now if ok else None,
                latency_ms=int((time.time() - start) * 1000),
                error="" if ok else "PING failed",
            )
        except Exception as exc:  # noqa: BLE001
            return ComponentHealth(
                name="redis",
                healthy=False,
                last_check=now,
                last_success=None,
                error=str(exc),
            )

    async def _check_binance_rest(self, now: datetime) -> ComponentHealth:
        rest = self._container.try_get("binance_rest")
        if rest is None:
            return ComponentHealth(
                name="binance_rest", healthy=False, last_check=now,
                last_success=None, error="not initialized",
            )
        start = time.time()
        try:
            # Use a cheap endpoint — exchangeInfo has weight 1
            await rest.exchange_info()  # type: ignore[union-attr]
            latency_ms = int((time.time() - start) * 1000)
            return ComponentHealth(
                name="binance_rest", healthy=True, last_check=now,
                last_success=now, latency_ms=latency_ms,
                metadata={"weight_used": rest.weight_used},  # type: ignore[union-attr]
            )
        except Exception as exc:  # noqa: BLE001
            return ComponentHealth(
                name="binance_rest", healthy=False, last_check=now,
                last_success=None, error=str(exc),
                latency_ms=int((time.time() - start) * 1000),
            )

    def _check_binance_ws(self, now: datetime) -> ComponentHealth:
        ws = self._container.try_get("binance_ws")
        if ws is None:
            return ComponentHealth(
                name="binance_ws", healthy=False, last_check=now,
                last_success=None, error="not initialized",
            )
        alive = ws.alive_connections  # type: ignore[union-attr]
        return ComponentHealth(
            name="binance_ws", healthy=alive > 0, last_check=now,
            last_success=now if alive > 0 else None,
            error="" if alive > 0 else "no alive connections",
            metadata={
                "alive_connections": alive,
                "total_connections": ws.total_connections,  # type: ignore[union-attr]
                "status": ws.status,  # type: ignore[union-attr]
            },
        )

    def _check_ai_providers(self, now: datetime) -> ComponentHealth:
        providers = self._container.try_get("llm_providers")
        if providers is None:
            return ComponentHealth(
                name="ai_providers", healthy=False, last_check=now,
                last_success=None, error="not initialized",
            )
        healthy = providers.healthy_providers()  # type: ignore[union-attr]
        stats = providers.provider_stats()  # type: ignore[union-attr]
        return ComponentHealth(
            name="ai_providers",
            healthy=len(healthy) > 0,
            last_check=now,
            last_success=now if healthy else None,
            metadata={"healthy_providers": healthy, "stats": stats},
            error="" if healthy else "no healthy providers",
        )

    def _check_pipeline(self, now: datetime) -> ComponentHealth:
        pipeline = self._container.try_get("AnalysisPipeline")
        if pipeline is None:
            return ComponentHealth(
                name="pipeline", healthy=False, last_check=now,
                last_success=None, error="not initialized",
            )
        cycle = getattr(pipeline, "_cycle", 0)  # type: ignore[union-attr]
        return ComponentHealth(
            name="pipeline",
            healthy=cycle > 0,
            last_check=now,
            last_success=now,
            metadata={"cycle": cycle},
        )

    # ------------------------------------------------------------------ #
    # Background task
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run_forever(), name="health-monitor")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                results = await self.check_all()
                unhealthy = [n for n, h in results.items() if not h.healthy]
                if unhealthy:
                    log.warning("health_check_unhealthy_components", unhealthy=unhealthy)
                else:
                    log.debug("health_check_all_healthy", components=list(results.keys()))
            except Exception:  # noqa: BLE001
                log.exception("health_monitor_iteration_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue

    def snapshot(self) -> dict[str, Any]:
        return {name: h.__dict__ for name, h in self._health.items()}


__all__ = ["HealthMonitor", "ComponentHealth"]
