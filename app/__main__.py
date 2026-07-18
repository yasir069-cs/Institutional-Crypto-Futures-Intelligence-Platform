"""CLI entrypoint: ``python -m app`` starts the platform."""

from __future__ import annotations

from app.core.lifespan import run_sync

if __name__ == "__main__":
    run_sync()
