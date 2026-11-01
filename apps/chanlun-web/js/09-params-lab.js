// js/09-params-lab.js — 参数实验室(纯前端重算)
//
// 目的: 不改 data.js 预计算结果, 在浏览器里按同一套缠论口径重算
//   包含处理 → 分型 → 笔 → (线段) → 中枢 → 背驰 → 一/二/三买
// 并把它画在主图上, 用滑动条实时观察「参数变化对中枢与买卖点的影响」。
//
// 口径(与 Python 引擎对齐的部分):
//   * MACD: EMA 种子=首值, hist = diff - dea;
//   * 包含处理: 向上取高高、向下取低低;
//   * 分型: 合并K三根高低点严格比较; 「收盘确认窗口」>0 时额外要求窗口内
//     有收盘价反向突破中K极值(实盘口径), =0 为原文纯几何口径;
//   * 笔: 交替分型中心间隔 ≥ minGap 根合并K(默认 4 ≈ 5根K线成笔);
//   * 中枢: 连续三段(笔或线段)的重叠区 [ZD,ZG], 重叠比例 ≥ pivotRatio;
//   * 背驰: 同向两段(隔一段反向)比较 MACD 柱面积/峰值, 后段/前段 < divRatio 且
//     价格创新低(顶背驰为新高) → 背驰。
//
// 一买触发(用户定稿): 底背驰成立后, 「绿柱首次开始缩短」那根 K 线的 收盘价 即买点;
// 另两个口径(不再缩短/柱体翻红)保留为可切换对照。
"use strict";

const LAB = window.LAB = {
  on: false,
  params: {
    macdFast: 12, macdSlow: 26, macdSignal: 9,
    divMode: 'area',        // area | peak
    divRatio: 0.90,         // 后段/前段 < 该值 → 背驰
    trigger: 'shrink',      // shrink(绿柱首次缩短) | stop(不再缩短) | flip(翻红)
    priceMode: 'close',     // close | low
    trigWindow: 20,         // 背离低点后多少根内找触发柱
    minGap: 4,              // 成笔: 分型中心最少间隔(合并K)
    confirmWin: 3,          // 分型收盘确认窗口(3=系统引擎口径, 0=原文纯几何)
    pivotRatio: 0.00,       // 中枢最小重叠比例(0=与引擎同口径, 不过滤窄中枢)
    thirdMaxDist: 0.12,     // 三买回抽低点距 ZG 的最大距离(超过视为远离中枢, 不作买点)
    pivotLevel: 'stroke',   // stroke(笔中枢) | segment(线段中枢)
    showPivots: true, showStrokes: true, showFracts: false,
    showBuys: true, showZones: true, showDiv: true, syncMacd: true,
  },
  result: null,
  _timer: null,
  _panel: null,
};

// ------------------------------------------- 计算(内核在 js/09-lab-engine.js) -- //
function labCompute() {
  return LabEngine.compute(candlesFor('raw'), LAB.params);
}

// ------------------------------------------------------------ 渲染 -- //
class LabRenderer {
  constructor(prim) { this._prim = prim; }
  draw(target) {
    const R = LAB.result;
    if (!LAB.on || !R || !R.bars || !R.bars.length) return;
    const p = LAB.params;
    const series = this._prim._series;
    const T = t();
    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context;
      const X = ts => timeToX(ts);
      const Y = pr => series.priceToCoordinate(pr);
      const W = (() => {
        // 单根K线像素宽, 用于区间带宽度
        const cs = R.bars;
        const a = X(cs[cs.length - 1].time), b = X(cs[cs.length - 2].time);
        const w = a != null && b != null ? Math.abs(a - b) : 8;
        return Math.max(3, Math.min(40, w || 8));
      })();
      ctx.lineCap = 'round';
      ctx.textBaseline = 'middle';
      ctx.font = '11px ' + FONT;

      // 中枢
      if (p.showPivots && R.pivots.length) {
        for (const pv of R.pivots) {
          const x1 = X(pv.tS), x2 = X(pv.tE);
          const yz = Y(pv.zg), yd = Y(pv.zd);
          if (x1 == null || x2 == null || yz == null || yd == null) continue;
          const x0 = Math.min(x1, x2), w = Math.max(2, Math.abs(x2 - x1));
          ctx.fillStyle = themeKey() === 'dark' ? 'rgba(41,98,255,.10)' : 'rgba(41,98,255,.07)';
          ctx.fillRect(x0, Math.min(yz, yd), w, Math.abs(yd - yz));
          ctx.strokeStyle = themeKey() === 'dark' ? 'rgba(96,165,250,.75)' : 'rgba(41,98,255,.55)';
          ctx.lineWidth = 1;
          ctx.setLineDash([4, 3]);
          ctx.beginPath(); ctx.moveTo(x0, yz); ctx.lineTo(x0 + w, yz); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(x0, yd); ctx.lineTo(x0 + w, yd); ctx.stroke();
          ctx.setLineDash([]);
        }
      }
      // 笔
      if (p.showStrokes && R.strokes.length) {
        ctx.strokeStyle = themeKey() === 'dark' ? 'rgba(148,163,184,.55)' : 'rgba(100,116,139,.55)';
        ctx.lineWidth = 1.4;
        ctx.setLineDash([2, 2]);
        for (const s of R.strokes) {
          const x1 = X(s.tS), y1 = Y(s.pS), x2 = X(s.tE), y2 = Y(s.pE);
          if (x1 == null || y1 == null || x2 == null || y2 == null) continue;
          ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
        }
        ctx.setLineDash([]);
      }
      // 分型
      if (p.showFracts) {
        for (const f of R.fr) {
          const m = R.merged[f.i];
          const isTop = f.kind === 'T';
          const bi = isTop ? m.i1 : m.i0;
          const x = X(R.bars[bi].time);
          const y = Y(isTop ? R.bars[bi].high : R.bars[bi].low);
          if (x == null || y == null) continue;
          ctx.fillStyle = isTop ? T.top : T.bottom;
          ctx.beginPath(); ctx.arc(x, y, 2.2, 0, Math.PI * 2); ctx.fill();
        }
      }
      // 背驰
      if (p.showDiv && R.divs.length) {
        for (const d of R.divs) {
          const x1 = X(d.prevT), y1 = Y(d.prevP), x2 = X(d.t), y2 = Y(d.p);
          if (x1 == null || y1 == null || x2 == null || y2 == null) continue;
          ctx.strokeStyle = d.kind === 'bottom' ? '#f0b90b' : '#14b8a6';
          ctx.lineWidth = 1.2;
          ctx.setLineDash([5, 4]);
          ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
          ctx.setLineDash([]);
          const label = '背 ' + (d.ratio * 100).toFixed(0) + '%';
          ctx.fillStyle = d.kind === 'bottom' ? '#f0b90b' : '#14b8a6';
          ctx.fillText(label, Math.min(x1, x2) + 4, Math.min(y1, y2) + (d.kind === 'bottom' ? 12 : -12));
        }
      }
      // 买卖点 + 区间
      if (p.showBuys && R.buys.length) {
        for (const b of R.buys) {
          const x = X(b.time), y = Y(b.price);
          if (x == null || y == null) continue;
          if (p.showZones && b.zone) {
            const [lo, hi] = b.zone;
            const y1 = Y(lo), y2 = Y(hi);
            if (y1 != null && y2 != null) {
              ctx.fillStyle = b.kind === '二买' ? 'rgba(38,166,154,.16)' : 'rgba(41,98,255,.16)';
              ctx.fillRect(x, Math.min(y1, y2), W * 6, Math.max(2, Math.abs(y2 - y1)));
            }
          }
          if (b.kind === '一买') {
            // 一买是点位: 虚线横贯 + 三角
            ctx.strokeStyle = '#26a69a';
            ctx.lineWidth = 1;
            ctx.setLineDash([4, 3]);
            ctx.beginPath(); ctx.moveTo(x - W * 18, y); ctx.lineTo(x + W * 18, y); ctx.stroke();
            ctx.setLineDash([]);
            ctx.fillStyle = '#26a69a';
            ctx.beginPath();
            ctx.moveTo(x, y + 9); ctx.lineTo(x - 5, y + 17); ctx.lineTo(x + 5, y + 17);
            ctx.closePath(); ctx.fill();
            ctx.fillText('一买 ' + Math.round(b.price) + ' · ' + fmtTime(b.time), x + 6, y + 24);
          } else {
            ctx.fillStyle = b.kind === '二买' ? '#0d9488' : '#2563eb';
            ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill();
            const z = b.zone ? ` [${Math.round(b.zone[0])}~${Math.round(b.zone[1])}]` : '';
            ctx.fillText(b.kind + ' ' + Math.round(b.price) + z, x + 6, y - 8);
          }
        }
      }
    });
  }
}
class LabView {
  constructor(prim) { this._prim = prim; this._renderer = new LabRenderer(prim); }
  update() {}
  renderer() { return this._renderer; }
}
class LabPrimitive {
  constructor(series, chart) { this._series = series; this._chart = chart; this._view = new LabView(this); this._req = null; }
  attached(p) { this._req = p.requestUpdate; }
  updateAllViews() {}
  paneViews() { return [this._view]; }
}
const labPrim = new LabPrimitive(candleSeries, chart);
candleSeries.attachPrimitive(labPrim);

// -------------------------------------------------------------- 面板 -- //
const LAB_ROWS = [
  { sec: 'MACD' },
  { key: 'macdFast', label: '快线', min: 3, max: 30, step: 1 },
  { key: 'macdSlow', label: '慢线', min: 10, max: 60, step: 1 },
  { key: 'macdSignal', label: '信号', min: 3, max: 20, step: 1 },
  { sec: '背驰' },
  { key: 'divRatio', label: '面积比<', min: 0.3, max: 1.5, step: 0.01, fmt: v => v.toFixed(2) },
  { key: 'divMode', label: '比较', type: 'select', options: [['area', '柱面积'], ['peak', '峰值']] },
  { sec: '一买触发' },
  { key: 'trigger', label: '柱体', type: 'select', options: [['shrink', '绿柱首次缩短'], ['stop', '不再缩短'], ['flip', '柱体翻红']] },
  { key: 'priceMode', label: '取价', type: 'select', options: [['close', '收盘价'], ['low', '最低价']] },
  { key: 'trigWindow', label: '窗口(根)', min: 3, max: 60, step: 1 },
  { sec: '结构' },
  { key: 'minGap', label: '成笔间隔', min: 2, max: 10, step: 1 },
  { key: 'confirmWin', label: '分型确认', min: 0, max: 5, step: 1 },
  { key: 'pivotRatio', label: '中枢重叠≥', min: 0, max: 0.8, step: 0.01, fmt: v => v.toFixed(2) },
  { key: 'thirdMaxDist', label: '三买距ZG≤', min: 0.02, max: 0.30, step: 0.01, fmt: v => (v * 100).toFixed(0) + '%' },
  { key: 'pivotLevel', label: '中枢级别', type: 'select', options: [['stroke', '笔中枢'], ['segment', '线段中枢(近似)']] },
];
const LAB_TOGGLES = [
  ['showPivots', '中枢'], ['showStrokes', '笔'], ['showFracts', '分型'],
  ['showBuys', '买卖点'], ['showZones', '区间'], ['showDiv', '背驰'], ['syncMacd', '同步MACD'],
];

function labBuildUI() {
  const el = document.createElement('div');
  el.id = 'labPanel';
  el.className = 'lab-panel';
  el.innerHTML = `
    <div class="lab-head">
      <span class="lab-title">参数实验室</span>
      <span class="lab-badge" id="labBadge">—</span>
      <a class="lab-btn" href="bi-compare.html" target="_blank" rel="noopener" title="三种笔口径对照（图解）">笔的区别?</a>
      <button class="lab-btn" id="labReset" title="恢复默认参数">重置</button>
      <button class="lab-btn" id="labMin" title="收起/展开">–</button>
    </div>
    <div class="lab-body" id="labBody">
      <div class="lab-rows" id="labRows"></div>
      <div class="lab-sec">显示</div>
      <div class="lab-toggles" id="labToggles"></div>
      <div class="lab-sec">最近信号 <span class="lab-hint">点击跳转</span></div>
      <div class="lab-sigs" id="labSigs"></div>
      <div class="lab-note" id="labNote"></div>
    </div>`;
  document.body.appendChild(el);
  // 标题带品种: 实验室结果与品种绑定, 避免切品种后误读上一品种的笔/中枢/买点
  el.querySelector('.lab-title').textContent = '参数实验室 · ' + currentSym;

  const rows = el.querySelector('#labRows');
  for (const r of LAB_ROWS) {
    if (r.sec) { rows.insertAdjacentHTML('beforeend', `<div class="lab-sec">${r.sec}</div>`); continue; }
    const val = LAB.params[r.key];
    if (r.type === 'select') {
      const opts = r.options.map(([v, t]) => `<option value="${v}"${v === val ? ' selected' : ''}>${t}</option>`).join('');
      rows.insertAdjacentHTML('beforeend',
        `<label class="lab-row"><span class="lab-lab">${r.label}</span>
           <select class="lab-sel" data-k="${r.key}">${opts}</select></label>`);
    } else {
      const fmt = r.fmt || (v => String(v));
      rows.insertAdjacentHTML('beforeend',
        `<label class="lab-row"><span class="lab-lab">${r.label}</span>
           <input class="lab-rng" type="range" data-k="${r.key}" min="${r.min}" max="${r.max}" step="${r.step}" value="${val}">
           <span class="lab-val" data-v="${r.key}">${fmt(val)}</span></label>`);
    }
  }
  const tg = el.querySelector('#labToggles');
  for (const [k, label] of LAB_TOGGLES) {
    tg.insertAdjacentHTML('beforeend',
      `<label class="lab-tg"><input type="checkbox" data-t="${k}"${LAB.params[k] ? ' checked' : ''}><span>${label}</span></label>`);
  }
  el.querySelector('#labRows').addEventListener('input', ev => {
    const k = ev.target.dataset.k;
    if (!k) return;
    const v = ev.target.type === 'range' ? Number(ev.target.value) : ev.target.value;
    LAB.params[k] = v;
    const out = el.querySelector(`[data-v="${k}"]`);
    if (out) {
      const row = LAB_ROWS.find(r => r.key === k);
      out.textContent = row && row.fmt ? row.fmt(v) : String(v);
    }
    labSaveParams();
    labSchedule();
  });
  el.querySelector('#labToggles').addEventListener('input', ev => {
    const k = ev.target.dataset.t;
    if (!k) return;
    LAB.params[k] = ev.target.checked;
    labSaveParams();
    if (k === 'syncMacd') labRefreshMacdPane();
    labRepaint();
  });
  el.querySelector('#labMin').addEventListener('click', () => {
    el.classList.toggle('lab-collapsed');
    el.querySelector('#labMin').textContent = el.classList.contains('lab-collapsed') ? '+' : '–';
  });
  el.querySelector('#labReset').addEventListener('click', () => {
    const d = LAB_DEFAULTS;
    Object.assign(LAB.params, d);
    for (const r of LAB_ROWS) {
      if (r.sec) continue;
      const inp = el.querySelector(`[data-k="${r.key}"]`);
      if (!inp) continue;
      inp.value = LAB.params[r.key];
      const out = el.querySelector(`[data-v="${r.key}"]`);
      if (out) out.textContent = r.fmt ? r.fmt(LAB.params[r.key]) : String(LAB.params[r.key]);
    }
    for (const [k] of LAB_TOGGLES) el.querySelector(`[data-t="${k}"]`).checked = LAB.params[k];
    labSaveParams();
    labSchedule();
  });
  el.querySelector('#labSigs').addEventListener('click', ev => {
    const row = ev.target.closest('.lab-sig');
    if (row) labFocus(Number(row.dataset.t));
  });
  LAB._panel = el;
}

// 把视口移到某个信号点附近(±60 根K线)
function labFocus(t) {
  const R = LAB.result;
  if (!R || !R.bars || !R.bars.length) return;
  const cs = R.bars;
  let i = cs.findIndex(c => c.time >= t);
  if (i < 0) i = cs.length - 1;
  const from = cs[Math.max(0, i - 60)].time;
  const to = cs[Math.min(cs.length - 1, i + 20)].time;
  if (from < to) chart.timeScale().setVisibleRange({ from, to });
}
const LAB_DEFAULTS = JSON.parse(JSON.stringify(LAB.params));

function labStats() {
  const R = LAB.result;
  const badge = document.getElementById('labBadge');
  const note = document.getElementById('labNote');
  if (!R || R.error) {
    if (badge) badge.textContent = '—';
    if (note) note.textContent = R && R.error ? `⚠ ${R.error}` : '';
    return;
  }
  const n1 = R.buys.filter(b => b.kind === '一买').length;
  const n2 = R.buys.filter(b => b.kind === '二买').length;
  const n3 = R.buys.filter(b => b.kind === '三买').length;
  if (badge) badge.textContent = `笔${R.strokes.length} 中枢${R.pivots.length} 1买${n1}/2买${n2}/3买${n3} 背驰${R.divs.length}`;
  const last = R.buys.filter(b => b.kind === '一买').slice(-1)[0];
  if (note) {
    note.textContent = last
      ? `最近一买：${fmtTime(last.time)} @ ${Math.round(last.price)}（背驰比 ${(last.ratio * 100).toFixed(0)}%）`
      : '当前参数下没有一买（可调背驰比/触发口径）';
  }
  const box = document.getElementById('labSigs');
  if (box) {
    const rows = R.buys.slice(-8).reverse();
    box.innerHTML = rows.length ? rows.map((b, i) => {
      const z = b.zone ? ` <span class="lab-z">${Math.round(b.zone[0])}~${Math.round(b.zone[1])}</span>` : '';
      const tip = `${b.kind} ${fmtTime(b.time)} @ ${Math.round(b.price)}` + (b.zone ? ` 区间 ${Math.round(b.zone[0])}~${Math.round(b.zone[1])}` : '');
      return `<div class="lab-sig" data-t="${b.time}" title="${tip}">` +
        `<span class="lab-k lab-k${'一二三'.indexOf(b.kind[0]) + 1}">${b.kind}</span>` +
        `<span class="lab-d">${fmtTime(b.time)}</span>` +
        `<span class="lab-p">${Math.round(b.price)}</span>${z}</div>`;
    }).join('') : '<div class="lab-empty">无</div>';
  }
}

function labRepaint() { if (labPrim && labPrim._req) labPrim._req(); }

function labRecompute() {
  if (!LAB.on) return;
  if (chunkedActive()) {
    LAB.result = { bars: [], error: '15F 分片周期无缠论，切到 1D/4H/1H' };
    LAB._sig = null;
    labStats(); labRepaint(); return;
  }
  const cs = candlesFor('raw');
  // 拖动/平移不改变输入 → 不必重算(重算 1D 约 7ms, 4H 约 17ms)
  // 品种必须进签名: BTC/ETH 同为 3297 根、末根时间相同, 只比长度会命中缓存 → 切品种后仍显示上一个品种的笔/中枢/买点
  const sig = currentSym + ':' + activeTF + ':' + cs.length + ':' + (cs.length ? cs[cs.length - 1].time : 0) + ':' + JSON.stringify(LAB.params);
  if (sig === LAB._sig && LAB.result) { labRepaint(); return; }
  LAB._sig = sig;
  LAB.result = LabEngine.compute(cs, LAB.params);
  labStats();
  labRepaint();
  labRefreshMacdPane();
}

function labSchedule() {
  if (LAB._timer) clearTimeout(LAB._timer);
  LAB._timer = setTimeout(() => { LAB._timer = null; labRecompute(); }, 60);
}

function labToggle(force) {
  const want = force == null ? !LAB.on : !!force;
  if (want === LAB.on) return;
  LAB.on = want;
  const btn = document.getElementById('labBtn');
  if (btn) btn.classList.toggle('active', want);
  const panel = LAB._panel;
  if (panel) panel.style.display = want ? '' : 'none';
  if (want) { labRecompute(); }
  else { labRepaint(); }
}

// -------------------------------------------------- MACD 面板同步 -- //
// 实验室的 MACD 参数要能反映到副图, 否则「绿柱缩短」看到的柱子和买点对不上。
// 只在实验室开启且勾选「同步MACD」时接管 macdFor, 关闭时完全走原逻辑。
function labWrapMacd() {
  const orig = window.macdFor;
  if (typeof orig !== 'function' || orig.__labWrapped) return;
  const wrapped = function (v) {
    if (!LAB.on || !LAB.params.syncMacd || chunkedActive()) return orig.apply(this, arguments);
    const cs = candlesFor(v);
    if (!cs.length) return orig.apply(this, arguments);
    return LabEngine.macd(cs.map(c => c.close),
      LAB.params.macdFast | 0, LAB.params.macdSlow | 0, LAB.params.macdSignal | 0);
  };
  wrapped.__labWrapped = true;
  window.macdFor = wrapped;
  try { macdFor = wrapped; } catch (e) { /* 严格模式不可赋值则忽略 */ }
}
function labRefreshMacdPane() {
  try {
    const md = buildMacdData(activeView);
    macdHistSeries.setData(md.hist);
    macdLineSeries.setData(md.diff);
    macdSignalSeries.setData(md.dea);
  } catch (e) { /* 面板未就绪 */ }
}

// -------------------------------------------------------------- 装配 -- //
const LAB_STORE = 'chanlun.lab.params.v1';
function labLoadParams() {
  try {
    const raw = localStorage.getItem(LAB_STORE);
    if (!raw) return;
    const saved = JSON.parse(raw);
    for (const k of Object.keys(LAB.params)) {
      if (k in saved && typeof saved[k] === typeof LAB.params[k]) LAB.params[k] = saved[k];
    }
  } catch (e) { /* 隐私模式/损坏 → 用默认值 */ }
}
function labSaveParams() {
  try { localStorage.setItem(LAB_STORE, JSON.stringify(LAB.params)); } catch (e) { /* ignore */ }
}

function labInit() {
  labLoadParams();
  // 工具栏入口(插在「缠论设置」之后, 找不到就放 fitBtn 前)
  const bar = document.querySelector('.toolbar');
  if (bar && !document.getElementById('labBtn')) {
    const btn = document.createElement('button');
    btn.id = 'labBtn';
    btn.className = 'btn';
    btn.textContent = '🎛 参数实验室';
    btn.title = '滑动条调参: MACD/背驰/成笔/中枢 → 实时重算中枢与买卖点(纯前端, 不改 data.js)';
    btn.addEventListener('click', () => labToggle());
    const anchor = document.getElementById('chanDetailsBtn');
    const fit = document.getElementById('fitBtn');
    if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(btn, anchor.nextSibling);
    else if (fit && fit.parentNode) fit.parentNode.insertBefore(btn, fit);
  }
  labBuildUI();
  LAB._panel.style.display = 'none';
  labWrapMacd();
  // 回填已保存参数到控件
  for (const r of LAB_ROWS) {
    if (r.sec || !LAB._panel) continue;
    const inp = LAB._panel.querySelector(`[data-k="${r.key}"]`);
    if (!inp) continue;
    inp.value = LAB.params[r.key];
    const out = LAB._panel.querySelector(`[data-v="${r.key}"]`);
    if (out) out.textContent = r.fmt ? r.fmt(LAB.params[r.key]) : String(LAB.params[r.key]);
  }

  // 数据/视图/周期变化 → 重算(包装 refreshAll, 不改变原行为)
  const orig = window.refreshAll;
  if (typeof orig === 'function' && !orig.__labWrapped) {
    const wrapped = function () {
      const r = orig.apply(this, arguments);
      labSchedule();
      return r;
    };
    wrapped.__labWrapped = true;
    window.refreshAll = wrapped;
    try { refreshAll = wrapped; } catch (e) { /* 严格模式下不可赋值则忽略 */ }
  }
  window.addEventListener('resize', () => { if (LAB.on) labRepaint(); });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', labInit);
else labInit();
