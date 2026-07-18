"""Structured logging setup using structlog.

The platform uses structlog throughout for:
- **JSON output** in production (machine-parseable, easy to ship to ELK)
- **Pretty console output** in development
- **Contextual binding** per module and per request

Usage
-----
>>> from app.core.logging import get_logger
>>> log = get_logger(__name__)
>>> log.info("scan_completed", duration_ms=42, candidates=23)

Every log call accepts structured keyword arguments; never use ``%``-format
or f-strings for the message — pass values as kwargs so they remain queryable.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

try:
    import structlog  # type: ignore
except ImportError:  # pragma: no cover - structlog is a hard dep at runtime
    structlog = None  # type: ignore

from app.config import settings


def _should_use_json() -> bool:
    """Return True if logs should be emitted as JSON."""
    return str(settings.log_format).lower() == "json"


def configure_logging() -> None:
    """Configure structlog and stdlib logging once at process start.

    Idempotent: safe to call multiple times.
    """
    if structlog is None:  # pragma: no cover
        # Fallback: configure plain stdlib logging.
        logging.basicConfig(
            level=getattr(logging, settings.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
            stream=sys.stdout,
        )
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Shared timestamper renders ISO-8601 in UTC.
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.dict_tracebacks,
    ]

    if _should_use_json():
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging into structlog so third-party libs are unified.
    logging.basicConfig(
        level=level,
        format="%(message)s",
        stream=sys.stdout,
        force=True,
    )
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True


def get_logger(name: str | None = None) -> Any:
    """Return a structured logger bound to ``name``.

    Falls back to stdlib logging if structlog is unavailable.
    """
    if structlog is not None:
        return structlog.get_logger(name) if name else structlog.get_logger()
    return logging.getLogger(name or "platform")


__all__ = ["configure_logging", "get_logger"]
