/* 笔的三种口径 · 对照页
 * 依赖: lightweight-charts(v4 standalone) + js/09-lab-engine.js + bi-compare-data.js
 * 思路: 同一份 1D K线, 用三套规则各算一遍"笔", 在同一个图上叠出来, 差异笔标红。
 */
(function () {
  'use strict';
  const E = window.LabEngine;
  const ROWS = window.BI_DATA;
  if (!E || !ROWS) { document.body.innerHTML = '<p style="padding:24px">数据或引擎没加载成功</p>'; return; }

  const CS = ROWS.map(([t, o, h, l, c]) => ({ time: t, open: o, high: h, low: l, close: c }));
  const MERGED = E.merge(CS);
  const dstr = ts => new Date(ts * 1000).toISOString().slice(0, 10);
  const md = ts => dstr(ts).slice(5);
  const num = v => Math.round(v).toLocaleString('en-US');

  // ---------------------------------------------------------------- 极简 K 线示意图
  function mini(bars, opt) {
    opt = opt || {};
    const W = 200, H = 92, padX = 12, padY = 12;
    const n = bars.length;
    const step = (W - 2 * padX) / n;
    let hi = -Infinity, lo = Infinity;
    bars.forEach(b => { hi = Math.max(hi, b[0]); lo = Math.min(lo, b[1]); });
    const y = v => padY + (hi - v) / (hi - lo) * (H - 2 * padY);
    const x = i => padX + step * (i + 0.5);
    const bw = Math.max(3, Math.min(8, step * 0.46));
    let s = `<svg class="mini" viewBox="0 0 ${W} ${H}">`;
    (opt.bands || []).forEach(b => {
      const x1 = padX + step * b.from, x2 = padX + step * (b.to + 1);
      s += `<rect x="${x1.toFixed(1)}" y="3" width="${(x2 - x1).toFixed(1)}" height="${H - 6}" fill="rgba(239,68,68,.10)"/>`;
    });
    bars.forEach((b, i) => {
      const prev = i > 0 ? bars[i - 1][2] : b[1];
      const up = b[2] >= prev;
      const col = up ? '#e6edf3' : '#8b98a8';
      s += `<line x1="${x(i).toFixed(1)}" y1="${y(b[0]).toFixed(1)}" x2="${x(i).toFixed(1)}" y2="${y(b[1]).toFixed(1)}" stroke="${col}" stroke-width="1"/>`;
      const y1 = y(Math.max(prev, b[2])), y2 = y(Math.min(prev, b[2]));
      s += `<rect x="${(x(i) - bw / 2).toFixed(1)}" y="${y1.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(2, y2 - y1).toFixed(1)}" fill="${col}"/>`;
    });
    if (opt.line) {
      const yy = y(opt.line.value).toFixed(1);
      s += `<line x1="4" y1="${yy}" x2="${W - 4}" y2="${yy}" stroke="#ef4444" stroke-width="1" stroke-dasharray="4 3"/>`;
      if (opt.line.label) s += `<text x="${W - 6}" y="${(y(opt.line.value) - 4).toFixed(1)}" fill="#ef4444" font-size="9" text-anchor="end">${opt.line.label}</text>`;
    }
    (opt.marks || []).forEach(m => {
      const px = x(m.i), py = m.kind === 'T' ? y(bars[m.i][0]) : y(bars[m.i][1]);
      s += `<circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="3" fill="#60a5fa"/>`;
      const dy = m.kind === 'T' ? -7 : 12;
      s += `<text x="${px.toFixed(1)}" y="${(py + dy).toFixed(1)}" fill="#60a5fa" font-size="9" text-anchor="middle">${m.kind === 'T' ? '顶' : '底'}</text>`;
    });
    (opt.tags || []).forEach(t => {
      s += `<text x="${x(t.i).toFixed(1)}" y="${(H - 3)}" fill="${t.color || '#ef4444'}" font-size="8.5" text-anchor="middle">${t.text}</text>`;
    });
    return s + '</svg>';
  }

  const FIG = {
    shared: mini([[12, 10, 11], [15, 12, 14], [11, 9, 10], [9.2, 7.5, 8], [10, 8.2, 9]],
      { marks: [{ i: 1, kind: 'T' }, { i: 3, kind: 'B' }], bands: [{ from: 2, to: 2 }], tags: [{ i: 2, text: '共用', color: '#ef4444' }] }),
    nongap: mini([[12, 10, 11], [15, 12, 14], [13, 11, 12], [12, 10.5, 11], [10, 8, 9], [10.5, 8.5, 9.8]],
      { marks: [{ i: 1, kind: 'T' }, { i: 4, kind: 'B' }], tags: [{ i: 2.5, text: '0根', color: '#f59e0b' }] }),
    onegap: mini([[12, 10, 11], [15, 12, 14], [13, 11, 12], [12.5, 10, 11], [11, 9, 10], [9.5, 7, 8], [10, 7.5, 9]],
      { marks: [{ i: 1, kind: 'T' }, { i: 5, kind: 'B' }], bands: [{ from: 3, to: 3 }], tags: [{ i: 3, text: '1根', color: '#4ade80' }] }),
    confirmBad: mini([[10, 8, 9], [12, 9.5, 11], [15, 11, 14], [12, 10.8, 11.6], [13, 11.6, 12.6], [14, 12.2, 13.4], [14.4, 12.8, 13.9]],
      { marks: [{ i: 2, kind: 'T' }], line: { value: 11, label: '中K最低 11' }, tags: [{ i: 5, text: '收盘都在上面 ✗', color: '#ef4444' }] }),
    confirmOk: mini([[10, 8, 9], [12, 9.5, 11], [15, 11, 14], [12, 10.4, 10.6], [13, 11.6, 12.6], [14, 12.2, 13.4], [14.4, 12.8, 13.9]],
      { marks: [{ i: 2, kind: 'T' }], line: { value: 11, label: '中K最低 11' }, tags: [{ i: 3, text: '收在下面 ✓', color: '#4ade80' }] }),
    priceBad: mini([[9, 7, 8], [11, 8.5, 10], [15, 11.5, 14], [13.4, 12.1, 12.9], [12.8, 11.9, 12.2], [11.9, 11.6, 11.7], [12.9, 11.8, 12.4]],
      { marks: [{ i: 2, kind: 'T' }, { i: 5, kind: 'B' }], line: { value: 11.5, label: '顶K最低 11.5' }, tags: [{ i: 5, text: '底 11.6 没跌破 ✗', color: '#ef4444' }] }),
    priceOk: mini([[9, 7, 8], [11, 8.5, 10], [15, 11.5, 14], [13.4, 12.1, 12.9], [12.8, 11.9, 12.2], [11.4, 10.9, 11], [12.9, 11.8, 12.4]],
      { marks: [{ i: 2, kind: 'T' }, { i: 5, kind: 'B' }], line: { value: 11.5, label: '顶K最低 11.5' }, tags: [{ i: 5, text: '跌破 ✓', color: '#4ade80' }] }),
  };

  // ---------------------------------------------------------------- 口径与案例
  const PRICE_ON = { gap: 4, confirm: 0, price: true };
  const TABS = [
    {
      id: 'gap', tab: '① 间隔', tabSub: '几根K线才算一笔',
      title: '① 顶和底之间，至少隔 1 根 K 线',
      rule: '顶分型和底分型之间，<em>至少隔 1 根独立 K 线</em>，才算一笔。'
        + '<br/>• 两个分型共用 1 根 K 线 → 不算（图3）'
        + '<br/>• 紧挨着、中间 0 根 → 缠师原话「最好不算」（图4）'
        + '<br/>• 中间隔 1 根 → 算一笔（图5）',
      figs: [['图3：共用 1 根 ✗', FIG.shared], ['图4：中间 0 根，最好不算', FIG.nongap], ['图5：隔 1 根 ✓', FIG.onegap]],
      base: { gap: 4, confirm: 0, price: false }, other: { gap: 3, confirm: 0, price: false },
      otherName: '图4 口径（间隔 3）',
      win: ['2022-05-10', '2022-06-30'],
      hint: '2022-05-26 底 → 05-31 顶：中间没有独立K线',
      caseHtml: '2022-05-26 的底 <span class="num">28,020</span> → 05-31 的顶 <span class="num">32,399</span>：'
        + '两个分型中心只隔 <b>3 根</b>合并 K，中间没有独立 K 线。原文跳过这个顶，一笔连到 06-06 的 '
        + '<span class="num">31,766</span>；图4 认这个顶，笔就画到 <span class="num">32,399</span>。'
        + '一个端点变了，后面几笔的端点也跟着挪——所以差异会连着出现。',
    },
    {
      id: 'confirm', tab: '② 收盘确认', tabSub: '分型要不要“确认”',
      title: '② 分型出来还要“确认”',
      rule: '分型认出来还不够，要<em>确认</em>：顶分型后面 3 根 K 线里，得有一根<b>收盘跌破</b>它的最低价；'
        + '底分型反过来，要有一根<b>收盘升破</b>它的最高价。没有 → 这个分型不算数。'
        + '<br/>（这是引擎自己加的降噪规则，原文的笔定义里没有这一条。）',
      figs: [['3 根收盘都没跌破 → 不算', FIG.confirmBad], ['有一根跌破 → 算', FIG.confirmOk]],
      base: { gap: 4, confirm: 0, price: false }, other: { gap: 4, confirm: 3, price: false },
      otherName: '加了收盘确认（3 根）',
      win: ['2025-01-28', '2025-03-20'],
      markDropped: true,
      hint: '2025-02 这一带：分型被“确认”否掉，笔就少了',
      caseHtml: '2025-02-12 的底分型：中 K 最高 <span class="num">98,084</span>，后面 3 根收盘 '
        + '<span class="num">96,608 / 97,501 / 97,570</span>，都没升破 → 不确认，这个底被丢掉'
        + '（02-05、02-11 两个顶同理）。少了这几个分型，02-03→02-21→02-28 这一段就接不上，笔少了两笔。',
    },
    {
      id: 'price', tab: '③ 价格条件', tabSub: '这一笔够不够“有效”',
      title: '③ 这一笔必须真的创新极值',
      rule: '引擎还加了一条：<em>向上笔的顶，必须高过「底分型那根 K 线的最高价」；向下笔的底，必须低过「顶分型那根 K 线的最低价」</em>。'
        + '否则这笔取消。'
        + '<br/>（原文同样没有这一条，属于工程护栏。）',
      figs: [['回调没跌回顶K区间 → 不认', FIG.priceBad], ['跌回去了 → 认', FIG.priceOk]],
      base: { gap: 4, confirm: 0, price: false }, other: PRICE_ON,
      otherName: '加了价格条件',
      win: ['2023-09-25', '2023-12-20'],
      hint: '2023-10/11：一个回调底没跌回顶K区间，向下笔被取消',
      caseHtml: '2023-10-31 的底 <span class="num">34,025</span>，前面顶分型那根 K 的最低价是 '
        + '<span class="num">33,751</span>。34,025 没跌回顶分型的区间 → 引擎不认这个底，'
        + '本该向下的那一笔被取消，上涨笔一路延伸到 12-08 的 <span class="num">44,700</span>。',
    },
    {
      id: 'engine', tab: '④ 全叠加 = 引擎', tabSub: '系统里实际用的笔',
      title: '④ 三条一起开，就是系统里的笔',
      rule: '引擎 = <em>收盘确认（3 根）+ 价格条件 + 间隔 4</em>，三条一起开。所以同一个位置，'
        + '原文画 3 笔，引擎只画 1 笔。实验室默认参数也是这一套，和原图层的笔逐笔一致。',
      figs: [],
      base: { gap: 4, confirm: 0, price: false }, other: { gap: 4, confirm: 3, price: true },
      otherName: '引擎口径（全开）',
      win: ['2025-06-01', '2025-07-20'],
      markDropped: true,
      hint: '2025-06：确认 + 价格条件把 3 笔压成 1 笔',
      caseHtml: '2025-06 这波：原文 <span class="num">100,372 → 108,952 → 105,100 → 123,218</span>（3 笔）；'
        + '引擎 <span class="num">100,372 → 123,218</span>（1 笔）。中间的顶和底分别被“收盘确认”和'
        + '“价格条件”否掉，整段合成了一笔上涨。',
    },
    {
      id: 'buys', tab: '⑤ 买点', tabSub: '三套买点，图上对比', buys: true,
      title: '⑤ 买点是从笔结构长出来的',
      rule: '一买 = 下跌背驰 + 触发（绿柱首次缩短）；二买 = 一买后的回调不破前低；三买 = 突破中枢后回抽不回中枢。'
        + '<br/>笔一变，中枢和背驰段跟着变，这三个买点也就整体重排 —— 有的消失、有的新增，不是平移。'
        + '<br/>全历史 1D：<b>原文 31 个 / 引擎 23 个</b>。',
      figs: [],
      base: { gap: 4, confirm: 0, price: false }, other: { gap: 4, confirm: 3, price: true },
      otherName: '引擎',
      win: [null, null],
      hint: '▲ 一买　◆ 二买　■ 三买　蓝=只在原文　橙=只在引擎　白=两套都有',
    },
  ];

  // ---------------------------------------------------------------- 图表
  const chartEl = document.getElementById('chart');
  const chart = LightweightCharts.createChart(chartEl, {
    layout: { background: { type: 'solid', color: '#0b0e11' }, textColor: '#a9b4c0', fontSize: 11 },
    grid: { vertLines: { color: 'rgba(35,43,54,.7)' }, horzLines: { color: 'rgba(35,43,54,.7)' } },
    rightPriceScale: { borderColor: '#232b36' },
    timeScale: { borderColor: '#232b36', timeVisible: false, rightOffset: 4 },
    crosshair: { mode: 0 },
    handleScroll: true,
  });
  const series = chart.addCandlestickSeries({
    upColor: '#26a69a', downColor: '#ef5350', borderVisible: false,
    wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    priceLineVisible: false, lastValueVisible: true,
  });
  series.setData(CS.map(c => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));
  series.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.32 } });

  // ---- 副图: MACD(12,26,9) —— 一买的“背驰 + 绿柱首次缩短”就发生在这里
  const MACD_DATA = E.macd(CS.map(c => c.close), 12, 26, 9);
  const histSeries = chart.addHistogramSeries({
    priceScaleId: 'macd', priceFormat: { type: 'price', precision: 0, minMove: 1 },
    priceLineVisible: false, lastValueVisible: false, base: 0,
  });
  const difSeries = chart.addLineSeries({
    priceScaleId: 'macd', color: '#cbd5e1', lineWidth: 1,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  });
  const deaSeries = chart.addLineSeries({
    priceScaleId: 'macd', color: '#f59e0b', lineWidth: 1,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  });
  const zeroSeries = chart.addLineSeries({
    priceScaleId: 'macd', color: 'rgba(110,123,139,.45)', lineWidth: 1, lineStyle: 2,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  });
  histSeries.setData(CS.map((c, i) => ({
    time: c.time, value: MACD_DATA.hist[i],
    color: MACD_DATA.hist[i] >= 0 ? 'rgba(38,166,154,.62)' : 'rgba(239,83,80,.62)',
  })));
  difSeries.setData(CS.map((c, i) => ({ time: c.time, value: MACD_DATA.diff[i] })));
  deaSeries.setData(CS.map((c, i) => ({ time: c.time, value: MACD_DATA.dea[i] })));
  zeroSeries.setData(CS.map(c => ({ time: c.time, value: 0 })));
  chart.priceScale('macd').applyOptions({ scaleMargins: { top: 0.72, bottom: 0.02 }, visible: false });

  const state = { base: [], other: [], diffs: [], dropped: [], buys: null };
  const buyFilter = { '一买': true, '二买': true, '三买': true, diffOnly: false };

  class Renderer {
    draw(target) {
      const ts = chart.timeScale();
      const X = t => ts.timeToCoordinate(t);
      const Y = p => series.priceToCoordinate(p);
      target.useMediaCoordinateSpace(scope => {
        const ctx = scope.context;
        ctx.lineCap = 'round';
        const seg = (a, b, color, w, dash, alpha) => {
          const x1 = X(a.tS), y1 = Y(a.pS), x2 = X(b.tE), y2 = Y(b.pE);
          if (x1 == null || y1 == null || x2 == null || y2 == null) return false;
          ctx.globalAlpha = alpha == null ? 1 : alpha;
          ctx.strokeStyle = color; ctx.lineWidth = w;
          ctx.setLineDash(dash || []);
          ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
          ctx.setLineDash([]); ctx.globalAlpha = 1;
          return true;
        };
        if (state.buys) {
          // 买点模式: 只画蜡烛 + 买点标记
          const COL = { base: '#60a5fa', other: '#f59e0b', both: '#e2e8f0' };
          const order = { both: 0, base: 1, other: 2 };
          state.buys.slice().sort((a, b) => order[a.presence] - order[b.presence]).forEach(b => {
            const x = X(b.time), y = Y(b.price);
            if (x == null || y == null) return;
            const col = COL[b.presence];
            ctx.beginPath();
            if (b.kind === '一买') { ctx.moveTo(x, y - 6.5); ctx.lineTo(x - 5.5, y + 4.5); ctx.lineTo(x + 5.5, y + 4.5); }
            else if (b.kind === '二买') { ctx.moveTo(x, y - 6.5); ctx.lineTo(x + 6.5, y); ctx.lineTo(x, y + 6.5); ctx.lineTo(x - 6.5, y); }
            else { ctx.rect(x - 4.5, y - 4.5, 9, 9); }
            ctx.closePath();
            ctx.globalAlpha = 0.28; ctx.fillStyle = col; ctx.fill();
            ctx.globalAlpha = 1; ctx.strokeStyle = col; ctx.lineWidth = 1.6; ctx.stroke();
          });
          return;
        }
        // 原文(蓝) 先画, 对比(橙) 后画
        state.base.forEach(s => seg(s, s, 'rgba(96,165,250,.9)', 1.8));
        state.other.forEach(s => seg(s, s, 'rgba(245,158,11,.92)', 2));
        // 差异笔: 红 + ≠ 标记
        state.diffs.forEach(s => {
          if (!seg(s, s, '#ef4444', 3.2, [7, 4])) return;
          const x1 = X(s.tS), y1 = Y(s.pS), x2 = X(s.tE), y2 = Y(s.pE);
          const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
          ctx.fillStyle = 'rgba(11,14,17,.85)';
          ctx.beginPath(); ctx.arc(mx, my, 7, 0, Math.PI * 2); ctx.fill();
          ctx.fillStyle = '#ef4444';
          ctx.font = 'bold 11px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
          ctx.fillText('≠', mx, my);
        });
        // 被“确认”否掉的分型
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        state.dropped.forEach(dd => {
          const x = X(dd.t), y = Y(dd.p);
          if (x == null || y == null) return;
          ctx.strokeStyle = '#f87171'; ctx.lineWidth = 1.4;
          ctx.beginPath(); ctx.arc(x, y, 5, 0, Math.PI * 2); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(x - 3, y - 3); ctx.lineTo(x + 3, y + 3);
          ctx.moveTo(x + 3, y - 3); ctx.lineTo(x - 3, y + 3); ctx.stroke();
        });
      });
    }
  }
  class View {
    constructor(p) { this._p = p; this._r = new Renderer(); }
    update() {}
    renderer() { return this._r; }
  }
  class Prim {
    constructor(s, c) { this.series = s; this.chart = c; this.view = new View(this); this._req = null; }
    attached(p) { this._req = p.requestUpdate; }
    updateAllViews() {}
    paneViews() { return [this.view]; }
    refresh() { if (this._req) this._req(); }
  }
  const prim = new Prim(series, chart);
  series.attachPrimitive(prim);

  // ---------------------------------------------------------------- 计算
  const cache = new Map();
  function strokesOf(cfg) {
    const k = cfg.gap + '|' + cfg.confirm + '|' + (cfg.price ? 1 : 0);
    if (cache.has(k)) return cache.get(k);
    const fr = E.fractals(MERGED, CS, cfg.confirm);
    const seq = E.alternate(MERGED, fr);
    const st = E.strokes(MERGED, CS, seq, cfg.gap, { priceCheck: cfg.price });
    cache.set(k, st);
    return st;
  }
  const keyOf = s => s.sRaw + '-' + s.eRaw + '-' + s.dir;

  // ---- 买点: MACD/背驰/触发 参数固定, 只有笔结构参数随口径变
  const MACD = { macdFast: 12, macdSlow: 26, macdSignal: 9, divMode: 'area', divRatio: 0.9,
    trigger: 'shrink', priceMode: 'close', trigWindow: 20, pivotRatio: 0, pivotLevel: 'stroke', thirdMaxDist: 0.12 };
  const computeCache = new Map();
  function computeOf(cfg) {
    const k = cfg.gap + '|' + cfg.confirm + '|' + (cfg.price ? 1 : 0);
    if (!computeCache.has(k)) computeCache.set(k, E.compute(CS, { ...MACD, minGap: cfg.gap, confirmWin: cfg.confirm, priceCheck: cfg.price }));
    return computeCache.get(k);
  }
  const buyKey = b => b.kind + '|' + b.barIdx;
  const idxOfDate = ds => { let i = 0; while (i < CS.length && dstr(CS[i].time) < ds) i++; return Math.max(0, Math.min(CS.length - 1, i)); };

  // ---------------------------------------------------------------- 渲染
  const tabsEl = document.getElementById('tabs');
  const cardEl = document.getElementById('card');
  const countsEl = document.getElementById('counts');
  const diffEl = document.getElementById('difflist');
  const hintEl = document.getElementById('hint');
  const legendEl = document.querySelector('.legend');
  const LEGEND_DEFAULT = legendEl.innerHTML;
  const LEGEND_BUYS = '<b>▲ 一买　◆ 二买　■ 三买</b><br/>'
    + '<span class="dot" style="background:#60a5fa"></span>只在原文　'
    + '<span class="dot" style="background:#f59e0b"></span>只在引擎　'
    + '<span class="dot" style="background:#e2e8f0"></span>两套都有<br/>'
    + '点下面的差异项，可以直接跳到那个买点。';
  let cur = TABS[0];
  let GLOBAL = null;
  try {
    const g = {};
    g.base = strokesOf({ gap: 4, confirm: 0, price: false }).length;
    g.gap3 = strokesOf({ gap: 3, confirm: 0, price: false }).length;
    g.engine = strokesOf({ gap: 4, confirm: 3, price: true }).length;
    GLOBAL = g;
  } catch (e) { GLOBAL = null; }

  function tabButton(t) {
    const b = document.createElement('button');
    b.className = 'tab' + (t === cur ? ' on' : '');
    b.innerHTML = `<b>${t.tab}</b>${t.tabSub}`;
    b.onclick = () => { cur = t; render(); };
    return b;
  }

  function buyStats() {
    const A = computeOf(cur.base).buys, C = computeOf(cur.other).buys;
    const aset = new Set(A.map(buyKey)), cset = new Set(C.map(buyKey));
    return { A, C, onlyA: A.filter(b => !cset.has(buyKey(b))), onlyC: C.filter(b => !aset.has(buyKey(b))), both: A.filter(b => cset.has(buyKey(b))) };
  }
  const cntByKind = list => ['一买', '二买', '三买'].map(k => list.filter(b => b.kind === k).length);

  function renderBuys(baseHtml) {
    const { A, C, onlyA, onlyC, both } = buyStats();
    const cA = cntByKind(A), cC = cntByKind(C);
    const rows = ['一买', '二买', '三买'].map((k, i) =>
      `<tr><td>${k}</td><td>${cA[i]}</td><td>${cC[i]}</td><td class="d">${Math.abs(cA[i] - cC[i])}</td></tr>`).join('');

    cardEl.innerHTML = baseHtml +
      `<table class="bt"><tr><th>买点</th><th>原文</th><th>引擎</th><th>差</th></tr>${rows}` +
      `<tr class="sum"><td>合计</td><td>${A.length}</td><td>${C.length}</td><td class="d">${Math.abs(A.length - C.length)}</td></tr></table>` +
      `<div class="filters">
         <label><input type="checkbox" data-k="一买" ${buyFilter['一买'] ? 'checked' : ''}/> ▲ 一买</label>
         <label><input type="checkbox" data-k="二买" ${buyFilter['二买'] ? 'checked' : ''}/> ◆ 二买</label>
         <label><input type="checkbox" data-k="三买" ${buyFilter['三买'] ? 'checked' : ''}/> ■ 三买</label>
         <label><input type="checkbox" data-diff ${buyFilter.diffOnly ? 'checked' : ''}/> 只看差异</label>
       </div>
       <div class="spans">
         <button data-span="0" class="on">全部</button>
         <button data-span="1095">近 3 年</button>
         <button data-span="365">近 1 年</button>
       </div>`;

    const put = (list, presence) => list.map(b => ({ kind: b.kind, price: b.price, time: b.time, barIdx: b.barIdx, presence }));
    const allBuys = [...put(both, 'both'), ...put(onlyA, 'base'), ...put(onlyC, 'other')];
    state.buys = allBuys.filter(b => buyFilter[b.kind] !== false && (!buyFilter.diffOnly || b.presence !== 'both'));
    prim.refresh();
    chart.timeScale().fitContent();

    countsEl.innerHTML =
      `<span><i class="dot" style="background:#60a5fa"></i>只在原文 <b>${onlyA.length}</b></span>` +
      `<span><i class="dot" style="background:#f59e0b"></i>只在引擎 <b>${onlyC.length}</b></span>` +
      `<span><i class="dot" style="background:#e2e8f0"></i>两套都有 <b>${both.length}</b></span>`;

    const items = [
      ...onlyA.map(b => ({ b, who: '只在原文', cls: 'k' })),
      ...onlyC.map(b => ({ b, who: '只在引擎', cls: 'o' })),
    ].sort((x, y) => x.b.time - y.b.time);
    diffEl.innerHTML = items.length
      ? items.map(({ b, who, cls }) => `<li class="click" data-bar="${b.barIdx}"><span class="${cls}">${who}</span> ${b.kind} ${dstr(b.time)} &nbsp;${num(b.price)}</li>`).join('')
      : '<li>两套口径买点完全一致</li>';

    cardEl.querySelectorAll('input[data-k]').forEach(el => el.onchange = () => {
      buyFilter[el.dataset.k] = el.checked; renderBuys(baseHtml);
    });
    cardEl.querySelector('input[data-diff]').onchange = el => {
      buyFilter.diffOnly = el.target.checked; renderBuys(baseHtml);
    };
    diffEl.querySelectorAll('li.click').forEach(el => el.onclick = () => {
      const i = +el.dataset.bar;
      chart.timeScale().setVisibleLogicalRange({ from: i - 55, to: i + 55 });
    });
    cardEl.querySelectorAll('.spans button').forEach(btn => btn.onclick = () => {
      cardEl.querySelectorAll('.spans button').forEach(b => b.classList.toggle('on', b === btn));
      const span = +btn.dataset.span;
      if (!span) chart.timeScale().fitContent();
      else chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, CS.length - span), to: CS.length });
    });
  }

  function render() {
    tabsEl.innerHTML = '';
    TABS.forEach(t => tabsEl.appendChild(tabButton(t)));

    const baseHtml =
      `<h2>${cur.title}</h2>` +
      `<p class="rule">${cur.rule}</p>` +
      (cur.figs.length
        ? `<div class="figs">${cur.figs.map(([cap, svg]) => `<div class="fig">${svg}<div class="cap">${cap}</div></div>`).join('')}</div>`
        : '') +
      (cur.caseHtml ? `<div class="case">${cur.caseHtml}</div>` : '');
    cardEl.innerHTML = baseHtml;
    hintEl.textContent = cur.hint;
    legendEl.innerHTML = LEGEND_DEFAULT;

    if (cur.buys) { legendEl.innerHTML = LEGEND_BUYS; renderBuys(baseHtml); return; }
    state.buys = null;

    const base = strokesOf(cur.base);
    const other = strokesOf(cur.other);
    const i0 = idxOfDate(cur.win[0]), i1 = idxOfDate(cur.win[1]);
    const inWin = s => s.sRaw >= i0 && s.eRaw <= i1;

    const bSet = new Set(base.map(keyOf)), oSet = new Set(other.map(keyOf));
    const onlyBase = base.filter(s => !oSet.has(keyOf(s)));
    const onlyOther = other.filter(s => !bSet.has(keyOf(s)));

    state.base = base;
    state.other = other;
    state.diffs = onlyBase.concat(onlyOther).filter(inWin);
    state.dropped = [];
    if (cur.markDropped) {
      const f3 = new Set(E.fractals(MERGED, CS, 3).map(f => f.i + '|' + f.kind));
      E.fractals(MERGED, CS, 0).forEach(f => {
        if (f3.has(f.i + '|' + f.kind)) return;
        if (f.i < i0 || f.i > i1) return;
        const ex = E.extreme(MERGED, MERGED[f.i], CS, f.kind);
        state.dropped.push({ t: ex.time, p: ex.price });
      });
    }
    prim.refresh();
    chart.timeScale().setVisibleLogicalRange({ from: i0 - 1.5, to: i1 + 1.5 });

    const nBase = base.filter(inWin).length, nOther = other.filter(inWin).length;
    countsEl.innerHTML =
      `<span><i class="dot" style="background:#60a5fa"></i>原文 <b>${nBase}</b> 笔</span>` +
      `<span><i class="dot" style="background:#f59e0b"></i>${cur.otherName} <b>${nOther}</b> 笔</span>` +
      `<span><i class="dot" style="background:#ef4444"></i>差异 <b>${state.diffs.length}</b></span>`;

    if (GLOBAL) {
      countsEl.innerHTML += `<span class="all">全历史：原文 ${GLOBAL.base} · 图4 ${GLOBAL.gap3} · 引擎 ${GLOBAL.engine} 笔</span>`;
    }
    if (!state.diffs.length) {
      diffEl.innerHTML = '<li>这个窗口里两种口径画得一样</li>';
    } else {
      diffEl.innerHTML = state.diffs.slice(0, 8).map(s => {
        const who = oSet.has(keyOf(s)) ? '对比独有' : '原文独有';
        return `<li><span class="k">${who}</span> ${md(s.tS)}→${md(s.tE)} &nbsp;${num(s.pS)}→${num(s.pE)}</li>`;
      }).join('') + (state.diffs.length > 8 ? `<li>…另有 ${state.diffs.length - 8} 笔</li>` : '');
    }
  }

  render();
  window.__bi = { state, buyFilter, tab: () => cur };
  window.addEventListener('resize', () => chart.applyOptions({ width: chartEl.clientWidth, height: chartEl.clientHeight }));
  chart.applyOptions({ width: chartEl.clientWidth, height: chartEl.clientHeight });
})();
