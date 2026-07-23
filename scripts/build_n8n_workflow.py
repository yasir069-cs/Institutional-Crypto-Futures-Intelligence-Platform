"""
Generate the n8n workflow JSON file for the Institutional Crypto Futures Intelligence Platform.

This workflow implements the same 3-stage pipeline as the Python platform:
  Stage 1: Fast math filter (500 → 30 candidates)
  Stage 2: Smart Money Concepts deep analysis (30 → 5 premium setups)
  Stage 3: AI validation via OpenRouter (5 → BUY/SELL/HOLD/REJECT)
  Final: Telegram alert (only for AI-approved BUY/SELL)

Output: /home/z/my-project/download/n8n_workflow.json
"""
import json
import uuid
from pathlib import Path

OUTPUT = Path("/home/z/my-project/download/n8n_workflow.json")
OUTPUT.parent.mkdir(parents=True, exist_ok=True)


def node_id() -> str:
    return str(uuid.uuid4())


# ─────────────────────────────────────────────────────────────────────────────
# JAVASCRIPT CODE for each Code node
# ─────────────────────────────────────────────────────────────────────────────

STAGE1_CODE = r"""
// ════════════════════════════════════════════════════════════════
// STAGE 1: Fast Mathematical Scanner
// Input: Binance 24h tickers (all USDT pairs)
// Output: Top 30 candidates by volume + basic criteria
// ════════════════════════════════════════════════════════════════

const tickers = items[0].json; // array of all tickers
const MIN_VOLUME_USD = 5000000;  // $5M minimum 24h volume
const MIN_TRADE_COUNT = 100;
const TOP_N = 30;

// Filter: USDT perpetuals with sufficient volume
const candidates = tickers
  .filter(t => {
    if (!t.symbol || !t.symbol.endsWith('USDT')) return false;
    const vol = parseFloat(t.quoteVolume || 0);
    const trades = parseInt(t.count || 0);
    return vol >= MIN_VOLUME_USD && trades >= MIN_TRADE_COUNT;
  })
  .map(t => ({
    symbol: t.symbol,
    price: parseFloat(t.lastPrice),
    price_change_pct_24h: parseFloat(t.priceChangePercent),
    volume_24h: parseFloat(t.volume),
    quote_volume_24h: parseFloat(t.quoteVolume),
    high_24h: parseFloat(t.highPrice),
    low_24h: parseFloat(t.lowPrice),
    trade_count_24h: parseInt(t.count),
  }))
  .sort((a, b) => b.quote_volume_24h - a.quote_volume_24h)
  .slice(0, 500);  // max 500 pairs

// Take top 30 by volume for Stage 2
const top30 = candidates.slice(0, TOP_N);

console.log(`Stage 1: ${candidates.length} candidates → top ${top30.length} selected`);

// Output one item per candidate (so Split In Batches can iterate)
return top30.map(c => ({ json: c }));
"""

STAGE2_CODE = r"""
// ════════════════════════════════════════════════════════════════
// STAGE 2: Smart Money Concepts Deep Analysis
// For each symbol: fetch 3 timeframes of klines, compute all
// indicators, structure, trend, liquidity, smart money, confluence.
// Filter to premium setups only (confluence >= 70, RR >= 1:2).
// ════════════════════════════════════════════════════════════════

// ─── Helper: Fetch klines from Binance ───────────────────────────
async function fetchKlines(symbol, interval, limit = 200) {
  const url = `https://fapi.binance.com/fapi/v1/klines?symbol=${symbol}&interval=${interval}&limit=${limit}`;
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`klines ${symbol} ${interval}: ${resp.status}`);
  const data = await resp.json();
  return data.map(k => ({
    openTime: k[0],
    open: parseFloat(k[1]),
    high: parseFloat(k[2]),
    low: parseFloat(k[3]),
    close: parseFloat(k[4]),
    volume: parseFloat(k[5]),
    closeTime: k[6],
    quoteVolume: parseFloat(k[7]),
    tradeCount: k[8],
    takerBuyVolume: parseFloat(k[9]),
    takerBuyQuoteVolume: parseFloat(k[10]),
  }));
}

// ─── Indicator: EMA ──────────────────────────────────────────────
function ema(values, period) {
  if (values.length === 0) return [];
  const alpha = 2 / (period + 1);
  const out = [values[0]];
  for (let i = 1; i < values.length; i++) {
    out.push(alpha * values[i] + (1 - alpha) * out[i - 1]);
  }
  return out;
}

// ─── Indicator: ATR ──────────────────────────────────────────────
function atr(candles, period = 14) {
  if (candles.length < 2) return 0;
  const trs = [0];
  for (let i = 1; i < candles.length; i++) {
    const tr = Math.max(
      candles[i].high - candles[i].low,
      Math.abs(candles[i].high - candles[i - 1].close),
      Math.abs(candles[i].low - candles[i - 1].close)
    );
    trs.push(tr);
  }
  let prev = trs.slice(1, period + 1).reduce((a, b) => a + b, 0) / period;
  for (let i = period + 1; i < trs.length; i++) {
    prev = (prev * (period - 1) + trs[i]) / period;
  }
  return prev;
}

// ─── Indicator: RSI ──────────────────────────────────────────────
function rsi(candles, period = 14) {
  if (candles.length < 2) return 50;
  let gains = 0, losses = 0;
  for (let i = 1; i <= period; i++) {
    const change = candles[i].close - candles[i - 1].close;
    if (change > 0) gains += change; else losses -= change;
  }
  let avgGain = gains / period;
  let avgLoss = losses / period;
  for (let i = period + 1; i < candles.length; i++) {
    const change = candles[i].close - candles[i - 1].close;
    avgGain = (avgGain * (period - 1) + Math.max(0, change)) / period;
    avgLoss = (avgLoss * (period - 1) + Math.max(0, -change)) / period;
  }
  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}

// ─── Indicator: ADX ──────────────────────────────────────────────
function adx(candles, period = 14) {
  if (candles.length < period + 2) return { adx: 0, plus_di: 0, minus_di: 0 };
  const plusDM = [0], minusDM = [0], trs = [0];
  for (let i = 1; i < candles.length; i++) {
    const up = candles[i].high - candles[i - 1].high;
    const down = candles[i - 1].low - candles[i].low;
    plusDM.push((up > down && up > 0) ? up : 0);
    minusDM.push((down > up && down > 0) ? down : 0);
    trs.push(Math.max(
      candles[i].high - candles[i].low,
      Math.abs(candles[i].high - candles[i - 1].close),
      Math.abs(candles[i].low - candles[i - 1].close)
    ));
  }
  let atr_s = trs.slice(1, period + 1).reduce((a, b) => a + b, 0);
  let pDM_s = plusDM.slice(1, period + 1).reduce((a, b) => a + b, 0);
  let mDM_s = minusDM.slice(1, period + 1).reduce((a, b) => a + b, 0);
  const dx = [];
  for (let i = period + 1; i < candles.length; i++) {
    atr_s = (atr_s * (period - 1) + trs[i]) / period;
    pDM_s = (pDM_s * (period - 1) + plusDM[i]) / period;
    mDM_s = (mDM_s * (period - 1) + minusDM[i]) / period;
    const pdi = atr_s > 0 ? (pDM_s / atr_s) * 100 : 0;
    const mdi = atr_s > 0 ? (mDM_s / atr_s) * 100 : 0;
    dx.push(pdi + mdi > 0 ? Math.abs(pdi - mdi) / (pdi + mdi) * 100 : 0);
  }
  const adxVal = dx.length >= period ? dx.slice(-period).reduce((a, b) => a + b, 0) / period : 0;
  const plus_di = atr_s > 0 ? (pDM_s / atr_s) * 100 : 0;
  const minus_di = atr_s > 0 ? (mDM_s / atr_s) * 100 : 0;
  return { adx: adxVal, plus_di, minus_di };
}

// ─── Market Structure: swing highs/lows ──────────────────────────
function findSwings(candles, left = 3, right = 3) {
  const highs = [], lows = [];
  for (let i = left; i < candles.length - right; i++) {
    const window = candles.slice(i - left, i + right + 1);
    if (candles[i].low === Math.min(...window.map(c => c.low))) lows.push({ idx: i, price: candles[i].low });
    if (candles[i].high === Math.max(...window.map(c => c.high))) highs.push({ idx: i, price: candles[i].high });
  }
  return { highs, lows };
}

// ─── Trend from candles ──────────────────────────────────────────
function trendFromCandles(candles) {
  const closes = candles.map(c => c.close);
  const ema9 = ema(closes, 9);
  const ema21 = ema(closes, 21);
  const ema50 = ema(closes, 50);
  const adxInfo = adx(candles);
  let bias = 'NEUTRAL';
  if (ema9[ema9.length-1] > ema21[ema21.length-1] && ema21[ema21.length-1] > ema50[ema50.length-1]) bias = 'BULLISH';
  else if (ema9[ema9.length-1] < ema21[ema21.length-1] && ema21[ema21.length-1] < ema50[ema50.length-1]) bias = 'BEARISH';
  return { bias, strength: Math.min(1, adxInfo.adx / 50), adx: adxInfo.adx, plus_di: adxInfo.plus_di, minus_di: adxInfo.minus_di };
}

// ─── Confluence scoring ──────────────────────────────────────────
function confluenceScore(htfTrend, mtfTrend, ltfTrend, rsiVal, adxVal, smartMoneyFlow, pressureNet, volume, htfCandles) {
  let score = 50; // start neutral
  const direction = htfTrend.bias === 'BULLISH' ? 1 : htfTrend.bias === 'BEARISH' ? -1 : 0;
  // Trend (18%)
  if (htfTrend.bias !== 'NEUTRAL' && htfTrend.bias === mtfTrend.bias) score += direction * 9;
  if (htfTrend.bias === ltfTrend.bias) score += direction * 4.5;
  // Smart money (15%)
  score += Math.max(-7.5, Math.min(7.5, smartMoneyFlow * 7.5));
  // Pressure (5%)
  score += Math.max(-2.5, Math.min(2.5, pressureNet * 2.5));
  // ADX (1%)
  if (adxVal > 25) score += direction * 0.5;
  // RSI (0.5%)
  if (direction > 0 && rsiVal > 50 && rsiVal < 70) score += 0.25;
  if (direction < 0 && rsiVal < 50 && rsiVal > 30) score -= 0.25;
  return Math.max(0, Math.min(100, Math.round(score)));
}

// ════════════════════════════════════════════════════════════════
// MAIN: Process all candidates from Stage 1
// ════════════════════════════════════════════════════════════════

const allCandidates = items.map(i => i.json);
console.log(`Stage 2: Processing ${allCandidates.length} candidates`);

const premiumSetups = [];

for (const candidate of allCandidates) {
  try {
    // Fetch 3 timeframes in parallel
    const [htf, mtf, ltf] = await Promise.all([
      fetchKlines(candidate.symbol, '1h', 200),
      fetchKlines(candidate.symbol, '15m', 200),
      fetchKlines(candidate.symbol, '5m', 200),
    ]);

    if (htf.length < 50 || mtf.length < 50 || ltf.length < 50) continue;

    // Compute trends
    const htfTrend = trendFromCandles(htf);
    const mtfTrend = trendFromCandles(mtf);
    const ltfTrend = trendFromCandles(ltf);

    // Reject if HTF neutral (no clear trend)
    if (htfTrend.bias === 'NEUTRAL') continue;
    // Reject if HTF ADX < 15 (no real trend)
    if (htfTrend.adx < 15) continue;

    // Indicators
    const closes = htf.map(c => c.close);
    const atrVal = atr(htf, 14);
    const atrPct = (atrVal / closes[closes.length - 1]) * 100;
    const rsiVal = rsi(htf, 14);
    const adxInfo = adx(htf, 14);

    // Smart money: taker buy/sell ratio from last 20 candles
    const recent = htf.slice(-20);
    const takerBuy = recent.reduce((s, c) => s + c.takerBuyVolume, 0);
    const totalVol = recent.reduce((s, c) => s + c.volume, 0);
    const buyPct = totalVol > 0 ? takerBuy / totalVol : 0.5;
    const smartMoneyFlow = (buyPct - 0.5) * 2; // -1 to +1

    // Pressure
    const pressureNet = smartMoneyFlow;

    // Volume spike
    const volAvg = htf.slice(-21, -1).reduce((s, c) => s + c.volume, 0) / 20;
    const volSpike = htf[htf.length - 1].volume / volAvg;

    // Direction
    const direction = htfTrend.bias === 'BULLISH' ? 'BUY' : 'SELL';

    // Risk: ATR-based SL/TP
    const entry = candidate.price;
    const slDist = atrVal * 1.5;
    const tpDist = atrVal * 3.0;
    const sl = direction === 'BUY' ? entry - slDist : entry + slDist;
    const tp = direction === 'BUY' ? entry + tpDist : entry - tpDist;
    const riskPct = (Math.abs(entry - sl) / entry) * 100;
    const rewardPct = (Math.abs(tp - entry) / entry) * 100;
    const rr = Math.abs(tp - entry) / Math.abs(entry - sl);

    // Reject if RR < 2.0
    if (rr < 2.0) continue;
    // Reject if SL too wide (intraday > 3%)
    if (riskPct > 3.0) continue;

    // Smart money must align with direction
    if (direction === 'BUY' && smartMoneyFlow < 0) continue;
    if (direction === 'SELL' && smartMoneyFlow > 0) continue;

    // Confluence
    const confluence = confluenceScore(htfTrend, mtfTrend, ltfTrend, rsiVal, adxInfo.adx, smartMoneyFlow, pressureNet, volSpike, htf);

    // Filter: confluence >= 70
    if (confluence < 70) continue;

    // Market structure event
    const swings = findSwings(htf);
    let msEvent = 'NONE';
    if (swings.highs.length >= 2 && swings.lows.length >= 2) {
      const prevHigh = swings.highs[swings.highs.length - 2];
      if (direction === 'BUY' && entry > prevHigh.price) msEvent = 'BOS_BULL';
      const prevLow = swings.lows[swings.lows.length - 2];
      if (direction === 'SELL' && entry < prevLow.price) msEvent = 'BOS_BEAR';
    }

    premiumSetups.push({
      symbol: candidate.symbol,
      direction,
      price: entry,
      entry,
      stop_loss: sl,
      take_profit: tp,
      risk_pct: riskPct,
      reward_pct: rewardPct,
      risk_reward: rr,
      confluence_score: confluence,
      htf_trend: htfTrend.bias,
      mtf_trend: mtfTrend.bias,
      ltf_trend: ltfTrend.bias,
      htf_adx: htfTrend.adx,
      rsi: rsiVal,
      atr_pct: atrPct,
      smart_money_flow: smartMoneyFlow,
      buy_pct: buyPct,
      sell_pct: 1 - buyPct,
      volume_spike: volSpike,
      market_structure_event: msEvent,
      price_change_pct_24h: candidate.price_change_pct_24h,
      indicators: {
        rsi: rsiVal,
        adx: adxInfo.adx,
        plus_di: adxInfo.plus_di,
        minus_di: adxInfo.minus_di,
        atr: atrVal,
        atr_pct: atrPct,
      },
    });
  } catch (e) {
    console.log(`Stage 2 error for ${candidate.symbol}: ${e.message}`);
  }
}

// Sort by confluence descending, take top 5
premiumSetups.sort((a, b) => b.confluence_score - a.confluence_score);
const top5 = premiumSetups.slice(0, 5);

console.log(`Stage 2: ${premiumSetups.length} premium setups → top ${top5.length} selected`);

if (top5.length === 0) {
  return [{ json: { no_setups: true, message: 'No premium setups found this cycle' } }];
}

return top5.map(s => ({ json: s }));
"""

AI_PARSE_CODE = r"""
// ════════════════════════════════════════════════════════════════
// Parse AI Response + Apply Safety Rules
// Input: OpenRouter chat completion response
// Output: { ai_decision, ai_confidence, ai_reasoning, should_alert }
// ════════════════════════════════════════════════════════════════

const setup = $('Take Top 5').item.json;
const aiResponse = items[0].json;

let decision = 'HOLD';
let confidence = 0.5;
let reasoning = 'AI response parse failed';

try {
  const content = aiResponse.choices[0].message.content;
  // Try to parse JSON from content
  const jsonMatch = content.match(/\{[\s\S]*\}/);
  if (jsonMatch) {
    const parsed = JSON.parse(jsonMatch[0]);
    decision = (parsed.decision || 'HOLD').toUpperCase();
    confidence = parseFloat(parsed.confidence || 0.5);
    reasoning = parsed.reasoning || content.substring(0, 300);
  } else {
    // Fallback: keyword extraction
    const lower = content.toLowerCase();
    if (lower.includes('buy')) decision = 'BUY';
    else if (lower.includes('sell')) decision = 'SELL';
    else if (lower.includes('reject')) decision = 'REJECT';
    else if (lower.includes('watchlist')) decision = 'WATCHLIST';
    reasoning = content.substring(0, 500);
  }
} catch (e) {
  console.log(`AI parse error: ${e.message}`);
}

// ═══ Apply Safety Rules (from spec) ═══
const safetyOverrides = [];

// 1. Confluence < 75 → HOLD
if (setup.confluence_score < 75 && (decision === 'BUY' || decision === 'SELL')) {
  safetyOverrides.push(`Confluence ${setup.confluence_score} < 75 → HOLD`);
  decision = 'HOLD';
}

// 2. HTF disagrees → HOLD
if (decision === 'BUY' && setup.htf_trend === 'BEARISH') {
  safetyOverrides.push('HTF BEARISH vs BUY → HOLD');
  decision = 'HOLD';
}
if (decision === 'SELL' && setup.htf_trend === 'BULLISH') {
  safetyOverrides.push('HTF BULLISH vs SELL → HOLD');
  decision = 'HOLD';
}

// 3. Smart money missing → WATCHLIST
if ((decision === 'BUY' || decision === 'SELL')) {
  if (decision === 'BUY' && setup.smart_money_flow < 0.15) {
    safetyOverrides.push(`Smart money ${setup.smart_money_flow.toFixed(2)} not confirming BUY → WATCHLIST`);
    decision = 'WATCHLIST';
  }
  if (decision === 'SELL' && setup.smart_money_flow > -0.15) {
    safetyOverrides.push(`Smart money ${setup.smart_money_flow.toFixed(2)} not confirming SELL → WATCHLIST`);
    decision = 'WATCHLIST';
  }
}

// 4. RR < 1:2 → REJECT
if ((decision === 'BUY' || decision === 'SELL') && setup.risk_reward < 2.0) {
  safetyOverrides.push(`RR ${setup.risk_reward.toFixed(2)} < 2.0 → REJECT`);
  decision = 'REJECT';
}

const shouldAlert = (decision === 'BUY' || decision === 'SELL');

return [{
  json: {
    ...setup,
    ai_decision: decision,
    ai_confidence: confidence,
    ai_reasoning: reasoning,
    safety_overrides: safetyOverrides,
    should_alert: shouldAlert,
  }
}];
"""

TELEGRAM_FORMAT_CODE = r"""
// ════════════════════════════════════════════════════════════════
// Format Telegram Alert — Institutional Template
// Input: setup with AI decision
// Output: { chat_id, text, parse_mode } for Telegram sendMessage
// ════════════════════════════════════════════════════════════════

const s = items[0].json;
const SEP = '━━━━━━━━━━━━━━━━━━━━';

// Direction emoji
const dirEmoji = {
  'BUY': '🟢 BUY',
  'SELL': '🔴 SELL',
  'WATCHLIST': '🟡 WATCHLIST',
  'HOLD': '⚪ HOLD',
  'REJECT': '⚫ REJECT',
};

// Symbol emoji
const symEmoji = s.symbol.startsWith('BTC') ? '₿' :
                 s.symbol.startsWith('ETH') ? 'Ξ' :
                 s.symbol.startsWith('BNB') ? '◈' : '🪙';

// Take Profit levels (1R, 2R, 3R)
const riskDist = Math.abs(s.entry - s.stop_loss);
let tp1, tp2, tp3;
if (s.direction === 'BUY') {
  tp1 = s.entry + riskDist;
  tp2 = s.entry + riskDist * 2;
  tp3 = s.entry + riskDist * 3;
} else {
  tp1 = s.entry - riskDist;
  tp2 = s.entry - riskDist * 2;
  tp3 = s.entry - riskDist * 3;
}

// Risk level
const riskLevel = s.risk_pct < 1.0 ? 'Low' : s.risk_pct > 2.0 ? 'High' : 'Medium';

// Trend short
const trendShort = (t) => t === 'BULLISH' ? 'BULL' : t === 'BEARISH' ? 'BEAR' : 'NEUTRAL';

// Quality score 0-10
const quality = Math.min(10, Math.max(0, (s.confluence_score / 10 + s.ai_confidence * 5) / 2));
const filledStars = Math.round(quality);
const stars = '●'.repeat(filledStars) + '○'.repeat(10 - filledStars);

// Priority
const priority = (s.ai_decision === 'BUY' || s.ai_decision === 'SELL') ? '🟢 HIGH' :
                 s.ai_decision === 'WATCHLIST' ? '🟡 WATCHLIST' :
                 s.ai_decision === 'HOLD' ? '⚪ MEDIUM' : '⚫ LOW';

// AI reasoning (trim to 2 sentences)
let aiReasoning = s.ai_reasoning || 'Python technicals aligned. No AI verification.';
const sentences = aiReasoning.split('. ');
if (sentences.length > 2) aiReasoning = sentences.slice(0, 2).join('. ') + '.';

// Build message
const message = [
  SEP,
  '🏛 <b>Institutional AI Market Intelligence</b>',
  SEP,
  `Coin: ${symEmoji} <b>${s.symbol}</b>`,
  `Direction Bias: ${dirEmoji[s.ai_decision] || s.ai_decision}`,
  `Signal Type: Trend Continuation`,
  '',
  SEP,
  `PRIORITY: ${priority}`,
  `Setup Quality: ${stars} (${quality.toFixed(1)}/10)`,
  `AI Confidence: ${(s.ai_confidence * 100).toFixed(0)}%`,
  '',
  SEP,
  '💰 <b>TRADE PLAN</b>',
  `Entry: <code>${s.entry}</code>`,
  `Stop Loss: <code>${s.stop_loss}</code>`,
  `TP1: <code>${tp1.toFixed(4)}</code> | TP2: <code>${tp2.toFixed(4)}</code> | TP3: <code>${tp3.toFixed(4)}</code>`,
  `Risk Reward: 1:${Math.round(s.risk_reward)} | Risk: ${riskLevel}`,
  '',
  SEP,
  '📊 <b>MARKET STRUCTURE</b>',
  `1H Trend: ${trendShort(s.htf_trend)} | 15M: ${trendShort(s.mtf_trend)} | 5M: ${trendShort(s.ltf_trend)}`,
  `ADX: ${s.indicators.adx.toFixed(1)} | RSI: ${s.indicators.rsi.toFixed(1)}`,
  `Structure: ${s.market_structure_event}`,
  '',
  SEP,
  '📈 <b>MARKET DATA</b>',
  `Buy Press: ${(s.buy_pct * 100).toFixed(1)}% | Sell Press: ${((1 - s.buy_pct) * 100).toFixed(1)}%`,
  `24H Change: ${s.price_change_pct_24h >= 0 ? '+' : ''}${s.price_change_pct_24h.toFixed(2)}%`,
  `ATR%: ${s.atr_pct.toFixed(2)}% | Volume Spike: ${s.volume_spike.toFixed(2)}x`,
  '',
  SEP,
  '🧠 <b>AI ANALYSIS</b>',
  aiReasoning,
  '',
  SEP,
  '⚠️ <b>TRADE INVALIDATION</b>',
  'Ignore this setup if:',
  s.direction === 'SELL' ?
    ['• 15M closes above EMA21', '• Price reclaims VWAP', '• Sell pressure weakens', '• Liquidity sweep fails'] :
    ['• 15M closes below EMA21', '• Price loses VWAP', '• Buy pressure weakens', '• Liquidity sweep fails'],
  '',
  SEP,
  '📝 <b>MANUAL CHECKLIST</b>',
  '☐ Support &amp; Resistance',
  '☐ Higher Timeframe Candle Close',
  '☐ Live Volume',
  '☐ BTC Market Direction',
  '☐ High Impact News',
  '☐ Position Size',
  '',
  SEP,
  '⚖️ <b>FINAL VERDICT</b>',
  `Decision: <b>${s.ai_decision}</b>`,
  `Confidence: ${(s.ai_confidence * 100).toFixed(0)}% | Quality: ${quality.toFixed(1)}/10`,
  '',
  SEP,
  '⚠ <i>AI Market Intelligence Only</i>',
  '<i>This is NOT financial advice.</i>',
  '<i>This alert highlights a potential market opportunity.</i>',
  '',
  '<b>Final Trading Decision:</b>',
  '👤 <b>MANUAL</b>',
  '',
  `<i>Time: ${new Date().toISOString().replace('T', ' ').substring(0, 19)} UTC</i>`,
].flat().join('\n');

// Get chat_id from environment (set in Telegram node credentials or here)
const chatId = process.env.TELEGRAM_CHAT_ID || '8337950513';

return [{
  json: {
    chat_id: chatId,
    text: message,
    parse_mode: 'HTML',
    disable_web_page_preview: true,
  }
}];
"""

# ─────────────────────────────────────────────────────────────────────────────
# BUILD THE WORKFLOW
# ─────────────────────────────────────────────────────────────────────────────

workflow = {
    "name": "🏛 Institutional Crypto Futures Intelligence Platform",
    "nodes": [
        # ─── Node 1: Schedule Trigger ─────────────────────────────────────
        {
            "parameters": {
                "rule": {
                    "interval": [{"field": "minutes", "minutesInterval": 1}]
                }
            },
            "id": node_id(),
            "name": "Every Minute",
            "type": "n8n-nodes-base.scheduleTrigger",
            "typeVersion": 1.1,
            "position": [0, 300],
            "notes": "Triggers scan every 60 seconds. Change minutesInterval to adjust frequency."
        },
        # ─── Node 2: HTTP Request — Get All Tickers ──────────────────────
        {
            "parameters": {
                "url": "https://fapi.binance.com/fapi/v1/ticker/24hr",
                "method": "GET",
                "options": {"timeout": 30000}
            },
            "id": node_id(),
            "name": "Get All Tickers",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.1,
            "position": [220, 300],
            "notes": "Fetches 24h ticker for ALL Binance USDT Futures pairs. No API key needed (public endpoint). Weight: 40."
        },
        # ─── Node 3: Code — Stage 1 Filter ───────────────────────────────
        {
            "parameters": {
                "jsCode": STAGE1_CODE
            },
            "id": node_id(),
            "name": "Stage 1: Filter Top 30",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [440, 300],
            "notes": "Filters 500+ pairs to top 30 by 24h volume (>$5M). No AI — pure math."
        },
        # ─── Node 4: Code — Stage 2 Deep Analysis ────────────────────────
        {
            "parameters": {
                "jsCode": STAGE2_CODE
            },
            "id": node_id(),
            "name": "Stage 2: Deep Analysis",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [660, 300],
            "notes": "For each candidate: fetch 3 timeframes (1h/15m/5m), compute indicators, market structure, smart money, confluence. Filters to confluence >= 70, RR >= 1:2. Returns top 5 premium setups."
        },
        # ─── Node 5: IF — Has Premium Setups? ────────────────────────────
        {
            "parameters": {
                "conditions": {
                    "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                    "conditions": [
                        {
                            "leftValue": "={{ $json.no_setups }}",
                            "rightValue": "",
                            "operator": {"type": "boolean", "operation": "notTrue", "singleValue": True}
                        }
                    ],
                    "combinator": "and"
                },
                "options": {}
            },
            "id": node_id(),
            "name": "Has Premium Setups?",
            "type": "n8n-nodes-base.if",
            "typeVersion": 2,
            "position": [880, 300],
            "notes": "True: proceed to AI validation. False: end cycle (no premium setups found)."
        },
        # ─── Node 6: Split In Batches — AI Loop ──────────────────────────
        {
            "parameters": {
                "batchSize": 1,
                "options": {}
            },
            "id": node_id(),
            "name": "AI Validation Loop",
            "type": "n8n-nodes-base.splitInBatches",
            "typeVersion": 3,
            "position": [1100, 300],
            "notes": "Processes each of the 5 premium setups one at a time (rate-limit friendly)."
        },
        # ─── Node 7: HTTP Request — OpenRouter AI ────────────────────────
        {
            "parameters": {
                "url": "https://openrouter.ai/api/v1/chat/completions",
                "method": "POST",
                "authentication": "genericCredentialType",
                "genericAuthType": "httpHeaderAuth",
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": """={
  "model": "google/gemma-4-26b-a4b-it:free",
  "messages": [
    {
      "role": "system",
      "content": "You are a senior institutional crypto futures trader. Validate ONLY premium setups. Respond ONLY with valid JSON: {decision: BUY|SELL|WATCHLIST|HOLD|REJECT, confidence: 0-1, probability: 0-1, trade_quality: A|B|C, risk_level: LOW|MEDIUM|HIGH, reasoning: string}. Safety: confluence<75→HOLD, HTF disagrees→HOLD, smart money missing→WATCHLIST, RR<1:2→REJECT."
    },
    {
      "role": "user",
      "content": "VALIDATE: Symbol: {{$json.symbol}}, Direction: {{$json.direction}}, Price: {{$json.price}}, Confluence: {{$json.confluence_score}}/100, HTF Trend: {{$json.htf_trend}} (ADX {{$json.indicators.adx.toFixed(1)}}), 15M: {{$json.mtf_trend}}, 5M: {{$json.ltf_trend}}, Smart Money flow: {{$json.smart_money_flow.toFixed(2)}}, RSI: {{$json.indicators.rsi.toFixed(1)}}, RR: 1:{{$json.risk_reward.toFixed(2)}}, Entry: {{$json.entry}}, SL: {{$json.stop_loss}}, TP: {{$json.take_profit}}, Market Structure: {{$json.market_structure_event}}, 24h Change: {{$json.price_change_pct_24h}}%. Return JSON only."
    }
  ],
  "temperature": 0.2,
  "max_tokens": 400
}""",
                "options": {"timeout": 60000}
            },
            "id": node_id(),
            "name": "OpenRouter AI",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.1,
            "position": [1320, 300],
            "notes": "Calls OpenRouter (Gemma 4 26B free). Create HTTP Header Auth credential with name 'OpenRouter Auth', header name 'Authorization', value 'Bearer YOUR_OPENROUTER_API_KEY'."
        },
        # ─── Node 8: Code — Parse AI + Apply Safety ──────────────────────
        {
            "parameters": {
                "jsCode": AI_PARSE_CODE
            },
            "id": node_id(),
            "name": "Parse AI + Safety Rules",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [1540, 300],
            "notes": "Parses AI JSON response, applies safety overrides (confluence<75→HOLD, HTF disagrees→HOLD, etc.). Sets should_alert=true only if AI says BUY or SELL."
        },
        # ─── Node 9: IF — AI Approved BUY/SELL? ─────────────────────────
        {
            "parameters": {
                "conditions": {
                    "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                    "conditions": [
                        {
                            "leftValue": "={{ $json.should_alert }}",
                            "rightValue": True,
                            "operator": {"type": "boolean", "operation": "true", "singleValue": True}
                        }
                    ],
                    "combinator": "and"
                },
                "options": {}
            },
            "id": node_id(),
            "name": "AI Approved BUY/SELL?",
            "type": "n8n-nodes-base.if",
            "typeVersion": 2,
            "position": [1760, 300],
            "notes": "True: send Telegram alert. False: skip this setup (don't alert)."
        },
        # ─── Node 10: Code — Format Telegram Message ─────────────────────
        {
            "parameters": {
                "jsCode": TELEGRAM_FORMAT_CODE
            },
            "id": node_id(),
            "name": "Format Telegram Alert",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [1980, 200],
            "notes": "Builds the institutional alert message with Entry/SL/TP1/TP2/TP3/Confluence/AI reasoning/Trade Invalidation/Manual Checklist."
        },
        # ─── Node 11: HTTP Request — Telegram sendMessage ────────────────
        {
            "parameters": {
                "url": "=https://api.telegram.org/bot{{$env.TELEGRAM_BOT_TOKEN}}/sendMessage",
                "method": "POST",
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ JSON.stringify($json) }}",
                "options": {"timeout": 15000}
            },
            "id": node_id(),
            "name": "Send Telegram Alert",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.1,
            "position": [2200, 200],
            "notes": "Sends alert to Telegram. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as environment variables in n8n (Settings > Variables)."
        },
        # ─── Node 12: NoOp — Continue Loop ───────────────────────────────
        {
            "parameters": {},
            "id": node_id(),
            "name": "Skip - No Alert",
            "type": "n8n-nodes-base.noOp",
            "typeVersion": 1,
            "position": [1980, 400],
            "notes": "Setup was filtered (HOLD/WATCHLIST/REJECT) — no alert sent."
        },
    ],
    "connections": {
        "Every Minute": {
            "main": [[{"node": "Get All Tickers", "type": "main", "index": 0}]]
        },
        "Get All Tickers": {
            "main": [[{"node": "Stage 1: Filter Top 30", "type": "main", "index": 0}]]
        },
        "Stage 1: Filter Top 30": {
            "main": [[{"node": "Stage 2: Deep Analysis", "type": "main", "index": 0}]]
        },
        "Stage 2: Deep Analysis": {
            "main": [[{"node": "Has Premium Setups?", "type": "main", "index": 0}]]
        },
        "Has Premium Setups?": {
            "main": [
                [{"node": "AI Validation Loop", "type": "main", "index": 0}],
                []
            ]
        },
        "AI Validation Loop": {
            "main": [
                [{"node": "OpenRouter AI", "type": "main", "index": 0}],
                []
            ]
        },
        "OpenRouter AI": {
            "main": [[{"node": "Parse AI + Safety Rules", "type": "main", "index": 0}]]
        },
        "Parse AI + Safety Rules": {
            "main": [[{"node": "AI Approved BUY/SELL?", "type": "main", "index": 0}]]
        },
        "AI Approved BUY/SELL?": {
            "main": [
                [{"node": "Format Telegram Alert", "type": "main", "index": 0}],
                [{"node": "Skip - No Alert", "type": "main", "index": 0}]
            ]
        },
        "Format Telegram Alert": {
            "main": [[{"node": "Send Telegram Alert", "type": "main", "index": 0}]]
        },
        "Send Telegram Alert": {
            "main": [[{"node": "AI Validation Loop", "type": "main", "index": 0}]]
        },
        "Skip - No Alert": {
            "main": [[{"node": "AI Validation Loop", "type": "main", "index": 0}]]
        },
    },
    "active": False,
    "settings": {"executionOrder": "v1"},
    "versionId": str(uuid.uuid4()),
    "meta": {
        "instanceId": str(uuid.uuid4()),
        "templateCredsSetupCompleted": False
    },
    "tags": [
        {"name": "crypto"},
        {"name": "binance"},
        {"name": "futures"},
        {"name": "institutional"}
    ],
    "pinData": {},
}

# Write the workflow JSON
OUTPUT.write_text(json.dumps(workflow, indent=2, ensure_ascii=False))
print(f"✅ n8n workflow JSON written to: {OUTPUT}")
print(f"   File size: {OUTPUT.stat().st_size:,} bytes")
print(f"   Nodes: {len(workflow['nodes'])}")
print(f"   Connections: {len(workflow['connections'])}")
