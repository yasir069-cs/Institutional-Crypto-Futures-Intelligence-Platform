"""ASGI entrypoint for uvicorn: ``uvicorn app.asgi:app``."""

from __future__ import annotations

from app.core.container import container
from app.core.lifespan import lifespan
from app.api.app import build_app

# Build the app at module level so uvicorn can import it.
# The lifespan wires all services into the container.
app = build_app(container)


@asynccontextmanager
async def _wrap_lifespan(app):  # type: ignore[no-untyped-def]
    async with lifespan(app):
        yield


from contextlib import asynccontextmanager

# Re-bind the app's lifespan
app.router.lifespan_context = _wrap_lifespan


__all__ = ["app"]
