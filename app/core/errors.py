"""Platform-level exception hierarchy.

Every domain module should raise subclasses of ``PlatformError`` so that
callers can catch errors uniformly. Avoid leaking third-party exceptions
across module boundaries — wrap them in one of the classes below.

Design principles
------------------
- **Stable contracts**: callers depend on exception *types*, not messages.
- **No leakage**: third-party errors are translated at the boundary.
- **Structured**: every error carries a stable ``code`` for telemetry.
- **Context-rich**: errors accept arbitrary keyword context for logs.
"""

from __future__ import annotations

from typing import Any


class PlatformError(Exception):
    """Base class for all platform-raised errors.

    Parameters
    ----------
    message:
        Human-readable description. Safe to surface in logs and alerts.
    code:
        Stable machine-readable error code (e.g. ``"binance.rate_limited"``).
        Used by monitoring to count occurrences without parsing strings.
    context:
        Arbitrary structured context attached for debugging. Never include
        secrets here — it is intended for log inclusion.
    """

    def __init__(
        self,
        message: str = "",
        *,
        code: str = "platform.error",
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.context: dict[str, Any] = dict(context or {})

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code}] {self.message}" if self.message else f"[{self.code}]"

    def to_dict(self) -> dict[str, Any]:
        """Return a structured representation suitable for logging/JSON."""
        return {
            "code": self.code,
            "message": self.message,
            "context": self.context,
        }


# --------------------------------------------------------------------------- #
# Configuration & environment
# --------------------------------------------------------------------------- #
class ConfigError(PlatformError):
    """Raised when configuration is missing or invalid."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="config.error", context=context)


class SecretNotFoundError(PlatformError):
    """Raised when a required secret is missing from the environment."""

    def __init__(self, key: str) -> None:
        super().__init__(
            f"Required secret '{key}' is not set in environment",
            code="config.secret_missing",
            context={"key": key},
        )


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
class DatabaseError(PlatformError):
    """Raised for database-level failures."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="db.error", context=context)


class CacheError(PlatformError):
    """Raised for Redis/cache-level failures."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="cache.error", context=context)


# --------------------------------------------------------------------------- #
# Exchange (Binance)
# --------------------------------------------------------------------------- #
class ExchangeError(PlatformError):
    """Base for exchange-related errors."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "exchange.error",
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code, context=context)


class RateLimitedError(ExchangeError):
    """Binance rate limit hit — caller should back off."""

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__(
            "Binance rate limit exceeded",
            code="exchange.rate_limited",
            context={"retry_after": retry_after},
        )
        self.retry_after = retry_after


class WebSocketDisconnectedError(ExchangeError):
    """WebSocket stream dropped — caller should trigger reconnect."""

    def __init__(self, reason: str = "disconnected") -> None:
        super().__init__(
            f"Binance WebSocket disconnected: {reason}",
            code="exchange.ws_disconnected",
            context={"reason": reason},
        )


# --------------------------------------------------------------------------- #
# AI providers
# --------------------------------------------------------------------------- #
class AIProviderError(PlatformError):
    """Base for AI provider failures."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "ai.error",
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code, context=context)


class AIProviderUnavailableError(AIProviderError):
    """All providers are unavailable — caller should use cache or skip."""

    def __init__(self, message: str = "All AI providers unavailable") -> None:
        super().__init__(message, code="ai.unavailable")


class AIResponseParseError(AIProviderError):
    """Provider returned a response that could not be parsed."""

    def __init__(self, raw: str | None = None) -> None:
        super().__init__(
            "Failed to parse AI provider response",
            code="ai.parse_error",
            context={"raw_snippet": (raw or "")[:500]},
        )


# --------------------------------------------------------------------------- #
# Notifier
# --------------------------------------------------------------------------- #
class NotificationError(PlatformError):
    """Raised when an outbound notification fails."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="notify.error", context=context)


__all__ = [
    "PlatformError",
    "ConfigError",
    "SecretNotFoundError",
    "DatabaseError",
    "CacheError",
    "ExchangeError",
    "RateLimitedError",
    "WebSocketDisconnectedError",
    "AIProviderError",
    "AIProviderUnavailableError",
    "AIResponseParseError",
    "NotificationError",
]
