// js/10-ma-slope.js — 均线斜率加粗
// 给每条均线一个斜率阈值滑块: 斜率落在设定区间内的均线段, 在图上加粗+发光显示。
// 斜率口径: 每根 K 线的均线变动百分比 = (MA[i]-MA[i-w]) / w / MA[i-w] * 100 (%/根),
// w = 计算窗口(根)。用百分比而不是价格差, 换品种/换周期量级可比。
"use strict";

const MA_SLOPE = window.MA_SLOPE = {
  on: false,         // 高亮总开关(工具栏「📐 斜率」): 默认关闭, 不改动原有观感
  win: 3,            // 斜率窗口(根)
  mode: 'lt',        // lt 斜率<X | gt 斜率>X | abslt |斜率|<X | absgt |斜率|>X
  items: [],         // [{period, color, cs, vals, slopes, thr, on}]
  _prim: null,
  _built: false,
};

const SLOPE_MODES = [
  ['lt', '斜率 < X（走弱/下跌段）'],
  ['gt', '斜率 > X（走强/上涨段）'],
  ['abslt', '|斜率| < X（走平段）'],
  ['absgt', '|斜率| > X（陡峭段）'],
];

// ---------------------------------------------------------------- 斜率判定 -- //
function maSlopeHit(item, s) {
  if (s == null || !item.on) return false;
  const x = item.thr;
  switch (MA_SLOPE.mode) {
    case 'gt': return s > x;
    case 'abslt': return Math.abs(s) < Math.abs(x);
    case 'absgt': return Math.abs(s) > Math.abs(x);
    default: return s < x;
  }
}

function maSlopeCount(item) {
  let n = 0;
  for (let i = 1; i < item.vals.length; i++) if (maSlopeHit(item, item.slopes[i])) n++;
  return n;
}

// ---------------------------------------------------------------- 计算 -- //
// 由 rebuildMA() 调用: 数据/周期/视图变化后重建斜率数组(阈值保留)
function maSlopeRefresh() {
  const cs = (typeof candlesFor === 'function') ? candlesFor(activeView) : [];
  const closes = cs.map(c => c.close);
  const periods = (typeof parseMaPeriods === 'function') ? parseMaPeriods() : [];
  const w = Math.max(1, Math.min(20, MA_SLOPE.win | 0));
  const prev = new Map(MA_SLOPE.items.map(it => [it.period, it]));
  MA_SLOPE.items = periods.map((p, i) => {
    const old = prev.get(p);
    const vals = (typeof sma === 'function') ? sma(closes, p) : [];
    const slopes = new Array(vals.length).fill(null);
    for (let j = 0; j < vals.length; j++) {
      const a = vals[j], b = j - w >= 0 ? vals[j - w] : null;
      if (a == null || b == null || !b) continue;
      slopes[j] = (a - b) / w / b * 100;      // %/根
    }
    return {
      period: p,
      color: (typeof MA_COLORS !== 'undefined' ? MA_COLORS[i % MA_COLORS.length] : '#2962ff'),
      cs, vals, slopes,
      thr: old ? old.thr : 0,
      on: old ? old.on : true,
    };
  });
  if (MA_SLOPE._built) { maSlopeRowsBuild(); maSlopeDraw(); }
}

function maSlopeDraw() {
  if (MA_SLOPE._prim) MA_SLOPE._prim.requestUpdate();
}

// ---------------------------------------------------------------- 绘制 -- //
class MaSlopeRenderer {
  constructor(prim) { this._prim = prim; }
  draw(target) {
    if (!MA_SLOPE.on || !MA_SLOPE.items.length) return;
    // MA 总开关关掉时不成画
    const btn = document.querySelector('#overlayToggle button[data-ov="ma"]');
    if (btn && !btn.classList.contains('active')) return;
    const series = candleSeries;
    // 只画屏幕内那一段: 4H 有近 2 万根, 全量逐点做坐标换算会让拖动滑块掉到 ~10fps
    const vr = chart.timeScale().getVisibleLogicalRange();
    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      for (const it of MA_SLOPE.items) {
        if (!it.on) continue;
        const n = it.vals.length;
        let i0 = 1, i1 = n - 1;
        if (vr) {
          i0 = Math.max(1, Math.floor(vr.from) - 2);
          i1 = Math.min(n - 1, Math.ceil(vr.to) + 2);
        }
        if (i1 <= i0) continue;
        let has = false;
        for (let i = i0; i <= i1; i++) if (maSlopeHit(it, it.slopes[i])) { has = true; break; }
        if (!has) continue;
        ctx.strokeStyle = it.color;
        ctx.lineWidth = 3.0;               // 基础线是 1.4, 高亮段加粗
        ctx.shadowColor = it.color;
        ctx.shadowBlur = 4;
        ctx.beginPath();
        let pen = false;
        for (let i = i0; i <= i1; i++) {
          const a = it.vals[i - 1], b = it.vals[i];
          if (a == null || b == null || !maSlopeHit(it, it.slopes[i])) { pen = false; continue; }
          const x1 = timeToX(it.cs[i - 1].time), y1 = series.priceToCoordinate(a);
          const x2 = timeToX(it.cs[i].time), y2 = series.priceToCoordinate(b);
          if (x1 == null || y1 == null || x2 == null || y2 == null) { pen = false; continue; }
          if (!pen) { ctx.moveTo(x1, y1); pen = true; }
          ctx.lineTo(x2, y2);
        }
        ctx.stroke();
      }
      ctx.shadowBlur = 0;
    });
  }
}
class MaSlopeView {
  constructor(prim) { this._prim = prim; this._renderer = new MaSlopeRenderer(prim); }
  update() {}
  renderer() { return this._renderer; }
}
class MaSlopePrimitive {
  constructor(series) { this._series = series; this._view = new MaSlopeView(this); this._req = null; }
  attached(p) { this._req = p.requestUpdate; }
  detached() { this._req = null; }
  updateAllViews() {}
  paneViews() { return [this._view]; }
  requestUpdate() { if (this._req) this._req(); }
}

// ---------------------------------------------------------------- 面板 -- //
function maSlopePanelBuild() {
  if (MA_SLOPE._built) return;
  const el = document.createElement('div');
  el.id = 'slopePanel';
  el.className = 'lab-panel slope-panel';
  el.style.display = 'none';
  el.innerHTML = `
    <div class="lab-head">
      <span class="lab-title">均线斜率</span>
      <span class="lab-badge" id="slopeBadge">—</span>
      <button class="lab-btn" id="slopeReset" title="阈值全部归零">归零</button>
      <button class="lab-btn" id="slopeClose" title="关闭">×</button>
    </div>
    <div class="lab-body">
      <div class="lab-row">
        <span class="lab-lab">条件</span>
        <select class="lab-sel" id="slopeMode">${SLOPE_MODES.map(([v, t]) => `<option value="${v}"${v === MA_SLOPE.mode ? ' selected' : ''}>${t}</option>`).join('')}</select>
      </div>
      <div class="lab-row">
        <span class="lab-lab">窗口</span>
        <input class="lab-rng" type="range" id="slopeWin" min="1" max="10" step="1" value="${MA_SLOPE.win}">
        <span class="lab-val" id="slopeWinV">${MA_SLOPE.win} 根</span>
      </div>
      <div class="lab-sec">各均线阈值 <span class="lab-hint">%/根</span></div>
      <div id="slopeRows"></div>
      <div class="lab-note" id="slopeNote">拖滑块：斜率满足条件的均线段加粗显示；数值为「每根均线变动百分比」。</div>
    </div>`;
  document.body.appendChild(el);
  MA_SLOPE._built = true;

  el.querySelector('#slopeClose').addEventListener('click', () => { el.style.display = 'none'; });
  el.querySelector('#slopeReset').addEventListener('click', () => {
    MA_SLOPE.items.forEach(it => { it.thr = 0; it.on = true; });
    maSlopeRowsBuild(); maSlopeDraw();
  });
  el.querySelector('#slopeMode').addEventListener('change', e => {
    MA_SLOPE.mode = e.target.value; maSlopeRowsBuild(); maSlopeDraw();
  });
  el.querySelector('#slopeWin').addEventListener('input', e => {
    MA_SLOPE.win = +e.target.value;
    el.querySelector('#slopeWinV').textContent = MA_SLOPE.win + ' 根';
    maSlopeRefresh();      // 窗口变了要重算斜率(阈值保留)
  });
  maSlopeRowsBuild();
}

// 每个 MA 一行: 勾选 + 周期 + 滑块 + 阈值 + 命中根数
function maSlopeRowsBuild() {
  if (!MA_SLOPE._built) return;
  const box = document.getElementById('slopeRows');
  if (!box) return;
  if (!MA_SLOPE.items.length) {
    box.innerHTML = '<div class="lab-note">当前没有均线（在 MA 周期框里填 20,60,120 之类）。</div>';
    maSlopeBadge();
    return;
  }
  box.innerHTML = MA_SLOPE.items.map((it, i) => `
    <div class="slope-row" data-i="${i}">
      <label class="slope-on"><input type="checkbox" data-on="${i}"${it.on ? ' checked' : ''}></label>
      <span class="slope-name" style="color:${it.color}">MA${it.period}</span>
      <input class="lab-rng" type="range" data-thr="${i}" min="-2" max="2" step="0.02" value="${it.thr}">
      <span class="lab-val" data-val="${i}">${it.thr >= 0 ? '+' : ''}${it.thr.toFixed(2)}</span>
      <span class="slope-cnt" data-cnt="${i}">0</span>
    </div>`).join('');

  box.querySelectorAll('input[data-thr]').forEach(inp => {
    inp.addEventListener('input', e => {
      const it = MA_SLOPE.items[+e.target.dataset.thr];
      it.thr = +e.target.value;
      box.querySelector(`[data-val="${e.target.dataset.thr}"]`).textContent =
        (it.thr >= 0 ? '+' : '') + it.thr.toFixed(2);
      maSlopeCountsUpdate(); maSlopeDraw();
    });
  });
  box.querySelectorAll('input[data-on]').forEach(inp => {
    inp.addEventListener('change', e => {
      const it = MA_SLOPE.items[+e.target.dataset.on];
      it.on = e.target.checked;
      maSlopeCountsUpdate(); maSlopeDraw();
    });
  });
  maSlopeCountsUpdate();
  maSlopeBadge();
}

function maSlopeCountsUpdate() {
  const box = document.getElementById('slopeRows');
  if (!box) return;
  let total = 0;
  MA_SLOPE.items.forEach((it, i) => {
    const n = it.on ? maSlopeCount(it) : 0;
    total += n;
    const el = box.querySelector(`[data-cnt="${i}"]`);
    if (el) el.textContent = `${n}根`;
  });
  maSlopeBadge(total);
}

function maSlopeBadge(total) {
  const b = document.getElementById('slopeBadge');
  if (!b) return;
  if (total == null) total = MA_SLOPE.items.filter(it => it.on).reduce((s, it) => s + maSlopeCount(it), 0);
  const mode = SLOPE_MODES.find(([v]) => v === MA_SLOPE.mode)?.[1].split('（')[0] || '';
  b.textContent = `${mode} · 高亮 ${total} 根`;
}

// ---------------------------------------------------------------- 启动 -- //
function maSlopeInit() {
  if (MA_SLOPE._prim) return;
  MA_SLOPE._prim = new MaSlopePrimitive(candleSeries);
  candleSeries.attachPrimitive(MA_SLOPE._prim);
  maSlopePanelBuild();
  maSlopeRefresh();
}

(function () {
  const btn = document.getElementById('maSlopeBtn');
  if (btn) {
    btn.addEventListener('click', () => {
      const el = document.getElementById('slopePanel');
      if (!el) return;
      MA_SLOPE.on = !MA_SLOPE.on;              // 按钮 = 高亮总开关
      btn.classList.toggle('active', MA_SLOPE.on);
      el.style.display = MA_SLOPE.on ? '' : 'none';
      if (MA_SLOPE.on && window.innerWidth > 760) {
        const r = btn.getBoundingClientRect();   // 贴着按钮弹出, 避免和左侧参数实验室面板重叠
        const w = el.offsetWidth || 326;
        el.style.left = Math.max(6, Math.min(r.left, window.innerWidth - w - 8)) + 'px';
        el.style.top = (r.bottom + 6) + 'px';
      }
      if (MA_SLOPE.on) { maSlopeRefresh(); maSlopeRowsBuild(); }
      maSlopeDraw();
    });
  }
  // 图表/数据就绪后再挂 primitive(脚本在 body 末尾, candleSeries 此时已存在)
  maSlopeInit();
})();
