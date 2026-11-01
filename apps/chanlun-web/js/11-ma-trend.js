// js/11-ma-trend.js — 日线 MA 突破 × 斜率：持仓区间高亮 + 买卖点标记 + 牛市段覆盖统计
// 规则(全部用收盘价, 收盘确认、次日执行, 无未来函数):
//   持仓 = 收盘 > MA(p) 且 MA(p) 的 w 根斜率 > 阈值
//   斜率 = (MA[i]-MA[i-w]) / w / MA[i-w] * 100  (%/根)
// 面板拖滑块改 p / w / 阈值, 图上实时重画; 并统计"覆盖了多少段 ≥30% 的牛市行情"。
"use strict";

const MA_TREND = window.MA_TREND = {
  on: false,
  p: 50,            // 均线周期
  w: 5,             // 斜率窗口(根)
  thr: -0.15,       // 斜率阈值 %/根
  shade: true,      // 持仓区间底色
  n: 0,             // 数据根数
  pos: null,        // 持仓序列
  entries: [],      // 入场下标
  exits: [],
  legs: [],         // 牛市段 [{i0,i1,gain,cap}]
  stats: null,
  _prim: null,
  _built: false,
};

const MT_COL = { entry: '#16a34a', exit: '#dc2626', band: 'rgba(37,99,235,0.075)' };

// ------------------------------------------------------------------ 计算 -- //
function mtSma(x, p) {
  const out = new Array(x.length).fill(null);
  let run = 0;
  for (let i = 0; i < x.length; i++) {
    run += x[i];
    if (i >= p) run -= x[i - p];
    if (i >= p - 1) out[i] = run / p;
  }
  return out;
}

// 收盘价反转 20% 的 zigzag, 只留涨幅 ≥30% 的上升段(与研究报告同一口径)
function mtZigzag(c, thr) {
  const legs = [];
  let dirn = 0, lastI = 0, lastP = c[0], extI = 0, extP = c[0];
  for (let i = 1; i < c.length; i++) {
    const p = c[i];
    if (dirn === 0) {
      if (p <= extP) { extI = i; extP = p; }
      else if (p >= extP * (1 + thr)) { legs.push(['down', lastI, extI, lastP, extP]); dirn = 1; lastI = extI; lastP = extP; extI = i; extP = p; }
    } else if (dirn === 1) {
      if (p >= extP) { extI = i; extP = p; }
      else if (p <= extP * (1 - thr)) { legs.push(['up', lastI, extI, lastP, extP]); dirn = -1; lastI = extI; lastP = extP; extI = i; extP = p; }
    } else {
      if (p <= extP) { extI = i; extP = p; }
      else if (p >= extP * (1 + thr)) { legs.push(['down', lastI, extI, lastP, extP]); dirn = 1; lastI = extI; lastP = extP; extI = i; extP = p; }
    }
  }
  legs.push([dirn === 1 ? 'up' : 'down', lastI, extI, lastP, extP]);
  return legs.filter(l => l[0] === 'up' && l[4] / l[3] - 1 >= 0.30);
}

function maTrendCompute() {
  const cs = candlesFor(activeView);
  const C = cs.map(c => c.close);
  const n = C.length;
  const p = Math.max(2, Math.min(300, MA_TREND.p | 0));
  const w = Math.max(1, Math.min(30, MA_TREND.w | 0));
  const ma = mtSma(C, p);
  const sl = new Array(n).fill(null);
  for (let i = w; i < n; i++) {
    if (ma[i] == null || ma[i - w] == null || !ma[i - w]) continue;
    sl[i] = (ma[i] - ma[i - w]) / w / ma[i - w] * 100;
  }
  const pos = new Uint8Array(n);
  const entries = [], exits = [];
  for (let i = 1; i < n; i++) {
    const ok = ma[i] != null && sl[i] != null && C[i] > ma[i] && sl[i] > MA_TREND.thr;
    if (ok) { pos[i] = 1; if (!pos[i - 1]) entries.push(i); }
    else if (pos[i - 1]) exits.push(i);
  }
  const legs = mtZigzag(C, 0.20).map(([_, i0, i1, p0, p1]) => {
    const leg = Math.log(p1 / p0);
    let got = 0;
    for (let i = i0 + 1; i <= i1; i++) if (pos[i - 1]) got += Math.log(C[i] / C[i - 1]);
    return { i0, i1, gain: p1 / p0 - 1, cap: leg > 0 ? Math.max(0, got) / leg : 0 };
  });
  const caps = legs.map(l => l.cap).sort((a, b) => a - b);
  const expo = pos.reduce((s, v) => s + v, 0) / n;
  MA_TREND.n = n; MA_TREND.pos = pos; MA_TREND.entries = entries; MA_TREND.exits = exits;
  MA_TREND.legs = legs;
  MA_TREND.stats = {
    entries: entries.length,
    expo,
    n50: caps.filter(c => c >= 0.5).length,
    n30: caps.filter(c => c >= 0.3).length,
    median: caps.length ? caps[Math.floor(caps.length / 2)] : 0,
    nLegs: legs.length,
    holding: pos[n - 1] === 1,
  };
}

// ------------------------------------------------------------------ 绘制 -- //
// 底色画在 K 线下面(zOrder 'bottom'), 买卖点画在上面
class MaTrendBandRenderer {
  draw(target) {
    if (!MA_TREND.on || !MA_TREND.pos || !MA_TREND.shade) return;
    const cs = candlesFor(activeView);
    const n = MA_TREND.n;
    const vr = chart.timeScale().getVisibleLogicalRange();
    let i0 = 1, i1 = n - 1;
    if (vr) { i0 = Math.max(1, Math.floor(vr.from) - 2); i1 = Math.min(n - 1, Math.ceil(vr.to) + 2); }
    if (i1 <= i0) return;
    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context;
      const H = scope.mediaSize.height;
      const pos = MA_TREND.pos;
      ctx.fillStyle = MT_COL.band;
      let i = i0;
      while (i <= i1) {
        if (!pos[i]) { i++; continue; }
        let j = i; while (j + 1 <= i1 && pos[j + 1]) j++;
        const x0 = timeToX(cs[i].time), x1 = timeToX(cs[j].time);
        if (x0 != null && x1 != null) ctx.fillRect(x0, 0, Math.max(1, x1 - x0), H);
        i = j + 1;
      }
    });
  }
}

class MaTrendMarkRenderer {
  draw(target) {
    if (!MA_TREND.on || !MA_TREND.pos) return;
    const cs = candlesFor(activeView);
    const n = MA_TREND.n;
    const vr = chart.timeScale().getVisibleLogicalRange();
    let i0 = 1, i1 = n - 1;
    if (vr) { i0 = Math.max(1, Math.floor(vr.from) - 2); i1 = Math.min(n - 1, Math.ceil(vr.to) + 2); }
    if (i1 <= i0) return;
    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context;
      const mark = (idx, up) => {
        if (idx < 1 || idx >= n) return;
        const x = timeToX(cs[idx].time);
        const y = candleSeries.priceToCoordinate(cs[idx][up ? 'low' : 'high']);
        if (x == null || y == null) return;
        const base = y + (up ? 9 : -9);
        ctx.fillStyle = up ? MT_COL.entry : MT_COL.exit;
        ctx.beginPath();
        if (up) { ctx.moveTo(x, base - 8); ctx.lineTo(x - 5, base); ctx.lineTo(x + 5, base); }
        else { ctx.moveTo(x, base + 8); ctx.lineTo(x - 5, base); ctx.lineTo(x + 5, base); }
        ctx.closePath();
        ctx.fill();
      };
      for (const i of MA_TREND.entries) if (i >= i0 && i <= i1) mark(i, true);
      for (const i of MA_TREND.exits) if (i >= i0 && i <= i1) mark(i, false);
    });
  }
}

class MaTrendBandView {
  constructor(prim) { this._prim = prim; this._r = new MaTrendBandRenderer(); }
  zOrder() { return 'bottom'; }
  update() {}
  renderer() { return this._r; }
}
class MaTrendMarkView {
  constructor(prim) { this._prim = prim; this._r = new MaTrendMarkRenderer(); }
  zOrder() { return 'top'; }
  update() {}
  renderer() { return this._r; }
}
class MaTrendPrimitive {
  constructor(series) {
    this._series = series;
    this._band = new MaTrendBandView(this);
    this._mark = new MaTrendMarkView(this);
    this._req = null;
  }
  attached(p) { this._req = p.requestUpdate; }
  detached() { this._req = null; }
  updateAllViews() {}
  paneViews() { return [this._band, this._mark]; }
  requestUpdate() { if (this._req) this._req(); }
}

function maTrendDraw() { if (MA_TREND._prim) MA_TREND._prim.requestUpdate(); }

// 数据/周期/视图/复盘位置变化后重算(由 rebuildMA 的钩子调用)
function maTrendRefresh() {
  if (!MA_TREND._built) return;
  maTrendCompute();
  maTrendStatsRender();
  maTrendDraw();
}

// ------------------------------------------------------------------ 面板 -- //
function maTrendPanelBuild() {
  if (MA_TREND._built) return;
  const el = document.createElement('div');
  el.id = 'trendPanel';
  el.className = 'lab-panel trend-panel';
  el.style.display = 'none';
  el.innerHTML = `
    <div class="lab-head">
      <span class="lab-title">MA 突破 × 斜率</span>
      <span class="lab-badge" id="trendBadge">—</span>
      <button class="lab-btn" id="trendClose" title="收起">×</button>
    </div>
    <div class="lab-body">
      <div class="lab-row">
        <span class="lab-lab">均线</span>
        <input class="lab-rng" type="range" id="trendP" min="10" max="200" step="1" value="${MA_TREND.p}">
        <span class="lab-val" id="trendPV">MA${MA_TREND.p}</span>
      </div>
      <div class="lab-row">
        <span class="lab-lab">斜率窗口</span>
        <input class="lab-rng" type="range" id="trendW" min="1" max="20" step="1" value="${MA_TREND.w}">
        <span class="lab-val" id="trendWV">${MA_TREND.w} 根</span>
      </div>
      <div class="lab-row">
        <span class="lab-lab">斜率阈值</span>
        <input class="lab-rng" type="range" id="trendThr" min="-0.6" max="0.4" step="0.02" value="${MA_TREND.thr}">
        <span class="lab-val" id="trendThrV">${MA_TREND.thr.toFixed(2)}</span>
      </div>
      <div class="lab-row">
        <label style="display:flex;align-items:center;gap:4px;color:var(--muted)">
          <input type="checkbox" id="trendShade"${MA_TREND.shade ? ' checked' : ''}>持仓区间底色
        </label>
        <span style="flex:1"></span>
        <button class="lab-btn" id="trendPreset" title="回到研究报告里的默认口径">默认</button>
      </div>
      <div class="lab-sec">统计（日线全样本）</div>
      <div id="trendStats" class="trend-stats"></div>
      <div class="lab-note">▲ 收盘站上均线且斜率达标；▼ 跌破均线或斜率转负。<br>
      口径同研究报告：收盘确认，次日执行，无未来函数。</div>
    </div>`;
  document.body.appendChild(el);
  MA_TREND._built = true;

  const bind = (id, key, fmt, isInt) => {
    const inp = el.querySelector(id);
    inp.addEventListener('input', e => {
      MA_TREND[key] = isInt ? +e.target.value : +(+e.target.value).toFixed(2);
      el.querySelector(id + 'V').textContent = fmt(MA_TREND[key]);
      maTrendCompute(); maTrendStatsRender(); maTrendDraw();
    });
  };
  bind('#trendP', 'p', v => 'MA' + v, true);
  bind('#trendW', 'w', v => v + ' 根', true);
  bind('#trendThr', 'thr', v => (v >= 0 ? '+' : '') + v.toFixed(2));
  el.querySelector('#trendShade').addEventListener('change', e => { MA_TREND.shade = e.target.checked; maTrendDraw(); });
  el.querySelector('#trendClose').addEventListener('click', () => { el.style.display = 'none'; });
  el.querySelector('#trendPreset').addEventListener('click', () => {
    MA_TREND.p = 50; MA_TREND.w = 5; MA_TREND.thr = -0.15; MA_TREND.shade = true;
    el.querySelector('#trendP').value = 50; el.querySelector('#trendPV').textContent = 'MA50';
    el.querySelector('#trendW').value = 5; el.querySelector('#trendWV').textContent = '5 根';
    el.querySelector('#trendThr').value = -0.15; el.querySelector('#trendThrV').textContent = '-0.15';
    el.querySelector('#trendShade').checked = true;
    maTrendCompute(); maTrendStatsRender(); maTrendDraw();
  });
}

function maTrendStatsRender() {
  const box = document.getElementById('trendStats');
  const b = document.getElementById('trendBadge');
  const s = MA_TREND.stats;
  if (!box || !s) return;
  box.innerHTML = `
    <div class="trend-stat"><span>入场</span><b>${s.entries} 次</b></div>
    <div class="trend-stat"><span>持仓占比</span><b>${(s.expo * 100).toFixed(0)}%</b></div>
    <div class="trend-stat"><span>覆盖牛市段</span><b>${s.n50} / ${s.nLegs} <i>(捕获≥50%)</i></b></div>
    <div class="trend-stat"><span>捕获≥30%</span><b>${s.n30} / ${s.nLegs}</b></div>
    <div class="trend-stat"><span>分段捕获中位数</span><b>${(s.median * 100).toFixed(0)}%</b></div>
    <div class="trend-stat"><span>当前</span><b style="color:${s.holding ? MT_COL.entry : 'var(--muted)'}">${s.holding ? '持仓中' : '空仓'}</b></div>`;
  if (b) b.textContent = `MA${MA_TREND.p} · 斜率>${MA_TREND.thr.toFixed(2)} · ${s.n50}/${s.nLegs} 段`;
}

// ------------------------------------------------------------------ 启动 -- //
function maTrendInit() {
  if (MA_TREND._prim) return;
  MA_TREND._prim = new MaTrendPrimitive(candleSeries);
  candleSeries.attachPrimitive(MA_TREND._prim);
  maTrendPanelBuild();
  maTrendCompute();
  maTrendStatsRender();
}

(function () {
  const btn = document.getElementById('maTrendBtn');
  if (!btn) return;
  btn.addEventListener('click', () => {
    const el = document.getElementById('trendPanel');
    if (!el) return;
    MA_TREND.on = !MA_TREND.on;
    btn.classList.toggle('active', MA_TREND.on);
    el.style.display = MA_TREND.on ? '' : 'none';
    if (MA_TREND.on) {
      maTrendCompute(); maTrendStatsRender();
      if (window.innerWidth > 760) {
        const r = btn.getBoundingClientRect();
        const w = el.offsetWidth || 326;
        el.style.left = Math.max(6, Math.min(r.left, window.innerWidth - w - 8)) + 'px';
        el.style.top = (r.bottom + 6) + 'px';
      }
    }
    maTrendDraw();
  });
  maTrendInit();
})();
