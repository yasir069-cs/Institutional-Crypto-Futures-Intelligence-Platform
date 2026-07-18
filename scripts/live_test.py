"""Live connectivity test for Binance, Telegram, and Groq."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Clear stale env
for k in ("DATABASE_URL",):
    os.environ.pop(k, None)


async def test_binance() -> bool:
    """Test Binance public market data — no API key needed."""
    print("\n" + "=" * 60)
    print("1. TESTING BINANCE FUTURES (public market data, no API key)")
    print("=" * 60)
    try:
        from app.exchange.binance_rest import BinanceRestClient
        rest = BinanceRestClient()

        # Test 1: ping exchangeInfo
        print("   → Fetching exchangeInfo...")
        symbols = await rest.exchange_info()
        usdt_perp = [s for s in symbols if s.quote_asset == "USDT" and s.contract_type == "PERPETUAL"]
        print(f"   ✓ {len(symbols)} symbols total, {len(usdt_perp)} USDT perpetuals")

        # Test 2: ticker 24h for BTCUSDT
        print("   → Fetching BTCUSDT 24h ticker...")
        btc = await rest.ticker_24h("BTCUSDT")
        print(f"   ✓ BTCUSDT last price: ${btc.last_price:,.2f}")
        print(f"   ✓ 24h volume: {btc.volume:,.0f} BTC ({btc.quote_volume:,.0f} USDT)")

        # Test 3: klines
        print("   → Fetching 100 1h candles for BTCUSDT...")
        candles = await rest.klines("BTCUSDT", "1h", limit=100)
        print(f"   ✓ Got {len(candles)} candles")
        print(f"   ✓ Latest candle: O={candles[-1].open:.2f} H={candles[-1].high:.2f} L={candles[-1].low:.2f} C={candles[-1].close:.2f}")

        await rest.aclose()
        return True
    except Exception as exc:
        print(f"   ✗ FAILED: {exc}")
        return False


async def test_telegram() -> bool:
    """Test Telegram bot can send a message."""
    print("\n" + "=" * 60)
    print("2. TESTING TELEGRAM BOT")
    print("=" * 60)
    try:
        from app.config import settings
        import aiohttp

        token = settings.telegram_bot_token.get_secret_value()
        chat_id = settings.telegram_chat_id

        if not token or not chat_id:
            print(f"   ✗ Missing token ({bool(token)}) or chat_id ({bool(chat_id)})")
            return False

        # First, verify bot exists
        print(f"   → Verifying bot token...")
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.telegram.org/bot{token}/getMe") as resp:
                data = await resp.json()
                if not data.get("ok"):
                    print(f"   ✗ Bot token invalid: {data}")
                    return False
                bot = data["result"]
                print(f"   ✓ Bot verified: @{bot['username']} ({bot['first_name']})")

            # Send a test message
            print(f"   → Sending test message to chat_id={chat_id}...")
            test_msg = (
                "<b>🚀 Platform Test Alert</b>\n\n"
                "This is a test message from your <b>Institutional Crypto Futures Intelligence Platform</b>.\n\n"
                "<i>If you received this, Telegram integration is working correctly.</i>\n\n"
                "<code>Status: ✅ All systems operational</code>"
            )
            async with session.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": test_msg,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            ) as resp:
                data = await resp.json()
                if data.get("ok"):
                    print(f"   ✓ Message sent successfully to chat_id={chat_id}")
                    return True
                else:
                    print(f"   ✗ Send failed: {data}")
                    return False
    except Exception as exc:
        print(f"   ✗ FAILED: {exc}")
        return False


async def test_groq() -> bool:
    """Test Groq API with a simple prompt."""
    print("\n" + "=" * 60)
    print("3. TESTING GROQ API (Llama 3.1 70B via Groq)")
    print("=" * 60)
    try:
        from app.config import settings
        from app.ai.provider_layer import GroqProvider
        import time

        if not settings.groq_api_key.get_secret_value():
            print("   ✗ GROQ_API_KEY not set")
            return False

        print(f"   → Initializing Groq provider (model: {settings.groq_model})...")
        provider = GroqProvider()

        # Test 1: simple prompt
        print("   → Sending test prompt to Groq...")
        start = time.time()
        response = await provider.complete([
            {"role": "system", "content": "You are a JSON-only responder. Output valid JSON, nothing else."},
            {"role": "user", "content": (
                "Validate this hypothetical trade setup as a senior institutional trader. "
                "Symbol: BTCUSDT, Direction: BUY, Confluence: 82/100, Trend: BULLISH (HTF aligned), "
                "Risk/Reward: 1:2.5, Smart Money flow: +0.4 (institutional buying). "
                "Respond with JSON: {decision, confidence (0-1), reasoning (1 sentence)}"
            )},
        ])
        latency = (time.time() - start) * 1000
        print(f"   ✓ Response received in {latency:.0f}ms")
        print(f"   ✓ Provider: {response.provider}, Model: {response.model}")
        print(f"   ✓ Decision: {response.decision}")
        print(f"   ✓ Confidence: {response.confidence:.2f}")
        print(f"   ✓ Reasoning: {response.reasoning[:200]}")
        print(f"   ✓ Latency tracked: {response.latency_ms}ms")

        await provider.aclose()
        return True
    except Exception as exc:
        print(f"   ✗ FAILED: {exc}")
        import traceback
        traceback.print_exc()
        return False


async def test_groq_signal_validation_prompt() -> bool:
    """Test Groq with the actual institutional signal validation prompt."""
    print("\n" + "=" * 60)
    print("4. TESTING GROQ WITH FULL INSTITUTIONAL PROMPT")
    print("=" * 60)
    try:
        from app.ai.provider_layer import GroqProvider, LLMProviderLayer

        # Use the actual provider layer (with caching)
        layer = LLMProviderLayer()
        print(f"   → Healthy providers: {layer.healthy_providers()}")

        # Build a realistic institutional context prompt
        messages = [
            {"role": "system", "content": (
                "You are the senior decision-maker on an institutional crypto futures trading desk. "
                "Validate ONLY the highest-quality setups. Approve exceptional opportunities, reject mediocre trades. "
                "Output JSON: {decision: BUY|SELL|WATCHLIST|HOLD|REJECT, confidence: 0-1, "
                "probability: 0-1, trade_quality: A|B|C, risk_level: LOW|MEDIUM|HIGH, reasoning: string}"
            )},
            {"role": "user", "content": (
                "VALIDATE THIS PREMIUM SETUP\n\n"
                "SYMBOL: BTCUSDT\n"
                "PROPOSED DIRECTION: BUY\n"
                "CURRENT PRICE: 95000\n\n"
                "TREND:\n"
                "  HTF (1h): BULLISH (strength 0.78, ADX 32 strong)\n"
                "  MTF (15m): BULLISH (strength 0.65, ADX 25 moderate)\n"
                "  LTF (5m): BULLISH (strength 0.55, ADX 20 moderate)\n"
                "  Aligned: YES, Score: 78/100\n\n"
                "MARKET STRUCTURE:\n"
                "  Bias: BULLISH (strength 0.75)\n"
                "  Recent event: BOS_BULL (break of structure up)\n"
                "  HH: 3, HL: 2, LH: 1, LL: 0\n\n"
                "SMART MONEY:\n"
                "  Strong institutional buying (score +70.0)\n"
                "  Institutional buying: 0.80, selling: 0.10\n"
                "  Net flow: +0.70\n\n"
                "CONFLUENCE: 82/100, Direction: BULLISH\n"
                "RISK: Entry 95000, SL 93850 (1.21% risk), TP 98000 (3.16% reward)\n"
                "Risk/Reward: 1:2.61\n"
                "Position size: 8.7 USDT notional\n\n"
                "Validate this setup and return JSON."
            )},
        ]

        snapshot = {
            "symbol": "BTCUSDT",
            "direction": "BUY",
            "price": 95000.0,
            "confluence_score": 82,
            "trend": {"overall_bias": "BULLISH"},
            "market_structure": {"bias": "BULLISH"},
            "smart_money": {"net_flow": 0.7},
        }

        print("   → Sending full institutional context to Groq...")
        response = await layer.validate(
            setup_id="TEST-BTCUSDT-BUY",
            market_context=snapshot,
            messages=messages,
        )

        print(f"   ✓ Decision: {response.decision}")
        print(f"   ✓ Confidence: {response.confidence:.2f}")
        print(f"   ✓ Probability: {response.probability:.2f}")
        print(f"   ✓ Trade Quality: {response.trade_quality}")
        print(f"   ✓ Risk Level: {response.risk_level}")
        print(f"   ✓ Latency: {response.latency_ms}ms")
        print(f"   ✓ Cached: {response.cached}")
        print(f"   ✓ Reasoning preview: {response.reasoning[:300]}...")

        # Test cache — second call should be cached
        print("\n   → Calling again to test cache...")
        response2 = await layer.validate(
            setup_id="TEST-BTCUSDT-BUY",
            market_context=snapshot,
            messages=messages,
        )
        print(f"   ✓ Second call cached: {response2.cached}")

        await layer.aclose()
        return True
    except Exception as exc:
        print(f"   ✗ FAILED: {exc}")
        import traceback
        traceback.print_exc()
        return False


async def main() -> int:
    print("=" * 60)
    print("LIVE CONNECTIVITY TEST")
    print("=" * 60)
    print(f"Python: {sys.version.split()[0]}")

    from app.config import settings
    print(f"Environment: {settings.environment}")
    print(f"Binance testnet: {settings.binance_testnet}")
    print(f"AI providers configured: {settings.ai_provider_list}")
    print(f"Telegram enabled: {settings.telegram_enabled}")

    results = {}
    results["binance"] = await test_binance()
    results["telegram"] = await test_telegram()
    results["groq_basic"] = await test_groq()
    results["groq_full_prompt"] = await test_groq_signal_validation_prompt()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for name, ok in results.items():
        symbol = "✅" if ok else "❌"
        print(f"  {symbol} {name}")

    all_ok = all(results.values())
    print(f"\n{'🎉 ALL TESTS PASSED' if all_ok else '⚠️  Some tests failed'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
