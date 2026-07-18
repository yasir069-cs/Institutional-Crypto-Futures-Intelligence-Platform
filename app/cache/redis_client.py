"""Async Redis client with namespace isolation and health check.

The platform uses Redis for:
- **Hot caches** — indicator snapshots, AI responses, market state
- **Rate limit windows** — Binance weight tracking, AI request count
- **Dedup keys** — Telegram alert deduplication
- **Pub/sub** — internal event distribution (optional)

All keys are namespaced under ``settings.redis_namespace`` so multiple
environments can share one Redis instance safely.
"""

from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.core.errors import CacheError
from app.core.logging import get_logger

log = get_logger(__name__)

try:
    from redis.asyncio import Redis, from_url
    from redis.exceptions import RedisError
except ImportError:  # pragma: no cover
    Redis = None  # type: ignore
    from_url = None  # type: ignore

    class RedisError(Exception):  # type: ignore[no-redef]
        pass


class NamespacedRedis:
    """Thin wrapper around ``redis.asyncio.Redis`` with key namespacing."""

    def __init__(self, client: Any, namespace: str) -> None:
        self._client = client
        self._ns = namespace

    # ------------------------------------------------------------------ #
    # Key helpers
    # ------------------------------------------------------------------ #
    def _key(self, key: str) -> str:
        return f"{self._ns}:{key}"

    # ------------------------------------------------------------------ #
    # Basic ops
    # ------------------------------------------------------------------ #
    async def get(self, key: str) -> str | None:
        try:
            return await self._client.get(self._key(key))
        except RedisError as exc:
            raise CacheError(f"Redis GET failed: {exc}", context={"key": key}) from exc

    async def set(self, key: str, value: str | bytes, ttl: int | None = None) -> bool:
        try:
            return bool(await self._client.set(self._key(key), value, ex=ttl))
        except RedisError as exc:
            raise CacheError(f"Redis SET failed: {exc}", context={"key": key}) from exc

    async def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        try:
            return int(await self._client.delete(*[self._key(k) for k in keys]))
        except RedisError as exc:
            raise CacheError(f"Redis DELETE failed: {exc}", context={"keys": list(keys)}) from exc

    async def exists(self, key: str) -> bool:
        try:
            return bool(await self._client.exists(self._key(key)))
        except RedisError as exc:
            raise CacheError(f"Redis EXISTS failed: {exc}", context={"key": key}) from exc

    async def expire(self, key: str, ttl: int) -> bool:
        try:
            return bool(await self._client.expire(self._key(key), ttl))
        except RedisError as exc:
            raise CacheError(f"Redis EXPIRE failed: {exc}", context={"key": key}) from exc

    async def incr(self, key: str, ttl: int | None = None) -> int:
        try:
            full = self._key(key)
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.incr(full)
                if ttl is not None:
                    pipe.expire(full, ttl, nx=True)  # only set TTL if no TTL yet
                results = await pipe.execute()
            return int(results[0])
        except RedisError as exc:
            raise CacheError(f"Redis INCR failed: {exc}", context={"key": key}) from exc

    # ------------------------------------------------------------------ #
    # JSON helpers
    # ------------------------------------------------------------------ #
    async def get_json(self, key: str, default: Any = None) -> Any:
        raw = await self.get(key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return default

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> bool:
        try:
            return await self.set(key, json.dumps(value, default=str), ttl=ttl)
        except (TypeError, ValueError) as exc:
            raise CacheError(f"JSON encode failed for key {key}: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #
    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except RedisError:
            return False

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001
            log.debug("redis_close_failed_ignored")


_redis_instance: NamespacedRedis | None = None


async def build_redis() -> NamespacedRedis | None:
    """Construct (or return cached) namespaced Redis client.

    On connection failure we log and return ``None`` — callers should
    use :func:`redis_health` to gate operations, and the platform should not
    crash just because Redis is unavailable. The lifespan will register the
    ``None`` so DI still works.
    """
    global _redis_instance
    if _redis_instance is not None:
        return _redis_instance

    if from_url is None:  # pragma: no cover
        raise CacheError("redis library not installed")

    try:
        client = from_url(
            settings.redis_url,
            max_connections=settings.redis_max_connections,
            decode_responses=True,
            encoding="utf-8",
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            health_check_interval=30,
            retry_on_timeout=True,
        )
        # Verify connectivity.
        await client.ping()
        _redis_instance = NamespacedRedis(client, settings.redis_namespace)
        log.info("redis_connected", url=settings.redis_url, namespace=settings.redis_namespace)
        return _redis_instance
    except (RedisError, OSError) as exc:
        log.warning(
            "redis_unavailable",
            url=settings.redis_url,
            error=str(exc),
            message="Platform will run without Redis (cache + rate limits degraded)",
        )
        return None


async def redis_health() -> bool:
    """Return True if Redis is reachable."""
    if _redis_instance is None:
        return False
    return await _redis_instance.ping()


async def close_redis() -> None:
    global _redis_instance
    if _redis_instance is not None:
        await _redis_instance.aclose()
        _redis_instance = None


__all__ = [
    "NamespacedRedis",
    "build_redis",
    "redis_health",
    "close_redis",
]
