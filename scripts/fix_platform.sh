#!/bin/bash
# ============================================================
# FAST FIX — Platform not running on AWS
# Run this on EC2: bash fix_platform.sh
# ============================================================
set -e

cd ~/Institutional-Crypto-Futures-Intelligence-Platform 2>/dev/null || \
cd /home/ubuntu/Institutional-Crypto-Futures-Intelligence-Platform

echo "============================================================"
echo "🔧 FAST FIX — Platform Startup"
echo "============================================================"
echo ""

# 1. Check if venv exists
if [ ! -d ".venv" ]; then
    echo "1️⃣ Creating venv + installing deps..."
    python3.12 -m venv .venv 2>/dev/null || python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
pip install -q aiosqlite 2>/dev/null || true
echo "✅ Python deps installed"
echo ""

# 2. Check .env exists
if [ ! -f ".env" ]; then
    echo "❌ .env file MISSING!"
    echo "   Please re-run the main setup script with your API keys first."
    exit 1
fi
echo "✅ .env file exists"
echo ""

# 3. Initialize DB
echo "2️⃣ Initializing database..."
rm -f platform.db
python3 -c "
import asyncio
from app.db.base import Base
from app.db.session import build_engine
from app.db import models
async def migrate():
    engine = build_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
asyncio.run(migrate())
" 2>&1 | tail -2
echo "✅ DB ready"
echo ""

# 4. Remove old broken service (if any)
sudo systemctl stop crypto-platform 2>/dev/null || true
sudo systemctl disable crypto-platform 2>/dev/null || true
sudo rm -f /etc/systemd/system/crypto-platform.service
sudo systemctl daemon-reload
echo "✅ Cleaned old service"
echo ""

# 5. Quick test: can Python start the platform?
echo "3️⃣ Testing platform startup (5 second test)..."
timeout 5 python3 -c "
import asyncio
from app.core.container import container
from app.core.lifespan import _startup, _shutdown

async def test():
    await _startup(container)
    print('   ✅ Lifespan starts OK')
    pipeline = await container.get('AnalysisPipeline')
    print('   ✅ Pipeline constructed OK')
    await _shutdown(container)
    print('   ✅ Shutdown OK')

asyncio.run(test())
" 2>&1 | grep -E "✅|❌|Error|FAIL" | head -10
echo ""

# 6. Create fresh systemd service
echo "4️⃣ Creating systemd service..."
USER=$(whoami)
WORKDIR=$(pwd)
sudo tee /etc/systemd/system/crypto-platform.service > /dev/null << EOF
[Unit]
Description=Institutional Crypto Futures Intelligence Platform
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$WORKDIR
EnvironmentFile=$WORKDIR/.env
ExecStart=$WORKDIR/.venv/bin/python -m app
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=crypto-platform

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable crypto-platform
echo "✅ Service created"
echo ""

# 7. Start the service
echo "5️⃣ Starting service..."
sudo systemctl start crypto-platform
sleep 8

# 8. Verify it's running
if sudo systemctl is-active --quiet crypto-platform; then
    echo "✅ Platform is RUNNING!"
else
    echo "❌ Platform failed to start. Recent logs:"
    sudo journalctl -u crypto-platform -n 40 --no-pager
    exit 1
fi
echo ""

# 9. Wait for pipeline to start scanning
echo "6️⃣ Waiting 20s for first pipeline cycle..."
sleep 20
echo ""

# 10. Show recent activity
echo "7️⃣ Recent platform logs (last 20 lines):"
sudo journalctl -u crypto-platform -n 20 --no-pager --output=short-iso
echo ""

# 11. Check pipeline cycles
echo "8️⃣ Pipeline activity check:"
CYCLES=$(sudo journalctl -u crypto-platform --no-pager 2>/dev/null | grep -c "pipeline_cycle_complete" || echo "0")
SEEDS=$(sudo journalctl -u crypto-platform --no-pager 2>/dev/null | grep -c "pipeline_market_data_seeded" || echo "0")
STAGE1=$(sudo journalctl -u crypto-platform --no-pager 2>/dev/null | grep -c "stage1_complete" || echo "0")
echo "   Pipeline cycles completed: $CYCLES"
echo "   Market data seeds:         $SEEDS"
echo "   Stage 1 scans:             $STAGE1"
echo ""

# 12. API health check
echo "9️⃣ API health check:"
sleep 3
if curl -s --max-time 5 http://localhost:8080/health > /dev/null; then
    echo "   ✅ API is responding"
    echo ""
    echo "   Symbols tracked:"
    curl -s --max-time 5 "http://localhost:8080/api/market?limit=5" 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    if isinstance(data, list):
        print(f'     Total: {len(data)} symbols (showing top 5)')
        for s in data[:5]:
            print(f'     • {s[\"symbol\"]}: \${s[\"last_price\"]:.4f} (vol: \${s[\"quote_volume_24h\"]:,.0f})')
    else:
        print('     No data yet')
except Exception as e:
    print(f'     Parse error: {e}')
" 2>/dev/null || echo "   Still warming up..."
else
    echo "   ⚠️ API not responding yet (still starting up)"
fi
echo ""

echo "============================================================"
echo "🎉 FIX COMPLETE!"
echo "============================================================"
echo ""
echo "📋 Useful commands:"
echo "   Live logs:        sudo journalctl -u crypto-platform -f"
echo "   Service status:   sudo systemctl status crypto-platform"
echo "   Restart:          sudo systemctl restart crypto-platform"
echo "   Stop:             sudo systemctl stop crypto-platform"
echo "   Re-diagnose:      bash scripts/diagnose.sh"
echo "   Test alert:       bash scripts/force_test_alert.sh"
echo ""
echo "🌐 Public access (your EC2 IP: 65.0.180.152):"
echo "   Dashboard:  http://65.0.180.152:8080/"
echo "   Status:     http://65.0.180.152:8080/api/status"
echo "   Providers:  http://65.0.180.152:8080/api/providers"
echo ""
echo "⚠️  Make sure AWS Security Group allows port 8080 (TCP, anywhere)"
echo ""
