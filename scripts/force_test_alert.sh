#!/bin/bash
# ============================================================
# Force Test Alert — Send a fake signal to verify alert format
# Run this on EC2 (or locally) after platform is installed
# ============================================================

set -e

# Find project directory (try common locations)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Activate venv if exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -n "$VIRTUAL_ENV" ]; then
    : # already in a venv
else
    echo "⚠️  No venv found — using system Python"
fi

echo "🚀 Sending force test alert with new institutional format..."
echo ""

python3 << 'PYEOF'
import asyncio
import os
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path.cwd()))

# Clear stale env
os.environ.pop("DATABASE_URL", None)


async def main():
    from datetime import datetime, timezone

    # Build a realistic mock signal (BTCUSDT SELL with strong bearish confluence)
    from app.signal.engine import Signal, SignalDirection, SignalType
    from app.confluence.engine import ConfluenceResult, ConfluenceComponent
    from app.risk.engine import RiskResult, TradeStyle
    from app.smart_money.engine import SmartMoneyResult
    from app.structure.market_structure import MarketStructureResult, StructureEvent, TrendBias
    from app.structure.trend import MultiTimeframeTrend, TimeframeTrend
    from app.ai.validation_engine import AIValidationResult
    from app.notifier.telegram import TelegramNotifier

    # Trend (strong bearish, aligned)
    htf = TimeframeTrend("1h", TrendBias.BEARISH, 0.85, -0.80, 38.5, "strong")
    mtf = TimeframeTrend("15m", TrendBias.BEARISH, 0.78, -0.70, 28.2, "moderate")
    ltf = TimeframeTrend("5m", TrendBias.BEARISH, 0.72, -0.60, 22.1, "moderate")
    trend = MultiTimeframeTrend(htf, mtf, ltf, overall_bias=TrendBias.BEARISH, aligned=True, score=82)

    # Market structure (BOS down = bearish continuation)
    ms = MarketStructureResult(
        bias=TrendBias.BEARISH, event=StructureEvent.BOS_BEAR,
        hh_count=0, hl_count=1, lh_count=2, ll_count=3,
        strength=0.82, broken_level=64250.0,
    )

    # Smart money (strong institutional selling)
    sm = SmartMoneyResult(
        institutional_buying=0.15, institutional_selling=0.78,
        net_flow=-0.63, score=-63.0,
        summary="Strong institutional selling (score -63, signals: taker_sell_dominance_0.78, bearish_ob_mitigated)",
        signals=["taker_sell_dominance_0.78", "bearish_ob_mitigated", "sell_side_sweep_recovery"],
    )

    # Confluence (high score = premium setup)
    components = [
        ConfluenceComponent("market_structure", 20, -0.82, -16.4),
        ConfluenceComponent("trend", 18, -0.80, -14.4),
        ConfluenceComponent("liquidity", 15, -0.60, -9.0),
        ConfluenceComponent("smart_money", 15, -0.63, -9.45),
        ConfluenceComponent("ema", 8, -0.85, -6.8),
        ConfluenceComponent("volume", 6, 0.50, 3.0),
        ConfluenceComponent("pressure", 5, -0.65, -3.25),
        ConfluenceComponent("open_interest", 4, 0.55, 2.2),
        ConfluenceComponent("funding", 3, 0.40, 1.2),
        ConfluenceComponent("vwap", 2, -0.55, -1.1),
        ConfluenceComponent("atr", 1, 0.35, 0.35),
        ConfluenceComponent("adx", 1, -0.45, -0.45),
        ConfluenceComponent("bollinger", 1, 0.35, 0.35),
        ConfluenceComponent("support_resistance", 1, 0.50, 0.5),
        ConfluenceComponent("rsi", 0, -0.30, 0.0),
    ]
    confluence = ConfluenceResult(
        score=82, direction="BEARISH",
        components=components,
        dominant_components=["market_structure", "trend", "liquidity"],
    )

    # Risk (BTC at $64,000, 2% SL, 4% TP, RR 1:2)
    risk = RiskResult(
        valid=True, direction="SELL", entry=64150.0,
        stop_loss=65430.0, take_profit=61590.0,
        risk_pct=2.0, reward_pct=4.0, risk_reward=2.0,
        position_size=0.0078, position_value=500.0,
        risk_amount=100.0, reward_amount=200.0,
        trade_style=TradeStyle.SWING,
    )

    # AI result (approved SELL with high confidence)
    ai = AIValidationResult(
        setup_id="TEST-FORCE-BTCUSDT-SELL",
        symbol="BTCUSDT", direction="SELL",
        ai_decision="SELL", confidence=0.88, probability=0.82,
        trade_quality="A", risk_level="MEDIUM",
        reasoning=(
            "Strong bearish confluence with BOS confirmation and aligned multi-timeframe trend. "
            "Smart money shows heavy institutional selling with taker sell dominance at 78%. "
            "Liquidity sweep below 64000 confirms downside continuation toward TP1 at 62810."
        ),
        provider="openrouter", model="google/gemma-4-26b-a4b-it:free",
        latency_ms=8500, cached=False, safety_overrides=[],
        stored_decision_id=None,
    )

    # Build signal
    signal = Signal(
        id="BTCUSDT-D-FORCE-TEST",
        signal_type=SignalType.TYPE_D,
        direction=SignalDirection.SELL,
        symbol="BTCUSDT",
        entry=64150.0, stop_loss=65430.0, take_profit=61590.0,
        risk_reward=2.0, confidence=0.88,
        confluence_score=82,
        trend=trend, market_structure=ms, smart_money=sm,
        confluence=confluence, risk=risk, ai=ai,
        created_at=datetime.now(timezone.utc),
        metadata={
            "trend_aligned": True,
            "market_structure_event": "BOS_BEAR",
            "smart_money_signals": sm.signals,
            "indicators": {
                "ema": {"score": -0.85, "emas": [-0.85, -0.80, -0.70, -0.50]},
                "vwap_position": -0.55,
                "atr_pct": 1.85,
                "rsi": {"value": 38.45, "slope": -5.5, "momentum": -2.0, "acceleration": 0.5},
                "adx": {"adx": 38.5, "plus_di": 18.0, "minus_di": 42.0, "trend_strength": "strong"},
                "macd": {"macd": -125.20, "signal": -85.40, "histogram": -39.80},
                "bollinger": {"upper": 65800, "middle": 64200, "lower": 62600, "width": 0.049, "percent_b": 0.18},
                "support_resistance": {"nearest_support": 62000, "nearest_resistance": 65800},
                "volume_spike_ratio": 2.35,
                "last_price": 64150.0,
            },
            "pressure": {"buy_pct": 0.22, "net_score": -0.56, "summary": "Strong sell pressure"},
            "liquidity_summary": "4 pools, 2 sweeps, 3 unfilled FVGs, 2 OBs",
            "funding_rate": 0.00012,
            "open_interest": 48523000000.0,
            "price_change_pct_24h": -2.85,
        },
    )

    # Send directly (bypass pipeline filter since this is a forced test)
    notifier = TelegramNotifier()
    print("📱 Sending forced test alert to Telegram...")

    # Format and preview
    msg = notifier._format_message(signal)
    print("\n" + "=" * 60)
    print("FORMATTED MESSAGE PREVIEW:")
    print("=" * 60)
    print(msg)
    print("=" * 60 + "\n")

    # Send directly via send_text (bypass send_signal's filter)
    sent = await notifier.send_text(msg)
    if sent:
        print("✅ TEST ALERT SENT! Check your Telegram chat.")
        print("")
        print("If you received this alert, the format is working correctly.")
        print("Real alerts will arrive when market conditions meet strict criteria:")
        print("  - Confluence ≥ 75/100")
        print("  - Pre-AI validation PROCEED")
        print("  - AI approves with BUY/SELL")
        print("  - HTF trend aligned")
        print("  - Smart money confirms")
    else:
        print("❌ Failed to send. Check Telegram bot token + chat ID in .env")

    await notifier.aclose()


asyncio.run(main())
PYEOF
