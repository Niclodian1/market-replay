// js/04-panels.js — 标记与索引 / applySeries / refreshAll / 周期切换 / 复盘 / 快照
// ---- 标记(分型 + 买点 + 卖点) ----
function buildMarkers(v){
  const T = t(), out = [];
  const sellTimes = new Set();
  if (layers.sells) sellsFor(v, pivotMode).forEach(x => sellTimes.add(x.t));
  // 注意: 底分型 ▲ 在 bar 下方(belowBar), 买点 ● 在 bar 上方(aboveBar),
  //       二者不重叠, 故底分型箭头不再被买点跳过 —— 一买/二买/三买处的
  //       ▲ 底分型应始终可见(如 2026-06-30 一买 与 ▲ 同 bar 并存)。
  //       顶分型↔卖点同在上方、真重叠, 仍跳过。
  // 抢先背驰层开启时, 分型箭头在其时间处让位给 告警/背驰买/背驰卖 标记。
  const fastTimes = new Set();
  if (layers.fast){
    alertsFor(v).forEach(x => fastTimes.add(x.t));
    fastFor(v, pivotMode).forEach(x => fastTimes.add(x.t));
  }

  topMarks(v).forEach(x => {
    if (layers.fractals && !sellTimes.has(x.time) && !fastTimes.has(x.time)) out.push({ time:x.time, position:'aboveBar', color:T.top, shape:'arrowDown', text:'', size:1 });
  });
  botMarks(v).forEach(x => {
    if (layers.fractals && !fastTimes.has(x.time)) out.push({ time:x.time, position:'belowBar', color:T.bottom, shape:'arrowUp', text:'', size:1 });
  });
  if (layers.buys) buysFor(v, pivotMode).forEach(b => {
    out.push({ time:b.t, position:'aboveBar', color:kindColor(b.k), shape:'circle', text:b.k, size:1 });
  });
  if (layers.sells) sellsFor(v, pivotMode).forEach(s => {
    out.push({ time:s.t, position:'aboveBar', color:kindColor(s.k), shape:'square', text:s.k, size:1 });
  });
  if (layers.fast){
    alertsFor(v).forEach(x => {
      out.push({ time:x.t, position: x.side === '卖' ? 'aboveBar' : 'belowBar', color:kindColor('告警'), shape:'circle', text:'警', size:1 });
    });
    fastFor(v, pivotMode).forEach(x => {
      out.push({ time:x.t, position: x.side === '卖' ? 'aboveBar' : 'belowBar', color:kindColor(x.k), shape:'circle', text:x.k, size:1 });
    });
  }
  if (layers.panbei) panbeiFor(v, pivotMode).forEach(p => {
    out.push({ time:p.t, position:'belowBar', color:kindColor(p.k), shape:'triangle', text:p.k, size:1 });
  });
  // 注: 回测交易不在 setMarkers 中画点 —— TradeOverlay 已在入场价处画 ●,
  //     避免同一位置双重圆点; 悬停提示走 markerMap(见 buildMaps)。
  out.sort((a,b) => a.time < b.time ? -1 : a.time > b.time ? 1 : 0);
  return out;
}

function buildVolData(v){
  const T = t(), cs = candlesFor(v), vols = volArrFor(v);
  return cs.map((c,i) => ({ time:c.time, value:vols[i], color: c.close >= c.open ? T.up : T.down }));
}

function buildMacdData(v){
  const T = t(), m = macdFor(v), cs = candlesFor(v);
  return {
    hist: cs.map((c,i) => ({ time:c.time, value:m.hist[i], color: m.hist[i] >= 0 ? T.up : T.down })),
    diff: cs.map((c,i) => ({ time:c.time, value:m.diff[i] })),
    dea:  cs.map((c,i) => ({ time:c.time, value:m.dea[i] })),
  };
}

function buildMaps(v){
  const cs = candlesFor(v);
  idxMap = {}; cs.forEach((c,i) => idxMap[c.time] = i);
  markerMap = {};
  topMarks(v).forEach(x => { if (layers.fractals) markerMap[x.time] = { kind:'top',    price:x.price, color:t().top }; });
  botMarks(v).forEach(x => { if (layers.fractals) markerMap[x.time] = { kind:'bottom', price:x.price, color:t().bottom }; });
  if (layers.buys) buysFor(v, pivotMode).forEach(b => {
    if (!markerMap[b.t]) markerMap[b.t] = { kind:b.k, price:b.p, color:kindColor(b.k) };
  });
  if (layers.sells) sellsFor(v, pivotMode).forEach(s => {
    if (!markerMap[s.t]) markerMap[s.t] = { kind:s.k, price:s.p, color:kindColor(s.k) };
  });
  if (layers.fast){
    alertsFor(v).forEach(x => markerMap[x.t] = { kind:'告警', price:x.p, color:kindColor('告警') });
    fastFor(v, pivotMode).forEach(x => markerMap[x.t] = { kind:x.k, price:x.p, color:kindColor(x.k) });
  }
  if (layers.panbei) panbeiFor(v, pivotMode).forEach(p => {
    if (!markerMap[p.t]) markerMap[p.t] = { kind:p.k, price:p.p, color:kindColor(p.k) };
  });
  // 回测交易入场点(最低优先级: 不覆盖分型/买卖点/抢先标记)
  if (layers.trades) tradesFor().forEach(tr => {
    if (!markerMap[tr.entry_t]) markerMap[tr.entry_t] = {
      kind:`回测·${tr.type}`, price:tr.entry, color:kindColor(tr.type), trade:tr,
    };
  });
  // ---- 悬停详情索引: bar时间 → 该bar上所有信号的完整生成依据 ----
  detailMap = {};
  const lvTag = pivotMode === 'seg' ? '线段级' : '笔级';
  const stdTag = strokeStd === 'old' ? '老笔' : '新笔';
  const pushD = (time, title, color, rows, why) =>
    (detailMap[time] = detailMap[time] || []).push({ title, color, rows, why });
  if (layers.buys) buysFor(v, pivotMode).forEach(b => {
    const rows = [
      ['级别', `${lvTag} · ${stdTag}`],
      ['价格', fmt(b.p)],
      ['触发', fmtTime(b.t) + (b.confirmedT !== b.t ? ' → 确认 ' + fmtTime(b.confirmedT) : '')],
    ];
    if (b.tier) rows.push(['强度', b.tier + (b.ratio != null ? ' · 比' + b.ratio : '')]);
    if (b.sub) rows.push(['次级别', b.sub]);
    if (b.upper) rows.push(['上级别', b.upper]);
    pushD(b.t, b.k, kindColor(b.k), rows, b.why);
  });
  if (layers.sells) sellsFor(v, pivotMode).forEach(s => {
    const rows = [
      ['级别', `${lvTag} · ${stdTag}`],
      ['价格', fmt(s.p)],
      ['触发', fmtTime(s.t) + (s.confirmedT !== s.t ? ' → 确认 ' + fmtTime(s.confirmedT) : '')],
    ];
    if (s.tier) rows.push(['强度', s.tier + (s.ratio != null ? ' · 比' + s.ratio : '')]);
    if (s.sub) rows.push(['次级别', s.sub]);
    if (s.upper) rows.push(['上级别', s.upper]);
    pushD(s.t, s.k, kindColor(s.k), rows, s.why);
  });
  if (layers.fast){
    fastFor(v, pivotMode).forEach(x => {
      pushD(x.t, `⚡ ${x.k}(抢先·${lvTag})`, kindColor(x.k), [
        ['价格', fmt(x.p)], ['比', x.ratio], ['触发', fmtTime(x.confirmedT)]], x.why);
    });
  }
  if (layers.panbei) panbeiFor(v, pivotMode).forEach(p => {
    pushD(p.t, `◈ ${p.k}(${lvTag})`, kindColor(p.k), [
      ['价格', fmt(p.p)], ['触发', fmtTime(p.confirmedT)]], p.why);
  });
  if (layers.trades) tradesFor().forEach(tr => {
    pushD(tr.entry_t, `回测·${tr.type}`, kindColor(tr.type), [
      ['入场', fmtTime(tr.entry_t) + ' @ ' + fmt(tr.entry)],
      ['出场', fmt(tr.exit)],
      ['收益', fmtRet(tr.ret)], ['原因', tr.reason],
    ], null);
  });
}

function applySeries(){
  candleSeries.setData(candlesFor(activeView));
  candleSeries.setMarkers(buildMarkers(activeView));
  volumeSeries.setData(buildVolData(activeView));
  const md = buildMacdData(activeView);
  macdHistSeries.setData(md.hist);
  macdLineSeries.setData(md.diff);
  macdSignalSeries.setData(md.dea);
  buildMaps(activeView);
  rebuildMA();
}

function refreshPanels(){
  renderTradeList();
  computeStats();
  renderTradePanel();
}
let _panelRaf = null;
// refreshAll 分级: light=true 时(步进/回放)面板走 rAF 合并刷新, 图表立即更新
function refreshAll(opts = {}){
  applySeries();
  updateLivePnl();   // 复盘持仓浮动盈亏随K线实时刷新(只改文本, 不重建表单)
  if (opts.light){
    if (_panelRaf == null){
      _panelRaf = requestAnimationFrame(() => { _panelRaf = null; refreshPanels(); });
    }
  } else {
    refreshPanels();
  }
  // 主图价格轴宽度首帧布局后才可知, 数据重建/切视图/切周期后对齐一次量/MACD 面板
  requestAnimationFrame(alignPanes);
  setTimeout(alignPanes, 80);
  // 练习视口锁定: 任何刷新(fitContent/切视图/步进)后都把可视范围拉回锁定窗口
  if (trLocked()) requestAnimationFrame(lockViewport);
}
window.addEventListener('resize', () => requestAnimationFrame(alignPanes));

function setView(v){
  activeView = v;
  document.querySelectorAll('#viewToggle button').forEach(b => b.classList.toggle('active', b.dataset.view === v));
  refreshAll();
  // 练习中禁止 fitContent(会暴露练习窗口之外的全部历史), 改为锁定视口
  if (trLocked()) lockViewport();
  else chart.timeScale().fitContent();
}

function setPivotMode(m){
  pivotMode = m;
  document.querySelectorAll('#pivotToggle button').forEach(b => b.classList.toggle('active', b.dataset.mode === m));
  refreshAll();
}

function setStd(s){
  if (strokeStd === s) return;
  strokeStd = s;
  document.querySelectorAll('#stdToggle button').forEach(b => b.classList.toggle('active', b.dataset.std === s));
  selectTrade(null);   // 交易锚点属于旧口径, 切换后清除
  refreshAll();
}

async function setTF(tf){
  const switching = tf !== activeTF;
  const needChunkLoad = chunkedActive() && !(ChunkStore.win && ChunkStore.win.key === `${currentSym}/${TF_DIR[tf]}`);
  if (!switching && !needChunkLoad) return;
  activeTF = tf;
  document.querySelectorAll('#tfToggle button').forEach(b => b.classList.toggle('active', b.dataset.tf === tf));
  document.getElementById('symbolBadge').textContent = `${DATA.meta.symbol} · ${tf}`;
  chart.applyOptions(chartOptions());
  volumeChart.applyOptions({ ...chartOptions(), grid: { vertLines: { visible: false }, horzLines: { color: t().grid } }, timeScale: { ...chartOptions().timeScale, visible: false } });
  macdChart.applyOptions({ ...chartOptions(), grid: { vertLines: { visible: false }, horzLines: { color: t().grid } }, timeScale: { ...chartOptions().timeScale, visible: false } });
  // 分片周期: 复盘日期输入升级到分钟精度; 结构类工具置灰
  document.getElementById('replayDate').type = chunkedActive() ? 'datetime-local' : 'date';
  document.body.classList.toggle('tf-chunked', chunkedActive());
  if (chunkedActive()){
    if (!(await ensureChunkWindow())){
      activeTF = '1D';   // 无分片数据回退日线
      document.querySelectorAll('#tfToggle button').forEach(b => b.classList.toggle('active', b.dataset.tf === '1D'));
      document.getElementById('symbolBadge').textContent = `${DATA.meta.symbol} · 1D`;
      document.getElementById('replayDate').type = 'date';
      document.body.classList.remove('tf-chunked');
      refreshAll();
      chart.timeScale().fitContent();
      return;
    }
  } else {
    ChunkStore.win = null;   // 离开分钟级释放窗口内存
  }
  refreshAll();
  chart.timeScale().fitContent();
}

// ---- 分钟级分片窗口装载 ----
function chunkWindowReady(){
  const w = ChunkStore.win;
  return !!w && w.key === `${currentSym}/${TF_DIR[activeTF]}`;
}
async function ensureChunkWindow(){
  if (chunkWindowReady()) return true;
  const man = await ChunkStore.manifest(currentSym, activeTF);
  if (!man || !man.total){ alert(`${currentSym} 暂无 ${activeTF} 分钟级分片数据`); return false; }
  const sec = man.sec;
  const lastT = man.chunks[man.chunks.length - 1].t1;
  const span = (TR.LOOKBACK * 2 + 20) * sec;   // 默认展示最近 LOOKBACK*2 根
  const ok = await ChunkStore.loadWindow(currentSym, activeTF,
    Math.max(man.chunks[0].t0, lastT - span), lastT + sec);
  if (!ok){ alert('分片加载失败, 请稍后重试'); return false; }
  return true;
}

// ---- 复盘模式: 截断到任意K线 + 向后逐根回放 ----
// 数据访问器全部按 replayT 过滤(买卖点按 confirmedT), 因此截断后的
// 笔/分型/中枢/信号 = 因果引擎在该时刻的真实所见, 无未来信息。
let replayTimer = null;
const REPLAY_STEP_MS = 400;   // 播放速度: 每根K线间隔

function allTimes(){
  if (chunkedActive()) return ChunkStore.win ? ChunkStore.win.times : [];
  return L().candles.map(c => c[0]);
}
function setReplayT(ts){
  const times = allTimes();
  if (!times.length) return;
  // 定位到 <= ts 的最后一根K线
  let lo = 0, hi = times.length - 1, pos = 0;
  while (lo <= hi){
    const mid = (lo + hi) >> 1;
    if (times[mid] <= ts){ pos = mid; lo = mid + 1; } else hi = mid - 1;
  }
  replayT = times[pos];
  document.getElementById('replayInfo').textContent =
    `${fmtTime(replayT)} · ${pos + 1}/${times.length}`;
  checkStopAndLiquidate();   // 新K线: 检查止损/爆仓
  checkAccountBlowup();      // 账户级爆仓: 净值≤0 强平并结束本局
  refreshAll({ light: true });   // 步进轻量刷新: 图表立即更新, 侧栏面板 rAF 合并
}
function enterReplay(ts){
  replayT = ts || allTimes()[Math.floor(allTimes().length * 0.8)];
  document.getElementById('replayCtl').style.display = 'flex';
  document.getElementById('replayBtn').textContent = '复盘中…';
  document.getElementById('replayBtn').classList.add('active');
  {
    const d = new Date(replayT * 1000);
    const ymd = `${d.getUTCFullYear()}-${p2(d.getUTCMonth() + 1)}-${p2(d.getUTCDate())}`;
    document.getElementById('replayDate').value = chunkedActive()
      ? `${ymd}T${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}`
      : ymd;
  }
  renderJournal();   // 刷新复盘模式显示与按钮可用态
  refreshAll();
}
function exitReplay(){
  stopPlay();
  replayT = null;
  document.getElementById('replayCtl').style.display = 'none';
  document.getElementById('replayBtn').textContent = '进入复盘';
  document.getElementById('replayBtn').classList.remove('active');
  document.getElementById('replayInfo').textContent = '';
  renderJournal();   // 退出复盘: 刷新模式显示与按钮可用态
  refreshAll();
  chart.timeScale().fitContent();
}
function stopPlay(){
  if (replayTimer){ clearInterval(replayTimer); replayTimer = null; }
  const pb = document.getElementById('replayPlay');
  if (pb) pb.textContent = '▶ 播放';
}
function stepReplay(dir){
  const times = allTimes();
  const i = times.indexOf(replayT);
  const ni = Math.max(0, Math.min(times.length - 1, (i < 0 ? times.length - 1 : i) + dir));
  setReplayT(times[ni]);
  // 分钟级: 步进到窗口末尾时自动续载下一分片并补一步(普通复盘向右漫游用)
  if (chunkedActive() && dir > 0 && ni === times.length - 1 && !ChunkStore._extending){
    ChunkStore._extending = true;
    ChunkStore.extend(1).then(added => {
      ChunkStore._extending = false;
      if (added > 0){ refreshAll(); stepReplay(1); }
    });
  }
}
function togglePlay(){
  if (replayTimer){ stopPlay(); return; }
  const times = allTimes();
  let i = times.indexOf(replayT);
  if (i >= times.length - 1) i = 0;   // 到末尾则从头播放
  document.getElementById('replayPlay').textContent = '⏸ 暂停';
  const tick = () => {
    i++;
    if (i >= times.length){ stopPlay(); return; }
    setReplayT(times[i]);
  };
  replayTimer = setInterval(tick, REPLAY_STEP_MS);
}
function setupReplay(){
  const btn = document.getElementById('replayBtn');
  btn.addEventListener('click', () => inReplay() ? exitReplay() : enterReplay());
  document.getElementById('replayGo').addEventListener('click', () => {
    const v = document.getElementById('replayDate').value;
    if (!v) return;
    const d = v.match(/^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?$/);
    if (!d) return;
    enterReplay(Date.UTC(+d[1], +d[2] - 1, +d[3], +(d[4] || 0), +(d[5] || 0)) / 1000);
  });
  document.getElementById('replayPrev').addEventListener('click', () => { stopPlay(); stepReplay(-1); });
  document.getElementById('replayNext').addEventListener('click', () => { stopPlay(); stepReplay(1); });
  document.getElementById('replayPlay').addEventListener('click', togglePlay);
  document.getElementById('replayExit').addEventListener('click', exitReplay);
}

// ---- 一键快照: 三面板合成一张图 → 剪贴板(并下载) ----
function setupSnapshot(){
  document.getElementById('snapshotBtn').addEventListener('click', async () => {
    // 有未读错误时优先展示完整堆栈(诊断用), 无错误才走截图
    if (globalThis.__ERRS && __ERRS.length){
      alert('最近捕获的错误:\n\n' + __ERRS.map((s, i) => `[${i + 1}] ${s}`).join('\n\n'));
      __ERRS.length = 0;
      flashBtn('snapshotBtn', '📷 快照');
      return;
    }
    try {
      // lightweight-charts v4 的 chart 提供 takeScreenshot()(canvas)
      const c1 = chart.takeScreenshot();
      const c2 = volumeChart.takeScreenshot();
      const c3 = macdChart.takeScreenshot();
      const W = Math.max(c1.width, c2.width, c3.width);
      const H = c1.height + c2.height + c3.height + 30;
      const out = document.createElement('canvas');
      out.width = W; out.height = H;
      const ctx = out.getContext('2d');
      ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--plane').trim() || '#0d0d0d';
      ctx.fillRect(0, 0, W, H);
      // 标题行
      ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--ink').trim() || '#fff';
      ctx.font = '13px -apple-system, sans-serif';
      const symShow = trLocked() ? '???' : DATA.meta.symbol;   // 盲测态快照不泄露品种
      const info = inReplay()
        ? `${symShow} ${activeTF} · 复盘至 ${fmtTime(replayT)}`
        : `${symShow} ${activeTF} · ${fmtTime(candlesFor(activeView).at(-1)?.time)}`;
      ctx.fillText(info, 12, 20);
      let y = 30;
      for (const c of [c1, c2, c3]){ ctx.drawImage(c, 0, y); y += c.height; }
      // 仅复制到剪贴板(不下载)
      const blob = await new Promise(r => out.toBlob(r, 'image/png'));
      try {
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
        flashBtn('snapshotBtn', '✓ 已复制到剪贴板');
      } catch { flashBtn('snapshotBtn', '✗ 剪贴板不可用'); }
    } catch (e) { alert('快照失败: ' + e.message); }
  });
}
function flashBtn(id, txt){
  const b = document.getElementById(id);
  const old = b.textContent;
  b.textContent = txt;
  setTimeout(() => b.textContent = old, 1600);
}
