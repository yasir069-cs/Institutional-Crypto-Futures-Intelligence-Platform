"""Unit tests for AI provider layer (mock provider + cache)."""

from __future__ import annotations

import asyncio

import pytest

from app.ai.provider_layer import LLMProviderLayer, MockProvider


@pytest.mark.asyncio
async def test_mock_provider_returns_hold_or_buy():
    provider = MockProvider()
    # Low confluence → HOLD
    response = await provider.complete([
        {"role": "user", "content": "Direction BUY, confluence 60. Please validate."}
    ])
    assert response.decision in ("HOLD", "WATCHLIST")
    assert 0 <= response.confidence <= 1.0

    # High confluence → BUY
    response = await provider.complete([
        {"role": "user", "content": "Direction BUY, confluence 85. Please validate."}
    ])
    assert response.decision == "BUY"


@pytest.mark.asyncio
async def test_provider_layer_uses_mock():
    """Layer should route to mock provider when configured."""
    layer = LLMProviderLayer()
    healthy = layer.healthy_providers()
    assert "mock" in healthy

    response = await layer.validate(
        setup_id="TEST-1",
        market_context={
            "symbol": "BTCUSDT",
            "direction": "BUY",
            "price": 100.0,
            "confluence_score": 85,
            "trend": {"overall_bias": "BULLISH"},
            "market_structure": {"bias": "BULLISH"},
            "smart_money": {"net_flow": 0.5},
        },
        messages=[
            {"role": "system", "content": "You are a trader."},
            {"role": "user", "content": "Direction BUY, confluence 85. Validate."},
        ],
    )
    assert response.provider == "mock"
    assert response.decision in ("BUY", "HOLD", "WATCHLIST", "SELL", "REJECT")


@pytest.mark.asyncio
async def test_provider_layer_caches_response():
    """Second identical call within TTL should return cached response."""
    layer = LLMProviderLayer()
    snapshot = {
        "symbol": "BTCUSDT",
        "direction": "BUY",
        "price": 100.0,
        "confluence_score": 85,
        "trend": {"overall_bias": "BULLISH"},
        "market_structure": {"bias": "BULLISH"},
        "smart_money": {"net_flow": 0.5},
    }
    messages = [
        {"role": "system", "content": "test"},
        {"role": "user", "content": "Direction BUY, confluence 85. Validate."},
    ]

    r1 = await layer.validate("SETUP-1", snapshot, messages)
    assert not r1.cached

    # Same call → cache hit
    r2 = await layer.validate("SETUP-1", snapshot, messages)
    assert r2.cached
    assert r2.decision == r1.decision

    await layer.aclose()


@pytest.mark.asyncio
async def test_provider_layer_invalidates_on_price_change():
    """Cache should be invalidated when price moves >0.5%."""
    layer = LLMProviderLayer()
    snapshot = {
        "symbol": "BTCUSDT",
        "direction": "BUY",
        "price": 100.0,
        "confluence_score": 85,
        "trend": {"overall_bias": "BULLISH"},
        "market_structure": {"bias": "BULLISH"},
        "smart_money": {"net_flow": 0.5},
    }
    messages = [
        {"role": "system", "content": "test"},
        {"role": "user", "content": "Direction BUY, confluence 85. Validate."},
    ]
    r1 = await layer.validate("SETUP-1", snapshot, messages)
    # Move price by 1% — should invalidate
    snapshot2 = dict(snapshot)
    snapshot2["price"] = 101.0
    r2 = await layer.validate("SETUP-1", snapshot2, messages)
    assert not r2.cached
    await layer.aclose()
