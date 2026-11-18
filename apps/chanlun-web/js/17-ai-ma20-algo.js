// js/17-ai-ma20-algo.js — AI-MA20交易算法 (v1.0 冻结版)
// 功能：
//   1. 冻结并导出 window.AIMA20Algo 规范接口与只读参数
//   2. 主图顶部工具栏「AI-MA20」开关 + 主图三角形买点 (▲ 2x 高置信 / ▲ 1x 中置信) 绘制
//   3. 鼠标滑动或点击图表上的三角形买点 (▲) 时，弹出浮动小卡片展示：
//      - 当前买点订单详情与 AI 置信度归因
//      - 不同置信度档位（★高置信2x / ●中置信1x / ✓全部买入）的历史止损率对比（≤5根/≤10根/≤20根/总止损率/未止损率/≥3R率/均R）
//      - 同置信度档位的历史买入订单明细列表（支持滚动查阅与点击定位跳转）
'use strict';

const AI_MA20_STATE = {
  on: false,                // 默认保持主图干净, 需要时可在「指标」菜单开启 AI-MA20
  tierFilter: 'active',     // 'active' (1x+2x 全部买入) | 'heavy' (仅2x) | 'std' (仅1x)
  hoverSig: null,           // 当前悬停或选中的信号对象
  hoverBarIdx: -1,          // 当前悬停信号的 K 线索引
  pinned: false,            // 点击三角形可固定卡片
  scopeMode: 'all',         // 'all' (全样本历史订单) | 'prior' (仅截至当前买点之前的历史订单)
  orderListTab: 'same',     // 'same' (与当前买点同置信度档位) | 'heavy' | 'std' | 'active'
  hideTimer: null,
  prim: null,
  evalCacheKey: '',
  evalCacheVal: null,
  customBarCache: new Map(),
};

try {
  const qp = new URLSearchParams(location.search).get('aima20');
  if (qp === '1' || localStorage.getItem('chanlun.ai_ma20.indicator.v2') === 'on') AI_MA20_STATE.on = true;
} catch (_) {}

// 带缓存获取 annMyBuyCdEval()，避免 mousemove 60fps 重复计算全量均线与树模型
function aiMa20GetEval() {
  if (typeof annMyBuyCdEval !== 'function') return null;
  const cs = candlesFor('raw');
  if (!cs || !cs.length) return null;
  const lastBar = cs[cs.length - 1];
  const mb = typeof ANN_MYBUY !== 'undefined' ? ANN_MYBUY : {};
  const src = typeof ANN !== 'undefined' ? ANN.source : '';
  const key = `${currentSym}|${activeTF}|${cs.length}|${lastBar ? lastBar.time : 0}|${mb.cooldownBars}|${mb.allowMa60Upgrade}|${mb.allowStopReset}|${mb.metaFilter}|${src}`;
  if (AI_MA20_STATE.evalCacheKey !== key || !AI_MA20_STATE.evalCacheVal) {
    AI_MA20_STATE.evalCacheKey = key;
    AI_MA20_STATE.evalCacheVal = annMyBuyCdEval();
    AI_MA20_STATE.customBarCache.clear();
  }
  return AI_MA20_STATE.evalCacheVal;
}

// 对任意 K 线索引 i 补齐 AI-MA20 评估字段（兼容非 kept 信号、v5 信号或任意标注三角形买点）
function aiMa20EnrichBar(barIdx, baseSig = null) {
  const ev = aiMa20GetEval();
  if (!ev) return null;
  const fromKept = ev.kept.find(x => x.i === barIdx);
  if (fromKept) return fromKept;
  const fromAll = ev.all.find(x => x.i === barIdx);
  if (fromAll) return fromAll;
  if (AI_MA20_STATE.customBarCache.has(barIdx)) {
    return AI_MA20_STATE.customBarCache.get(barIdx);
  }

  const cs = candlesFor('raw');
  const bar = cs[barIdx];
  if (!bar) return null;
  const n = cs.length;
  const h = cs.map(x => x.high), l = cs.map(x => x.low), c = cs.map(x => x.close), o = cs.map(x => x.open);
  const v = volArrFor('raw');
  const m20 = mdSma(c, 20), m60 = mdSma(c, 60), m120 = mdSma(c, 120);
  const av20 = new Array(n).fill(null);
  for (let i = 20; i < n; i++) { let s = 0; for (let k = i - 20; k < i; k++) s += v[k]; av20[i] = s / 20; }
  const tr = new Array(n).fill(0);
  for (let i = 1; i < n; i++) tr[i] = Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1]));
  const atr = new Array(n).fill(null);
  for (let i = 14; i < n; i++) { let s = 0; for (let k = i - 13; k <= i; k++) s += tr[k]; atr[i] = s / 14; }
  const ctx = { bars:cs, h, l, c, o, v, m20, m60, m120, av20, atr };
  const meta = typeof annMetaScoreAt === 'function'
    ? annMetaScoreAt(barIdx, ctx)
    : { p: 0.5, tier: 'std', mult: 1.0, label: '1x标准', reasons: [] };

  const e = c[barIdx];
  const rawStop = l[barIdx];
  const execStop = (baseSig && baseSig.stop != null)
    ? baseSig.stop
    : (meta.execStop != null ? meta.execStop : rawStop);
  const rawRiskPct = (e - rawStop) / Math.max(1e-9, e);
  const riskPct = (e - execStop) / Math.max(1e-9, e);
  const out = typeof annCalcOrderOutcome === 'function'
    ? annCalcOrderOutcome(barIdx, execStop, c, h, l, m20)
    : (() => {
      const r0 = Math.max(e * 1e-4, e - execStop);
      const end = Math.min(n - 1, barIdx + 120);
      const end60 = Math.min(n - 1, barIdx + 60);
      let stopBar = null, mfe = 0, mfe60 = 0, rA = null, holdA = end - barIdx, exitReasonA = '120根到期';
      let curStop = execStop, mfeB = 0, stopBarB = null, rB = null, holdB = end - barIdx, exitReasonB = '120根到期';
      for (let j = barIdx + 1; j <= end; j++) {
        const k = j - barIdx;
        if (stopBar == null) {
          if (l[j] <= execStop) { stopBar = k; rA = -1.0; holdA = k; exitReasonA = `第${k}根止损`; }
          else { const d = h[j] - e; if (d > mfe) mfe = d; if (j <= end60 && d > mfe60) mfe60 = d; }
        }
        if (rB == null) {
          if (l[j] <= curStop) {
            rB = (curStop - e) / r0; holdB = k;
            if (curStop > execStop + 1e-12) exitReasonB = curStop >= e + r0 ? `第${k}根锁盈(+1.2R)` : `第${k}根保本止损`;
            else { stopBarB = k; exitReasonB = `第${k}根止损`; }
          } else {
            const urR = (h[j] - e) / r0; if (urR > mfeB) mfeB = urR;
            if (k >= 3 && m20[j] != null && c[j] < m20[j]) { rB = (c[j] - e) / r0; holdB = k; exitReasonB = `第${k}根破MA20`; }
            else {
              if (mfeB >= 2.5 && c[j] > e + 0.2 * r0) curStop = Math.max(curStop, e + 0.05 * r0);
              if (mfeB >= 4.0 && c[j] > e + 1.4 * r0) curStop = Math.max(curStop, e + 1.2 * r0);
            }
          }
        }
        if (stopBar != null && rB != null) break;
      }
      if (rA == null) rA = (c[end] - e) / r0;
      if (rB == null) rB = (c[end] - e) / r0;
      return { rA, holdA, exitReasonA, rB, holdB, exitReasonB, stopBar, stopBarB, mfePct: mfe / Math.max(1e-9, e), mfeR: mfe / r0, mfe60R: mfe60 / r0 };
    })();

  const dateStr = annDate(bar.time);
  const structStop = meta.structStop != null ? meta.structStop : rawStop;
  const structRiskPct = meta.structRiskPct != null ? meta.structRiskPct : rawRiskPct;
  const enriched = {
    ...(baseSig || {}),
    i: barIdx,
    time: bar.time,
    date: dateStr,
    dateStr,
    orderId: `AIMA20-${dateStr.replace(/-/g, '')}`,
    entry: e,
    stop: execStop,
    rawStop,
    narrowStopProtected: (baseSig && baseSig.narrowStopProtected != null) ? baseSig.narrowStopProtected : !!meta.narrowStopProtected,
    structStop,
    riskPct,
    rawRiskPct,
    structRiskPct,
    grade: (baseSig && baseSig.grade) || (meta.tradScore >= 75 ? 'A' : meta.tradScore >= 55 ? 'B' : (meta.tradScore >= 35 ? 'C' : 'D')),
    score: (baseSig && baseSig.score) != null ? baseSig.score : (meta.tradScore != null ? meta.tradScore : 50),
    tradScore: (baseSig && baseSig.tradScore) != null ? baseSig.tradScore : meta.tradScore,
    fng: (baseSig && baseSig.fng) != null ? baseSig.fng : meta.fng,
    metaP: (baseSig && baseSig.metaP != null) ? baseSig.metaP : meta.p,
    metaTier: (baseSig && baseSig.metaTier) || meta.tier,
    metaMult: (baseSig && baseSig.metaMult != null) ? baseSig.metaMult : meta.mult,
    metaLabel: (baseSig && baseSig.metaLabel) || meta.label,
    metaReasons: (baseSig && baseSig.metaReasons) || meta.reasons,
    isBuy: ((baseSig && baseSig.metaTier) || meta.tier) !== 'skip',
    stopBar: out.stopBar,
    stopBarB: out.stopBarB,
    stoppedLe5: out.stopBar != null && out.stopBar <= 5,
    stoppedLe10: out.stopBar != null && out.stopBar <= 10,
    stoppedLe20: out.stopBar != null && out.stopBar <= 20,
    stopped120: out.stopBar != null,
    mfePct: out.mfePct,
    mfeR: out.mfeR,
    mfe60R: out.mfe60R,
    ge1R: out.mfeR >= 1.0,
    ge2R: out.mfeR >= 2.0,
    ge3R: out.mfe60R >= 3.0,
    rVal: out.rA,
    rA: out.rA,
    rB: out.rB,
    holdA: out.holdA,
    exitReasonA: out.exitReasonA,
    holdB: out.holdB,
    exitReasonB: out.exitReasonB,
  };
  AI_MA20_STATE.customBarCache.set(barIdx, enriched);
  return enriched;
}

// 暴露冻结版全局只读接口
window.AIMA20Algo = Object.freeze({
  id: 'AI-MA20-v2.1-FROZEN',
  name: 'AI-MA20交易算法',
  version: '2.1.0',
  frozenDate: '2026-11-11',
  getSpec: () => (typeof AI_MA20_FROZEN_SPEC !== 'undefined' ? AI_MA20_FROZEN_SPEC : null),
  getModel: () => (typeof ANN_META_MODEL !== 'undefined' ? ANN_META_MODEL : null),
  evaluate: () => aiMa20GetEval(),
  getBuyOrders: (tier = 'active') => {
    const ev = aiMa20GetEval();
    if (!ev) return [];
    if (tier === 'heavy') return ev.metaHeavy || [];
    if (tier === 'std') return ev.metaStd || [];
    return ev.metaActive || [];
  },
});

// 获取当前主图上所有可见的买点三角形信号（合并 AI-MA20 主图买点与当前开启的标注抽屉三角形买点）
function aiMa20VisibleSignals() {
  if (!AI_MA20_STATE.on && !(typeof ANN !== 'undefined' && ANN.on) && !(typeof COVER67_INDICATOR !== 'undefined' && COVER67_INDICATOR.on)) return [];
  const ev = aiMa20GetEval();
  if (!ev || !ev.kept) return [];
  const cs = candlesFor('raw');
  const maxBarIdx = (typeof ANN !== 'undefined' && ANN.on && ANN.hideFuture && ANN.sigs && ANN.sigs[ANN.idx])
    ? ANN.sigs[ANN.idx].i
    : cs.length - 1;

  const mapByBar = new Map();

  // 1. 若 AI-MA20 指标开启，放入 AI-MA20 的买入信号
  if (AI_MA20_STATE.on) {
    const base = AI_MA20_STATE.tierFilter === 'heavy'
      ? ev.metaHeavy
      : AI_MA20_STATE.tierFilter === 'std'
        ? ev.metaStd
        : ev.metaActive;
    for (const sg of (base || [])) {
      if (sg.i <= maxBarIdx) mapByBar.set(sg.i, sg);
    }
  }

  // 2. 若标注抽屉正打开 (ANN.on)，把当前标注抽屉中正在绘制三角形的信号也纳入可点击/可悬停集合
  if (typeof ANN !== 'undefined' && ANN.on && Array.isArray(ANN.sigs)) {
    for (const sg of ANN.sigs) {
      if (sg.i > maxBarIdx) continue;
      if (!mapByBar.has(sg.i)) {
        const enriched = aiMa20EnrichBar(sg.i, sg);
        if (enriched) mapByBar.set(sg.i, enriched);
      }
    }
  }

  // 3. 若 MA突破 (COVER67_INDICATOR.on) 开启，也支持点击其三角形弹出 AI-MA20 历史订单对比卡
  if (typeof COVER67_INDICATOR !== 'undefined' && COVER67_INDICATOR.on && typeof cover67IndicatorRows === 'function') {
    for (const r of cover67IndicatorRows()) {
      if (r.i <= maxBarIdx && !mapByBar.has(r.i)) {
        const enriched = aiMa20EnrichBar(r.i);
        if (enriched) mapByBar.set(r.i, enriched);
      }
    }
  }

  return [...mapByBar.values()];
}

// 主图独立 Canvas Primitive：绘制 AI-MA20 三角形买点 ▲ 及悬停高亮圈
class AIMA20TriangleRenderer {
  constructor(prim) { this._prim = prim; }
  draw(target) {
    if (!AI_MA20_STATE.on && AI_MA20_STATE.hoverBarIdx < 0) return;
    const ev = aiMa20GetEval();
    if (!ev) return;
    const cs = candlesFor('raw');
    const viewCs = candlesFor(activeView);
    const ts = this._prim._chart.timeScale();
    const series = this._prim._series;
    const maxBarIdx = (typeof ANN !== 'undefined' && ANN.on && ANN.hideFuture && ANN.sigs && ANN.sigs[ANN.idx])
      ? ANN.sigs[ANN.idx].i
      : cs.length - 1;

    // 若标注抽屉已打开且处于 mybuy_meta 模式，AnnPrimitive 已经在画同一批三角形
    const annDrawingSame = typeof ANN !== 'undefined' && ANN.on && ANN.source === 'mybuy_meta';
    const drawList = AI_MA20_STATE.on ? (ev.metaActive || []).filter(sg => sg.i <= maxBarIdx) : [];

    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context;
      const W = scope.mediaSize.width;

      // 若当前有悬停/固定的买点，先画醒目的高亮光晕圈
      if (AI_MA20_STATE.hoverBarIdx >= 0 && cs[AI_MA20_STATE.hoverBarIdx]) {
        const hBar = cs[AI_MA20_STATE.hoverBarIdx];
        let hTime = hBar.time, hLow = hBar.low;
        if (activeView !== 'raw' && typeof cover67IndicatorBar === 'function') {
          const j = cover67IndicatorBar(hBar.time, viewCs);
          if (j >= 0 && viewCs[j]) { hTime = viewCs[j].time; hLow = viewCs[j].low; }
        }
        const hx = ts.timeToCoordinate(hTime);
        const hy = series.priceToCoordinate(hLow);
        if (hx != null && hy != null) {
          const isHeavy = AI_MA20_STATE.hoverSig && AI_MA20_STATE.hoverSig.metaTier === 'heavy';
          ctx.save();
          ctx.fillStyle = isHeavy ? 'rgba(16,185,129,0.26)' : 'rgba(56,189,248,0.26)';
          ctx.strokeStyle = '#f59e0b';
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(hx, hy + 14, 12, 0, Math.PI * 2);
          ctx.fill();
          ctx.stroke();
          ctx.restore();
        }
      }

      if (!AI_MA20_STATE.on || annDrawingSame) return;

      const labelXs = [];
      // 先收集需要显示文本标签的优先项 (悬停 > 2x重仓 > 1x标准)，避免缩小时相邻 1x/2x 文字重叠
      const items = [];
      for (const sg of drawList) {
        const rawBar = cs[sg.i];
        if (!rawBar) continue;
        let drawTime = rawBar.time;
        let drawLow = rawBar.low;
        if (activeView !== 'raw' && typeof cover67IndicatorBar === 'function') {
          const j = cover67IndicatorBar(rawBar.time, viewCs);
          if (j >= 0 && viewCs[j]) {
            drawTime = viewCs[j].time;
            drawLow = viewCs[j].low;
          }
        }
        const sx = ts.timeToCoordinate(drawTime);
        if (sx == null || sx < -12 || sx > W + 12) continue;
        const yl = series.priceToCoordinate(drawLow);
        if (yl == null) continue;
        items.push({
          sg, sx, yl,
          isHover: AI_MA20_STATE.hoverBarIdx === sg.i,
          isHeavy: sg.metaTier === 'heavy',
          showLabel: false,
        });
      }
      const byPriority = items.slice().sort((a, b) => (b.isHover - a.isHover) || (b.isHeavy - a.isHeavy) || (a.sx - b.sx));
      for (const it of byPriority) {
        if (it.isHover || !labelXs.some(x => Math.abs(x - it.sx) < 18)) {
          it.showLabel = true;
          labelXs.push(it.sx);
        }
      }

      for (const it of items) {
        const { sg, sx, yl, isHover, isHeavy, showLabel } = it;
        const col = isHeavy ? '#10b981' : '#38bdf8';

        const rSz = isHover ? 7.0 : (isHeavy ? 5.8 : 4.8);
        ctx.save();
        ctx.fillStyle = isHover ? '#f59e0b' : col;
        ctx.beginPath();
        ctx.moveTo(sx, yl + 7);
        ctx.lineTo(sx - rSz, yl + 17.5);
        ctx.lineTo(sx + rSz, yl + 17.5);
        ctx.closePath();
        ctx.fill();

        if (showLabel) {
          ctx.fillStyle = isHover ? '#fbbf24' : col;
          ctx.font = (isHeavy || isHover ? '700 ' : '600 ') + '9.5px system-ui, -apple-system, sans-serif';
          ctx.textAlign = 'center';
          ctx.fillText(isHeavy ? '2x' : '1x', sx, yl + 27.5);
          ctx.textAlign = 'left';
        }

        if (sg.stop != null) {
          const ys = series.priceToCoordinate(sg.stop);
          if (ys != null) {
            ctx.strokeStyle = isHover ? 'rgba(248,113,113,0.95)' : 'rgba(248,113,113,0.42)';
            ctx.lineWidth = isHover ? 1.8 : 1;
            ctx.setLineDash(isHover ? [4, 3] : [2, 3]);
            ctx.beginPath();
            ctx.moveTo(sx, ys);
            ctx.lineTo(Math.min(W, sx + (isHover ? 42 : 18)), ys);
            ctx.stroke();
            ctx.setLineDash([]);
          }
        }
        ctx.restore();
      }
    });
  }
}

class AIMA20TriangleView {
  constructor(prim) { this._prim = prim; this._r = new AIMA20TriangleRenderer(prim); }
  zOrder() { return 'top'; }
  update() {}
  renderer() { return this._r; }
}

class AIMA20TrianglePrimitive {
  constructor(series, chartRef) {
    this._series = series;
    this._chart = chartRef;
    this._view = new AIMA20TriangleView(this);
    this._req = null;
  }
  attached(p) { this._req = p.requestUpdate; if (p.chart) this._chart = p.chart; }
  detached() { this._req = null; }
  updateAllViews() {}
  paneViews() { return [this._view]; }
  requestUpdate() { if (this._req) this._req(); }
}

function aiMa20AlgoRefresh() {
  const symTf = `${typeof currentSym !== 'undefined' ? currentSym : 'BTC'}|${typeof activeTF !== 'undefined' ? activeTF : '1D'}`;
  if (AI_MA20_STATE._lastSymTf && AI_MA20_STATE._lastSymTf !== symTf) {
    aiMa20HideHoverCard(true);
  }
  AI_MA20_STATE._lastSymTf = symTf;
  const btn = document.getElementById('aiMa20AlgoToggle');
  if (btn) {
    const ev = AI_MA20_STATE.on ? aiMa20GetEval() : null;
    const n = ev && ev.metaActive ? ev.metaActive.length : 0;
    btn.classList.toggle('active', AI_MA20_STATE.on);
    btn.setAttribute('aria-pressed', String(AI_MA20_STATE.on));
    btn.textContent = AI_MA20_STATE.on ? `AI-MA20 (${n})` : 'AI-MA20';
  }
  if (AI_MA20_STATE.prim) AI_MA20_STATE.prim.requestUpdate();
}

// 注入主题自适应浮动小卡片样式
function aiMa20InjectStyles() {
  if (document.getElementById('aiMa20HoverStyles')) return;
  const st = document.createElement('style');
  st.id = 'aiMa20HoverStyles';
  st.textContent = `
    .ai-ma20-hover-card {
      position: fixed;
      z-index: 1200;
      width: 292px;
      max-width: calc(100vw - 20px);
      background: color-mix(in srgb, var(--surface) 96%, transparent);
      border: 1px solid var(--border-strong);
      border-radius: 10px;
      box-shadow: var(--shadow);
      color: var(--ink);
      font-family: inherit;
      font-size: 12px;
      line-height: 1.48;
      padding: 11px 13px;
      backdrop-filter: blur(10px);
      transition: opacity 0.12s ease;
      user-select: text;
    }
    .ai-ma20-hover-card[hidden] { display: none !important; }
    .ai-ma20-hover-card.tier-heavy { border-color: color-mix(in srgb, var(--up) 55%, var(--border)); }
    .ai-ma20-hover-card.tier-std { border-color: color-mix(in srgb, var(--accent) 55%, var(--border)); }
    .ai-ma20-hc-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 6px;
      padding-bottom: 7px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 8px;
    }
    .ai-ma20-hc-badge {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 700;
    }
    .ai-ma20-hc-badge.heavy { background: var(--up-soft); color: var(--up); border: 1px solid var(--up-border); }
    .ai-ma20-hc-badge.std   { background: var(--accent-soft); color: var(--accent); border: 1px solid var(--accent-border); }
    .ai-ma20-hc-badge.skip  { background: var(--down-soft); color: var(--down); border: 1px solid var(--down-border); }
    .ai-ma20-hc-sec {
      font-size: 11px;
      font-weight: 600;
      color: var(--muted);
      margin: 8px 0 5px;
    }
    .ai-ma20-hc-stat-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 5px;
    }
    .ai-ma20-hc-cell {
      background: var(--surface-2);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 5px 6px;
      text-align: center;
    }
    .ai-ma20-hc-cell span {
      display: block;
      font-size: 10px;
      color: var(--muted);
      margin-bottom: 1px;
    }
    .ai-ma20-hc-cell b {
      font-size: 12.5px;
      font-variant-numeric: tabular-nums;
      color: var(--ink);
    }
  `;
  document.head.appendChild(st);
}

// 渲染并定位精简版买点置信度与历史表现小卡片
function aiMa20RenderHoverCard(sg, anchorX, anchorY) {
  if (!sg) return;
  sg = aiMa20EnrichBar(sg.i, sg) || sg;
  aiMa20InjectStyles();
  let card = document.getElementById('aiMa20HoverCard');
  if (!card) {
    card = document.createElement('section');
    card.id = 'aiMa20HoverCard';
    card.className = 'ai-ma20-hover-card';
    card.hidden = true;
    card.addEventListener('mouseenter', () => {
      if (AI_MA20_STATE.hideTimer) {
        clearTimeout(AI_MA20_STATE.hideTimer);
        AI_MA20_STATE.hideTimer = null;
      }
    });
    card.addEventListener('mouseleave', () => {
      if (!AI_MA20_STATE.pinned) aiMa20ScheduleHide(180);
    });
    document.body.appendChild(card);
  }

  const ev = aiMa20GetEval();
  if (!ev) return;
  const cs = candlesFor('raw');
  const blind = typeof ANN !== 'undefined' && ANN.on && ANN.hideFuture;
  const cutoffBar = blind ? sg.i : Infinity;

  const isHeavy = sg.metaTier === 'heavy';
  const isStd = sg.metaTier === 'std';
  const tierCls = isHeavy ? 'heavy' : isStd ? 'std' : 'skip';
  const tierColor = isHeavy ? 'var(--up)' : isStd ? 'var(--accent)' : 'var(--down)';
  const scoreVal = sg.tradScore != null ? sg.tradScore : Math.round((sg.metaP || 0) * 100);
  const tierName = isHeavy ? '★ A档强推买点 · 2.0x重仓' : isStd ? '● B档合格买点 · 1.0x标准' : (scoreVal >= 35 ? '× C档观望 · 0x不买' : '× D档一票否决 · 0x不买');
  const tierRule = isHeavy ? '≥ 75分' : isStd ? '55 ~ 74分' : '< 55分';

  const pool = isHeavy ? ev.metaHeavy : isStd ? ev.metaStd : ev.metaSkipped;
  const st = ev.calcTierOrderStats(pool, cutoffBar);

  const fmtPct = v => ((v || 0) * 100).toFixed(1) + '%';
  const fmtR = v => (v >= 0 ? '+' : '') + (v || 0).toFixed(2) + 'R';
  const fmtPrice = v => {
    if (v == null || !Number.isFinite(v)) return '—';
    return v >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 1 })
         : v >= 1 ? v.toFixed(2) : v.toPrecision(4);
  };
  const reasonsHtml = (sg.metaReasons || []).slice(0, 4).map(r => {
    const col = r.impact >= 0 ? 'var(--up)' : 'var(--down)';
    return `<span style="display:inline-block;padding:1px 6px;border-radius:4px;background:var(--surface-2);border:1px solid color-mix(in srgb, ${col} 35%, var(--border));color:${col};font-size:10.5px;margin:1px 3px 1px 0">${r.text}</span>`;
  }).join('');
  const stStop = sg.structStop != null ? sg.structStop : sg.stop;
  const stRisk = sg.structRiskPct != null ? sg.structRiskPct : sg.riskPct;

  card.className = `ai-ma20-hover-card tier-${tierCls}`;
  card.innerHTML = `
    <div class="ai-ma20-hc-head">
      <span class="ai-ma20-hc-badge ${tierCls}">${tierName}</span>
      <span style="font-size:11px;color:var(--muted)">${sg.dateStr || annDate(cs[sg.i].time)}${sg.fng != null ? ` · <b style="color:${sg.fng>=56&&sg.fng<=74?'var(--down)':sg.fng>=26&&sg.fng<=55?'var(--up)':'var(--ink-2)'}">FGI=${sg.fng}</b>` : ''}</span>
    </div>

    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px">
      <span>查表置信分：<b style="color:${tierColor};font-size:14px">${scoreVal}分</b> <small style="color:var(--muted)">(${tierRule})</small></span>
      <span style="font-size:11px;color:var(--ink-2)">执行止损宽 <b style="color:var(--down)">${fmtPct(sg.riskPct)}</b>${sg.narrowStopProtected ? ' <small style="color:var(--warn)">(窄损托底)</small>' : ''}</span>
    </div>
    <div style="font-size:11px;color:var(--muted);padding-bottom:6px;border-bottom:1px solid var(--border)">
      买入 <b style="color:var(--ink)">${fmtPrice(sg.entry)}</b> · 执行止损 <b style="color:var(--down)">${fmtPrice(sg.stop)}</b> · 3根结构损 <b style="color:var(--warn)">${fmtPrice(stStop)}</b>
      <div style="font-size:10.5px;color:var(--muted);margin-top:2px">风控：冲2.5R移保本 · 冲4R锁+1.2R · ≥3根破MA20离场${!blind && sg.rB != null ? ` · 本笔 <b style="color:${sg.rB >= 0 ? 'var(--up)' : 'var(--down)'}">${fmtR(sg.rB)}</b> (${sg.exitReasonB || ''})` : ''}</div>
    </div>
    ${reasonsHtml ? `<div style="margin:5px 0 3px">${reasonsHtml}</div>` : ''}

    <div class="ai-ma20-hc-sec">
      该分档历史表现（共 <b style="color:var(--ink)">${st.n}</b> 笔${blind ? '·截至当根前' : ''}）
    </div>
    <div class="ai-ma20-hc-stat-grid">
      <div class="ai-ma20-hc-cell">
        <span>≤5根快损率</span>
        <b style="color:${st.stopLe5Rate <= 0.35 ? 'var(--up)' : 'var(--warn)'}">${fmtPct(st.stopLe5Rate)}</b>
      </div>
      <div class="ai-ma20-hc-cell">
        <span>≤10根止损率</span>
        <b style="color:${st.stopLe10Rate <= 0.40 ? 'var(--up)' : 'var(--warn)'}">${fmtPct(st.stopLe10Rate)}</b>
      </div>
      <div class="ai-ma20-hc-cell">
        <span>从未触止损</span>
        <b style="color:${tierColor}">${fmtPct(st.neverStopRate)}</b>
      </div>
      <div class="ai-ma20-hc-cell">
        <span>≥1R 达成率</span>
        <b>${fmtPct(st.ge1RRate)}</b>
      </div>
      <div class="ai-ma20-hc-cell">
        <span>≥3R 大波段率</span>
        <b style="color:${tierColor}">${fmtPct(st.ge3RRate)}</b>
      </div>
      <div class="ai-ma20-hc-cell" title="v2.1规则：破MA20离场+阶梯保本锁盈单笔均R（120根持有均R：${fmtR(st.meanRA)}）">
        <span>MA20单笔均R</span>
        <b style="color:${st.meanRB >= 0 ? 'var(--up)' : 'var(--down)'}">${fmtR(st.meanRB)}</b>
      </div>
    </div>
  `;

  card.hidden = false;

  const chartBox = document.getElementById('chart');
  const rect = chartBox.getBoundingClientRect();
  const cardW = card.offsetWidth || 284;
  const cardH = card.offsetHeight || 195;
  let left = rect.left + (Number.isFinite(anchorX) ? anchorX : rect.width / 2) + 16;
  if (left + cardW > window.innerWidth - 12) {
    left = Math.max(12, rect.left + (Number.isFinite(anchorX) ? anchorX : rect.width / 2) - cardW - 16);
  }
  let top = rect.top + (Number.isFinite(anchorY) ? anchorY : rect.height / 2) - Math.round(cardH * 0.45);
  top = Math.max(12, Math.min(window.innerHeight - cardH - 12, top));
  card.style.left = Math.round(left) + 'px';
  card.style.top = Math.round(top) + 'px';
}

function aiMa20ScheduleHide(delayMs = 180) {
  if (AI_MA20_STATE.hideTimer) clearTimeout(AI_MA20_STATE.hideTimer);
  AI_MA20_STATE.hideTimer = setTimeout(() => {
    const card = document.getElementById('aiMa20HoverCard');
    if (card && card.matches(':hover')) return;
    aiMa20HideHoverCard(false);
  }, delayMs);
}

function aiMa20HideHoverCard(force = false) {
  if (AI_MA20_STATE.pinned && !force) return;
  if (AI_MA20_STATE.hideTimer) {
    clearTimeout(AI_MA20_STATE.hideTimer);
    AI_MA20_STATE.hideTimer = null;
  }
  AI_MA20_STATE.pinned = false;
  AI_MA20_STATE.hoverSig = null;
  AI_MA20_STATE.hoverBarIdx = -1;
  const card = document.getElementById('aiMa20HoverCard');
  if (card) card.hidden = true;
  if (AI_MA20_STATE.prim) AI_MA20_STATE.prim.requestUpdate();
}

// 检测鼠标坐标 (mx, my) 或 Lightweight Charts 时间戳 paramTime 是否命中三角形买点 ▲
function aiMa20FindTriangleAtPoint(mx, my, paramTime = null, isClick = false) {
  const sigs = aiMa20VisibleSignals();
  if (!sigs.length) return null;
  const cs = candlesFor('raw');
  const viewCs = candlesFor(activeView);
  const ts = chart.timeScale();

  // 点击时放宽判定半径（点击三角形、2x/1x标签或对应信号K线下半部均可触发）
  const maxDx = isClick ? 24 : 18;
  const minDy = isClick ? -48 : -20;
  const maxDy = isClick ? 42 : 30;

  let best = null;
  let bestDist = Infinity;
  for (const sg of sigs) {
    const rawBar = cs[sg.i];
    if (!rawBar) continue;
    let drawTime = rawBar.time;
    let drawLow = rawBar.low;
    if (activeView !== 'raw' && typeof cover67IndicatorBar === 'function') {
      const j = cover67IndicatorBar(rawBar.time, viewCs);
      if (j >= 0 && viewCs[j]) {
        drawTime = viewCs[j].time;
        drawLow = viewCs[j].low;
      }
    }
    const sx = ts.timeToCoordinate(drawTime);
    const yl = candleSeries.priceToCoordinate(drawLow);
    if (sx == null || yl == null) continue;

    const dx = Math.abs(mx - sx);
    const dy = my - (yl + 15);
    const timeMatched = paramTime != null && drawTime === paramTime;

    if ((dx <= maxDx && dy >= minDy && dy <= maxDy) || (isClick && timeMatched && Math.abs(dy) <= 85)) {
      const dist = Math.hypot(dx, dy) - (timeMatched ? 8 : 0);
      if (dist < bestDist) {
        bestDist = dist;
        best = { sg, sx, yl: yl + 14 };
      }
    }
  }
  return best;
}

function aiMa20HandleMove(mx, my, paramTime = null) {
  if (!Number.isFinite(mx) || !Number.isFinite(my)) return;
  const chartEl = document.getElementById('chart');
  const hit = aiMa20FindTriangleAtPoint(mx, my, paramTime, false);
  if (hit) {
    if (AI_MA20_STATE.hideTimer) {
      clearTimeout(AI_MA20_STATE.hideTimer);
      AI_MA20_STATE.hideTimer = null;
    }
    if (chartEl) chartEl.style.cursor = 'pointer';
    if (!AI_MA20_STATE.pinned && AI_MA20_STATE.hoverBarIdx !== hit.sg.i) {
      AI_MA20_STATE.hoverSig = hit.sg;
      AI_MA20_STATE.hoverBarIdx = hit.sg.i;
      AI_MA20_STATE.orderListTab = 'same';
      if (AI_MA20_STATE.prim) AI_MA20_STATE.prim.requestUpdate();
      aiMa20RenderHoverCard(hit.sg, hit.sx, hit.yl);
    }
  } else {
    if (chartEl && chartEl.style.cursor === 'pointer') chartEl.style.cursor = '';
    if (AI_MA20_STATE.hoverBarIdx >= 0 && !AI_MA20_STATE.pinned) {
      aiMa20ScheduleHide(180);
    }
  }
}

function aiMa20HandleClick(mx, my, paramTime = null, targetEl = null) {
  if (typeof annChartPickBusy === 'function' && annChartPickBusy()) return;
  if (!Number.isFinite(mx) || !Number.isFinite(my)) return;
  const hit = aiMa20FindTriangleAtPoint(mx, my, paramTime, true);
  if (hit) {
    if (AI_MA20_STATE.hideTimer) {
      clearTimeout(AI_MA20_STATE.hideTimer);
      AI_MA20_STATE.hideTimer = null;
    }
    AI_MA20_STATE.pinned = true;
    AI_MA20_STATE.hoverSig = hit.sg;
    AI_MA20_STATE.hoverBarIdx = hit.sg.i;
    AI_MA20_STATE.orderListTab = 'same';
    if (AI_MA20_STATE.prim) AI_MA20_STATE.prim.requestUpdate();
    aiMa20RenderHoverCard(hit.sg, hit.sx, hit.yl);
    // 若标注抽屉正打开，同步右侧抽屉到该买点（不移动当前图表视口）
    if (typeof ANN !== 'undefined' && ANN.on && Array.isArray(ANN.sigs)) {
      const ix = ANN.sigs.findIndex(x => x.i === hit.sg.i);
      if (ix >= 0 && ANN.idx !== ix) {
        ANN.idx = ix;
        if (typeof annRender === 'function') annRender();
        if (typeof annDraw === 'function') annDraw();
      }
    }
  } else if (AI_MA20_STATE.pinned) {
    const card = document.getElementById('aiMa20HoverCard');
    if (!targetEl || !card || !card.contains(targetEl)) {
      aiMa20HideHoverCard(true);
    }
  }
}

// 初始化主图按钮、Primitive 与 Lightweight Charts 原生 + DOM 双通道监听
(function initAIMA20Algo() {
  const indToggle = document.getElementById('indicatorToggle');
  if (indToggle && !document.getElementById('aiMa20AlgoToggle')) {
    const btn = document.createElement('button');
    btn.id = 'aiMa20AlgoToggle';
    btn.title = 'AI-MA20交易算法 (v1.0冻结版)：在主图显示高/中置信度三角形买点 (▲2x/▲1x)，鼠标滑至或点击三角形查看该置信度历史止损率与历史订单卡片';
    btn.setAttribute('aria-pressed', String(AI_MA20_STATE.on));
    btn.textContent = 'AI-MA20';
    btn.addEventListener('click', () => {
      AI_MA20_STATE.on = !AI_MA20_STATE.on;
      try { localStorage.setItem('chanlun.ai_ma20.indicator.v2', AI_MA20_STATE.on ? 'on' : 'off'); } catch (_) {}
      if (!AI_MA20_STATE.on) aiMa20HideHoverCard(true);
      aiMa20AlgoRefresh();
    });
    indToggle.appendChild(btn);
  }

  if (typeof candleSeries !== 'undefined' && typeof chart !== 'undefined' && !AI_MA20_STATE.prim) {
    AI_MA20_STATE.prim = new AIMA20TrianglePrimitive(candleSeries, chart);
    candleSeries.attachPrimitive(AI_MA20_STATE.prim);
  }

  // 通道 1：Lightweight Charts 原生 subscribeCrosshairMove 与 subscribeClick（坐标与 timeToCoordinate/priceToCoordinate 完全同源）
  if (typeof chart !== 'undefined') {
    chart.subscribeCrosshairMove(param => {
      if (!param || !param.point) return;
      aiMa20HandleMove(param.point.x, param.point.y, param.time ?? null);
    });
    chart.subscribeClick(param => {
      if (!param || !param.point) return;
      aiMa20HandleClick(param.point.x, param.point.y, param.time ?? null, null);
    });
  }

  // 通道 2：DOM 鼠标滑动与点击兜底（处理光标在 K 线下方空白处移动时触发）
  const chartEl = document.getElementById('chart');
  if (chartEl) {
    chartEl.addEventListener('mousemove', e => {
      const rect = chartEl.getBoundingClientRect();
      aiMa20HandleMove(e.clientX - rect.left, e.clientY - rect.top, null);
    });
    chartEl.addEventListener('click', e => {
      const rect = chartEl.getBoundingClientRect();
      aiMa20HandleClick(e.clientX - rect.left, e.clientY - rect.top, null, e.target);
    });
  }

  window.addEventListener('keydown', e => {
    if (e.key === 'Escape') aiMa20HideHoverCard(true);
  });

  aiMa20AlgoRefresh();
})();
