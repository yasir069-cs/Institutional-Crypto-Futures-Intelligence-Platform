"""Pydantic-settings based configuration.

All platform behavior is configurable via environment variables. The
``.env`` file is loaded if present. Settings are validated at startup —
invalid configuration fails fast instead of producing silent misbehavior.

Categories
----------
- **Runtime**: environment, log level, log format
- **Database**: PostgreSQL DSN, pool size
- **Cache**: Redis URL
- **Binance**: testnet flag, API keys, rate limits
- **AI**: provider order, keys, model names, cache TTL, concurrency
- **Telegram**: bot token, chat ID
- **Scanning**: stage 1/2/3 thresholds, timeframes, scan interval
- **Risk**: max risk %, SL limits, RR minimum
- **Notifications**: dedup window, throttle
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Runtime
    # ------------------------------------------------------------------ #
    environment: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    # When true, platform runs without external services (DB/Redis/Binance/AI)
    # Useful for tests and local development without infrastructure.
    offline_mode: bool = False

    # ------------------------------------------------------------------ #
    # Database (PostgreSQL)
    # ------------------------------------------------------------------ #
    database_url: str = Field(
        default="postgresql+asyncpg://platform:platform@localhost:5432/platform",
        description="SQLAlchemy async DSN",
    )
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout: float = 30.0
    db_pool_recycle: int = 1800  # seconds
    db_echo: bool = False

    # ------------------------------------------------------------------ #
    # Redis
    # ------------------------------------------------------------------ #
    redis_url: str = "redis://localhost:6379/0"
    redis_namespace: str = "platform"
    redis_max_connections: int = 50

    # ------------------------------------------------------------------ #
    # Binance Futures
    # ------------------------------------------------------------------ #
    binance_testnet: bool = True
    binance_rest_url: str = ""  # empty = auto-derive from testnet flag
    binance_ws_url: str = ""  # empty = auto-derive from testnet flag
    binance_api_key: SecretStr = SecretStr("")
    binance_api_secret: SecretStr = SecretStr("")
    binance_recv_window_ms: int = 5000
    binance_request_timeout: float = 10.0
    # Rate limits — Binance defaults to 2400 weight/min for futures.
    binance_weight_per_minute: int = 2400
    binance_order_per_10s: int = 300
    binance_order_per_day: int = 100_000
    # WebSocket
    binance_ws_reconnect_delay: float = 1.0
    binance_ws_reconnect_max_delay: float = 60.0
    binance_ws_heartbeat_sec: int = 30
    binance_ws_max_streams_per_conn: int = 200

    # ------------------------------------------------------------------ #
    # AI providers
    # ------------------------------------------------------------------ #
    # Comma-separated provider order, e.g. "groq,nim,openrouter,mock"
    # Groq is recommended primary (free tier: 30 req/min on Llama 3.1 70B)
    ai_provider_order: str = "groq,nim,openrouter,mock"
    ai_default_model: str = "meta-llama/llama-3.1-70b-instruct"

    # Groq (free, OpenAI-compatible, very fast)
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "llama-3.1-70b-versatile"
    groq_timeout: float = 30.0

    # NVIDIA NIM
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nim_api_key: SecretStr = SecretStr("")
    nim_model: str = "meta/llama-3.1-70b-instruct"
    nim_timeout: float = 30.0

    # OpenRouter
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_model: str = "meta-llama/llama-3.1-70b-instruct"
    openrouter_timeout: float = 30.0

    # AI behaviour
    ai_max_requests_per_cycle: int = 10
    ai_concurrency: int = 3
    ai_cache_ttl_sec: int = 300
    ai_temperature: float = 0.2
    ai_max_tokens: int = 800
    # Skip AI if any of these conditions hold (gate before sending)
    ai_skip_if_confluence_below: int = 75
    ai_skip_if_rr_below: float = 2.0

    # ------------------------------------------------------------------ #
    # Telegram
    # ------------------------------------------------------------------ #
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""
    telegram_enabled: bool = False
    telegram_dedup_window_sec: int = 600
    telegram_throttle_per_min: int = 20

    # ------------------------------------------------------------------ #
    # Scanning pipeline
    # ------------------------------------------------------------------ #
    scan_interval_sec: int = 60
    scan_max_pairs: int = 500
    scan_stage1_top_n: int = 30
    scan_stage2_top_n: int = 5
    scan_timeframes: str = "1h,15m,5m"  # HTF,MTF,LTF
    scan_target_scan_duration_sec: float = 5.0

    # Stage 1 thresholds (fast math filter)
    stage1_min_confluence: int = 50
    stage1_min_atr_pct: float = 0.5  # ATR/price %
    stage1_min_volume_usd: float = 5_000_000

    # Stage 2 thresholds (rule engine)
    stage2_min_confluence: int = 70
    stage2_min_rr: float = 2.0

    # Stage 3 thresholds (AI gate)
    stage3_min_confluence: int = 75
    stage3_min_rr: float = 2.0

    # ------------------------------------------------------------------ #
    # Risk management
    # ------------------------------------------------------------------ #
    risk_account_balance: float = 10_000.0
    risk_max_risk_per_trade_pct: float = 1.0
    risk_intraday_sl_max_pct: float = 3.0
    risk_swing_sl_max_pct: float = 5.0
    risk_min_rr: float = 2.0
    risk_atr_multiplier_sl: float = 1.5
    risk_atr_multiplier_tp: float = 3.0
    risk_max_open_positions: int = 5

    # ------------------------------------------------------------------ #
    # Confluence weights (must sum to 100; validated below)
    # ------------------------------------------------------------------ #
    confluence_weight_market_structure: int = 20
    confluence_weight_trend: int = 18
    confluence_weight_liquidity: int = 15
    confluence_weight_smart_money: int = 15
    confluence_weight_ema: int = 8
    confluence_weight_volume: int = 6
    confluence_weight_pressure: int = 5
    confluence_weight_open_interest: int = 4
    confluence_weight_funding: int = 3
    confluence_weight_vwap: int = 2
    confluence_weight_atr: int = 1
    confluence_weight_adx: int = 1
    confluence_weight_bollinger: int = 1
    confluence_weight_support_resistance: int = 1
    confluence_weight_rsi: int = 0  # supporting only

    # ------------------------------------------------------------------ #
    # Retention
    # ------------------------------------------------------------------ #
    retention_market_data_days: int = 30
    retention_signals_days: int = 365
    retention_ai_decisions_days: int = 90
    retention_logs_days: int = 30
    retention_metrics_days: int = 30

    # ------------------------------------------------------------------ #
    # API server
    # ------------------------------------------------------------------ #
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    api_workers: int = 1

    # ------------------------------------------------------------------ #
    # Validators
    # ------------------------------------------------------------------ #
    @field_validator("scan_timeframes")
    @classmethod
    def _validate_timeframes(cls, v: str) -> str:
        tfs = [t.strip() for t in v.split(",") if t.strip()]
        if len(tfs) != 3:
            raise ValueError("scan_timeframes must contain exactly 3 comma-separated values (HTF,MTF,LTF)")
        return ",".join(tfs)

    # ------------------------------------------------------------------ #
    # Convenience derived properties
    # ------------------------------------------------------------------ #
    @property
    def timeframes(self) -> tuple[str, str, str]:
        htf, mtf, ltf = self.scan_timeframes.split(",")
        return htf, mtf, ltf

    @property
    def binance_rest_base_url(self) -> str:
        if self.binance_rest_url:
            return self.binance_rest_url
        return (
            "https://testnet.binancefuture.com/fapi"
            if self.binance_testnet
            else "https://fapi.binance.com/fapi"
        )

    @property
    def binance_ws_base_url(self) -> str:
        if self.binance_ws_url:
            return self.binance_ws_url
        return (
            "wss://stream.binancefuture.com"
            if self.binance_testnet
            else "wss://fstream.binance.com"
        )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def ai_provider_list(self) -> list[str]:
        return [p.strip() for p in self.ai_provider_order.split(",") if p.strip()]

    def confluence_weights_total(self) -> int:
        """Sum of all confluence weights — must equal 100 for correct scoring."""
        return (
            self.confluence_weight_market_structure
            + self.confluence_weight_trend
            + self.confluence_weight_liquidity
            + self.confluence_weight_smart_money
            + self.confluence_weight_ema
            + self.confluence_weight_volume
            + self.confluence_weight_pressure
            + self.confluence_weight_open_interest
            + self.confluence_weight_funding
            + self.confluence_weight_vwap
            + self.confluence_weight_atr
            + self.confluence_weight_adx
            + self.confluence_weight_bollinger
            + self.confluence_weight_support_resistance
            + self.confluence_weight_rsi
        )


@lru_cache(maxsize=1)
def _build_settings() -> Settings:
    """Construct the singleton Settings instance.

    Wrapped in lru_cache so tests can clear it via ``_build_settings.cache_clear()``
    after monkeypatching env vars.
    """
    s = Settings()
    # Warn (don't fail) if weights don't sum to 100 — easier for ops to tweak.
    total = s.confluence_weights_total()
    if total != 100:
        import warnings

        warnings.warn(
            f"Confluence weights sum to {total}, not 100. Scores will be off.",
            stacklevel=2,
        )
    return s


# Module-level singleton. Import this everywhere; never mutate.
settings: Settings = _build_settings()


__all__ = ["Settings", "settings"]
