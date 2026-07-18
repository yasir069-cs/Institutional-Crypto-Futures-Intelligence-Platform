"""Send a test alert with the new institutional format to verify it looks right."""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Clear stale env
for k in ("DATABASE_URL",):
    os.environ.pop(k, None)


async def main() -> None:
    from datetime import datetime, timezone

    # Build a mock signal with realistic data
    from app.signal.engine import Signal, SignalDirection, SignalType
    from app.signal.validation import SignalVerdict
    from app.confluence.engine import ConfluenceResult, ConfluenceComponent
    from app.risk.engine import RiskResult, TradeStyle
    from app.smart_money.engine import SmartMoneyResult
    from app.structure.market_structure import MarketStructureResult, StructureEvent, TrendBias
    from app.structure.trend import MultiTimeframeTrend, TimeframeTrend
    from app.ai.validation_engine import AIValidationResult
    from app.notifier.telegram import TelegramNotifier

    # Build trend
    htf = TimeframeTrend("1h", TrendBias.BEARISH, 0.78, -0.8, 32.0, "strong")
    mtf = TimeframeTrend("15m", TrendBias.BEARISH, 0.65, -0.6, 25.0, "moderate")
    ltf = TimeframeTrend("5m", TrendBias.BEARISH, 0.55, -0.4, 20.0, "moderate")
    trend = MultiTimeframeTrend(htf, mtf, ltf, overall_bias=TrendBias.BEARISH, aligned=True, score=78)

    # Market structure
    ms = MarketStructureResult(
        bias=TrendBias.BEARISH, event=StructureEvent.BOS_BEAR,
        hh_count=1, hl_count=0, lh_count=2, ll_count=3,
        strength=0.75, broken_level=4.65,
    )

    # Smart money
    sm = SmartMoneyResult(
        institutional_buying=0.20, institutional_selling=0.70,
        net_flow=-0.50, score=-50.0,
        summary="Strong institutional selling (score -50.0, signals: taker_sell_dominance_0.70)",
        signals=["taker_sell_dominance_0.70", "bearish_ob_mitigated"],
    )

    # Confluence
    components = [
        ConfluenceComponent("market_structure", 20, -0.85, -17.0),
        ConfluenceComponent("trend", 18, -0.78, -14.04),
        ConfluenceComponent("liquidity", 15, -0.50, -7.5),
        ConfluenceComponent("smart_money", 15, -0.50, -7.5),
        ConfluenceComponent("ema", 8, -0.80, -6.4),
        ConfluenceComponent("volume", 6, 0.40, 2.4),
        ConfluenceComponent("pressure", 5, -0.60, -3.0),
        ConfluenceComponent("open_interest", 4, 0.50, 2.0),
        ConfluenceComponent("funding", 3, 0.30, 0.9),
        ConfluenceComponent("vwap", 2, -0.50, -1.0),
        ConfluenceComponent("atr", 1, 0.30, 0.3),
        ConfluenceComponent("adx", 1, -0.40, -0.4),
        ConfluenceComponent("bollinger", 1, 0.30, 0.3),
        ConfluenceComponent("support_resistance", 1, 0.40, 0.4),
        ConfluenceComponent("rsi", 0, -0.20, 0.0),
    ]
    confluence = ConfluenceResult(score=28, direction="BEARISH", components=components, dominant_components=["market_structure", "trend", "liquidity"])

    # Risk
    risk = RiskResult(
        valid=True, direction="SELL", entry=4.579,
        stop_loss=4.7346, take_profit=4.2678,
        risk_pct=3.39, reward_pct=6.79, risk_reward=2.0,
        position_size=87.0, position_value=400.0,
        risk_amount=100.0, reward_amount=200.0,
        trade_style=TradeStyle.SWING,
    )

    # AI result
    ai = AIValidationResult(
        setup_id="TEST-FWDIUSDT-SELL-1",
        symbol="FWDIUSDT", direction="SELL",
        ai_decision="SELL", confidence=0.88, probability=0.82,
        trade_quality="A", risk_level="MEDIUM",
        reasoning=(
            "Strong bearish confluence with BOS confirmation and aligned multi-timeframe trend. "
            "Smart money shows heavy institutional selling with taker sell dominance at 70%. "
            "Liquidity sweep below 4.50 confirms downside continuation toward TP1 at 4.42."
        ),
        provider="openrouter", model="google/gemma-4-26b-a4b-it:free",
        latency_ms=8500, cached=False, safety_overrides=[],
        stored_decision_id=None,
    )

    # Build signal
    signal = Signal(
        id="FWDIUSDT-D-TEST1",
        signal_type=SignalType.TYPE_D,
        direction=SignalDirection.SELL,
        symbol="FWDIUSDT",
        entry=4.579, stop_loss=4.7346, take_profit=4.2678,
        risk_reward=2.0, confidence=0.88,
        confluence_score=28,
        trend=trend, market_structure=ms, smart_money=sm,
        confluence=confluence, risk=risk, ai=ai,
        created_at=datetime.now(timezone.utc),
        metadata={
            "trend_aligned": True,
            "market_structure_event": "BOS_BEAR",
            "smart_money_signals": sm.signals,
            "indicators": {
                "ema": {"score": -0.80, "emas": [-0.85, -0.80, -0.70, -0.50]},
                "vwap_position": -0.55,
                "atr_pct": 1.85,
                "rsi": {"value": 41.43, "slope": -5.0, "momentum": -2.0, "acceleration": 0.5},
                "adx": {"adx": 23.33, "plus_di": 18.0, "minus_di": 35.0, "trend_strength": "moderate"},
                "macd": {"macd": -0.012, "signal": -0.008, "histogram": -0.004},
                "bollinger": {"upper": 4.80, "middle": 4.65, "lower": 4.50, "width": 0.065, "percent_b": 0.20},
                "support_resistance": {"nearest_support": 4.42, "nearest_resistance": 4.80},
                "volume_spike_ratio": 1.85,
                "last_price": 4.579,
            },
            "pressure": {"buy_pct": 0.24, "net_score": -0.52, "summary": "Strong sell pressure"},
            "liquidity_summary": "3 pools, 1 sweeps, 2 unfilled FVGs, 1 OBs",
            "funding_rate": 0.00025,
            "open_interest": 247135.8,
            "price_change_pct_24h": 14.05,
        },
    )

    # Send to Telegram
    notifier = TelegramNotifier()
    print("Sending test alert with new institutional format...")
    print(f"Signal: {signal.symbol} {signal.direction.value} (confluence {signal.confluence_score})")

    # Format and preview
    msg = notifier._format_message(signal)
    print("\n" + "=" * 60)
    print("FORMATTED MESSAGE PREVIEW:")
    print("=" * 60)
    print(msg)
    print("=" * 60)

    # Send via Telegram
    sent = await notifier.send_signal(signal)
    if sent:
        print("\n✅ Alert sent to Telegram! Check your chat.")
    else:
        print("\n⚠ Alert was not sent (filter blocked it — using direct send_text instead)")
        # Force send the formatted message
        sent_direct = await notifier.send_text(msg)
        if sent_direct:
            print("✅ Alert sent via send_text fallback.")
        else:
            print("❌ Failed to send alert.")

    await notifier.aclose()


if __name__ == "__main__":
    asyncio.run(main())
