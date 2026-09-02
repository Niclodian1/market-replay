// js/03-charts.js — 图表创建 / MA / 时间轴同步 / overlay原语 / 测距
// ---- 主图 ----
function chartOptions(){
  const T = t();
  return {
    layout: {
      background: { type: LightweightCharts.ColorType.Solid, color: T.surface },
      textColor: T.ink2, fontSize: 11, fontFamily: FONT, attributionLogo: false,
      localization: { timeFormatter: fmtTime },
    },
    grid: { vertLines: { color: T.grid }, horzLines: { color: T.grid } },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: T.border, lineStyle: LightweightCharts.LineStyle.Dashed, labelBackgroundColor: T.ink2, labelTextColor: T.surface },
      horzLine: { color: T.border, labelBackgroundColor: T.ink2, labelTextColor: T.surface },
    },
    rightPriceScale: { borderColor: T.border },
    timeScale: { borderColor: T.border, timeVisible: activeTF !== '1D', secondsVisible: false, rightOffset: 3 },
    autoSize: true,
  };
}

const chart = LightweightCharts.createChart(document.getElementById('chart'), chartOptions());
const candleSeries = chart.addCandlestickSeries({
  upColor: t().up, downColor: t().down,
  borderUpColor: t().up, borderDownColor: t().down,
  wickUpColor: t().up, wickDownColor: t().down,
});

// ---- 成交量面板 ----
const volumeChart = LightweightCharts.createChart(document.getElementById('volume'), {
  ...chartOptions(),
  grid: { vertLines: { visible: false }, horzLines: { color: t().grid } },
  timeScale: { ...chartOptions().timeScale, visible: false },
});
const volumeSeries = volumeChart.addHistogramSeries({
  priceFormat: { type: 'volume' }, priceScaleId: '',
  lastValueVisible: false, priceLineVisible: false,
});
volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.12, bottom: 0.04 } });

// ---- MACD 面板 ----
const macdChart = LightweightCharts.createChart(document.getElementById('macd'), {
  ...chartOptions(),
  grid: { vertLines: { visible: false }, horzLines: { color: t().grid } },
  timeScale: { ...chartOptions().timeScale, visible: false },
});
const macdHistSeries = macdChart.addHistogramSeries({
  priceScaleId: '', lastValueVisible: false, priceLineVisible: false,
});
const macdLineSeries = macdChart.addLineSeries({
  color: t().ink2, lineWidth: 1, priceScaleId: '',
  lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
});
const macdSignalSeries = macdChart.addLineSeries({
  color: t().muted, lineWidth: 1, priceScaleId: '',
  lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
});

// ---- MA 均线(主图叠加, 周期可配) ----
// 默认 MA20/MA60; 输入框可改周期, 逗号分隔, 留空隐藏。颜色循环取 MA_COLORS。
const MA_COLORS = ['#f5a623', '#4a90d9', '#b45cd6', '#2fbf8f', '#e05c78', '#8a9299'];
const maSeries = [];   // {period, series}
function parseMaPeriods(){
  const raw = document.getElementById('maPeriods').value.trim();
  if (!raw) return [];
  return [...new Set(raw.split(/[,，\s]+/).map(Number).filter(n => n >= 1 && n <= 2000))].slice(0, 6);
}
function sma(closes, period){
  const out = new Array(closes.length).fill(null);
  let sum = 0;
  for (let i = 0; i < closes.length; i++){
    sum += closes[i];
    if (i >= period) sum -= closes[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}
function rebuildMA(){
  const periods = parseMaPeriods();
  const cs = candlesFor(activeView);
  const closes = cs.map(c => c.close);
  // 复用已有 series, 多退少补
  while (maSeries.length < periods.length){
    const s = chart.addLineSeries({
      lineWidth: 1.4, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    maSeries.push({ period: 0, series: s });
  }
  while (maSeries.length > periods.length){
    const { series } = maSeries.pop();
    chart.removeSeries(series);
  }
  periods.forEach((p, i) => {
    const item = maSeries[i];
    item.period = p;
    item.series.applyOptions({ color: MA_COLORS[i % MA_COLORS.length] });
    const vals = sma(closes, p);
    item.series.setData(cs.map((c, j) => vals[j] != null ? { time: c.time, value: vals[j] } : null).filter(Boolean));
  });
}
macdHistSeries.priceScale().applyOptions({ scaleMargins: { top: 0.25, bottom: 0.05 } });

// ---- 三面板时间轴同步 ----
const syncAll = r => {
  if (!r) return;
  chart.timeScale().setVisibleLogicalRange(r);
  volumeChart.timeScale().setVisibleLogicalRange(r);
  macdChart.timeScale().setVisibleLogicalRange(r);
};
chart.timeScale().subscribeVisibleLogicalRangeChange(syncAll);
volumeChart.timeScale().subscribeVisibleLogicalRangeChange(syncAll);
macdChart.timeScale().subscribeVisibleLogicalRangeChange(syncAll);
// 分钟级复盘(非练习): 向左滚近窗口左缘自动前插上一分片, 并平移视口保持画面不跳
// 练习模式(trLocked)不走此漫游——练习视口有硬边界, 只展示本次练习窗口
chart.timeScale().subscribeVisibleLogicalRangeChange(r => {
  if (!chunkedActive() || trLocked() || !r || !ChunkStore.win || ChunkStore._extending) return;
  if (r.from < 30){
    ChunkStore._extending = true;
    ChunkStore.extend(-1).then(added => {
      ChunkStore._extending = false;
      if (added > 0){
        refreshAll();
        chart.timeScale().setVisibleLogicalRange({ from: r.from + added, to: r.to + added });
      }
    });
  }
});
// 练习缩放捕获: 用户滚轮/双指缩放时记录可见宽度, lockViewport 跟随该级别
// (_lvLocking 标记程序性设置, 避免把锁定值误当成用户缩放)
chart.timeScale().subscribeVisibleLogicalRangeChange(r => {
  if (!trLocked() || !r || _lvLocking) return;
  TR.zoomBars = Math.min(1000, Math.max(30, Math.round(r.to - r.from) - 2));
});

// ---- 笔/线段/中枢 overlay primitive ----
function getLayerData(){
  const th = themeKey();
  return {
    strokes:  layers.strokes  ? strokesFor(activeView)  : null,
    segments: layers.segments ? segmentsFor(activeView) : null,
    pivots:   layers.pivots   ? pivotsFor(activeView, pivotMode) : null,
    segUp:   th === 'dark' ? '#199e70' : '#1baf7a',
    segDown: th === 'dark' ? '#d95926' : '#eb6834',
    // 老笔口径下笔线用紫色(原「老笔」对照层配色), 提示当前为对照链
    strokeColor: strokeStd === 'old'
      ? (th === 'dark' ? 'rgba(176,138,255,0.8)' : 'rgba(122,60,214,0.75)')
      : 'rgba(137,135,129,0.5)',
    strokeDash: strokeStd === 'old' ? [5,4] : [2,3],
    pivotFill: th === 'dark' ? 'rgba(255,255,255,0.07)' : 'rgba(11,11,11,0.06)',
    pivotEdge: th === 'dark' ? 'rgba(255,255,255,0.30)' : 'rgba(11,11,11,0.30)',
  };
}
class ChanOverlayRenderer {
  constructor(prim){ this._prim = prim; }
  draw(target){
    const Ld = getLayerData();
    if (!Ld) return;
    const th = themeKey();
    const series = this._prim._series;
    const timeScale = this._prim._chart.timeScale();
    // v4.2: useMediaCoordinateSpace 回调式; priceToCoordinate 在 series 上
    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context;
      ctx.lineCap = 'round';
      const X = t => timeToX(t);   // 支持K线范围外的时间(手绘线可拉出K线区)
      const Y = p => series.priceToCoordinate(p);

      if (Ld.strokes && Ld.strokes.length){
        ctx.lineWidth = 2.8;
        ctx.strokeStyle = Ld.strokeColor;
        ctx.setLineDash(Ld.strokeDash || [2,3]);
        for (const s of Ld.strokes){
          const x1 = X(s[0]), y1 = Y(s[1]);
          const x2 = X(s[2]), y2 = Y(s[3]);
          if (x1 == null || x2 == null || y1 == null || y2 == null) continue;
          ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
        }
        ctx.setLineDash([]);
      }
      if (Ld.segments && Ld.segments.length){
        ctx.lineWidth = 2.5;
        for (const s of Ld.segments){
          ctx.strokeStyle = s[4] === 1 ? Ld.segUp : Ld.segDown;
          const x1 = X(s[0]), y1 = Y(s[1]);
          const x2 = X(s[2]), y2 = Y(s[3]);
          if (x1 == null || x2 == null || y1 == null || y2 == null) continue;
          ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
        }
      }
      if (Ld.pivots && Ld.pivots.length){
        ctx.lineWidth = 1;
        for (const p of Ld.pivots){
          const x1 = X(p[0]), x2 = X(p[1]);
          const yz = Y(p[3]);   // ZG
          const yd = Y(p[2]);   // ZD
          if (x1 == null || x2 == null || yz == null || yd == null) continue;
          const X0 = Math.min(x1, x2), W = Math.abs(x2 - x1);
          ctx.fillStyle = Ld.pivotFill;
          ctx.fillRect(X0, Math.min(yz, yd), W, Math.abs(yd - yz));
          ctx.strokeStyle = Ld.pivotEdge;
          ctx.setLineDash([3,3]);
          ctx.beginPath(); ctx.moveTo(X0, yz); ctx.lineTo(X0 + W, yz); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(X0, yd); ctx.lineTo(X0 + W, yd); ctx.stroke();
          ctx.setLineDash([]);
        }
      }
      // ---- 手绘趋势线(两点, time/price 锚定) ----
      const trends = window._trendLines || [];
      if (trends.length){
        ctx.lineWidth = 2;
        for (const r of trends){
          const x1 = X(r.t1), y1 = Y(r.p1), x2 = X(r.t2), y2 = Y(r.p2);
          if (x1 == null || x2 == null || y1 == null || y2 == null) continue;
          ctx.strokeStyle = r.color || '#4a90d9';
          ctx.setLineDash([]);
          ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
          // 端点小圆
          ctx.fillStyle = r.color || '#4a90d9';
          ctx.beginPath(); ctx.arc(x1, y1, 3, 0, Math.PI*2); ctx.fill();
          ctx.beginPath(); ctx.arc(x2, y2, 3, 0, Math.PI*2); ctx.fill();
        }
      }
      // ---- 画线中: 趋势线第2端跟随鼠标的虚线预览 ----
      const pend = window._pendingTrend;
      if (pend){
        const qx1 = X(pend.t1), qy1 = Y(pend.p1), qx2 = X(pend.t2), qy2 = Y(pend.p2);
        if (qx1 != null && qy1 != null && qx2 != null && qy2 != null){
          const c = pend.color || '#4a90d9';
          ctx.lineWidth = 2;
          ctx.strokeStyle = c;
          ctx.globalAlpha = 0.85;
          ctx.setLineDash([6,4]);
          ctx.beginPath(); ctx.moveTo(qx1, qy1); ctx.lineTo(qx2, qy2); ctx.stroke();
          ctx.setLineDash([]);
          ctx.fillStyle = c;
          ctx.beginPath(); ctx.arc(qx2, qy2, 3, 0, Math.PI*2); ctx.fill();
          ctx.globalAlpha = 1;
        }
      }
      // ---- Shift+拖动 测距: 两点间波动距离(波幅矩形 + 对角线 + 读数) ----
      const m = window._measure;
      if (m){
        const mx1 = X(m.t1), my1 = Y(m.p1), mx2 = X(m.t2), my2 = Y(m.p2);
        if (mx1 != null && my1 != null && mx2 != null && my2 != null){
          ctx.save();
          ctx.fillStyle = m.dir >= 0 ? 'rgba(47,191,143,.10)' : 'rgba(217,89,38,.10)';
          ctx.fillRect(Math.min(mx1,mx2), Math.min(my1,my2), Math.abs(mx2-mx1), Math.abs(my2-my1));
          ctx.strokeStyle = m.color; ctx.lineWidth = 1.6; ctx.setLineDash([5,4]);
          ctx.beginPath(); ctx.moveTo(mx1,my1); ctx.lineTo(mx2,my2); ctx.stroke();
          ctx.setLineDash([]);
          ctx.fillStyle = m.color;
          ctx.beginPath(); ctx.arc(mx1,my1,3,0,Math.PI*2); ctx.fill();
          ctx.beginPath(); ctx.arc(mx2,my2,3,0,Math.PI*2); ctx.fill();
          // 读数标签(跟随终点, 自动避开图表边缘)
          ctx.font = '11px ' + FONT;
          const lines = m.label.split('\n');
          const lw = Math.max(...lines.map(s => ctx.measureText(s).width)) + 14;
          const lh = lines.length * 15 + 8;
          let lx = mx2 + 12, ly = my2 - lh - 8;
          lx = Math.min(Math.max(4, lx), chartElW() - lw - 4);
          if (ly < 4) ly = my2 + 12;
          ctx.fillStyle = th === 'dark' ? 'rgba(20,20,20,.92)' : 'rgba(255,255,255,.95)';
          ctx.strokeStyle = m.color; ctx.lineWidth = 1;
          ctx.fillRect(lx, ly, lw, lh); ctx.strokeRect(lx, ly, lw, lh);
          ctx.fillStyle = th === 'dark' ? '#e6e3dc' : '#22211f';
          ctx.textBaseline = 'top';
          lines.forEach((s,i) => {
            ctx.fillStyle = i === 0 ? m.color : ctx.fillStyle;   // 首行价格变动用主题色
            ctx.fillText(s, lx + 7, ly + 5 + i * 15);
          });
          ctx.restore();
        }
      }
    });
  }
}
class ChanOverlayView {
  constructor(prim){ this._prim = prim; this._renderer = new ChanOverlayRenderer(prim); }
  update(){}
  renderer(){ return this._renderer; }
}
class ChanOverlayPrimitive {
  constructor(series, chart){ this._series = series; this._chart = chart; this._view = new ChanOverlayView(this); this._req = null; }
  attached(p){ this._req = p.requestUpdate; }   // 画线预览需主动请求重绘
  updateAllViews(){}
  paneViews(){ return [this._view]; }
}
const chanOverlayPrim = new ChanOverlayPrimitive(candleSeries, chart);
candleSeries.attachPrimitive(chanOverlayPrim);
const refreshChanOverlay = () => { if (chanOverlayPrim._req) chanOverlayPrim._req(); };

// ---- 手绘线时间↔坐标换算 ----
// LW 的 timeToCoordinate/coordinateToTime 在数据范围(首/尾K线)之外返回 null,
// 画线需要能拉到K线之外的空白区 — 用首/尾各两根K线的坐标线性外推补齐。
function _barAnchors(){
  const cs = candlesFor(activeView);
  const ts = chart.timeScale();
  if (!cs.length) return null;
  if (cs.length === 1){
    const x = ts.timeToCoordinate(cs[0].time);
    return x == null ? null : { one: true, tF: cs[0].time, xF: x };
  }
  const tF = cs[0].time, tS = cs[1].time, tL = cs[cs.length-1].time, tP = cs[cs.length-2].time;
  const xF = ts.timeToCoordinate(tF), xS = ts.timeToCoordinate(tS);
  const xL = ts.timeToCoordinate(tL), xP = ts.timeToCoordinate(tP);
  if (xF == null || xS == null || xL == null || xP == null) return null;
  return { one: false, tF, xF, tS, xS, tL, xL, tP, xP };
}
// 时间 → x 像素: 整数K线时间走 LW 原生; 小数时间用相邻两根K线插值(LW 对非K线时间返回 null); 范围外线性外推
function timeToX(t){
  const ts = chart.timeScale();
  const x = ts.timeToCoordinate(t);
  if (x != null) return x;
  const a = _barAnchors();
  if (!a) return null;
  if (a.one) return a.xF;
  if (t >= a.tL) return a.xL + (t - a.tL) / (a.tL - a.tP) * (a.xL - a.xP);
  if (t <= a.tF) return a.xF + (t - a.tF) / (a.tS - a.tF) * (a.xS - a.xF);
  // 范围内的小数时间: 定位相邻两根K线, 按时间比例线性插值
  const cs = candlesFor(activeView);
  const n = cs.length;
  let i = Math.floor((t - a.tF) / (a.tL - a.tF) * (n - 1));
  i = Math.max(0, Math.min(n - 2, i));
  while (i > 0 && cs[i].time > t) i--;
  while (i < n - 2 && cs[i+1].time <= t) i++;
  const xa = ts.timeToCoordinate(cs[i].time), xb = ts.timeToCoordinate(cs[i+1].time);
  if (xa == null || xb == null) return null;
  return xa + (t - cs[i].time) / (cs[i+1].time - cs[i].time) * (xb - xa);
}
// 鼠标 x 像素 → 精确时间(K线之外可外推, K线之间含小数部分, 完全跟手)
function xToTime(x){
  const a = _barAnchors();
  if (!a) return null;
  if (a.one) return a.tF;
  if (x >= a.xL) return a.tL + (x - a.xL) / (a.xL - a.xP) * (a.tL - a.tP);
  if (x <= a.xF) return a.tF + (x - a.xF) / (a.xS - a.xF) * (a.tS - a.tF);
  // 范围内: coordinateToTime 给出 ≤x 的K线时间, 再按相邻K线间距补小数部分
  const t0 = chart.timeScale().coordinateToTime(x);
  if (t0 == null) return null;
  const x0 = chart.timeScale().timeToCoordinate(t0);
  if (x0 == null) return t0;
  const cs = candlesFor(activeView);
  const i = idxMap[t0] ?? 0;
  const dt = i > 0 ? t0 - cs[i-1].time : cs[1].time - cs[0].time;
  const xp = i > 0 ? chart.timeScale().timeToCoordinate(cs[i-1].time) : x0 - (x0 - a.xS);
  if (xp == null || xp === x0) return t0;
  return t0 + (x - x0) / (x0 - xp) * dt;
}

// ---- Shift+单击测距: 从一个点到另一个点的波动距离 ----
// Shift+单击锚定起点后即可松开左键, 移动鼠标仍实时显示 Δ价格 / 波幅% / K线数 / 时间跨度;
// 再次 Shift+单击固定终点, 读数保留(随缩放平移跟随); 普通单击或 Esc 清除。
const chartElW = () => document.getElementById('chart').clientWidth;
function humanSpan(sec){
  const d = Math.floor(sec/86400), h = Math.floor(sec % 86400 / 3600), mi = Math.round(sec % 3600 / 60);
  if (d) return `${d}天${h}小时`;
  if (h) return `${h}小时${mi}分`;
  return `${mi}分钟`;
}
function setupMeasure(){
  const chartEl = document.getElementById('chart');
  let measuring = false;   // true=移动鼠标实时更新终点(无需按住左键)
  const rightAxisW = () => { try { return chart.priceScale('right').width(); } catch { return 60; } };
  const pickPoint = e => {
    const rect = chartEl.getBoundingClientRect();
    const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
    if (cx >= chartEl.clientWidth - rightAxisW()) return null;
    const t = xToTime(cx), pr = cursorPrice(cy);
    return t == null || pr == null ? null : { t, p: +pr.toFixed(2) };
  };
  // capture 阶段拦截 shift+左键, 阻止 lightweight-charts 把它当平移手势
  chartEl.addEventListener('mousedown', e => {
    if (!e.shiftKey || e.button !== 0){
      if (window._measure){ window._measure = null; refreshChanOverlay(); }   // 普通单击清除测量
      return;
    }
    e.preventDefault(); e.stopPropagation();
    const pt = pickPoint(e);
    if (!pt){ flashBtn('snapshotBtn', '✗ 该位置无法定位'); return; }
    if (!measuring){
      // 第1次 Shift+单击: 锚定起点; 松开左键后移动鼠标仍实时测量
      measuring = true;
      window._measure = { t1: pt.t, p1: pt.p, t2: pt.t, p2: pt.p, dir: 1, color: '#4a90d9', label: 'Δ 0.00' };
      flashBtn('snapshotBtn', '测距: 起点已定, 移动鼠标读数; 再 Shift+单击固定终点');
    } else {
      // 第2次 Shift+单击: 固定终点, 读数保留(单击/Esc清除)
      measuring = false;
      flashBtn('snapshotBtn', '✓ 测距完成(单击或Esc清除)');
    }
    refreshChanOverlay();
  }, true);
  chartEl.addEventListener('mousemove', e => {
    if (!measuring || !window._measure) return;
    const pt = pickPoint(e);
    if (!pt) return;
    const m = window._measure;
    const dp = pt.p - m.p1;
    m.t2 = pt.t; m.p2 = pt.p; m.dir = dp >= 0 ? 1 : -1;
    const cs = candlesFor(activeView);
    const dt = cs.length > 1 ? cs[cs.length-1].time - cs[cs.length-2].time : 86400;   // 一根K线秒数
    const nb = Math.max(0, Math.round(Math.abs(pt.t - m.t1) / dt));
    m.label = `Δ ${dp >= 0 ? '+' : ''}${dp.toFixed(2)} (${fmtRet(dp/m.p1*100)})\n${nb}根K线 · ${humanSpan(Math.abs(pt.t - m.t1))}`;
    refreshChanOverlay();
  });
  window.addEventListener('keydown', e => {
    if (e.key === 'Escape' && window._measure){ window._measure = null; refreshChanOverlay(); }
  });
}

// ---- 回测交易 overlay: 入场点●→出场点■ 连线(绿盈红亏, 止损为虚线) ----
class TradeOverlayRenderer {
  constructor(prim){ this._prim = prim; }
  draw(target){
    const list = layers.trades ? tradesFor() : [];
    if (!list.length && !selectedTrade) return;
    const series = this._prim._series;
    const timeScale = this._prim._chart.timeScale();
    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context;
      ctx.lineCap = 'round';
      const X = tt => timeScale.timeToCoordinate(tt);
      const Y = p => series.priceToCoordinate(p);
      const H = scope.mediaSize.height;
      const dim = selectedTrade != null;
      for (const tr of list){
        if (tr !== selectedTrade) this._drawTrade(ctx, X, Y, H, tr, false, dim);
      }
      if (selectedTrade) this._drawTrade(ctx, X, Y, H, selectedTrade, true, false);
    });
  }
  _drawTrade(ctx, X, Y, H, tr, isSel, dim){
    const xi = X(tr.entry_t), yi = Y(tr.entry);
    const xo = X(tr.exit_ts != null ? tr.exit_ts : tr.entry_t), yo = Y(tr.exit);
    if (xi == null || xo == null || yi == null || yo == null) return;
    const win = tr.ret > 0;
    const col = win ? t().up : t().down;
    ctx.save();
    ctx.globalAlpha = isSel ? 1 : (dim ? 0.22 : 0.75);
    ctx.lineWidth = isSel ? 2.5 : 1.5;
    ctx.strokeStyle = col;
    if (tr.reason === '止损') ctx.setLineDash([5,3]);
    ctx.beginPath(); ctx.moveTo(xi, yi); ctx.lineTo(xo, yo); ctx.stroke();
    ctx.setLineDash([]);
    // 入场圆点: 买点类型色
    ctx.fillStyle = kindColor(tr.type);
    ctx.beginPath(); ctx.arc(xi, yi, isSel ? 4.5 : 3, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = t().surface; ctx.lineWidth = 1; ctx.stroke();
    // 出场方块: 盈亏色
    const s = isSel ? 3.5 : 2.5;
    ctx.fillStyle = col;
    ctx.fillRect(xo - s, yo - s, s * 2, s * 2);
    // 选中: 入/出场竖直参考线贯穿面板
    if (isSel){
      ctx.strokeStyle = t().muted;
      ctx.lineWidth = 1;
      ctx.setLineDash([3,4]);
      ctx.beginPath(); ctx.moveTo(xi, 0); ctx.lineTo(xi, H); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(xo, 0); ctx.lineTo(xo, H); ctx.stroke();
    }
    ctx.restore();
  }
}
class TradeOverlayView {
  constructor(prim){ this._prim = prim; this._renderer = new TradeOverlayRenderer(prim); }
  update(){}
  renderer(){ return this._renderer; }
}
class TradeOverlayPrimitive {
  constructor(series, chart){ this._series = series; this._chart = chart; this._view = new TradeOverlayView(this); this._req = null; }
  attached(p){ this._req = p.requestUpdate; }
  updateAllViews(){}
  paneViews(){ return [this._view]; }
}
const tradeOverlay = new TradeOverlayPrimitive(candleSeries, chart);
candleSeries.attachPrimitive(tradeOverlay);
const refreshTrades = () => { if (tradeOverlay._req) tradeOverlay._req(); };

// ---- 三面板纵向虚线联动(十字光标连接 K线/成交量/MACD) ----
// 各面板右侧价格轴宽度不同会导致同 bar 像素 x 不同, 故每面板用自己 timeScale 算 x;
// 配合 alignPanes() 收窄量/MACD 面板使时间刻度像素一致, 虚线连续贯穿同 bar。
let syncT = null;
class VLineRenderer {
  constructor(prim){ this._prim = prim; }
  draw(target){
    if (syncT == null) return;
    const x = this._prim._chart.timeScale().timeToCoordinate(syncT);
    if (x == null) return;
    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context;
      ctx.save();
      ctx.lineCap = 'butt';
      ctx.lineWidth = 1;
      ctx.strokeStyle = t().muted;
      ctx.globalAlpha = 0.75;
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, scope.mediaSize.height);
      ctx.stroke();
      ctx.restore();
    });
  }
}
class VLineView {
  constructor(prim){ this._prim = prim; this._renderer = new VLineRenderer(prim); }
  update(){}
  renderer(){ return this._renderer; }
}
class VLinePrimitive {
  constructor(series, chart){ this._series = series; this._chart = chart; this._view = new VLineView(this); this._req = null; }
  attached(p){ this._req = p.requestUpdate; }
  updateAllViews(){}
  paneViews(){ return [this._view]; }
}
const vlineMain = new VLinePrimitive(candleSeries, chart);
const vlineVolume = new VLinePrimitive(volumeSeries, volumeChart);
const vlineMacd = new VLinePrimitive(macdHistSeries, macdChart);
candleSeries.attachPrimitive(vlineMain);
volumeSeries.attachPrimitive(vlineVolume);
macdHistSeries.attachPrimitive(vlineMacd);
const refreshVLine = () => {
  if (vlineMain._req) vlineMain._req();
  if (vlineVolume._req) vlineVolume._req();
  if (vlineMacd._req) vlineMacd._req();
};
