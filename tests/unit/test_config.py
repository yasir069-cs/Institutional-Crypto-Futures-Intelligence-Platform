"""Unit tests for config and environment management."""

from __future__ import annotations

from app.config import settings
from app.config.environment import mask, validate_runtime


def test_settings_loads_defaults():
    assert settings.environment in ("development", "staging", "production")
    assert settings.scan_interval_sec >= 5
    assert settings.scan_stage1_top_n > 0


def test_timeframes_tuple():
    htf, mtf, ltf = settings.timeframes
    assert htf == "1h"
    assert mtf == "15m"
    assert ltf == "5m"


def test_provider_order_list():
    providers = settings.ai_provider_list
    assert isinstance(providers, list)
    assert len(providers) >= 1


def test_mask_function():
    assert mask("abcdef") == "**cdef"
    assert mask("ab") == "**"
    assert mask("") == ""
    assert mask("abcd") == "****"


def test_validate_runtime_returns_list():
    warnings = validate_runtime()
    assert isinstance(warnings, list)


def test_binance_url_derivation():
    # Testnet URL
    settings.binance_testnet = True
    assert "testnet" in settings.binance_rest_base_url
    assert "binancefuture" in settings.binance_ws_base_url or "fstream" in settings.binance_ws_base_url


def test_confluence_weights_total():
    total = settings.confluence_weights_total()
    # Should be 100 (or close if user overrode)
    assert isinstance(total, int)
    assert total > 0
