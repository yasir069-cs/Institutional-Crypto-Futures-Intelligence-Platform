#!/bin/bash
# ============================================================
# Platform Diagnostic Script — Check platform health + activity
# ============================================================

# Find project directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "============================================================"
echo "📊 PLATFORM DIAGNOSTIC"
echo "============================================================"
echo ""

# 1. Service status
echo "1️⃣  Service status:"
sudo systemctl status crypto-platform --no-pager -l 2>/dev/null | head -10 || echo "   ❌ Service not found"
echo ""

# 2. Last 30 log lines
echo "2️⃣  Last 30 log lines (live activity):"
sudo journalctl -u crypto-platform -n 30 --no-pager 2>/dev/null | tail -30
echo ""

# 3. Check if pipeline is running cycles
echo "3️⃣  Pipeline cycle activity (last 5 cycles):"
sudo journalctl -u crypto-platform --no-pager 2>/dev/null | grep "pipeline_cycle_complete" | tail -5
echo ""

# 4. Check Stage 1 results
echo "4️⃣  Stage 1 results (last 5 scans):"
sudo journalctl -u crypto-platform --no-pager 2>/dev/null | grep "stage1_complete" | tail -5
echo ""

# 5. Check Stage 2 results
echo "5️⃣  Stage 2 results (last 5):"
sudo journalctl -u crypto-platform --no-pager 2>/dev/null | grep "stage2_complete" | tail -5
echo ""

# 6. Check what's being rejected
echo "6️⃣  Stage 1 rejection reasons (sample):"
sudo journalctl -u crypto-platform --no-pager 2>/dev/null | grep "ATR%" | tail -5
echo ""

# 7. Check AI calls
echo "7️⃣  AI validation activity:"
sudo journalctl -u crypto-platform --no-pager 2>/dev/null | grep "ai_validation_success" | tail -5
echo ""

# 8. Check for signals generated (even if not alerted)
echo "8️⃣  Signals generated (not necessarily alerted):"
sudo journalctl -u crypto-platform --no-pager 2>/dev/null | grep "signal_skipped_alert\|pipeline_signal_alerted" | tail -10
echo ""

# 9. Check provider health
echo "9️⃣  AI Provider health:"
curl -s http://localhost:8080/api/providers 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "   ⚠️ API not responding"
echo ""

# 10. Check platform status
echo "🔟 Platform status:"
curl -s http://localhost:8080/api/status 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "   ⚠️ API not responding"
echo ""

# 11. Check if market data is loaded
echo "1️⃣1️⃣ Symbols tracked:"
curl -s "http://localhost:8080/api/market?limit=5" 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    if isinstance(data, list) and len(data) > 0:
        print(f'   ✅ {len(data)} symbols shown (top 5)')
        for s in data[:5]:
            print(f'      {s[\"symbol\"]}: \${s[\"last_price\"]:.4f} (vol: \${s[\"quote_volume_24h\"]:,.0f})')
    else:
        print('   ⚠️ No market data yet')
except:
    print('   ⚠️ Could not parse')
"
echo ""

# 12. Recent signals from DB
echo "1️⃣2️⃣ Recent signals in database:"
curl -s "http://localhost:8080/api/signals?limit=5" 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    if isinstance(data, list) and len(data) > 0:
        print(f'   ✅ {len(data)} signals in DB')
        for s in data[:5]:
            print(f'      {s[\"symbol\"]} {s[\"direction\"]} (confluence {s[\"confluence_score\"]}, status: {s[\"status\"]})')
    else:
        print('   ⚠️ No signals yet — platform is filtering strictly')
except:
    print('   ⚠️ Could not parse')
"
echo ""

echo "============================================================"
echo "🎯 DIAGNOSIS GUIDE"
echo "============================================================"
echo ""
echo "If you see:"
echo "  ✅ 'pipeline_cycle_complete' logs → Platform IS scanning"
echo "  ✅ 'stage1_complete' with passed > 0 → Stage 1 finding candidates"
echo "  ✅ 'stage2_complete' with setups > 0 → Stage 2 finding setups"
echo "  ✅ 'pipeline_signal_alerted' → Telegram alert was sent"
echo "  ⚠️ 'signal_skipped_alert' → Signal generated but filtered (weak setup)"
echo "  ❌ No pipeline_cycle_complete logs → Platform not running"
echo ""
echo "Most common reason for NO alerts:"
echo "  → Market is ranging (low ATR%) → Stage 1 rejects all symbols"
echo "  → Confluence < 75 → Stage 2/3 rejects setups"
echo "  → AI not approved → Telegram gate blocks alert"
echo ""
echo "To FORCE TEST an alert, run:"
echo "  bash force_test_alert.sh"
