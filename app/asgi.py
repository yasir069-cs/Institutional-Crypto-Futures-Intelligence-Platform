"""ASGI entrypoint for uvicorn: ``uvicorn app.asgi:app``."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.core.container import container
from app.core.lifespan import lifespan
from app.api.app import build_app

# Build the app at module level so uvicorn can import it.
app = build_app(container)


@asynccontextmanager
async def _wrap_lifespan(app) -> AsyncIterator[None]:
    """Bridge FastAPI's lifespan to our platform lifespan."""
    async with lifespan(app):
        yield


# Bind our lifespan to the FastAPI app.
app.router.lifespan_context = _wrap_lifespan


__all__ = ["app"]
