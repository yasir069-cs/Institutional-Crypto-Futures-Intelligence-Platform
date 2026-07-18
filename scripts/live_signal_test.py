"""Live test: full platform with OpenRouter AI + Telegram alert for BTCUSDT."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Clear stale env
for k in ("DATABASE_URL",):
    os.environ.pop(k, None)


async def test_openrouter_via_provider_layer() -> bool:
    """Test OpenRouter via the platform's LLMProviderLayer (with caching)."""
    print("\n" + "=" * 60)
    print("TEST 1: OpenRouter via LLMProviderLayer")
    print("=" * 60)
    try:
        from app.ai.provider_layer import LLMProviderLayer

        layer = LLMProviderLayer()
        healthy = layer.healthy_providers()
        print(f"   ✓ Providers registered: {list(layer._providers.keys())}")
        print(f"   ✓ Healthy providers: {healthy}")

        if "openrouter" not in healthy:
            print("   ✗ OpenRouter not in healthy providers")
            return False

        messages = [
            {"role": "system", "content": (
                "You are a senior institutional crypto futures trader. "
                "Respond ONLY with valid JSON. Schema: "
                '{"decision":"BUY|SELL|WATCHLIST|HOLD|REJECT","confidence":0.0-1.0,'
                '"probability":0.0-1.0,"trade_quality":"A|B|C",'
                '"risk_level":"LOW|MEDIUM|HIGH","reasoning":"one paragraph"}'
            )},
            {"role": "user", "content": (
                "VALIDATE: BTCUSDT BUY at 95000, confluence 82/100, trend BULLISH aligned, "
                "RR 1:2.5, smart money flow +0.5, BOS_BULL event, bullish OB mitigated. "
                "Return JSON only."
            )},
        ]
        snapshot = {
            "symbol": "BTCUSDT", "direction": "BUY", "price": 95000.0,
            "confluence_score": 82, "trend": {"overall_bias": "BULLISH"},
            "market_structure": {"bias": "BULLISH"}, "smart_money": {"net_flow": 0.5},
        }

        print("   → Sending to OpenRouter (Gemma 4 26B free)...")
        start = time.time()
        response = await layer.validate("TEST-1", snapshot, messages)
        elapsed = (time.time() - start) * 1000

        print(f"   ✓ Decision: {response.decision}")
        print(f"   ✓ Confidence: {response.confidence:.2f}")
        print(f"   ✓ Probability: {response.probability:.2f}")
        print(f"   ✓ Trade Quality: {response.trade_quality}")
        print(f"   ✓ Risk Level: {response.risk_level}")
        print(f"   ✓ Provider latency: {response.latency_ms}ms (total: {elapsed:.0f}ms)")
        print(f"   ✓ Reasoning: {response.reasoning[:200]}")

        await layer.aclose()
        return True
    except Exception as exc:
        print(f"   ✗ FAILED: {exc}")
        import traceback
        traceback.print_exc()
        return False


async def test_telegram_via_notifier() -> bool:
    """Test Telegram via the platform's TelegramNotifier."""
    print("\n" + "=" * 60)
    print("TEST 2: Telegram via TelegramNotifier")
    print("=" * 60)
    try:
        from app.notifier.telegram import TelegramNotifier
        notifier = TelegramNotifier()
        ok = await notifier.send_text(
            "<b>🔧 Platform Integration Test</b>\n\n"
            "OpenRouter AI + Telegram are working.\n"
            "Next: real BTCUSDT signal will arrive here shortly."
        )
        await notifier.aclose()
        if ok:
            print("   ✓ Test alert sent — check your Telegram!")
        return ok
    except Exception as exc:
        print(f"   ✗ FAILED: {exc}")
        return False


async def test_real_btcusdt_signal() -> bool:
    """Fetch real BTCUSDT data from Binance, run full pipeline, send Telegram alert."""
    print("\n" + "=" * 60)
    print("TEST 3: REAL BTCUSDT SIGNAL GENERATION")
    print("=" * 60)
    try:
        from app.exchange.binance_rest import BinanceRestClient
        from app.market.indicator_engine import IndicatorEngine
        from app.structure.market_structure import MarketStructureEngine, TrendBias
        from app.structure.trend import TrendEngine
        from app.liquidity.engine import LiquidityEngine
        from app.smart_money.engine import SmartMoneyEngine
        from app.funding.engine import FundingEngine
        from app.open_interest.engine import OpenInterestEngine
        from app.pressure.engine import PressureEngine
        from app.volume.engine import VolumeEngine
        from app.confluence.engine import ConfluenceEngine
        from app.risk.engine import RiskEngine, TradeStyle
        from app.signal.validation import SignalValidationEngine
        from app.ai.validation_engine import AIValidationEngine, SetupContext
        from app.ai.provider_layer import LLMProviderLayer
        from app.signal.engine import SignalEngine
        from app.notifier.telegram import TelegramNotifier
        from app.exchange.binance_rest import Candle as CandleType

        rest = BinanceRestClient()
        print("   → Fetching BTCUSDT data from Binance Futures...")

        # Fetch 3 timeframes
        htf_candles = await rest.klines("BTCUSDT", "1h", limit=200)
        mtf_candles = await rest.klines("BTCUSDT", "15m", limit=200)
        ltf_candles = await rest.klines("BTCUSDT", "5m", limit=200)
        ticker = await rest.ticker_24h("BTCUSDT")
        print(f"   ✓ BTCUSDT = ${ticker.last_price:,.2f}  (24h: {ticker.price_change_pct:+.2f}%)")
        print(f"   ✓ HTF candles: {len(htf_candles)}, MTF: {len(mtf_candles)}, LTF: {len(ltf_candles)}")

        # Fetch OI and funding
        try:
            oi_history = await rest.open_interest_history("BTCUSDT", period="15m", limit=30)
            print(f"   ✓ OI history: {len(oi_history)} snapshots")
        except Exception:
            oi_history = []
        try:
            funding_history = await rest.funding_rate_history("BTCUSDT", limit=30)
            print(f"   ✓ Funding history: {len(funding_history)} snapshots")
        except Exception:
            funding_history = []

        # Run all engines
        print("\n   → Running analysis engines...")
        structure = MarketStructureEngine()
        trend_engine = TrendEngine(structure)
        liquidity = LiquidityEngine()
        sm_engine = SmartMoneyEngine(liquidity)
        oi_engine = OpenInterestEngine()
        funding_engine = FundingEngine()
        volume_engine = VolumeEngine()
        pressure_engine = PressureEngine()
        confluence_engine = ConfluenceEngine()
        risk_engine = RiskEngine()
        validator = SignalValidationEngine()
        signal_engine = SignalEngine()
        providers = LLMProviderLayer()
        ai_engine = AIValidationEngine(providers=providers)
        notifier = TelegramNotifier()

        ms = structure.analyze(htf_candles)
        print(f"   ✓ Market structure: {ms.bias.value} (strength {ms.strength:.2f}, event {ms.event.value})")

        trend = trend_engine.analyze(htf_candles, mtf_candles, ltf_candles, "1h", "15m", "5m")
        print(f"   ✓ Trend: HTF={trend.htf.bias.value} MTF={trend.mtf.bias.value} LTF={trend.ltf.bias.value}, score {trend.score}/100")

        liq = liquidity.analyze(htf_candles)
        print(f"   ✓ Liquidity: {len(liq.pools)} pools, {len(liq.recent_sweeps)} sweeps, {len(liq.fvgs)} FVGs, {len(liq.order_blocks)} OBs")

        # Live state for smart money / pressure (we'll use HTF candles as proxy)
        from app.market.data_engine import SymbolState
        state = SymbolState(symbol="BTCUSDT")
        state.bid_price = ticker.last_price * 0.9999
        state.bid_qty = 1.5
        state.ask_price = ticker.last_price * 1.0001
        state.ask_qty = 1.2

        sm = sm_engine.analyze(htf_candles, state, liq)
        print(f"   ✓ Smart money: net_flow={sm.net_flow:+.2f} ({sm.summary[:80]})")

        pressure = pressure_engine.analyze(candles=ltf_candles, state=state)
        print(f"   ✓ Pressure: net={pressure.net_score:+.2f} ({pressure.summary[:80]})")

        volume = volume_engine.analyze(htf_candles)
        print(f"   ✓ Volume: spike={volume.spike_ratio:.2f}x, trend={volume.trend}, climax={volume.climax}")

        ind_engine = IndicatorEngine()
        ind = ind_engine.compute_all("BTCUSDT", "1h", htf_candles)
        print(f"   ✓ Indicators: ATR%={ind['atr_pct']:.2f}, RSI={ind['rsi']['value']:.1f}, ADX={ind['adx']['adx']:.1f}")

        oi_result = oi_engine.analyze(
            oi_history, price_now=ticker.last_price,
            price_1h_ago=htf_candles[-5].close if len(htf_candles) >= 5 else ticker.last_price,
        )
        print(f"   ✓ OI: delta_1h={oi_result.delta_pct_1h:+.2f}%, divergence={oi_result.divergence}")

        funding_result = funding_engine.analyze(funding_history)
        print(f"   ✓ Funding: {funding_result.regime}, current={funding_result.current_rate*100:.4f}%/8h")

        # Confluence
        confluence = confluence_engine.compute(
            trend=trend, market_structure=ms, liquidity=liq, smart_money=sm,
            pressure=pressure, oi=oi_result, funding=funding_result,
            volume=volume, indicators=ind,
        )
        print(f"   ✓ Confluence: {confluence.score}/100 ({confluence.direction})")
        print(f"   ✓ Dominant components: {confluence.dominant_components}")

        # Decide direction
        if trend.overall_bias == TrendBias.BULLISH:
            direction = "BUY"
        elif trend.overall_bias == TrendBias.BEARISH:
            direction = "SELL"
        else:
            # fallback: smart money direction
            direction = "BUY" if sm.net_flow > 0 else "SELL"
        print(f"\n   → Direction: {direction}")

        # Risk
        risk = risk_engine.compute(
            direction=direction, entry=ticker.last_price,
            atr=ind["atr"], trade_style=TradeStyle.INTRADAY,
        )
        print(f"   ✓ Risk: entry={risk.entry:.2f}, SL={risk.stop_loss:.2f} ({risk.risk_pct:.2f}%), TP={risk.take_profit:.2f} ({risk.reward_pct:.2f}%), RR=1:{risk.risk_reward:.2f}")
        print(f"   ✓ Risk valid: {risk.valid} ({risk.rejection_reason or 'OK'})")

        # Pre-AI validation
        validation = validator.validate(
            confluence=confluence, trend=trend, smart_money=sm,
            risk=risk, direction=direction,
        )
        print(f"   ✓ Pre-AI validation: {validation.verdict.value} (can_send_to_ai={validation.can_send_to_ai})")
        print(f"     Reasons: {validation.reasons}")

        # AI validation
        ai_result = None
        if validation.can_send_to_ai:
            print("\n   → Sending to AI (OpenRouter Gemma 4 26B free)...")
            ctx = SetupContext(
                symbol="BTCUSDT", direction=direction, price=ticker.last_price,
                trend=trend, market_structure=ms, smart_money=sm,
                confluence=confluence, risk=risk,
                liquidity_summary=f"{len(liq.pools)} pools, {len(liq.recent_sweeps)} sweeps, {sum(1 for f in liq.fvgs if not f.filled)} unfilled FVGs",
                pressure_summary=pressure.summary,
                funding_summary=funding_result.summary,
                oi_summary=oi_result.summary,
                volume_summary=volume.summary,
                indicators_summary=ind,
            )
            ai_result = await ai_engine.validate(ctx)
            print(f"   ✓ AI decision: {ai_result.ai_decision} (confidence {ai_result.confidence:.2f})")
            print(f"   ✓ AI provider: {ai_result.provider} (latency {ai_result.latency_ms}ms)")
            print(f"   ✓ AI reasoning: {ai_result.reasoning[:300]}")
            if ai_result.safety_overrides:
                print(f"   ⚠ Safety overrides applied: {ai_result.safety_overrides}")

        # Generate signal
        signal = signal_engine.generate(
            symbol="BTCUSDT", direction=direction, trend=trend,
            market_structure=ms, smart_money=sm, confluence=confluence,
            risk=risk, ai=ai_result,
        )
        # Override direction with AI decision if applicable
        if ai_result and ai_result.ai_decision in ("BUY", "SELL", "WATCHLIST", "HOLD", "REJECT"):
            from app.signal.engine import SignalDirection
            try:
                signal.direction = SignalDirection(ai_result.ai_decision)
            except ValueError:
                pass

        signal.metadata["liquidity_summary"] = f"{len(liq.pools)} pools, {len(liq.recent_sweeps)} sweeps"

        print(f"\n   📊 SIGNAL GENERATED:")
        print(f"      Type: {signal.signal_type.value}")
        print(f"      Direction: {signal.direction.value}")
        print(f"      Entry: ${signal.entry:,.2f}")
        print(f"      Stop Loss: ${signal.stop_loss:,.2f}")
        print(f"      Take Profit: ${signal.take_profit:,.2f}")
        print(f"      Risk/Reward: 1:{signal.risk_reward:.2f}")
        print(f"      Confidence: {signal.confidence*100:.0f}%")
        print(f"      Confluence: {signal.confluence_score}/100")

        # Send to Telegram
        print("\n   → Sending signal to Telegram...")
        sent = await notifier.send_signal(signal)
        if sent:
            print("   ✅ SIGNAL ALERT SENT TO TELEGRAM — check your chat!")
        else:
            print("   ⚠ Telegram send skipped (not actionable or dedup or throttle)")

        await notifier.aclose()
        await providers.aclose()
        await rest.aclose()
        return True

    except Exception as exc:
        print(f"   ✗ FAILED: {exc}")
        import traceback
        traceback.print_exc()
        return False


async def main() -> int:
    print("=" * 60)
    print("LIVE PLATFORM TEST — OpenRouter + Telegram + Real BTCUSDT")
    print("=" * 60)

    from app.config import settings
    print(f"AI providers: {settings.ai_provider_list}")
    print(f"OpenRouter model: {settings.openrouter_model}")
    print(f"Telegram: enabled={settings.telegram_enabled}, chat_id={settings.telegram_chat_id}")

    results = {}
    results["openrouter"] = await test_openrouter_via_provider_layer()
    results["telegram"] = await test_telegram_via_notifier()
    results["btcusdt_signal"] = await test_real_btcusdt_signal()

    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    for name, ok in results.items():
        symbol = "✅" if ok else "❌"
        print(f"  {symbol} {name}")

    all_ok = all(results.values())
    print(f"\n{'🎉 ALL TESTS PASSED — check your Telegram!' if all_ok else '⚠ Some tests failed'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
