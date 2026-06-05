/* ============================================================
   Mock data for Market Replay prototype
============================================================ */

// Seeded pseudo-random so data is deterministic across reloads
function mulberry32(seed) {
  return function () {
    let t = (seed += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function generateCandles({ symbol, start = 180, startPrice = 100, vol = 0.018, trend = 0.0008, seed = 42 }) {
  const rnd = mulberry32(seed);
  const out = [];
  let price = startPrice;
  const now = Math.floor(Date.now() / 1000);
  const dayS = 86400;
  // generate [start..0] daily candles, newest last
  for (let i = start - 1; i >= 0; i--) {
    const t = now - i * dayS;
    const drift = trend + (rnd() - 0.5) * vol;
    const open = price;
    const close = Math.max(0.01, open * (1 + drift));
    const wick = Math.abs(drift) + rnd() * vol * 0.8;
    const high = Math.max(open, close) * (1 + wick * 0.4);
    const low = Math.min(open, close) * (1 - wick * 0.4);
    const volume = Math.round(1e6 * (0.6 + rnd() * 0.9) * (1 + Math.abs(drift) * 20));
    out.push({ time: t, open, high, low, close, volume });
    price = close;
  }
  return out;
}

// ── Instruments & series ───────────────────────────────────
const SYMBOLS = [
  { symbol: "AAPL",  name: "Apple Inc.",                asset_type: "stock",  exchange: "NASDAQ", currency: "USD", price: 218.42, ch: 1.83,   chp: 0.85,  seed: 11, startPrice: 170, trend: 0.0018 },
  { symbol: "NVDA",  name: "NVIDIA Corporation",         asset_type: "stock",  exchange: "NASDAQ", currency: "USD", price: 886.20, ch: 24.70,  chp: 2.87,  seed: 22, startPrice: 480, trend: 0.0041 },
  { symbol: "TSLA",  name: "Tesla, Inc.",                asset_type: "stock",  exchange: "NASDAQ", currency: "USD", price: 247.81, ch: -3.22,  chp: -1.28, seed: 33, startPrice: 260, trend: -0.0004, vol: 0.028 },
  { symbol: "MSFT",  name: "Microsoft Corp.",             asset_type: "stock",  exchange: "NASDAQ", currency: "USD", price: 430.15, ch: 2.46,   chp: 0.58,  seed: 44, startPrice: 360, trend: 0.0014 },
  { symbol: "SPY",   name: "SPDR S&P 500 ETF",            asset_type: "etf",    exchange: "ARCA",   currency: "USD", price: 548.22, ch: 1.18,   chp: 0.22,  seed: 55, startPrice: 480, trend: 0.0008 },
  { symbol: "QQQ",   name: "Invesco QQQ Trust",           asset_type: "etf",    exchange: "NASDAQ", currency: "USD", price: 476.18, ch: 2.32,   chp: 0.49,  seed: 66, startPrice: 400, trend: 0.0011 },
  { symbol: "BTC/USD", name: "Bitcoin",                   asset_type: "crypto", exchange: "CRYPTO", currency: "USD", price: 68420.5, ch: 1210.2, chp: 1.80,  seed: 77, startPrice: 42000, trend: 0.0024, vol: 0.032 },
  { symbol: "ETH/USD", name: "Ethereum",                  asset_type: "crypto", exchange: "CRYPTO", currency: "USD", price: 3542.8, ch: -28.5,  chp: -0.80, seed: 88, startPrice: 2800, trend: 0.0012, vol: 0.036 },
  { symbol: "^GSPC", name: "S&P 500 Index",               asset_type: "index",  exchange: "SNP",    currency: "USD", price: 5488.18, ch: 8.26,  chp: 0.15,  seed: 99, startPrice: 4800, trend: 0.0008 },
];

const CANDLES = {};
for (const s of SYMBOLS) {
  CANDLES[s.symbol] = generateCandles({
    symbol: s.symbol,
    start: 220,
    startPrice: s.startPrice,
    vol: s.vol || 0.018,
    trend: s.trend,
    seed: s.seed,
  });
  // ensure the latest close matches the declared "current price" roughly
  const last = CANDLES[s.symbol][CANDLES[s.symbol].length - 1];
  const scale = s.price / last.close;
  CANDLES[s.symbol] = CANDLES[s.symbol].map((c) => ({
    ...c,
    open: c.open * scale,
    high: c.high * scale,
    low: c.low * scale,
    close: c.close * scale,
  }));
}

// ── Indicator helpers (client side) ────────────────────────
function sma(arr, n) {
  const out = new Array(arr.length).fill(null);
  let sum = 0;
  for (let i = 0; i < arr.length; i++) {
    sum += arr[i];
    if (i >= n) sum -= arr[i - n];
    if (i >= n - 1) out[i] = sum / n;
  }
  return out;
}
function ema(arr, n) {
  const out = new Array(arr.length).fill(null);
  const k = 2 / (n + 1);
  let prev = null;
  for (let i = 0; i < arr.length; i++) {
    if (i < n - 1) continue;
    if (prev === null) {
      let s = 0; for (let j = i - n + 1; j <= i; j++) s += arr[j];
      prev = s / n;
    } else {
      prev = arr[i] * k + prev * (1 - k);
    }
    out[i] = prev;
  }
  return out;
}
function rsi(arr, n = 14) {
  const out = new Array(arr.length).fill(null);
  let gains = 0, losses = 0;
  for (let i = 1; i < arr.length; i++) {
    const d = arr[i] - arr[i - 1];
    const g = Math.max(d, 0);
    const l = Math.max(-d, 0);
    if (i <= n) {
      gains += g; losses += l;
      if (i === n) {
        const ag = gains / n, al = losses / n;
        out[i] = al === 0 ? 100 : 100 - 100 / (1 + ag / al);
      }
    } else {
      const prevAg = (out._ag ?? 0);
      const prevAl = (out._al ?? 0);
      const ag = (prevAg * (n - 1) + g) / n;
      const al = (prevAl * (n - 1) + l) / n;
      out._ag = ag; out._al = al;
      out[i] = al === 0 ? 100 : 100 - 100 / (1 + ag / al);
    }
  }
  // initialize rolling avgs from first computed value
  if (out[n] != null) {
    let ag = 0, al = 0;
    for (let i = 1; i <= n; i++) {
      const d = arr[i] - arr[i - 1];
      ag += Math.max(d, 0);
      al += Math.max(-d, 0);
    }
    ag /= n; al /= n;
    for (let i = n + 1; i < arr.length; i++) {
      const d = arr[i] - arr[i - 1];
      ag = (ag * (n - 1) + Math.max(d, 0)) / n;
      al = (al * (n - 1) + Math.max(-d, 0)) / n;
      out[i] = al === 0 ? 100 : 100 - 100 / (1 + ag / al);
    }
  }
  return out;
}
function macd(arr, fast = 12, slow = 26, signal = 9) {
  const ef = ema(arr, fast);
  const es = ema(arr, slow);
  const line = arr.map((_, i) => (ef[i] != null && es[i] != null ? ef[i] - es[i] : null));
  const validLine = line.filter((v) => v != null);
  const sigFiltered = ema(validLine, signal);
  const sig = new Array(arr.length).fill(null);
  let j = 0;
  for (let i = 0; i < arr.length; i++) {
    if (line[i] != null) {
      sig[i] = sigFiltered[j];
      j++;
    }
  }
  const hist = line.map((v, i) => (v != null && sig[i] != null ? v - sig[i] : null));
  return { line, signal: sig, hist };
}

// ── Watchlist, notes, alerts, backtests ────────────────────
const WATCHLIST = [
  "AAPL", "NVDA", "TSLA", "MSFT", "SPY", "QQQ", "BTC/USD", "ETH/USD", "^GSPC",
].map((sym, i) => {
  const m = SYMBOLS.find((s) => s.symbol === sym);
  return { id: i + 1, symbol: sym, name: m.name, asset_type: m.asset_type, created_at: "2026-03-10" };
});

const REPLAY_NOTES = [
  {
    id: 1, symbol: "AAPL", timeframe: "1day",
    replay_start_time: "2025-11-04T00:00:00Z",
    note_time: "2026-01-17T00:00:00Z",
    trend_view: "短期向上，MA20 上穿 MA60 形成金叉，量能温和放大。",
    support_level: 212.5, resistance_level: 224.0,
    entry_idea: "回踩 MA20 附近布多，止损放 MA60 下方。",
    stop_loss: 208.0, take_profit: 232.0,
    summary: "结构完好，等待回踩。",
    created_at: "2026-01-17T10:12:00Z", updated_at: "2026-01-17T10:12:00Z",
  },
  {
    id: 2, symbol: "BTC/USD", timeframe: "4h",
    replay_start_time: "2026-02-01T00:00:00Z",
    note_time: "2026-03-22T00:00:00Z",
    trend_view: "4h 级别冲高回落，MACD 顶背离。",
    support_level: 63800, resistance_level: 71200,
    entry_idea: "破位 64000 考虑轻仓试空。",
    stop_loss: 72500, take_profit: 59000,
    summary: "关注 MA60 是否失守。",
    created_at: "2026-03-22T08:04:00Z", updated_at: "2026-03-22T08:04:00Z",
  },
  {
    id: 3, symbol: "NVDA", timeframe: "1day",
    replay_start_time: "2025-12-15T00:00:00Z",
    note_time: "2026-04-02T00:00:00Z",
    trend_view: "中线趋势强劲，每次回踩 MA20 均被买入。",
    support_level: 820, resistance_level: 920,
    entry_idea: "分批建仓，ATR 1.5 倍止损。",
    stop_loss: 788, take_profit: 980,
    summary: "强势股，坚持持有。",
    created_at: "2026-04-02T14:22:00Z", updated_at: "2026-04-02T14:22:00Z",
  },
  {
    id: 4, symbol: "TSLA", timeframe: "1h",
    replay_start_time: "2026-03-25T00:00:00Z",
    note_time: "2026-04-11T00:00:00Z",
    trend_view: "1h 箱体震荡，上沿 252 下沿 240。",
    support_level: 240, resistance_level: 252,
    entry_idea: "箱体下沿试多，破下沿止损。",
    stop_loss: 238.5, take_profit: 258,
    summary: "等待突破方向。",
    created_at: "2026-04-11T09:48:00Z", updated_at: "2026-04-11T09:48:00Z",
  },
];

const ALERTS = [
  { id: 1, name: "AAPL 突破前高",    symbol: "AAPL",    condition_type: "price_above", threshold: 225.0,  timeframe: "1day", discord_webhook_url: "https://discord.com/api/webhooks/****/****", is_active: true,  is_triggered: false, triggered_at: null, created_at: "2026-04-01T02:10:00Z" },
  { id: 2, name: "BTC 跌破 MA60",    symbol: "BTC/USD", condition_type: "ma_cross_down", threshold: 60.0,  timeframe: "4h",   discord_webhook_url: "https://discord.com/api/webhooks/****/****", is_active: true,  is_triggered: true,  triggered_at: "2026-04-14T22:03:00Z", created_at: "2026-03-28T09:00:00Z" },
  { id: 3, name: "NVDA RSI 超买",    symbol: "NVDA",    condition_type: "rsi_above",     threshold: 78.0,  timeframe: "1day", discord_webhook_url: "https://discord.com/api/webhooks/****/****", is_active: true,  is_triggered: false, triggered_at: null, created_at: "2026-03-15T14:00:00Z" },
  { id: 4, name: "SPY 跌破支撑",     symbol: "SPY",     condition_type: "price_below",   threshold: 520.0, timeframe: "1day", discord_webhook_url: "https://discord.com/api/webhooks/****/****", is_active: false, is_triggered: false, triggered_at: null, created_at: "2026-02-22T12:18:00Z" },
  { id: 5, name: "ETH 金叉",         symbol: "ETH/USD", condition_type: "ma_cross_up",   threshold: 0.0,   timeframe: "1day", discord_webhook_url: "https://discord.com/api/webhooks/****/****", is_active: true,  is_triggered: false, triggered_at: null, created_at: "2026-04-10T03:30:00Z" },
];

// Synthetic backtest result for MA cross on NVDA
function buildBacktest(symbolKey = "NVDA", strategy = "ma_cross") {
  const candles = CANDLES[symbolKey];
  const closes = candles.map((c) => c.close);
  const fast = sma(closes, 5);
  const slow = sma(closes, 20);
  const trades = [];
  let pos = 0, entry = 0, entryT = 0;
  const equity = [];
  let capital = 10000;
  for (let i = 1; i < closes.length; i++) {
    if (fast[i] == null || slow[i] == null || fast[i - 1] == null || slow[i - 1] == null) {
      equity.push({ time: candles[i].time, value: capital + pos * closes[i] });
      continue;
    }
    const pv = fast[i - 1] - slow[i - 1];
    const cv = fast[i] - slow[i];
    if (pv <= 0 && cv > 0 && pos === 0) {
      pos = capital / closes[i];
      entry = closes[i]; entryT = candles[i].time; capital = 0;
    } else if (pv >= 0 && cv < 0 && pos > 0) {
      capital = pos * closes[i];
      trades.push({
        entry_time: entryT, entry_price: entry,
        exit_time: candles[i].time, exit_price: closes[i],
        pnl_pct: ((closes[i] - entry) / entry) * 100,
      });
      pos = 0;
    }
    equity.push({ time: candles[i].time, value: capital + pos * closes[i] });
  }
  if (pos > 0) {
    const last = closes[closes.length - 1];
    capital = pos * last;
    trades.push({
      entry_time: entryT, entry_price: entry,
      exit_time: candles[candles.length - 1].time, exit_price: last,
      pnl_pct: ((last - entry) / entry) * 100,
    });
  }
  const initial = 10000;
  const final = capital;
  const totalRet = ((final - initial) / initial) * 100;
  const days = (equity[equity.length - 1].time - equity[0].time) / 86400;
  const ann = (Math.pow(1 + totalRet / 100, 365 / Math.max(days, 1)) - 1) * 100;
  let peak = equity[0].value, dd = 0;
  for (const e of equity) { peak = Math.max(peak, e.value); dd = Math.max(dd, (peak - e.value) / peak * 100); }
  const rets = [];
  for (let i = 1; i < equity.length; i++) {
    if (equity[i - 1].value > 0) rets.push((equity[i].value - equity[i - 1].value) / equity[i - 1].value);
  }
  const mean = rets.reduce((a, b) => a + b, 0) / rets.length;
  const sd = Math.sqrt(rets.reduce((a, b) => a + (b - mean) ** 2, 0) / (rets.length - 1));
  const sharpe = sd === 0 ? 0 : (mean / sd) * Math.sqrt(252);
  const winning = trades.filter((t) => t.pnl_pct >= 0);
  const bh = ((closes[closes.length - 1] - closes[0]) / closes[0]) * 100;
  return {
    symbol: symbolKey, timeframe: "1day", strategy,
    start_date: new Date(candles[0].time * 1000).toISOString().slice(0, 10),
    end_date: new Date(candles[candles.length - 1].time * 1000).toISOString().slice(0, 10),
    initial_capital: initial,
    total_return_pct: totalRet, annualized_return_pct: ann,
    max_drawdown_pct: dd, sharpe_ratio: sharpe,
    win_rate_pct: trades.length ? (winning.length / trades.length) * 100 : 0,
    total_trades: trades.length, winning_trades: winning.length, losing_trades: trades.length - winning.length,
    buy_hold_return_pct: bh,
    equity_curve: equity, trades,
  };
}

const BACKTEST = buildBacktest("NVDA", "ma_cross");

// Expose
window.MR = {
  SYMBOLS, CANDLES, WATCHLIST, REPLAY_NOTES, ALERTS, BACKTEST,
  sma, ema, rsi, macd, buildBacktest,
};
