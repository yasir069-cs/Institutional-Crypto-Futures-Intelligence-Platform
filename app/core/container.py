"""Dependency Injection container.

The platform uses a lightweight **composition root** pattern: at process
startup, all long-lived services are constructed once and registered here.
Modules then receive their dependencies via constructor injection — they
never import a singleton directly. This keeps modules testable (just pass
mocks) and prevents hidden coupling.

Design rules
------------
- The container itself is **async-context-manager** aware.
- Registration is explicit: ``container.register(Interface, instance)``.
- Resolution returns the *same* instance per interface (singleton scope).
- Modules MUST NOT import the container at module top-level — only inside
  composition roots (``main.py``, lifespan handlers, tests). Domain code
  receives dependencies as constructor arguments.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Generic, TypeVar

from app.core.errors import PlatformError
from app.core.logging import get_logger

T = TypeVar("T")
log = get_logger(__name__)

# Type alias for an async factory that produces an instance.
AsyncFactory = Callable[["ServiceContainer"], Awaitable[Any]]


class ServiceNotRegisteredError(PlatformError):
    """Raised when a service is requested but never registered."""

    def __init__(self, key: Any) -> None:
        super().__init__(
            f"Service not registered: {key!r}",
            code="di.not_registered",
            context={"key": str(key)},
        )


class ServiceContainer:
    """Async-friendly singleton service registry.

    Two registration modes are supported:

    1. **Instance**: ``register(KeyType, obj)`` — obj is returned as-is.
    2. **Factory**: ``register_factory(KeyType, async_factory)`` — factory
       is awaited lazily on first ``get()``, then cached.

    Factories are awaited under a lock so concurrent ``get()`` calls during
    startup do not produce duplicate instances.
    """

    def __init__(self) -> None:
        self._instances: dict[Any, Any] = {}
        self._factories: dict[Any, AsyncFactory] = {}
        self._locks: dict[Any, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._closed = False

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    def register(self, key: Any, instance: Any) -> None:
        """Register an already-constructed instance."""
        if self._closed:
            raise PlatformError("Cannot register on a closed container", code="di.closed")
        self._instances[key] = instance

    def register_factory(self, key: Any, factory: AsyncFactory) -> None:
        """Register an async factory; called once on first ``get()``."""
        if self._closed:
            raise PlatformError("Cannot register on a closed container", code="di.closed")
        self._factories[key] = factory

    # ------------------------------------------------------------------ #
    # Resolution
    # ------------------------------------------------------------------ #
    async def get(self, key: type[T] | str) -> T:
        """Resolve a service. Creates it lazily if a factory was registered."""
        if self._closed:
            raise PlatformError("Container is closed", code="di.closed")

        if key in self._instances:
            return self._instances[key]  # type: ignore[no-any-return]

        if key not in self._factories:
            raise ServiceNotRegisteredError(key)

        # Per-key lock prevents duplicate construction under concurrency.
        lock = self._locks.get(key)
        if lock is None:
            async with self._global_lock:
                lock = self._locks.get(key)
                if lock is None:
                    lock = asyncio.Lock()
                    self._locks[key] = lock

        async with lock:
            # Re-check after acquiring; another task may have built it.
            if key in self._instances:
                return self._instances[key]  # type: ignore[no-any-return]
            instance = await self._factories[key](self)
            self._instances[key] = instance
            return instance  # type: ignore[no-any-return]

    def try_get(self, key: type[T] | str) -> T | None:
        """Return an already-built instance, or None. Never triggers factory."""
        return self._instances.get(key)  # type: ignore[no-any-return]

    def has(self, key: Any) -> bool:
        return key in self._instances or key in self._factories

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def close(self) -> None:
        """Close all registered instances that expose ``aclose()``.

        Order is reverse-registration so dependents shut down before their
        dependencies. Errors are logged but do not abort the shutdown.
        """
        if self._closed:
            return
        self._closed = True

        # Reverse insertion order: dependents first, dependencies last.
        for key, inst in reversed(list(self._instances.items())):
            closer = getattr(inst, "aclose", None)
            if callable(closer):
                try:
                    result = closer()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:  # noqa: BLE001 - best-effort shutdown
                    log.exception("di.close_failed", key=str(key))
        self._instances.clear()
        self._factories.clear()
        self._locks.clear()


# A module-level singleton. ONLY the composition root (main.py / lifespan)
# should touch this directly. Domain code receives dependencies via ctor.
container = ServiceContainer()


__all__ = [
    "ServiceContainer",
    "ServiceNotRegisteredError",
    "container",
    "AsyncFactory",
]
