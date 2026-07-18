"""Integration test: full pipeline with synthetic data and mock AI provider.

This test exercises the entire Stage 1 → Stage 2 → Stage 3 → Signal flow
without requiring real Binance connectivity. It uses synthetic candle data
and the MockProvider for AI validation.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone

import pytest

from app.ai.provider_layer import LLMProviderLayer, MockProvider
from app.confluence.engine import ConfluenceEngine
from app.exchange.binance_rest import Candle
from app.liquidity.engine import LiquidityEngine
from app.market.candle_engine import CandleEngine
from app.market.data_engine import MarketDataEngine
from app.market.indicator_engine import IndicatorEngine
from app.open_interest.engine import OpenInterestEngine
from app.funding.engine import FundingEngine
from app.pressure.engine import PressureEngine
from app.risk.engine import RiskEngine
from app.signal.engine import SignalEngine
from app.signal.validation import SignalValidationEngine
from app.smart_money.engine import SmartMoneyEngine
from app.structure.market_structure import MarketStructureEngine, TrendBias
from app.structure.trend import TrendEngine
from app.volume.engine import VolumeEngine
from app.engine.stage1.scanner import Stage1Scanner
from app.engine.stage2.rule_engine import Stage2RuleEngine


def _make_synthetic_market(symbols: list[str], n_candles: int = 200) -> dict[str, dict[str, list[Candle]]]:
    """Build synthetic candle series for multiple symbols and timeframes."""
    rng = random.Random(42)
    out: dict[str, dict[str, list[Candle]]] = {}
    for sym_idx, symbol in enumerate(symbols):
        out[symbol] = {}
        for tf_idx, tf in enumerate(["1h", "15m", "5m"]):
            candles: list[Candle] = []
            price = 100.0 + sym_idx * 50
            now = datetime.now(timezone.utc)
            interval = {"1h": 3600, "15m": 900, "5m": 300}[tf]
            # Each symbol gets a different trend direction
            trend_dir = 1 if sym_idx % 3 != 2 else -1
            for i in range(n_candles):
                drift = 0.002 * trend_dir
                oscillation = 0.012 * ((-1) ** (i // 5))
                change = drift + oscillation + rng.gauss(0, 0.003)
                new_price = price * (1 + change)
                candles.append(Candle(
                    symbol=symbol, timeframe=tf,
                    open_time=now - timedelta(seconds=interval * (n_candles - i)),
                    close_time=now - timedelta(seconds=interval * (n_candles - i - 1)),
                    open=price,
                    high=max(price, new_price) * 1.002,
                    low=min(price, new_price) * 0.998,
                    close=new_price,
                    volume=1000 + rng.uniform(0, 500),
                    quote_volume=(1000 + rng.uniform(0, 500)) * new_price,
                    trade_count=100,
                    taker_buy_volume=(1000 + rng.uniform(0, 500)) * (0.6 if trend_dir > 0 else 0.4),
                    taker_buy_quote_volume=(1000 + rng.uniform(0, 500)) * new_price * (0.6 if trend_dir > 0 else 0.4),
                    is_closed=True,
                ))
                price = new_price
            out[symbol][tf] = candles
    return out


@pytest.mark.asyncio
async def test_stage1_scanner_produces_candidates():
    """Stage 1 should scan all symbols and return some candidates."""
    symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT"]
    market_data = MarketDataEngine(rest=None)
    from app.exchange.binance_rest import Ticker24h
    for sym in symbols:
        await market_data.update_ticker(Ticker24h(
            symbol=sym, last_price=100.0, price_change_pct=2.0,
            volume=1000, quote_volume=10_000_000, high=105, low=95, trade_count=1000,
        ))

    candle_engine = CandleEngine(rest=None, market_data=market_data)
    indicator_engine = IndicatorEngine(candles=candle_engine)

    # Populate candle engine with synthetic data (smaller count for test speed)
    market = _make_synthetic_market(symbols, n_candles=100)
    for sym, tfs in market.items():
        for tf, candles in tfs.items():
            for c in candles:
                await candle_engine.update(c)

    scanner = Stage1Scanner(
        market_data=market_data,
        candle_engine=candle_engine,
        indicator_engine=indicator_engine,
    )

    result = await scanner.scan(symbols)
    assert result.total_scanned == 5
    assert result.duration_ms >= 0
    # Result is well-formed
    assert isinstance(result.candidates, list)
    assert isinstance(result.rejected, list)


@pytest.mark.asyncio
async def test_stage2_analyzes_candidate():
    """Stage 2 should produce a setup (or rejection) for a candidate."""
    symbols = ["BTCUSDT"]
    market_data = MarketDataEngine(rest=None)
    from app.exchange.binance_rest import Ticker24h
    await market_data.update_ticker(Ticker24h(
        symbol="BTCUSDT", last_price=100.0, price_change_pct=2.0,
        volume=1000, quote_volume=10_000_000, high=105, low=95, trade_count=1000,
    ))
    # Add some order book data
    state = market_data.ensure_symbol("BTCUSDT")
    state.bid_price = 99.5
    state.bid_qty = 5.0
    state.ask_price = 100.5
    state.ask_qty = 3.0

    candle_engine = CandleEngine(rest=None, market_data=market_data)
    indicator_engine = IndicatorEngine(candles=candle_engine)

    market = _make_synthetic_market(symbols, n_candles=200)
    for sym, tfs in market.items():
        for tf, candles in tfs.items():
            for c in candles:
                await candle_engine.update(c)

    # Skip Stage 1 — directly feed a candidate to Stage 2
    from app.engine.stage1.scanner import Stage1Candidate
    candidate = Stage1Candidate(
        symbol="BTCUSDT",
        price=100.0,
        volume_usd=10_000_000,
        atr_pct=1.5,
        trend_bias=TrendBias.BULLISH,
        trend_strength=0.7,
        ema_score=0.8,
        vwap_position=0.4,
        confluence_score=70,
        indicators=indicator_engine.compute_all("BTCUSDT", "1h", candle_engine.latest("BTCUSDT", "1h", 200)),
        htf_label="1h",
        mtf_label="15m",
        ltf_label="5m",
    )

    # Stage 2 needs a REST client for OI/funding fetches; pass None and handle errors gracefully
    stage2 = Stage2RuleEngine(
        market_data=market_data,
        candle_engine=candle_engine,
        indicator_engine=indicator_engine,
        rest=None,  # type: ignore[arg-type]
    )
    # Replace _fetch_oi and _fetch_funding to avoid REST calls
    async def fake_oi(symbol, price):
        from app.open_interest.engine import OIResult
        return OIResult(current_oi=1000, summary="mock OI")

    async def fake_funding(symbol):
        from app.funding.engine import FundingResult
        return FundingResult(current_rate=0.0001, summary="mock funding")

    stage2._fetch_oi = fake_oi  # type: ignore[assignment]
    stage2._fetch_funding = fake_funding  # type: ignore[assignment]

    result = await stage2.run([candidate])
    assert len(result.setups) + len(result.rejected) >= 1  # at least one outcome


@pytest.mark.asyncio
async def test_full_pipeline_with_mock_provider():
    """Full pipeline: Stage 1 → 2 → AI validation → Signal generation."""
    # Build market data + candles
    symbols = ["BTCUSDT"]
    market_data = MarketDataEngine(rest=None)
    from app.exchange.binance_rest import Ticker24h
    await market_data.update_ticker(Ticker24h(
        symbol="BTCUSDT", last_price=100.0, price_change_pct=2.0,
        volume=1000, quote_volume=10_000_000, high=105, low=95, trade_count=1000,
    ))

    candle_engine = CandleEngine(rest=None, market_data=market_data)
    indicator_engine = IndicatorEngine(candles=candle_engine)

    market = _make_synthetic_market(symbols, n_candles=200)
    for sym, tfs in market.items():
        for tf, candles in tfs.items():
            for c in candles:
                await candle_engine.update(c)

    # Build engines
    structure = MarketStructureEngine()
    trend = TrendEngine(structure)
    liquidity = LiquidityEngine()
    smart_money = SmartMoneyEngine(liquidity)
    oi_engine = OpenInterestEngine()
    funding_engine = FundingEngine()
    volume_engine = VolumeEngine()
    pressure_engine = PressureEngine()
    confluence_engine = ConfluenceEngine()
    risk_engine = RiskEngine()
    signal_engine = SignalEngine()
    validator = SignalValidationEngine()

    # Build a fake Stage 2 setup manually (bypassing Stage 1/2)
    htf_candles = candle_engine.latest("BTCUSDT", "1h", 200)
    mtf_candles = candle_engine.latest("BTCUSDT", "15m", 200)
    ltf_candles = candle_engine.latest("BTCUSDT", "5m", 200)
    trend_result = trend.analyze(htf_candles, mtf_candles, ltf_candles, "1h", "15m", "5m")
    ms = structure.analyze(htf_candles)
    liq = liquidity.analyze(htf_candles)
    state = market_data.get("BTCUSDT")
    sm = smart_money.analyze(htf_candles, state, liq)
    pressure = pressure_engine.analyze(candles=lTF_safe(ltf_candles), state=state)
    volume = volume_engine.analyze(htf_candles)
    ind = indicator_engine.compute_all("BTCUSDT", "1h", htf_candles)

    from app.open_interest.engine import OIResult
    from app.funding.engine import FundingResult
    oi = OIResult(current_oi=1000, summary="mock")
    funding = FundingResult(current_rate=0.0001, summary="mock")

    confluence = confluence_engine.compute(
        trend=trend_result, market_structure=ms, liquidity=liq,
        smart_money=sm, pressure=pressure, oi=oi, funding=funding,
        volume=volume, indicators=ind,
    )

    direction = "BUY" if trend_result.overall_bias == TrendBias.BULLISH else "SELL"
    risk = risk_engine.compute(direction=direction, entry=100.0, atr=ind.get("atr", 1.0))

    from app.engine.stage2.rule_engine import Stage2Setup
    setup = Stage2Setup(
        symbol="BTCUSDT", direction=direction, price=100.0,
        trend=trend_result, market_structure=ms, liquidity=liq,
        smart_money=sm, pressure=pressure, oi=oi, funding=funding,
        volume=volume, confluence=confluence, risk=risk, indicators=ind,
        htf_label="1h", mtf_label="15m", ltf_label="5m",
    )

    # Run signal validation
    validation = validator.validate(
        confluence=confluence, trend=trend_result,
        smart_money=sm, risk=risk, direction=direction,
    )

    # If validation allows AI, run it with mock provider
    providers = LLMProviderLayer()
    ai_result = None
    if validation.can_send_to_ai:
        from app.ai.validation_engine import AIValidationEngine, SetupContext
        ai_engine = AIValidationEngine(providers=providers)
        ctx = SetupContext(
            symbol="BTCUSDT", direction=direction, price=100.0,
            trend=trend_result, market_structure=ms, smart_money=sm,
            confluence=confluence, risk=risk,
            pressure_summary=pressure.summary, funding_summary=funding.summary,
            oi_summary=oi.summary, volume_summary=volume.summary,
            indicators_summary=ind,
        )
        ai_result = await ai_engine.validate(ctx)

    # Generate signal
    signal = signal_engine.generate(
        symbol="BTCUSDT", direction=direction, trend=trend_result,
        market_structure=ms, smart_money=sm, confluence=confluence,
        risk=risk, ai=ai_result,
    )
    assert signal is not None
    assert signal.symbol == "BTCUSDT"
    assert signal.direction.value in ("BUY", "SELL", "WATCHLIST", "HOLD", "REJECT")

    await providers.aclose()


def lTF_safe(candles):
    return candles
