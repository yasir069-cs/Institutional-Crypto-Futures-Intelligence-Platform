"""Configuration package — single source of truth for all settings.

Exposes :data:`settings`, a process-wide immutable config object built from
environment variables (and an optional ``.env`` file). All modules import
``settings`` only to read values; they MUST NOT mutate it.
"""

from __future__ import annotations

from app.config.settings import Settings, settings

__all__ = ["Settings", "settings"]
