"""Environment management — secret loading and validation.

Provides helpers to:
- Detect environment (dev/staging/prod)
- Validate required secrets are present before startup
- Mask secrets when logging
- Provide safe defaults for local development

Nothing in this module ever *prints* a secret. It only verifies presence.
"""

from __future__ import annotations

from typing import Iterable

from app.config import settings
from app.core.errors import ConfigError, SecretNotFoundError
from app.core.logging import get_logger

log = get_logger(__name__)


def detect_environment() -> str:
    """Return the active environment name."""
    return settings.environment


def is_production() -> bool:
    return settings.is_production


def is_testnet() -> bool:
    return settings.binance_testnet


def require_secrets(keys: Iterable[str]) -> None:
    """Verify that each named secret is non-empty.

    Raises ``SecretNotFoundError`` for the first missing secret. Use this at
    startup for components that cannot operate without credentials (e.g.
    AI providers when no mock is available).
    """
    for key in keys:
        value = _get_secret(key)
        if not value:
            raise SecretNotFoundError(key)


def _get_secret(key: str) -> str:
    """Read a secret from the Settings object by attribute name."""
    # We deliberately do not log the value.
    attr = getattr(settings, key, None)
    if attr is None:
        return ""
    # SecretStr → str
    if hasattr(attr, "get_secret_value"):
        return attr.get_secret_value()  # type: ignore[no-any-return]
    return str(attr)


def mask(value: str, visible: int = 4) -> str:
    """Return ``value`` masked except for the last ``visible`` chars.

    >>> mask("sk-abcdef123456")
    'sk-****************3456'
    """
    if not value:
        return ""
    if len(value) <= visible:
        return "*" * len(value)
    return "*" * (len(value) - visible) + value[-visible:]


def validate_runtime() -> list[str]:
    """Return a list of startup warnings (empty if everything is fine).

    This does NOT raise; it's meant for the lifespan to log a health summary.
    Hard failures should still use :func:`require_secrets`.
    """
    warnings: list[str] = []

    # Confluence weight sanity
    total = settings.confluence_weights_total()
    if total != 100:
        warnings.append(f"Confluence weights sum to {total}, not 100")

    # Provider order sanity
    providers = settings.ai_provider_list
    if not providers:
        warnings.append("No AI providers configured")
    if "mock" not in providers and settings.offline_mode:
        warnings.append("offline_mode is on but no 'mock' provider in ai_provider_order")

    # Telegram
    if settings.telegram_enabled and not settings.telegram_bot_token.get_secret_value():
        warnings.append("telegram_enabled=true but token is empty")

    # Binance keys (only needed for private endpoints; public data works without)
    if not settings.binance_testnet and not settings.binance_api_key.get_secret_value():
        warnings.append("Mainnet selected but API key is empty — private endpoints will fail")

    return warnings


def assert_config_valid() -> None:
    """Run all hard validations; raise ConfigError on the first failure."""
    htf, mtf, ltf = settings.timeframes
    if not htf or not mtf or not ltf:
        raise ConfigError("scan_timeframes must specify HTF, MTF, LTF")
    if settings.scan_interval_sec < 5:
        raise ConfigError("scan_interval_sec must be >= 5 to avoid Binance rate limits")
    if settings.ai_max_requests_per_cycle < 1:
        raise ConfigError("ai_max_requests_per_cycle must be >= 1")
    if settings.risk_max_risk_per_trade_pct <= 0 or settings.risk_max_risk_per_trade_pct > 5:
        raise ConfigError("risk_max_risk_per_trade_pct must be in (0, 5]")


__all__ = [
    "detect_environment",
    "is_production",
    "is_testnet",
    "require_secrets",
    "mask",
    "validate_runtime",
    "assert_config_valid",
]
