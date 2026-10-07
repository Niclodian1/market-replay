// js/07-main.js — 全屏 / 面板对齐 / 统计与列表 / 控件绑定 / 初始化
// 手绘线(趋势线/水平线/斐波那契/折线)已迁移到 js/08-draw.js, 使用 TradingView 风格画线组件。
// 鼠标像素 Y → 真实价位(测距完全跟手的关键, 而非取K线收盘价)
const cursorPrice = y => candleSeries.coordinateToPrice(y);

// ---- 全屏模式: 仅图表区(chart-pane)全屏, 侧栏隐藏 ----
function setupFullscreen(){
  document.getElementById('fsBtn').addEventListener('click', () => {
    const pane = document.querySelector('.chart-pane');
    if (!document.fullscreenElement){
      (pane.requestFullscreen?.() || Promise.reject()).catch(() => alert('浏览器拒绝了全屏请求'));
    } else {
      document.exitFullscreen?.();
    }
  });
  document.addEventListener('fullscreenchange', () => {
    document.getElementById('fsBtn').textContent =
      document.fullscreenElement ? '⤢ 退出全屏' : '⛶ 全屏';
    // 全屏后重算三面板尺寸
    setTimeout(() => { alignPanes(); chart.applyOptions({ width: undefined, height: undefined }); }, 120);
    setTimeout(alignPanes, 400);
  });
}

function applyTheme(){
  chart.applyOptions(chartOptions());
  candleSeries.applyOptions({
    upColor: t().up, downColor: t().down,
    borderUpColor: t().up, borderDownColor: t().down,
    wickUpColor: t().up, wickDownColor: t().down,
  });
  volumeChart.applyOptions({ ...chartOptions(), grid: { vertLines: { visible: false }, horzLines: { color: t().grid } }, timeScale: { ...chartOptions().timeScale, visible: false } });
  macdChart.applyOptions({ ...chartOptions(), grid: { vertLines: { visible: false }, horzLines: { color: t().grid } }, timeScale: { ...chartOptions().timeScale, visible: false } });
  macdLineSeries.applyOptions({ color: t().ink2 });
  macdSignalSeries.applyOptions({ color: t().muted });
  candleSeries.setMarkers(buildMarkers(activeView));
  volumeSeries.setData(buildVolData(activeView));
  const md = buildMacdData(activeView);
  macdHistSeries.setData(md.hist);
  macdLineSeries.setData(md.diff);
  macdSignalSeries.setData(md.dea);
  buildMaps(activeView);
  renderTradeList();
  computeStats();
  renderTradePanel();
  refreshTrades();
}

// ---- 三面板十字光标同步 + 时间刻度对齐 ----
// 主图右侧价格轴占 psM 宽, 量/MACD 面板各自空价格轴占 psV/psD 宽, 导致三面板同 bar
// 的像素 x 不同(约 25~50px 错位)。alignPanes() 收窄量/MACD 面板宽度, 使
// (面板宽 - 自身价格轴宽) 三面板一致 → 时间刻度像素一致, 虚线真正贯穿同 bar。
// 注: autoSize 按 clientWidth(含 padding) 自适应, 故必须改元素实际宽度而非 padding。
function alignPanes(){
  const Wm = chart.timeScale().width();
  if (!Wm || Wm < 100) return;                 // 图表未完成布局时不写副图宽度(防止写死成极窄)
  const setW = (id, ps) => {
    if (!ps || ps < 20) return;                // 副图价格轴读数异常时跳过本次
    const el = document.getElementById(id);
    const target = Math.max(80, Math.round(Wm + ps));   // 副图最小可用宽度保护
    if (el.clientWidth !== target) el.style.width = target + 'px';
  };
  setW('volume', volumeChart.priceScale('right').width());
  setW('macd', macdChart.priceScale('right').width());
}
// 副图自检: 容器无 canvas 或高度为 0 时提示(定位"副图空白")
function assertSubPanes(){
  const v = document.getElementById('volume'), m = document.getElementById('macd');
  const volOk = !!(v && v.querySelector('canvas')) && v.clientHeight > 20;
  const macdOk = !!(m && m.querySelector('canvas')) && m.clientHeight > 20;
  if (!volOk || !macdOk){
    console.warn('[副图自检] volume=', volOk, 'macd=', macdOk,
      ' clientH=', v && v.clientHeight, '/', m && m.clientHeight);
    flashBtn('snapshotBtn', '⚠ 副图异常 重试中…');
    return false;
  }
  return true;
}
// 十字光标: 在任一面板悬停, K线/成交量/MACD 三面板同 bar 位置虚线贯穿。
const onCrossSync = param => {
  syncT = param.time == null ? null : param.time;
  refreshVLine();
};
chart.subscribeCrosshairMove(onCrossSync);
volumeChart.subscribeCrosshairMove(onCrossSync);
macdChart.subscribeCrosshairMove(onCrossSync);
// 价格格式化: 自适应精度(PEPE 等微价币 ≥0.01 显示4位, 否则8位; 常规价格1位)
const fmt = n => {
  n = Number(n);
  const d = Math.abs(n) >= 1 ? 1 : Math.abs(n) >= 0.01 ? 4 : 8;
  return n.toLocaleString('en-US', { maximumFractionDigits: d });
};
// 信号悬停详情卡(fixed 定位跟随光标): 展示买卖点/背驰/盘整/交易的完整生成依据
const pointCardEl = document.createElement('div');
pointCardEl.className = 'point-card';
pointCardEl.style.display = 'none';
document.body.appendChild(pointCardEl);
chart.subscribeCrosshairMove(param => {
  const oh = document.getElementById('legendOhlc');
  const ch = document.getElementById('legendChg');
  const dt = document.getElementById('legendDate');
  const dist = document.getElementById('distTag');
  const tip = document.getElementById('markerTip');
  const d = param.seriesData && param.seriesData.get(candleSeries);
  if (!param.time || !d){
    oh.textContent = '—'; ch.textContent = ''; dt.textContent = '—';
    if (dist){ dist.textContent = ''; dist.style.display = 'none'; }
    tip.style.display = 'none'; pointCardEl.style.display = 'none';
    return;
  }
  dt.textContent = fmtTime(d.time);
  oh.innerHTML = `O ${fmt(d.open)}  H <span style="color:var(--up)">${fmt(d.high)}</span>  L <span style="color:var(--down)">${fmt(d.low)}</span>  C ${fmt(d.close)}`;
  const cs = candlesFor(activeView), i = idxMap[d.time];
  const prev = i > 0 ? cs[i-1].close : null;
  if (prev){
    const pct = (d.close - prev) / prev * 100;
    ch.textContent = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
    ch.style.color = pct >= 0 ? 'var(--up)' : 'var(--down)';
  } else ch.textContent = '';
  // 光标价位 ↔ 最新收盘的距离(实时跟手): 贴在右侧价格栏, 随光标纵向移动
  const last = cs.length ? cs[cs.length - 1] : null;
  if (dist && last && param.point){
    const px = cursorPrice(param.point.y);
    if (px == null){ dist.style.display = 'none'; }
    else {
      const dp = px - last.close;
      const pp = last.close ? dp / last.close * 100 : 0;
      const nb = i == null ? 0 : cs.length - 1 - i;
      const col = dp >= 0 ? 'var(--up)' : 'var(--down)';
      // 先展示光标当前价格(轴标签会被本浮标遮挡), 再展示距最新的距离
      dist.innerHTML = `<b style="color:var(--ink)">${fmt(px)}</b> · 距最新 <span style="color:${col}">${dp >= 0 ? '+' : ''}${fmt(dp)} (${pp >= 0 ? '+' : ''}${pp.toFixed(2)}%)</span>${nb > 0 ? ` · ${nb}根` : ''}`;
      dist.style.color = '';
      const boxRect = document.querySelector('.chart-box').getBoundingClientRect();
      const chRect = document.getElementById('chart').getBoundingClientRect();
      const top = Math.min(chRect.height - 12, Math.max(12, (chRect.top - boxRect.top) + param.point.y));
      dist.style.top = Math.round(top) + 'px';
      dist.style.display = 'block';
    }
  } else if (dist) dist.style.display = 'none';
  // 悬停详情卡: 该bar上有信号 → 展示完整生成依据(级别/口径/价格/触发确认/why)
  const ents = detailMap && detailMap[d.time];
  if (ents && ents.length && param.point){
    let h = '';
    for (const e of ents){
      h += `<div class="pc-t" style="color:${e.color}">${e.title}</div>`;
      h += `<div class="pc-r">${e.rows.map(r =>
        `<span class="pc-k">${r[0]}</span><span class="pc-v">${r[1]}</span>`).join('')}</div>`;
      if (e.why) h += `<div class="pc-why">${e.why}</div>`;
    }
    pointCardEl.innerHTML = h;
    pointCardEl.style.display = 'block';
    const rc = document.getElementById('chart').getBoundingClientRect();
    let px = rc.left + param.point.x + 18, py = rc.top + param.point.y + 14;
    if (px + pointCardEl.offsetWidth > window.innerWidth - 8)
      px = Math.max(8, rc.left + param.point.x - pointCardEl.offsetWidth - 14);
    if (py + pointCardEl.offsetHeight > window.innerHeight - 8)
      py = Math.max(8, window.innerHeight - pointCardEl.offsetHeight - 8);
    pointCardEl.style.left = px + 'px';
    pointCardEl.style.top = py + 'px';
    tip.style.display = 'none';
    return;
  }
  pointCardEl.style.display = 'none';
  const mk = markerMap[d.time];
  if (mk){
    if (mk.kind === 'top') tip.textContent = `▼ 顶分型 · 最高 ${fmt(mk.price)}`;
    else if (mk.kind === 'bottom') tip.textContent = `▲ 底分型 · 最低 ${fmt(mk.price)}`;
    else if (mk.kind === '告警') tip.textContent = `⚠ 告警(潜在背驰) · ${fmt(mk.price)}`;
    else if (mk.kind === '背驰买' || mk.kind === '背驰卖') tip.textContent = `⚡ ${mk.kind}(抢先) · ${fmt(mk.price)}`;
    else if (mk.kind.indexOf('回测') === 0){
      tip.textContent = `${mk.kind} · 入场 ${fmt(mk.price)} → 出场 ${fmt(mk.trade.exit)} (${fmtRet(mk.trade.ret)})`;
    }
    else tip.textContent = `${mk.kind} · ${mk.kind.indexOf('买') >= 0 ? '买入价' : '卖出价'} ${fmt(mk.price)}`;
    tip.style.color = mk.color;
    tip.style.display = 'block';
  } else tip.style.display = 'none';
});

// ---- 统计面板 ----
function computeStats(){
  if (chunkedActive()){
    // 分钟级裸K模式: 无结构统计, 只展示行情概览
    const cs = candlesFor('raw');
    if (!cs.length){
      document.getElementById('statsGrid').innerHTML = '<span class="k">暂无数据</span><span class="v">—</span>';
      return;
    }
    const hi = cs.reduce((a,c) => c.high > a.high ? c : a, cs[0]);
    const lo = cs.reduce((a,c) => c.low  < a.low  ? c : a, cs[0]);
    const lastC = cs[cs.length - 1];
    document.getElementById('statsGrid').innerHTML = [
      ['周期 / 模式', `<strong>${activeTF}</strong> · 裸K指标级（不做笔计算）`],
      ['已加载 / 全集', `<strong>${(ChunkStore.win ? ChunkStore.win.candles.length : 0).toLocaleString()}</strong> / ${(ChunkStore.win?.total || 0).toLocaleString()} 根`],
      ['区间最高/最低', `${fmt(hi.high)} / ${fmt(lo.low)}`],
      ['最新收盘', `${fmt(lastC.close)} (${fmtTime(lastC.time)})`],
      ['说明', '分钟级仅用于复盘/练习; 笔/中枢请看 1D/4H/1H'],
    ].map(r => `<span class="k">${r[0]}</span><span class="v">${r[1]}</span>`).join('');
    document.getElementById('dataNote').textContent =
      `数据源: Binance ${currentSym}USDT ${TF_DIR[activeTF]} 分片按需加载(intraday/${currentSym}/${TF_DIR[activeTF]}) · 裸K+MA/MACD`;
    return;
  }
  const m = L().meta;
  const cs = candlesFor('raw');
  const ridx = {}; cs.forEach((c,i) => ridx[c.time] = i);
  const topIdx = L().topMarkers.map(x => ridx[x.time]).filter(i => i != null);
  const botIdx = L().bottomMarkers.map(x => ridx[x.time]).filter(i => i != null);
  const hi = cs.reduce((a,c) => c.high > a.high ? c : a, cs[0]);
  const lo = cs.reduce((a,c) => c.low  < a.low  ? c : a, cs[0]);
  const lastC = cs[cs.length-1];
  const buys = buysFor(activeView, pivotMode);
  const sells = sellsFor(activeView, pivotMode);
  const cnt = (arr, k) => arr.filter(b => b.k.startsWith(k) && !b.k.includes('·段')).length;
  const cntSeg = (arr, k) => arr.filter(b => b.k.startsWith(k) && b.k.includes('·段')).length;
  const nB = buys.length, nS = sells.length;
  // 结构计数随笔口径切换(老笔链为独立推导的对照结构)
  const nStrokes = strokeStd === 'old' ? m.nOldStrokes : m.nStrokes;
  const nSegments = strokeStd === 'old' ? m.nOldSegments : m.nSegments;
  const nPivS = strokeStd === 'old' ? m.nOldPivotsStroke : m.nPivotsStroke;
  const nPivG = strokeStd === 'old' ? m.nOldPivotsSegment : m.nPivotsSegment;
  const rows = [
    ['周期 / 口径', `<strong>${m.interval}</strong> · <strong>${strokeStd === 'old' ? '老笔' : '新笔'}</strong> · <strong>${m.nBars}</strong> 根`],
    ['原始/合并', `<strong>${m.nBars}</strong> / <strong>${m.nMerged}</strong>`],
    ['顶/底分型', `<strong>${m.nTops}</strong> / <strong>${m.nBottoms}</strong>`],
    ['笔 / 线段', `<strong>${nStrokes}</strong> / <strong>${nSegments}</strong>`],
    ['中枢 笔/线段', `<strong>${nPivS}</strong> / <strong>${nPivG}</strong>`],
    ['当前买点', `<strong>${nB}</strong>（笔级: 一${cnt(buys,'一买')}·二${cnt(buys,'二买')}·三${cnt(buys,'三买')} / 线段级: 一${cntSeg(buys,'一买')}·二${cntSeg(buys,'二买')}·三${cntSeg(buys,'三买')}）`],
    ['当前卖点', `<strong>${nS}</strong>（笔级: 一${cnt(sells,'一卖')}·二${cnt(sells,'二卖')}·三${cnt(sells,'三卖')} / 线段级: 一${cntSeg(sells,'一卖')}·二${cntSeg(sells,'二卖')}·三${cntSeg(sells,'三卖')}）`],
    ['区间最高/最低', `${fmt(hi.high)} / ${fmt(lo.low)}`],
    ['最新收盘', `${fmt(lastC.close)} (${fmtTime(lastC.time)})`],
  ];
  if (m.nBeichiAlert != null){
    rows.push(['抢先(告警/买/卖)', `<strong>${m.nBeichiAlert}</strong> / ${m.nBeichiBuy} / ${m.nBeichiSell}`]);
  }
  const edge = strokeStd === 'old' ? (m.edgeOld || m.edge) : m.edge;
  if (edge){
    rows.push(['末端分型', edge.lastFractal || '—']);
    rows.push(['末端在途', edge.pending || '—']);
    const lv = edge.live;
    if (lv){
      rows.push(['抢先实时', `${lv.side}侧[${lv.from}~${lv.to}] 比${lv.ratio} ${lv.note || lv.alert || ''}`]);
    }
  }
  document.getElementById('statsGrid').innerHTML =
    rows.map(r => `<span class="k">${r[0]}</span><span class="v">${r[1]}</span>`).join('');
  document.getElementById('dataNote').textContent = DATA.meta.source
    ? `数据源: ${DATA.meta.source} · ${DATA.meta.multiTF ? '多级别独立计算 + 次级别联动' : '日线单级别'}缠论计算, 导出 ${currentSym === 'BTC' ? 'data-view/data.js' : 'data-view/data_' + currentSym.toLowerCase() + '.js'}`
    : `数据源: BTC_USD_${m.interval.toLowerCase()}.parquet · ${m.nBars} 根 (${m.firstDate} ~ ${m.lastDate}) · chanlun.py 三周期独立计算 + 次级别联动, 导出 data.js`;
  document.getElementById('tradeListMode').textContent =
    `（${pivotMode === 'seg' ? '线段中枢' : '笔中枢'} · ${activeView === 'raw' ? '原始' : '合并'}）`;
}

// ---- 买卖点列表 + 详情 ----
function renderTradeList(){
  const buys = buysFor(activeView, pivotMode);
  const sells = sellsFor(activeView, pivotMode);
  const grid = document.getElementById('tradeList');
  if (!buys.length && !sells.length && !(layers.fast && (alertsFor(activeView).length || fastFor(activeView, pivotMode).length))){
    grid.innerHTML = '<div class="note">当前模式/视图下无买卖点。</div>';
    return;
  }
  const groups = { '一买':[], '二买':[], '三买':[], '一卖':[], '二卖':[], '三卖':[], '告警':[], '背驰买':[], '背驰卖':[] };
  buys.forEach(b => (groups[b.k] || []).push(b));
  sells.forEach(s => (groups[s.k] || []).push(s));
  if (layers.fast){
    alertsFor(activeView).forEach(x => groups['告警'].push(x));
    fastFor(activeView, pivotMode).forEach(x => (groups[x.k] || []).push(x));
  }
  let html = '';
  for (const kind of ['一买','二买','三买','一卖','二卖','三卖',
                      ...(layers.fast ? ['告警','背驰买','背驰卖'] : [])]){
    const g = groups[kind];
    if (!g.length) continue;
    html += `<div class="buy-group"><span style="color:${kindColor(kind)}">${kind}</span><span class="buy-count">${g.length}</span></div>`;
    g.forEach(b => {
      html += `<div class="buy-row" data-time="${b.t}" title="点击查看产生说明"><span class="buy-date">${fmtTime(b.t)}</span><span class="buy-price">${fmt(b.p)}</span></div>`;
    });
  }
  grid.innerHTML = html;
}

function showDetail(b){
  const el = document.getElementById('tradeDetail');
  const sub = b.sub && b.sub !== '' ? b.sub : '—';
  const upper = b.upper && b.upper !== '' ? b.upper : '—';
  const ct = b.confirmedT ? fmtTime(b.confirmedT) : '—（在途，待收盘确认）';
  const tier = b.tier ? ` · 背驰${b.tier}` : '';
  const ratio = b.ratio != null ? ` · 运行/参考=${b.ratio}` : '';
  const st = b.state === 'forming' ? '（末端在途）' : '';
  const fastLine = (b.trigger && b.alertT)
    ? `<div class="dsub">⚡ 抢先触发 ${b.trigger} · 告警 ${fmtTime(b.alertT)}${ratio}</div>` : '';
  el.innerHTML =
    `<div class="dk" style="color:${kindColor(b.k)}">${b.k} · ${fmtTime(b.t)} @ ${fmt(b.p)}${st}</div>` +
    (fastLine || `<div class="dsub">⏱ 最早可确认 ${ct}${tier}</div>`) +
    `<div class="dwhy">${b.why}</div>` +
    `<div class="dsub">◆ 次级别确认：${sub}</div>` +
    `<div class="dsub">◇ 上级别语境：${upper}</div>`;
  el.style.display = 'block';
}
document.getElementById('tradeList').addEventListener('click', e => {
  const row = e.target.closest('.buy-row');
  if (!row || row.dataset.time == null) return;
  const time = Number(row.dataset.time);
  const i = idxMap[time];
  const all = buysFor(activeView, pivotMode).concat(sellsFor(activeView, pivotMode));
  const p = all.find(x => x.t === time);
  if (p) showDetail(p);
  if (i == null) return;
  const half = Math.max(60, Math.floor(candlesFor('raw').length * 0.08));
  chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, i - half), to: i + half });
});

// ---- 回测交易面板 ----
function computeTradeStats(list){
  const n = list.length;
  if (!n) return null;
  const rets = list.map(x => x.ret);
  const wins = rets.filter(r => r > 0), losses = rets.filter(r => r <= 0);
  let eq = 1, peak = 1, mdd = 0;
  for (const r of rets){ eq *= 1 + r / 100; peak = Math.max(peak, eq); mdd = Math.max(mdd, (peak - eq) / peak); }
  const gw = wins.reduce((a,b)=>a+b,0), gl = losses.reduce((a,b)=>a+b,0);
  return {
    n,
    win: 100 * wins.length / n,
    avg: rets.reduce((a,b)=>a+b,0) / n,
    total: (eq - 1) * 100,
    pf: gl !== 0 ? gw / Math.abs(gl) : Infinity,
    mdd: mdd * 100,
    stops: list.filter(x => x.reason === '止损').length,
    hold: list.reduce((a,x)=>a+x.hold_bars,0) / n,
  };
}

function renderTradePanel(){
  const stBox = document.getElementById('tradeStats');
  const listBox = document.getElementById('backtestTradeList');
  document.getElementById('tradePanelMode').textContent =
    `（${activeTF} · ${tradeMode === 'stroke' ? '笔级' : '线段级'} · ${tradeCfg}${layers.trades ? '' : ' · 图层已关闭'}）`;
  const list = layers.trades ? tradesFor() : [];
  const st = computeTradeStats(list);
  if (!st){
    stBox.innerHTML = '';
    listBox.innerHTML = `<div class="note">${strokeStd === 'old'
      ? '老笔为对照口径，暂无对应回测交易（回测基于新笔信号）。'
      : layers.trades ? '当前组合无交易。' : '「交易」图层未开启，打开后显示回测交易。'}</div>`;
    return;
  }
  const cells = [
    ['笔数', String(st.n)],
    ['胜率', st.win.toFixed(1) + '%'],
    ['均笔', (st.avg >= 0 ? '+' : '') + st.avg.toFixed(2) + '%'],
    ['复利', (st.total >= 0 ? '+' : '') + st.total.toFixed(1) + '%'],
    ['PF', st.pf === Infinity ? '∞' : st.pf.toFixed(2)],
    ['最大回撤', st.mdd.toFixed(1) + '%'],
    ['止损次数', String(st.stops)],
    ['平均持有', st.hold.toFixed(0) + '根'],
  ];
  stBox.innerHTML = cells.map(c => `<div><span class="k">${c[0]}</span><span class="v"><strong>${c[1]}</strong></span></div>`).join('');
  // 按类型分组逐笔列出: 入场时间 | 出场原因 | 收益率
  const groups = { '一买':[], '二买':[], '三买':[] };
  list.forEach((tr,i) => { if (groups[tr.type]) groups[tr.type].push({tr,i}); });
  let html = '';
  for (const kind of ['一买','二买','三买']){
    const g = groups[kind];
    if (!g.length) continue;
    html += `<div class="trade-group"><span style="color:${kindColor(kind)}">${kind}</span><span style="color:var(--muted);font-weight:400">${g.length}</span></div>`;
    for (const {tr,i} of g){
      html += `<div class="trade-row${selectedTrade === tr ? ' sel' : ''}" data-i="${i}" title="点击定位该笔交易">` +
        `<span class="buy-date">${fmtTime(tr.entry_t)}</span>` +
        `<span style="display:flex;gap:8px"><span style="color:var(--muted)">${tr.reason}</span>` +
        `<span class="trade-ret" style="color:${tr.ret > 0 ? 'var(--up)' : 'var(--down)'}">${fmtRet(tr.ret)}</span></span></div>`;
    }
  }
  listBox.innerHTML = html;
}

function showTradeDetail(tr){
  const el = document.getElementById('tradeDetailBox');
  const reasonTxt = tr.reason === '止损' ? '🛑 盘中止损触发'
                  : tr.reason === '期末' ? '⏹ 数据末端强平'
                  : `🏁 ${tr.reason} 确认收盘卖出`;
  el.innerHTML =
    `<div class="dk" style="color:${kindColor(tr.type)}">回测·${tr.type} · ${tr.mode === 'stroke' ? '笔级' : '线段级'} · ${tr.cfg}</div>` +
    `<div>入场 <strong>${fmtTime(tr.entry_t)}</strong> @ <strong>${fmt(tr.entry)}</strong>（买点确认根收盘）</div>` +
    `<div>出场 <strong>${fmtTime(tr.exit_ts)}</strong> @ <strong>${fmt(tr.exit)}</strong> · ${reasonTxt}</div>` +
    `<div>收益 <strong style="color:${tr.ret > 0 ? 'var(--up)' : 'var(--down)'}">${fmtRet(tr.ret)}</strong> · 持有 ${tr.hold_bars} 根K线 · 止损距离 ${tr.stop_dist}%</div>`;
  el.style.display = 'block';
}

// 选中一笔交易: 图表高亮 + 框住入场→出场区间
function selectTrade(tr){
  selectedTrade = tr;
  refreshTrades();
  renderTradePanel();
  if (!tr){ document.getElementById('tradeDetailBox').style.display = 'none'; return; }
  showTradeDetail(tr);
  const i1 = locateIdx(tr.entry_t), i2 = locateIdx(tr.exit_ts);
  if (i1 != null){
    const pad = 20;
    const from = Math.max(0, i1 - pad);
    const to = i2 != null && i2 > i1 ? i2 + pad : from + 160;
    chart.timeScale().setVisibleLogicalRange({ from, to: Math.max(to, from + 60) });
  }
}

document.getElementById('backtestTradeList').addEventListener('click', e => {
  const row = e.target.closest('.trade-row');
  if (!row || row.dataset.i == null) return;
  const tr = tradesFor()[Number(row.dataset.i)];
  if (tr) selectTrade(tr);
});

function setTradeMode(m){
  tradeMode = m;
  document.querySelectorAll('#tradeModeToggle button').forEach(b => b.classList.toggle('active', b.dataset.tmode === m));
  selectTrade(null);
  refreshAll();
}
document.getElementById('tradeModeToggle').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (b) setTradeMode(b.dataset.tmode);
});

// ---- 控件 ----
document.getElementById('tfToggle').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (b) setTF(b.dataset.tf);
});
document.getElementById('viewToggle').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (b) setView(b.dataset.view);
});
document.getElementById('pivotToggle').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (b) setPivotMode(b.dataset.mode);
});
document.getElementById('stdToggle').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (b) setStd(b.dataset.std);
});
document.getElementById('layerToggle').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b || b.dataset.layer == null) return;
  const key = b.dataset.layer;
  layers[key] = !layers[key];
  b.classList.toggle('active', layers[key]);
  refreshAll();
});
document.getElementById('fitBtn').addEventListener('click', () => { if (!trLocked()) chart.timeScale().fitContent(); });
document.getElementById('themeBtn').addEventListener('click', () => {
  const cur = document.documentElement.dataset.theme;
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  document.getElementById('themeBtn').textContent = next === 'dark' ? '☀️ 浅色' : '🌙 深色';
  applyTheme();
});
// 侧栏折叠/展开: 收起后图表占满全宽
document.getElementById('sideToggleBtn').addEventListener('click', () => {
  const collapsed = document.body.classList.toggle('side-collapsed');
  document.getElementById('sideToggleBtn').textContent = collapsed ? '\u21f2 侧栏' : '\u21f1 侧栏';
  // alignPanes 会把副图宽度写死成 px; 折叠后先清掉让其回到 100% 弹性, 再重新对齐
  ['volume', 'macd'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.width = '';
  });
  requestAnimationFrame(alignPanes);
  setTimeout(alignPanes, 140);
  requestAnimationFrame(alignPanes);
});

// ---- 初始化 ----
window.__INIT_OK = false;
try {
document.title = document.title.replace('BTC/USD', DATA.meta.symbol);
document.getElementById('symbolBadge').textContent = `${DATA.meta.symbol} · ${activeTF}`;
syncTfTabs();
// ?layers=buys,sells,fractals,fast... 深链直达指定结构图层(分享视图/渲染测试用)
if (QP.get('layers')){
  for (const k of QP.get('layers').split(',')) if (k in layers) layers[k] = true;
}
// 图层按钮高亮状态与内部开关对齐(默认全关)
document.querySelectorAll('#layerToggle button').forEach(b => b.classList.toggle('active', !!layers[b.dataset.layer]));
{
  const ls = document.querySelector('.legend-symbol');
  if (ls) ls.textContent = DATA.meta.symbol;
}
setupReplay();
setupTrainer();
setupSnapshot();
setupJournal();
setupMeasure();
setupFullscreen();
// MA 周期输入: 回车/失焦时重建(applySeries 已含 rebuildMA)
document.getElementById('maPeriods').addEventListener('change', () => {
  rebuildMA();
  flashBtn('snapshotBtn', 'MA 已更新');
});
// MA 关闭/恢复: 隐藏时记住周期, 再点一键恢复
let _maSaved = document.getElementById('maPeriods').value.trim();
// ---- 视角开关: 缠论 / MA / MACD / 量 —— 平级的观察视角 ----
let chanMaster = true, savedLayers = null;
document.getElementById('overlayToggle').addEventListener('click', e => {
  const b = e.target.closest('button[data-ov]'); if (!b) return;
  const ov = b.dataset.ov;
  b.classList.toggle('active');
  const on = b.classList.contains('active');
  if (ov === 'chan'){
    // 缠论总开关: 关=全部结构图层隐藏(记住子开关), 开=恢复; ⚙ 入口随动
    chanMaster = on;
    if (!on){ savedLayers = {...layers}; Object.keys(layers).forEach(k => layers[k] = false); }
    else if (savedLayers) Object.assign(layers, savedLayers);
    document.querySelectorAll('#layerToggle button').forEach(x =>
      x.classList.toggle('active', !!layers[x.dataset.layer]));
    document.getElementById('chanDetailsBtn').style.display = on ? '' : 'none';
    refreshAll();
  } else if (ov === 'ma'){
    maSeries.forEach(it => it.series.applyOptions({ visible: on }));
    document.getElementById('maPeriods').style.display = on ? '' : 'none';
    renderMaLegend();
  } else {
    const el = document.getElementById(ov === 'macd' ? 'macd' : 'volume');
    el.style.display = on ? '' : 'none';
    requestAnimationFrame(alignPanes);
  }
});
document.getElementById('chanDetailsBtn').addEventListener('click', () => {
  const d = document.getElementById('chanDetails');
  d.style.display = d.style.display === 'none' ? 'flex' : 'none';
});
setView(QP.get('view') === 'merged' ? 'merged' : 'raw');
if (QP.get('pivot') === 'stroke') setPivotMode('stroke');
if (QP.get('std') === 'old') setStd('old');
if (QP.get('tf') && DATA.meta.timeframes.includes(QP.get('tf'))) setTF(QP.get('tf'));
if (QP.get('sym')) switchSymbol(QP.get('sym').toUpperCase(), true);
// 全局错误捕获: 保留最近 5 条完整堆栈, 点快照按钮可查看(避免静默失败且信息丢失)
globalThis.__ERRS = [];
const __pushErr = msg => {
  __ERRS.unshift(String(msg).slice(0, 600));
  if (__ERRS.length > 5) __ERRS.pop();
  flashBtn('snapshotBtn', `⚠ 错误×${__ERRS.length} 点此查看`);
};
window.addEventListener('error', e =>
  __pushErr((e.message || '脚本错误') + '\n' + ((e.error && e.error.stack) || '').split('\n').slice(1, 4).join('\n')));
window.addEventListener('unhandledrejection', e =>
  __pushErr(((e.reason && e.reason.message) || '异步错误') + '\n' + ((e.reason && e.reason.stack) || '').split('\n').slice(0, 3).join('\n')));
if (QP.get('fast') === '1'){
  layers.fast = true;
  document.querySelectorAll('#layerToggle button').forEach(b => {
    if (b.dataset.layer === 'fast') b.classList.add('active');
  });
}
if (QP.get('detail')){
  const all = buysFor(activeView, pivotMode).concat(sellsFor(activeView, pivotMode));
  let target = null;
  if (QP.get('t')) target = all.find(x => String(x.t) === QP.get('t'));
  if (!target) target = all.find(x => x.k === '一买') || all[0];
  if (target){
    showDetail(target);
    const i = idxMap[target.t];
    if (i != null){
      const half = Math.max(60, Math.floor(candlesFor('raw').length * 0.08));
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, i - half), to: i + half });
    }
  }
}
  window.__INIT_OK = true;
} catch (e){
  globalThis.__ERRS = globalThis.__ERRS || [];
  __ERRS.unshift('init: ' + e.message + '\n' + String(e.stack || '').split('\n').slice(1, 5).join('\n'));
  flashBtn('snapshotBtn', '⚠ init 异常 点此查看');
  try { applySeries(); alignPanes(); } catch {}
}

window.addEventListener('load', () => setTimeout(() => {
  const v = document.getElementById('volume'), m = document.getElementById('macd');
  const volOk = !!(v && v.querySelector('canvas')) && v.clientHeight > 20;
  const macdOk = !!(m && m.querySelector('canvas')) && m.clientHeight > 20;
  if (!window.__INIT_OK || !volOk || !macdOk){
    const errs = (globalThis.__ERRS && __ERRS.length) ? '\n\n最近错误:\n' + __ERRS.join('\n---\n') : '\n(无捕获错误, 可能为加载/缓存问题, 请强制刷新 Cmd+Shift+R)';
    alert('页面初始化自检未通过:\n' +
      'INIT_OK=' + !!window.__INIT_OK + '  成交量副图=' + volOk + '  MACD副图=' + macdOk +
      '  容器高度=' + (v && v.clientHeight) + '/' + (m && m.clientHeight) + errs +
      '\n\n请截图此弹窗反馈。');
  }
}, 1500));
