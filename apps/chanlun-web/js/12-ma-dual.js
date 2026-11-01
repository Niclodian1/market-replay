// js/12-ma-dual.js — MA20/MA60 双均线突破: 金叉买 / 死叉卖, 信号 K 线闪烁 + 盈亏统计
// 口径: 收盘确认, 次日执行; 交易对 = 金叉当日收盘 → 死叉当日收盘(可换离场规则)。
"use strict";

const MA_DUAL = window.MA_DUAL = {
  on: false,
  fast: 20,
  slow: 60,
  exit: 'death',        // death(死叉) | fast(跌破快线) | slow(跌破慢线)
  blink: false,         // 持续闪烁(默认只在打开/改参数后闪 6 秒)
  sigs: [],             // [{i, side:'buy'|'sell'}]
  trades: [],           // [{e,x,ret,days}]
  stats: null,
  _prim: null,
  _built: false,
  _timer: null,
  _until: 0,
};

const MD_COL = { buy: '#16a34a', sell: '#dc2626' };

function mdSma(x, p) {
  const out = new Array(x.length).fill(null);
  let run = 0;
  for (let i = 0; i < x.length; i++) {
    run += x[i];
    if (i >= p) run -= x[i - p];
    if (i >= p - 1) out[i] = run / p;
  }
  return out;
}

function maDualCompute() {
  const cs = candlesFor(activeView);
  const C = cs.map(c => c.close);
  const n = C.length;
  const f = Math.max(2, Math.min(120, MA_DUAL.fast | 0));
  const s = Math.max(f + 1, Math.min(300, MA_DUAL.slow | 0));
  const F = mdSma(C, f), S = mdSma(C, s);
  const sigs = [];
  for (let i = 1; i < n; i++) {
    if (!F[i] || !S[i] || !F[i - 1] || !S[i - 1]) continue;
    if (F[i - 1] <= S[i - 1] && F[i] > S[i]) sigs.push({ i, side: 'buy' });
    else if (F[i - 1] >= S[i - 1] && F[i] < S[i]) sigs.push({ i, side: 'sell' });
  }
  // 配对成交易
  const exitAt = i => {
    if (MA_DUAL.exit === 'fast') return C[i] < F[i];
    if (MA_DUAL.exit === 'slow') return C[i] < S[i];
    return false;
  };
  const trades = [];
  let open = null;
  for (const g of sigs) {
    if (g.side === 'buy' && open == null) { open = g.i; continue; }
    if (open == null) continue;
    const isDeath = g.side === 'sell';
    if (isDeath || exitAt(g.i)) {
      const x = g.i;
      trades.push({ e: open, x, ret: C[x] / C[open] - 1, days: x - open });
      open = null;
    }
  }
  if (open != null) trades.push({ e: open, x: n - 1, ret: C[n - 1] / C[open] - 1, days: n - 1 - open, open: true });

  const r = trades.map(t => t.ret);
  const win = r.filter(v => v > 0), loss = r.filter(v => v <= 0);
  const gross = win.reduce((a, b) => a + b, 0), bad = Math.abs(loss.reduce((a, b) => a + b, 0));
  const srt = [...r].sort((a, b) => a - b);
  MA_DUAL.sigs = sigs;
  MA_DUAL.trades = trades;
  MA_DUAL.stats = {
    n: trades.length,
    winRate: r.length ? win.length / r.length : 0,
    mean: r.length ? r.reduce((a, b) => a + b, 0) / r.length : 0,
    median: srt.length ? srt[Math.floor(srt.length / 2)] : 0,
    avgWin: win.length ? gross / win.length : 0,
    avgLoss: loss.length ? loss.reduce((a, b) => a + b, 0) / loss.length : 0,
    pf: bad > 0 ? gross / bad : Infinity,
    avgDays: trades.length ? trades.reduce((a, t) => a + t.days, 0) / trades.length : 0,
    comp: trades.reduce((a, t) => a * (1 + t.ret), 1),
    buys: sigs.filter(g => g.side === 'buy').length,
    sells: sigs.filter(g => g.side === 'sell').length,
  };
}

// ------------------------------------------------------------------ 绘制 -- //
class MaDualRenderer {
  constructor() { this._r = null; }
  draw(target) {
    if (!MA_DUAL.on || !MA_DUAL.sigs.length) return;
    const cs = candlesFor(activeView);
    const n = cs.length;
    const vr = chart.timeScale().getVisibleLogicalRange();
    let i0 = 1, i1 = n - 1;
    if (vr) { i0 = Math.max(1, Math.floor(vr.from) - 2); i1 = Math.min(n - 1, Math.ceil(vr.to) + 2); }
    if (i1 <= i0) return;
    const now = performance.now();
    const pulsing = MA_DUAL._until > now;
    const alpha = pulsing ? 0.35 + 0.6 * Math.abs(Math.sin(now / 260)) : 0.75;
    const bs = chart.timeScale().options().barSpacing || 6;
    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context;
      for (const g of MA_DUAL.sigs) {
        if (g.i < i0 || g.i > i1) continue;
        const c = cs[g.i];
        const x = timeToX(c.time);
        const yH = candleSeries.priceToCoordinate(c.high);
        const yL = candleSeries.priceToCoordinate(c.low);
        if (x == null || yH == null || yL == null) continue;
        const w = Math.max(5, Math.min(26, bs * 0.9));
        const pad = 5;
        const top = Math.min(yH, yL) - pad, bot = Math.max(yH, yL) + pad;
        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.strokeStyle = g.side === 'buy' ? MD_COL.buy : MD_COL.sell;
        ctx.lineWidth = 2;
        ctx.shadowColor = ctx.strokeStyle;
        ctx.shadowBlur = 8;
        ctx.beginPath();
        const rr = 3, x0 = x - w / 2, x1 = x + w / 2;
        ctx.moveTo(x0 + rr, top);
        ctx.lineTo(x1 - rr, top); ctx.quadraticCurveTo(x1, top, x1, top + rr);
        ctx.lineTo(x1, bot - rr); ctx.quadraticCurveTo(x1, bot, x1 - rr, bot);
        ctx.lineTo(x0 + rr, bot); ctx.quadraticCurveTo(x0, bot, x0, bot - rr);
        ctx.lineTo(x0, top + rr); ctx.quadraticCurveTo(x0, top, x0 + rr, top);
        ctx.stroke();
        // 三角: 买在下方/卖在上方
        const up = g.side === 'buy';
        const ty = up ? bot + 10 : top - 10;
        ctx.globalAlpha = Math.min(1, alpha + 0.15);
        ctx.fillStyle = ctx.strokeStyle;
        ctx.beginPath();
        ctx.moveTo(x, up ? ty - 8 : ty + 8);
        ctx.lineTo(x - 5, ty); ctx.lineTo(x + 5, ty);
        ctx.closePath(); ctx.fill();
        ctx.restore();
      }
    });
  }
}
class MaDualView {
  constructor(prim) { this._prim = prim; this._r = new MaDualRenderer(); }
  zOrder() { return 'top'; }
  update() {}
  renderer() { return this._r; }
}
class MaDualPrimitive {
  constructor(series) { this._series = series; this._view = new MaDualView(this); this._req = null; }
  attached(p) { this._req = p.requestUpdate; }
  detached() { this._req = null; }
  updateAllViews() {}
  paneViews() { return [this._view]; }
  requestUpdate() { if (this._req) this._req(); }
}

function maDualDraw() { if (MA_DUAL._prim) MA_DUAL._prim.requestUpdate(); }

// 闪烁: 低帧率驱动重绘, 避免 60fps 全图重画把 CPU 打满
function maDualBlinkStart(ms) {
  MA_DUAL._until = performance.now() + (ms || 6000);
  if (MA_DUAL._timer) return;
  MA_DUAL._timer = setInterval(() => {
    const active = MA_DUAL.on && (MA_DUAL.blink || performance.now() < MA_DUAL._until);
    if (!active) { clearInterval(MA_DUAL._timer); MA_DUAL._timer = null; maDualDraw(); return; }
    maDualDraw();
  }, 110);
}

// ------------------------------------------------------------------ 面板 -- //
function maDualPanelBuild() {
  if (MA_DUAL._built) return;
  const el = document.createElement('div');
  el.id = 'dualPanel';
  el.className = 'lab-panel dual-panel';
  el.style.display = 'none';
  el.innerHTML = `
    <div class="lab-head">
      <span class="lab-title">双均线 20/60</span>
      <span class="lab-badge" id="dualBadge">—</span>
      <button class="lab-btn" id="dualClose" title="收起">×</button>
    </div>
    <div class="lab-body">
      <div class="lab-row">
        <span class="lab-lab">快线</span>
        <input class="lab-rng" type="range" id="dualFast" min="5" max="60" step="1" value="${MA_DUAL.fast}">
        <span class="lab-val" id="dualFastV">MA${MA_DUAL.fast}</span>
      </div>
      <div class="lab-row">
        <span class="lab-lab">慢线</span>
        <input class="lab-rng" type="range" id="dualSlow" min="20" max="200" step="1" value="${MA_DUAL.slow}">
        <span class="lab-val" id="dualSlowV">MA${MA_DUAL.slow}</span>
      </div>
      <div class="lab-row">
        <span class="lab-lab">离场</span>
        <select class="lab-sel" id="dualExit">
          <option value="death">死叉离场（金叉↔死叉配对）</option>
          <option value="fast">跌破快线</option>
          <option value="slow">跌破慢线</option>
        </select>
      </div>
      <div class="lab-row">
        <label style="display:flex;align-items:center;gap:4px;color:var(--muted)">
          <input type="checkbox" id="dualBlink"${MA_DUAL.blink ? ' checked' : ''}>信号 K 线闪烁
        </label>
        <span style="flex:1"></span>
        <button class="lab-btn" id="dualReplay" title="再闪一次">重播</button>
        <button class="lab-btn" id="dualPreset" title="回到 20/60 默认">默认</button>
      </div>
      <div class="lab-sec">盈亏统计（日线全样本，收盘价进出）</div>
      <div id="dualStats" class="trend-stats"></div>
      <div class="lab-note">绿框 = 金叉（买），红框 = 死叉（卖）。口径：收盘确认、次日执行，不含手续费。</div>
    </div>`;
  document.body.appendChild(el);
  MA_DUAL._built = true;

  const refresh = () => { maDualCompute(); maDualStatsRender(); maDualBlinkStart(5000); };
  el.querySelector('#dualFast').addEventListener('input', e => {
    MA_DUAL.fast = +e.target.value;
    if (MA_DUAL.fast >= MA_DUAL.slow) { MA_DUAL.slow = MA_DUAL.fast + 1; el.querySelector('#dualSlow').value = MA_DUAL.slow; el.querySelector('#dualSlowV').textContent = 'MA' + MA_DUAL.slow; }
    el.querySelector('#dualFastV').textContent = 'MA' + MA_DUAL.fast;
    refresh();
  });
  el.querySelector('#dualSlow').addEventListener('input', e => {
    MA_DUAL.slow = Math.max(+e.target.value, MA_DUAL.fast + 1);
    el.querySelector('#dualSlowV').textContent = 'MA' + MA_DUAL.slow;
    refresh();
  });
  el.querySelector('#dualExit').addEventListener('change', e => { MA_DUAL.exit = e.target.value; refresh(); });
  el.querySelector('#dualBlink').addEventListener('change', e => { MA_DUAL.blink = e.target.checked; if (MA_DUAL.blink) maDualBlinkStart(); else maDualDraw(); });
  el.querySelector('#dualReplay').addEventListener('click', () => maDualBlinkStart(6000));
  el.querySelector('#dualClose').addEventListener('click', () => { el.style.display = 'none'; });
  el.querySelector('#dualPreset').addEventListener('click', () => {
    MA_DUAL.fast = 20; MA_DUAL.slow = 60; MA_DUAL.exit = 'death'; MA_DUAL.blink = true;
    el.querySelector('#dualFast').value = 20; el.querySelector('#dualFastV').textContent = 'MA20';
    el.querySelector('#dualSlow').value = 60; el.querySelector('#dualSlowV').textContent = 'MA60';
    el.querySelector('#dualExit').value = 'death'; el.querySelector('#dualBlink').checked = true;
    refresh();
  });
}

function maDualStatsRender() {
  const box = document.getElementById('dualStats');
  const b = document.getElementById('dualBadge');
  const s = MA_DUAL.stats;
  if (!box || !s) return;
  const pct = v => (v * 100).toFixed(0) + '%';
  box.innerHTML = `
    <div class="trend-stat"><span>交易笔数</span><b>${s.n} <i>(金叉${s.buys}/死叉${s.sells})</i></b></div>
    <div class="trend-stat"><span>胜率</span><b>${pct(s.winRate)}</b></div>
    <div class="trend-stat"><span>盈亏比</span><b>${s.pf === Infinity ? '∞' : s.pf.toFixed(2)}</b></div>
    <div class="trend-stat"><span>平均 / 中位</span><b>${s.mean >= 0 ? '+' : ''}${pct(s.mean)} / ${s.median >= 0 ? '+' : ''}${pct(s.median)}</b></div>
    <div class="trend-stat"><span>平均盈 / 平均亏</span><b>+${pct(s.avgWin)} / ${pct(s.avgLoss)}</b></div>
    <div class="trend-stat"><span>平均持仓</span><b>${s.avgDays.toFixed(0)} 天</b></div>
    <div class="trend-stat"><span>累计(复利)</span><b>${s.comp.toFixed(1)}x</b></div>`;
  if (b) b.textContent = `MA${MA_DUAL.fast}/${MA_DUAL.slow} · ${s.n}笔 · 胜率${pct(s.winRate)}`;
}

function maDualRefresh() {
  if (!MA_DUAL._built) return;
  maDualCompute(); maDualStatsRender(); maDualDraw();
}

function maDualInit() {
  if (MA_DUAL._prim) return;
  MA_DUAL._prim = new MaDualPrimitive(candleSeries);
  candleSeries.attachPrimitive(MA_DUAL._prim);
  maDualPanelBuild();
  maDualCompute();
  maDualStatsRender();
}

(function () {
  const btn = document.getElementById('maDualBtn');
  if (!btn) return;
  btn.addEventListener('click', () => {
    const el = document.getElementById('dualPanel');
    if (!el) return;
    MA_DUAL.on = !MA_DUAL.on;
    btn.classList.toggle('active', MA_DUAL.on);
    el.style.display = MA_DUAL.on ? '' : 'none';
    if (MA_DUAL.on) {
      maDualCompute(); maDualStatsRender();
      if (window.innerWidth > 760) {
        const r = btn.getBoundingClientRect();
        const w = el.offsetWidth || 326;
        el.style.left = Math.max(6, Math.min(r.left, window.innerWidth - w - 8)) + 'px';
        el.style.top = (r.bottom + 6) + 'px';
      }
      maDualBlinkStart(6000);
    } else {
      if (MA_DUAL._timer) { clearInterval(MA_DUAL._timer); MA_DUAL._timer = null; }
    }
    maDualDraw();
  });
  maDualInit();
})();
