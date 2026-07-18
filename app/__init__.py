"""Institutional Crypto Futures Intelligence Platform.

Top-level application package. The platform is organized into modular
sub-packages, each independently testable and replaceable:

- ``app.config``     — Configuration & environment management
- ``app.core``       — Cross-cutting concerns (logging, errors, lifecycle)
- ``app.db``         — Async PostgreSQL persistence layer
- ``app.cache``      — Redis cache layer
- ``app.exchange``   — Binance REST & WebSocket integrations
- ``app.market``     — Market data, candle, indicator engines
- ``app.structure``  — Market structure, trend, liquidity, smart money
- ``app.engine``     — Three-stage analysis pipeline (Stage 1/2/3)
- ``app.confluence`` — Weighted confluence scoring
- ``app.risk``       — Risk management & position sizing
- ``app.signal``     — Signal generation & validation
- ``app.ai``         — LLM provider layer (NIM → OpenRouter fallback)
- ``app.notifier``   — Telegram notification engine
- ``app.api``        — FastAPI REST surface
- ``app.monitoring`` — Health checks & operational metrics
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
