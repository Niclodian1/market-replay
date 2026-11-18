// js/06-trainer.js — 盘感练习器: 随机盲测 / 步进 / 揭示 / 战绩与历史存档
// ---- 盘感练习器: 随机品种 × 随机周期 × 随机起点, 盲测逐步回放 ----
// 复用复盘模式的因果截断(before)与 journal 开单流程; 增量能力:
//   ① 视口锁定过去 LOOKBACK 根(handleScroll/Scale 禁用 + 每次刷新后拉回窗口)
//   ② 时间打码: fmtTime → trLabel 相对根序号(T-87 过去 / T+12 起点后第12根)
//   ③ 固定步数走满强制揭示; 揭示展开完整未来 + 本局交易复盘横幅
//   ④ 战绩 localStorage 持久化(trainer_stats_v1), 只计已平仓单
// 战绩 localStorage 持久化(trainer_stats_v1), 只计已平仓单
const TRKEY = 'chan_trainer_stats_v1';
// 练习局历史存档: 每局独立本金 + 独立交易明细, 可回溯查看
const tsLoad = () => PracticeStore.list();
const TR_EMPTY = () => ({ sessions:0, orders:0, wins:0, grossWin:0, grossLoss:0, pnlSum:0, pnlPctSum:0, perSymbol:Object.create(null) });
const randInt = n => Math.floor(Math.random() * n);

// ================= 净值曲线 / 加仓对照 / 专项选题 =================
// 不加仓对照净值 = 实际净值 − 已实现加仓贡献 − 未平仓加仓的保证金与浮盈。
// 口径: 每笔只保留首仓, 出场时点/比例与实操完全一致(即"不加仓、保持原退出时点")。
function cfEquity(){
  let eq = acctEquity() - (TR.addPnlRealized || 0);
  const bar = curBar();
  for (const p of positions){
    if (!posLayoutOk(p) || p.entries.length < 2) continue;
    const addMargin = p.entries.slice(1).reduce((a,e) => a + e.capital, 0);
    const firstOnly = { ...p, entries: [p.entries[0]] };
    eq -= addMargin + (bar ? pnlOf(p, bar.close) - pnlOf(firstOnly, bar.close) : 0);
  }
  return eq;
}
// 记录本局净值点(同一 step 覆盖, 避免开/平仓产生重复点)
function recordEquity(){
  if (!TR.active) return;
  const eq = +acctEquity().toFixed(2), cf = +cfEquity().toFixed(2);
  const last = TR.curve[TR.curve.length - 1];
  if (last && last.step === TR.step){ last.eq = eq; last.cf = cf; }
  else TR.curve.push({ step: TR.step, eq, cf });
  renderLiveCurve();
}
// 净值曲线 sparkline: 实线=实际净值; 灰虚线=不加仓对照(两者有差异时才画); base=本金基准线
function equitySparkSvg(pts, w = 168, h = 34, base){
  if (!pts || pts.length < 2) return '<span style="opacity:.5">—</span>';
  const baseV = base ?? (TR.capital0 || INIT_CAPITAL);
  const eqs = pts.map(p => p.eq), cfs = pts.map(p => p.cf ?? p.eq);
  const min = Math.min(...eqs, ...cfs), max = Math.max(...eqs, ...cfs);
  const span = Math.max(max - min, Math.max(Math.abs(max), 1) * 1e-4);
  const X = i => 1 + i / (pts.length - 1) * (w - 2);
  const Y = v => 1 + (max - v) / span * (h - 2);
  const line = arr => arr.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join('');
  const up = eqs[eqs.length - 1] >= eqs[0];
  const y0 = Math.min(h - 1, Math.max(1, Y(baseV)));
  const diff = cfs.some((v, i) => Math.abs(v - eqs[i]) > 0.01);
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="vertical-align:middle;">` +
    `<line x1="0" y1="${y0.toFixed(1)}" x2="${w}" y2="${y0.toFixed(1)}" stroke="var(--muted)" stroke-width="1" stroke-dasharray="3 3" opacity=".45"/>` +
    (diff ? `<path d="${line(cfs)}" fill="none" stroke="var(--muted)" stroke-width="1.2" stroke-dasharray="3 3" opacity=".9"/>` : '') +
    `<path d="${line(eqs)}" fill="none" stroke="${up ? 'var(--up)' : 'var(--down)'}" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
}
function maxDrawdown(pts, key = 'eq'){
  let peak = -Infinity, mdd = 0;
  for (const p of pts || []){
    const v = typeof p === 'number' ? p : p[key];
    if (v > peak) peak = v;
    if (peak > 0) mdd = Math.max(mdd, (peak - v) / peak * 100);
  }
  return mdd;
}
function renderLiveCurve(){
  const el = document.getElementById('trCurveLive');
  if (!el) return;
  const on = TR.active && TR.curve.length >= 2;
  el.style.display = on ? 'inline-flex' : 'none';
  if (on) el.innerHTML = equitySparkSvg(TR.curve, 120, 22);
}
// 最近练过的区间(避开相邻窗口; 支持同一区间隔一段时间再盲练)
const TRRECENT = 'chan_trainer_recent_v1';
const trRecentLoad = () => { try { return JSON.parse(localStorage.getItem(TRRECENT)) || []; } catch { return []; } };
function trRecentPush(sym, tf, ts){
  const arr = trRecentLoad().filter(r => !(r.sym === sym && r.tf === tf && Math.abs(r.ts - ts) < 1));
  arr.unshift({ sym, tf, ts });
  localStorage.setItem(TRRECENT, JSON.stringify(arr.slice(0, 60)));
}
function recentHit(cs, i, recent){
  if (!recent || !recent.length || i + 1 >= cs.length) return false;
  const sec = cs[i + 1].time - cs[i].time;
  return recent.some(r => Math.abs(r.ts - cs[i].time) <= 30 * sec);
}
// 专项选题: 按"起点起 maxStep 根的窗口形态"给候选起点打分(窗口内容在练习中隐藏)。
// 只看窗口 OHLC 与起点前 60 根; 各类取最典型窗口, 返回 -Infinity = 不符合。
function drillScore(cs, i, n, drill){
  if (i < 60 || i + n > cs.length) return -Infinity;
  const win = cs.slice(i, i + n);
  const pre = cs.slice(i - 60, i);
  const preHi = Math.max(...pre.map(c => c.high)), preLo = Math.min(...pre.map(c => c.low));
  const atr = win.reduce((a,c) => a + (c.high - c.low), 0) / win.length || 1e-9;
  const first = win[0].close, last = win[win.length - 1].close;
  let path = 0; for (let k = 1; k < win.length; k++) path += Math.abs(win[k].close - win[k-1].close);
  const eff = Math.abs(last - first) / Math.max(path, 1e-9);
  const netAtr = Math.abs(last - first) / atr;
  if (drill === '趋势持有') return eff * netAtr;
  if (drill === '震荡反复止损'){
    let cross = 0;
    for (let k = 1; k < win.length; k++)
      if ((win[k].close - first) * (win[k-1].close - first) < 0) cross++;
    return cross * 2 - netAtr * 0.6;
  }
  if (drill === '突破失败'){
    // 起点前需是区间(低效率), 排除"趋势中回撤"被误判为突破失败
    let prePath = 0; for (let k = 1; k < pre.length; k++) prePath += Math.abs(pre[k].close - pre[k-1].close);
    const preEff = Math.abs(pre[pre.length-1].close - pre[0].close) / Math.max(prePath, 1e-9);
    if (preEff > 0.35) return -Infinity;
    const hi = Math.max(...win.map(c => c.high)), lo = Math.min(...win.map(c => c.low));
    const failUp = hi > preHi * 1.0005 && last < preHi;   // 上破起点前区间后收回区间内
    const failDn = lo < preLo * 0.9995 && last > preLo;   // 下破后收回
    if (!failUp && !failDn) return -Infinity;
    return failUp ? (hi - preHi) / atr + (hi - last) / atr : (preLo - lo) / atr + (last - lo) / atr;
  }
  if (drill === '加仓后回撤'){
    const mid = Math.floor(win.length / 2);
    const c0 = win[0].close, c1 = win[mid - 1].close;
    const move1 = Math.abs(c1 - c0) / atr;                 // 前半段趋势强度(ATR 计)
    if (move1 < 2) return -Infinity;
    const up = c1 > c0;
    const ext = up ? Math.max(...win.map(c => c.high)) : Math.min(...win.map(c => c.low));
    const extIdx = win.findIndex(c => (up ? c.high : c.low) === ext);
    if (extIdx < mid * 0.6) return -Infinity;              // 极值需在中后段(先加仓后回撤)
    const after = win.slice(extIdx + 1);
    if (!after.length) return -Infinity;
    const pull = up ? ext - Math.min(...after.map(c => c.low)) : Math.max(...after.map(c => c.high)) - ext;
    const retr = pull / Math.max(Math.abs(ext - c0), 1e-9);
    if (retr < 0.35) return -Infinity;                     // 回撤需 ≥ 前段幅度的 35%
    return move1 * Math.min(retr, 1);
  }
  return 0;
}
// 在 [lo,hi] 选起点: 无专项=随机(优先避开最近区间); 有专项=取分数最高的若干窗口随机一个
function pickStartByDrill(cs, lo, hi, n, drill, recent){
  hi = Math.max(lo, hi);
  if (!drill){
    for (let t = 0; t < 40; t++){
      const i = lo + randInt(hi - lo + 1);
      if (!recentHit(cs, i, recent)) return i;
    }
    return lo + randInt(hi - lo + 1);
  }
  const scan = useRecent => {
    const out = [];
    for (let i = lo; i <= hi; i++){
      if (useRecent && recentHit(cs, i, recent)) continue;
      const s = drillScore(cs, i, n, drill);
      if (s > -Infinity) out.push([s, i]);
    }
    return out;
  };
  let scored = scan(true);
  if (!scored.length) scored = scan(false);
  if (!scored.length) return lo + randInt(hi - lo + 1);
  scored.sort((a,b) => b[0] - a[0]);
  const top = scored.slice(0, Math.min(12, scored.length));
  return top[randInt(top.length)][1];
}
// 在 [t,o,h,l,c] 数组上按时间戳二分定位(重练定位用)
function idxOfTsArr(arr, ts){
  let lo = 0, hi = arr.length - 1, ans = null;
  while (lo <= hi){ const m = (lo + hi) >> 1; if (arr[m][0] <= ts){ ans = m; lo = m + 1; } else hi = m - 1; }
  return ans;
}

// ---- 题目描述 / 趋势转换 / 用时 / 信号K标记 ----
// 揭示后按本局实际走过的窗口生成题目描述(强趋势/筑底/高位震荡...)与趋势转换
function describeWindow(cs, i, n){
  const win = cs.slice(i, Math.min(i + n, cs.length));
  if (win.length < 10) return { desc:'', transition:'' };
  const atr = win.reduce((a,c) => a + (c.high - c.low), 0) / win.length || 1e-9;
  const first = win[0].close, last = win[win.length - 1].close;
  const hi = Math.max(...win.map(c => c.high)), lo = Math.min(...win.map(c => c.low));
  let path = 0; for (let k = 1; k < win.length; k++) path += Math.abs(win[k].close - win[k-1].close);
  const net = (last - first) / first, eff = Math.abs(last - first) / Math.max(path, 1e-9);
  const netAtr = Math.abs(last - first) / atr;
  const pos = (last - lo) / Math.max(hi - lo, 1e-9);
  let desc;
  if (eff >= 0.45 && netAtr >= 5) desc = net > 0 ? '强趋势上涨' : '强趋势下跌';
  else if (eff >= 0.25 && netAtr >= 2.5) desc = net > 0 ? '趋势上涨' : '趋势下跌';
  else {
    const loIdx = win.findIndex(c => c.low === lo), hiIdx = win.findIndex(c => c.high === hi);
    if (netAtr >= 2 && loIdx <= win.length * 0.6 && pos >= 0.55) desc = '筑底反转';
    else if (netAtr >= 2 && hiIdx <= win.length * 0.6 && pos <= 0.45) desc = '筑顶反转';
    else if (pos >= 0.7) desc = '高位震荡';
    else if (pos <= 0.3) desc = '低位震荡';
    else desc = '中位震荡';
  }
  // 趋势转换: 前后两半方向翻转(ATR 计)
  const mid = win.length >> 1;
  const h1 = win.slice(0, mid), h2 = win.slice(mid);
  const m1 = (h1[h1.length-1].close - h1[0].close) / atr;
  const m2 = (h2[h2.length-1].close - h2[0].close) / atr;
  let transition = '';
  if (m1 <= -2 && m2 >= 2) transition = '下跌 → 上涨';
  else if (m1 >= 2 && m2 <= -2) transition = '上涨 → 下跌';
  else if (m1 <= -2 && m2 >= 1 && desc === '筑底反转') transition = '下跌 → 筑底';
  else if (m1 >= 2 && m2 <= -1 && desc === '筑顶反转') transition = '上涨 → 筑顶';
  return { desc, transition };
}
function fmtDur(sec){
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  const p2 = n => String(n).padStart(2, '0');
  return h ? `${h}:${p2(m)}:${p2(s)}` : `${p2(m)}:${p2(s)}`;
}
let _trTimer = null;
function renderElapsed(){
  const el = document.getElementById('trainerElapsed');
  if (!el) return;
  el.textContent = TR.active && TR.startWallTs ? fmtDur((Date.now() - TR.startWallTs) / 1000) : '00:00';
}
function startTrainerTimer(){
  stopTrainerTimer();
  renderElapsed();
  _trTimer = setInterval(renderElapsed, 1000);
}
function stopTrainerTimer(){
  if (_trTimer){ clearInterval(_trTimer); _trTimer = null; }
}
// 结算本局元信息: 用时 + 题目描述/趋势转换(在 replayT 清空前调用)
function finishSessionMeta(){
  stopTrainerTimer();
  TR.durSec = Math.max(1, Math.round((Date.now() - (TR.startWallTs || Date.now())) / 1000));
  renderElapsed();
  const idx0 = locateIdx(TR.startTs);
  if (idx0 != null && TR.step >= 10){
    const dw = describeWindow(candlesFor('raw'), idx0, TR.step);
    TR.desc = dw.desc; TR.transition = dw.transition;
  }
}
// 信号K / 观察标记: 对当前K线切换(两类互斥), 上图并刷新按钮态
function toggleTrainerMark(kind){
  if (!trLocked()) return;
  const bar = curBar();
  if (!bar) return;
  const idx = TR.marks.findIndex(m => m.t === bar.time);
  if (idx >= 0 && TR.marks[idx].kind === kind) TR.marks.splice(idx, 1);
  else if (idx >= 0) TR.marks[idx] = { t: bar.time, kind, step: TR.step };
  else TR.marks.push({ t: bar.time, kind, step: TR.step });
  prCheckpoint();
  refreshAll({ light: true });
  updateTrainerUI();
}
// 信号K对交易的影响: 标记后 10 根内是否开仓; 有信号 vs 无信号开仓的平均盈亏
function markImpact(s){
  const marks = s.marks || [], trades = s.trades || [];
  const entryStep = t => (t.entries && t.entries[0] && t.entries[0].step != null) ? t.entries[0].step : null;
  const rows = marks.map(m => ({
    m, tr: trades.find(t => { const st = entryStep(t); return st != null && st >= m.step && st <= m.step + 10; }) || null,
  }));
  const followed = new Set(rows.filter(r => r.tr).map(r => r.tr));
  const withSig = trades.filter(t => followed.has(t));
  const withoutSig = trades.filter(t => !followed.has(t));
  const avg = arr => arr.length ? arr.reduce((a,t) => a + (t.pnl_u || 0), 0) / arr.length : null;
  return {
    rows,
    sigCount: marks.filter(m => m.kind === 'signal').length,
    watchCount: marks.filter(m => m.kind === 'watch').length,
    sigFollow: rows.filter(r => r.m.kind === 'signal' && r.tr).length,
    sigMiss: rows.filter(r => r.m.kind === 'signal' && !r.tr).length,
    watchFollow: rows.filter(r => r.m.kind === 'watch' && r.tr).length,
    watchMiss: rows.filter(r => r.m.kind === 'watch' && !r.tr).length,
    avgWith: avg(withSig), nWith: withSig.length,
    avgWithout: avg(withoutSig), nWithout: withoutSig.length,
  };
}

function applyBlind(on){
  document.body.classList.toggle('trainer-blind', on);
  document.querySelector('.legend-symbol').textContent = on ? '???' : DATA.meta.symbol;
  document.getElementById('symbolBadge').textContent =
    on ? `盲测中 · ${activeTF}` : `${DATA.meta.symbol} · ${activeTF}`;
  document.title = on ? '盘感练习 · 盲测中' : DATA.meta.symbol + ' · 缠论多级别联动';
  document.getElementById('replayCtl').style.display = 'none';   // 练习中禁用普通复盘控件(防跳步作弊)
  // 盲测态临时隐藏算法买点图层(不篡改 localStorage 偏好), 揭示/退出后恢复
  let algoChanged = false;
  if (on){
    if (!TR._blindAlgoSnap){
      TR._blindAlgoSnap = {
        aiMa20: typeof AI_MA20_STATE !== 'undefined' && !!AI_MA20_STATE.on,
        macd3seg: typeof MACD3SEG !== 'undefined' && !!MACD3SEG.on,
        cover67: typeof COVER67_INDICATOR !== 'undefined' && !!COVER67_INDICATOR.on,
        v3: typeof MA20_V3 !== 'undefined' && !!MA20_V3.on,
        ai: typeof MA20_AI !== 'undefined' && !!MA20_AI.on,
      };
      if (TR._blindAlgoSnap.aiMa20 && typeof AI_MA20_STATE !== 'undefined'){
        AI_MA20_STATE.on = false;
        if (typeof aiMa20HideHoverCard === 'function') aiMa20HideHoverCard(true);
        if (typeof aiMa20AlgoRefresh === 'function') aiMa20AlgoRefresh();
        algoChanged = true;
      }
      if (TR._blindAlgoSnap.macd3seg && typeof MACD3SEG !== 'undefined'){
        MACD3SEG.on = false; MACD3SEG.open = false; MACD3SEG.pinned = false;
        if (MACD3SEG.popup) MACD3SEG.popup.hidden = true;
        if (typeof macd3segRefresh === 'function') macd3segRefresh();
        algoChanged = true;
      }
      if (TR._blindAlgoSnap.cover67 && typeof COVER67_INDICATOR !== 'undefined'){
        COVER67_INDICATOR.on = false; COVER67_INDICATOR.open = false;
        if (typeof cover67VolumeClose === 'function') cover67VolumeClose();
        if (typeof cover67IndicatorRefresh === 'function') cover67IndicatorRefresh();
        algoChanged = true;
      }
      if (TR._blindAlgoSnap.v3 && typeof MA20_V3 !== 'undefined'){
        MA20_V3.on = false; MA20_V3.open = false;
        if (typeof ma20StructureRefresh === 'function') ma20StructureRefresh();
        algoChanged = true;
      }
      if (TR._blindAlgoSnap.ai && typeof MA20_AI !== 'undefined'){
        MA20_AI.on = false; MA20_AI.open = false;
        if (typeof ma20AIRefresh === 'function') ma20AIRefresh();
        algoChanged = true;
      }
    }
  } else if (TR._blindAlgoSnap){
    const snap = TR._blindAlgoSnap; TR._blindAlgoSnap = null;
    if (snap.aiMa20 && typeof AI_MA20_STATE !== 'undefined'){
      AI_MA20_STATE.on = true;
      if (typeof aiMa20AlgoRefresh === 'function') aiMa20AlgoRefresh();
      algoChanged = true;
    }
    if (snap.macd3seg && typeof MACD3SEG !== 'undefined'){
      MACD3SEG.on = true;
      if (typeof macd3segRefresh === 'function') macd3segRefresh();
      algoChanged = true;
    }
    if (snap.cover67 && typeof COVER67_INDICATOR !== 'undefined'){
      COVER67_INDICATOR.on = true;
      if (typeof cover67IndicatorRefresh === 'function') cover67IndicatorRefresh();
      algoChanged = true;
    }
    if (snap.v3 && typeof MA20_V3 !== 'undefined'){
      MA20_V3.on = true;
      if (typeof ma20StructureRefresh === 'function') ma20StructureRefresh();
      algoChanged = true;
    }
    if (snap.ai && typeof MA20_AI !== 'undefined'){
      MA20_AI.on = true;
      if (typeof ma20AIRefresh === 'function') ma20AIRefresh();
      algoChanged = true;
    }
  }
  if (algoChanged && typeof candleSeries !== 'undefined' && typeof buildMarkers === 'function'){
    candleSeries.setMarkers(buildMarkers(activeView));
  }
  updateModeBadge();
}

// 练习态锁定三面板滚动/缩放 + 主图时间轴刻度打码; 解锁时恢复库默认
function trainerChartLock(on){
  // 练习中完全锁定: 禁平移+禁缩放(2026-09 用户要求: 只展示练习窗口内的K线, 缩放会露出窗口外历史)
  const o = on ? { handleScroll: false, handleScale: false } : { handleScroll: true, handleScale: true };
  chart.applyOptions(o); volumeChart.applyOptions(o); macdChart.applyOptions(o);
  chart.timeScale().applyOptions({
    tickMarkFormatter: on
      ? (ts => trLabel(typeof ts === 'object' && ts != null ? (ts.time ?? ts) : ts))
      : undefined,
  });
}

// 视口锁定: 右缘锚定当前K线, 可见宽度 = 回看200根 + 已走步数(+缓冲)
// RIGHT_PAD: 最新K线与右缘价格轴的留白(根) —— 给持仓线轴标/弹层留出空间, 不遮挡最新走势
const RIGHT_PAD = 10;
let _lvLocking = false;
function lockViewport(){
  if (!trLocked()) return;
  const n = candlesFor(activeView).length;
  if (!n) return;
  const maxW = TR.LOOKBACK + TR.step + RIGHT_PAD + 3;           // 练习窗口总宽 + 缓冲
  const w = Math.min(TR.LOOKBACK + 5, maxW);
  _lvLocking = true;
  chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - w), to: n + RIGHT_PAD });
  requestAnimationFrame(() => { _lvLocking = false; });
}

// 练习窗口左边界(逻辑下标): 引擎级=起点-LOOKBACK(全序列), 分片级=0(窗口首根)
function practiceWindowStart(){
  return CHUNK_TFS.includes(activeTF) ? 0 : Math.max(0, TR.startIdx - TR.LOOKBACK);
}
// 练习视口硬边界: 缩放越界(左: 回看窗口之前的历史; 右: 当前根之外的空白)立即弹回
// → 保证"只展示本次练习的K线数据": 起点-200根 ~ 当前已走根
// 注意: 数据缩短(练习开始时 421→201 根)后残留的旧视口可能整体落在界外,
//       此时直接对齐整个练习窗口; 部分越界则两条边分别夹取(保证 from ≤ to)。
function enforcePracticeBounds(r){
  if (!trLocked() || !r || _lvLocking || !TR.sym) return;   // TR.sym=未进入有效局时不修正
  const left = practiceWindowStart();
  const right = candlesFor('raw').length + RIGHT_PAD;
  // 强制吸附精确练习窗口: 任何视口变化(缩放/平移/误按键/数据长度突变)都立即弹回
  // (旧版"越界才修正"无法覆盖零宽视口等退化场景 → barSpacing 被打爆卡在放大态)
  if (Math.abs(r.from - left) > 0.5 || Math.abs(r.to - right) > 0.5){
    _lvLocking = true;
    chart.timeScale().setVisibleLogicalRange({ from: left, to: right });
    requestAnimationFrame(() => { _lvLocking = false; });
  }
}
chart.timeScale().subscribeVisibleLogicalRangeChange(enforcePracticeBounds);

async function enterTrainer(opts = {}){
  const rep = opts.repeat || null;   // 错题重练: 固定品种/级别/起点/步数
  const drill = rep ? '' : (document.getElementById('trDrillSel')?.value || '');
  const avoid = !rep && !!document.getElementById('trAvoidRecent')?.checked;
  const capInput = +document.getElementById('trCapitalInput')?.value || INIT_CAPITAL;
  const cap0 = rep ? (+rep.capital0 || INIT_CAPITAL) : Math.min(1e9, Math.max(1000, capInput));
  if (rep && rep.steps) TR.maxStep = Math.min(500, Math.max(20, +rep.steps || TR.maxStep));
  if (positions.length){ alert('当前有未平仓持仓, 请先平仓再开始练习'); return; }
  if (inReplay()) exitReplay();
  stopPlay();
  const btn = document.getElementById('trainerBtn');
  btn.disabled = true; btn.textContent = rep ? '重练定位中…' : '随机定位中…';
  try {
    await PracticeStore.ready;
    const syms = [...document.querySelectorAll('#symSelect option')].map(o => o.value);
    const need = TR.LOOKBACK + TR.maxStep + 5;
    // 市场×级别预过滤: 选了级别且非1D → 只有加密有分钟/小时级数据
    const symSel = rep ? rep.sym : (document.getElementById('trSymSel')?.value || '');
    const mktSel = rep ? '' : (document.getElementById('trMktSel')?.value || '');
    const tfSel = rep ? rep.tf : (document.getElementById('trTfSel')?.value || '');
    // 清除上一局残留的定位状态(否则边界修正会用旧 startIdx 算出非法视口 → 库断言崩溃)
    TR.startIdx = 0; TR.startTs = null; TR.endReason = '';
    // 品种指定优先(与市场互斥); 未指定品种时按市场过滤; 都不选=全市场随机
    let pool = symSel ? [symSel]
      : (mktSel ? (MARKET_GROUPS[mktSel] || []).filter(s => syms.includes(s)) : syms);
    if (tfSel && tfSel !== '1D') pool = pool.filter(s => CHUNK_SYMBOLS.includes(s));
    if (!pool.length){
      alert(symSel
        ? `${symSel} 没有可用的${tfSel ? ' ' + tfSel + ' ' : ' '}练习数据——分钟级(15F)分片当前仅 BTC 已生成。`
        : '该「市场 × 级别」组合暂无品种——分钟级(15F)目前仅加密可用');
      updateTrainerUI();
      return;
    }
    // 分钟级预检: 先探明哪些品种真的有分片数据, 剔除未生成的(避免随机抽中后白白重载再失败)
    if (tfSel && CHUNK_TFS.includes(tfSel)){
      const have = [];
      for (const s of pool){
        const m = await ChunkStore.manifest(s, tfSel);
        if (m && m.total >= need) have.push(s);
      }
      pool = have;
      if (!pool.length){
        alert(`${tfSel} 分钟级分片尚未生成（目前仅 BTC 已下载）。\n可先用「级别·随机 / 1D」练习，或等其他品种数据铺开。`);
        updateTrainerUI();
        return;
      }
    }
    for (let tries = 0; tries < 30; tries++){
      const sym = rep ? rep.sym : pool[randInt(pool.length)];
      await switchSymbol(sym, true);
      if (currentSym !== sym) continue;   // 加载失败(switchSymbol 内部已 alert), 重抽
      // 候选周期 = 引擎级别 + 可用的分钟级分片级别(15F)
      let tfs = (DATA.meta.timeframes && DATA.meta.timeframes.length) ? [...DATA.meta.timeframes] : ['1D'];
      for (const ctf of CHUNK_TFS){
        const man = await ChunkStore.manifest(sym, ctf);
        if (man && man.total >= need) tfs.push(ctf);
      }
      if (tfSel && !tfs.includes(tfSel)) continue;   // 该品种没有所选级别, 换一个
      const ok = tfs.filter(tf => {
        if (CHUNK_TFS.includes(tf)) return !!(ChunkStore.manifests[`${sym}/${TF_DIR[tf]}`]?.total >= need);
        return DATA.levels[tf] && DATA.levels[tf].candles.length >= need;
      });
      if (!ok.length) continue;
      const tf = tfSel || ok[randInt(ok.length)];
      TR.step = 0; TR.session = []; TR.revealed = false; TR.active = true;
      TR.research = null; TR.archiveId = null; prCloseDecision();
      TR.startWallTs = Date.now();
      TR.endCapital = cap0;
      TR.drill = drill;
      TR.capital0 = cap0;
      TR.marks = []; TR.desc = ''; TR.transition = ''; TR.durSec = 0;
      TR.curve = []; TR.addPnlRealized = 0; TR.hadAdds = false;
      acctResetTo(cap0);   // 每局独立初始本金(可在练习设置里配置, 默认10万U)
      if (CHUNK_TFS.includes(tf)){
        // 分钟级: 只装载练习窗口(前200+后205根); 重练=按原起点时间定位, 否则随机后按专项挑
        const man = ChunkStore.manifests[`${sym}/${TF_DIR[tf]}`];
        const tStart = rep ? rep.startTs
          : manifestIndexAt(man, TR.LOOKBACK + randInt(man.total - TR.LOOKBACK - TR.maxStep - 2));
        // 专项需要更多候选窗口: 多载一些未来K线供窗口打分(盲测仍只显示到当前步)
        const fwdBars = (!rep && drill) ? TR.maxStep * 3 + 65 : TR.maxStep + 65;
        const okL = await ChunkStore.loadWindow(sym, tf,
          tStart - TR.LOOKBACK * man.sec, tStart + fwdBars * man.sec);
        if (!okL) continue;
        // 质量门控: 缺口数据(交易所维护等)不适合练习 —— 窗口内大缺口/回看不足/右侧余量不足 → 重抽
        const w0 = ChunkStore.win;
        const t0 = w0.times;
        let di = lowerBoundTime(t0, tStart);
        let maxGap = 0;
        for (let i = 1; i < t0.length; i++){ const g = t0[i] - t0[i-1]; if (g > maxGap) maxGap = g; }
        const lookOk = di >= TR.LOOKBACK;                        // 回看足 200 根
        const fwdOk = t0.length - 1 - di >= TR.maxStep;          // 右侧余量足 maxStep 根
        const gapOk = maxGap <= 15 * man.sec;                    // 窗口内无 ≥15 根当量的缺口
        if (!lookOk || !fwdOk || !gapOk) continue;
        if (!rep && drill){   // 专项: 在已装载窗口内按形态挑起点
          di = pickStartByDrill(w0.candles, TR.LOOKBACK, t0.length - 1 - TR.maxStep,
            TR.maxStep, drill, avoid ? trRecentLoad() : null);
        }
        // 对齐到 ≥ tStart 的第一根真实K线(消除缺口导致的索引→时间漂移)
        const startTsReal = t0[di];
        const startIdxReal = w0.baseIdx + di;
        await setTF(tf);   // 视觉切换(检测到窗口已就绪不会重复加载)
        TR.sym = sym; TR.tf = activeTF;
        TR.startIdx = startIdxReal;
        TR.startTs = startTsReal;
        enterReplay(startTsReal);
      } else {
        setTF(tf);
        const arr = DATA.levels[activeTF].candles;
        const len = arr.length;
        let startIdx;
        if (rep){
          const idx = idxOfTsArr(arr, rep.startTs);
          if (idx == null || idx < TR.LOOKBACK || idx + TR.maxStep > len - 1) continue;
          startIdx = idx;
        } else {
          const cs = arr.map(c => ({ time:c[0], open:c[1], high:c[2], low:c[3], close:c[4] }));
          startIdx = pickStartByDrill(cs, TR.LOOKBACK, len - TR.maxStep - 2,
            TR.maxStep, drill, avoid ? trRecentLoad() : null);
        }
        TR.sym = sym; TR.tf = activeTF;
        TR.startIdx = startIdx;
        TR.startTs = arr[startIdx][0];
        enterReplay(arr[startIdx][0]);
      }
      applyBlind(true);
      trainerChartLock(true);
      document.getElementById('trainerBanner').style.display = 'none';
      trRecentPush(sym, TR.tf, TR.startTs);
      recordEquity();
      prBegin(rep, drill, avoid);
      startTrainerTimer();
      updateTrainerUI();
      lockViewport();
      if (typeof setSideTab === 'function') setSideTab('trade');
      return;
    }
    alert(rep
      ? '重练定位失败：该区间已不在当前数据范围内（数据更新后起点可能失效）。'
      : `随机定位失败(已尝试 ${tries} 次)。若选了分钟级, 请在面板把「市场」改为「加密」重试——分钟级分片目前仅 BTC 已生成。`);
    TR.active = false;   // 失败路径恢复按钮态(避免卡在"练习中")
    updateTrainerUI();
  } catch (e){
    TR.active=false; applyBlind(false); trainerChartLock(false);
    alert('练习器启动失败: ' + e.message + '\n' + (String(e.stack || '').split('\n')[1] || '').trim());
    console.error(e);
    updateTrainerUI();
  }
}

function trainerStep(){
  if (!trLocked()) return;
  if(prDraft){
    const hint=prPick?prEl('practicePickHint'):prEl('prDecisionError');
    hint.textContent='请先完成选点并保存判断，或关闭判断面板。';
    return;
  }
  closePosPopup();
  stopPlay();
  TR.step++;
  stepReplay(1);
  if(!trLocked())return;
  recordEquity();
  prCheckpoint();
  if (TR.step >= TR.maxStep){ revealTrainer(true); return; }
  // 分钟级: 距窗口右缘不足60根时静默续载下一分片
  if (chunkedActive() && ChunkStore.win && !ChunkStore._extending){
    const ts = allTimes(), i = ts.indexOf(replayT);
    if (ts.length - 1 - i < 60){
      ChunkStore._extending = true;
      ChunkStore.extend(1).finally(() => {
        ChunkStore._extending = false;
        refreshAll();
        lockViewport();
      });
    }
  }
  updateTrainerUI();
  lockViewport();
}

function revealTrainer(auto, note){
  if (!TR.active || TR.revealed) return;
  prCloseDecision();
  while (positions.length) closePosObj(positions[0], 1, auto ? '步数走满，按收盘结算' : '主动揭示，按收盘结算', '练习结束结算', true);
  prFinish();
  recordEquity();                  // 全部平仓后的终值点
  const endCapital = acctBal();   // 全部平仓后 = 本局最终净值(revealed 置位前抓取, 模式仍为 trainer)
  TR.addGain = +(TR.addPnlRealized || 0).toFixed(2);
  TR.ddActual = +maxDrawdown(TR.curve, 'eq').toFixed(2);
  TR.ddCf = +maxDrawdown(TR.curve, 'cf').toFixed(2);
  // 结束原因: 三条揭示路径各自标注, 存档与横幅可见, 避免争议
  TR.endReason = (note && String(note).includes('爆仓')) ? '账户爆仓'
    : (auto ? '步数走满' : '手动揭示');
  TR.revealed = true;
  finishSessionMeta();   // 用时 + 题目描述/趋势转换(replayT 清空前结算)
  stopPlay();
  // 展开完整数据(笔/段/中枢/买卖点随 before() 自动补全未来段), 但保持练习会话直到再来一局/退出
  replayT = null;
  document.getElementById('replayCtl').style.display = 'none';
  document.getElementById('replayInfo').textContent = '';
  document.getElementById('replayBtn').textContent = '进入复盘';
  document.getElementById('replayBtn').classList.remove('active');
  trainerChartLock(false);
  applyBlind(false);
  TR.endCapital = endCapital;
  archiveSession(TR.endReason === '账户爆仓' ? '爆仓' : '完成');
  renderJournal();
  refreshAll();
  chart.timeScale().fitContent();
  showAnswerBanner();
  updateTrainerUI();
  if (typeof setSideTab === 'function') setSideTab('practice');
}

function endTrainer(){
  prCloseDecision();
  PracticeStore.active(null);
  TR.active = false; TR.revealed = false; TR.session = []; TR.startTs = null;
  stopTrainerTimer();
  TR.marks = [];   // 清掉信号K/观察标记, 避免结束后残留到复盘图表
  trainerChartLock(false);
  applyBlind(false);
  if (inReplay()) exitReplay();
  document.getElementById('trainerBanner').style.display = 'none';
  refreshAll();
  updateTrainerUI();
}

function quitTrainer(){
  if (!TR.active) return;
  if (TR.revealed){ endTrainer(); return; }
  if (!confirm('放弃本次练习? 未平仓仓位退还保证金; 本局将标记为「放弃」存档')) return;
  for (const p of positions) acctAdj(posMargin(p));   // 弃局退还未平仓保证金(不计盈亏; 浮盈加仓部分本就不是余额划出)
  positions = [];
  TR.endCapital = acctBal();   // 已平仓单盈亏已结算进余额
  finishSessionMeta();
  archiveSession('放弃');
  endTrainer();
  const b = document.getElementById('trainerBanner');
  b.innerHTML = '<span>已放弃本次练习（未计入统计）</span>';
  b.style.display = 'flex';
  setTimeout(() => { if (!TR.active) b.style.display = 'none'; }, 2500);
}

function showAnswerBanner(extra){
  const el = document.getElementById('trainerBanner');
  const n = PracticeCore.positions(TR.session).filter(p=>p.closed).length;
  const tot = TR.session.reduce((a,r) => a + r.pnl_u, 0);
  // 净盈亏率 = 对本局初始本金的净盈亏(而不是逐笔保证金收益率的加总)
  const pct = tot / (TR.capital0 || INIT_CAPITAL) * 100;
  const sigN = (TR.marks || []).filter(m => m.kind === 'signal').length;
  const watN = (TR.marks || []).filter(m => m.kind === 'watch').length;
  el.innerHTML =
    `<b class="tb-title">本局答案：${DATA.meta.symbol} · ${TR.tf}</b>` +
    `<span class="tb-item">起点 <b>${fmtTimeReal(TR.startTs)}</b> · 走了 ${TR.step} 根</span>` +
    `<span class="tb-item">结束: <b>${TR.endReason || '—'}</b></span>` +
    (TR.desc ? `<span class="tb-item">题目: <b>${TR.desc}</b>${TR.transition ? ` · 趋势转换: <b>${TR.transition}</b>` : ' · 无趋势转换'}</span>` : '') +
    `<span class="tb-item">用时 <b>${fmtDur(TR.durSec)}</b></span>` +
    `<span class="tb-item">完整持仓 <b>${n}</b> · 净 <b class="tnum" style="color:${tot >= 0 ? 'var(--up)' : 'var(--down)'}">${tot >= 0 ? '+' : ''}${tot.toFixed(1)}U (${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%)</b></span>` +
    (TR.drill ? `<span class="ts-badge" title="本局专项">专项: ${TR.drill}</span>` : '') +
    ((sigN || watN) ? `<span class="tb-item">信号K <b>${sigN}</b> · 观察 <b>${watN}</b></span>` : '') +
    `<span class="tb-item" title="本局净值曲线(实线)">净值 ${equitySparkSvg(TR.curve, 168, 34, TR.capital0)}</span>` +
    (TR.hadAdds ? `<span class="tb-item" title="对照口径: 仅首仓、出场时点/比例与实际一致">加仓效果: 贡献 <b class="tnum" style="color:${(TR.addGain || 0) >= 0 ? 'var(--up)' : 'var(--down)'}">${(TR.addGain || 0) >= 0 ? '+' : ''}${(TR.addGain || 0).toFixed(1)}U</b> · 回撤 实际 ${(TR.ddActual || 0).toFixed(1)}% vs 不加仓 ${(TR.ddCf || 0).toFixed(1)}%</span>` : '') +
    (TR.research ? `<span><button class="btn btn-outline" id="trReviewBtn">本局复盘</button></span>` : '') +
    `<span><button class="btn btn-outline" id="trJumpBtn" title="跳回本局练习的K线区间">📍 定位起点</button></span>` +
    (extra ? `<span class="tb-item" style="color:var(--down); font-weight:700;">${extra}</span>` : '');
  el.style.display = 'flex';
  const review = document.getElementById('trReviewBtn');
  if (review) review.onclick = () => {
    if (typeof setSideTab === 'function') setSideTab('practice');
    const item = document.querySelector(`#trHistory [data-tsid="${TR.archiveId}"]`);
    const detail = item?.querySelector('.ts-detail');
    if (detail){detail.style.display='block';detail.querySelector('.pr-review')?.setAttribute('open','');item.scrollIntoView({block:'nearest'});}
  };
  const jb = document.getElementById('trJumpBtn');
  if (jb) jb.onclick = () => {
    if (currentSym !== TR.sym || activeTF !== TR.tf){
      alert(`当前为 ${DATA.meta.symbol}·${activeTF}, 请先切回本局的品种和周期再定位`);
      return;
    }
    const pos = locateIdx(TR.startTs);
    if (pos == null){ alert('定位失败: 起点不在当前数据中'); return; }
    chart.timeScale().setVisibleLogicalRange({
      from: Math.max(0, pos - TR.LOOKBACK),
      to: pos + TR.step + 15,
    });
  };
}

function updateTrainerUI(){
  document.getElementById('trainerCtl').style.display = TR.active ? 'flex' : 'none';
  const inRun = trLocked();
  document.getElementById('trainerProgress').textContent =
    `${TR.step}/${TR.maxStep} · 判断 ${TR.research?.decisions.length||0} · 平仓 ${TR.session.length} 次`;
  document.getElementById('trainerNext').disabled = !inRun;
  prEl('trainerDecision').disabled = !inRun;
  prEl('jOpen').title = inRun && TR.research?.mode==='research' ? '规则研究：按当前原始 K 线收盘入场、以本根极值止损' : '';
  if(!inRun)prCloseDecision();
  document.getElementById('trainerReveal').style.display = inRun ? '' : 'none';
  document.getElementById('trainerQuit').style.display = inRun ? '' : 'none';
  document.getElementById('trainerEnd').style.display = (TR.active && TR.revealed) ? '' : 'none';
  document.getElementById('trainerAgain').style.display = (TR.active && TR.revealed) ? '' : 'none';
  const btn = document.getElementById('trainerBtn');
  btn.disabled = TR.active;
  btn.textContent = !TR.active ? '开始练习' : (TR.revealed ? '练习完成' : '练习中…');
  btn.classList.toggle('active', TR.active);
  updateModeBadge();
  // 信号K/观察按钮: 高亮当前K线标记, 练习中可点
  const bar = curBar();
  const curMark = (TR.marks || []).find(m => bar && m.t === bar.time);
  const sigBtn = document.getElementById('trainerSignal');
  const watBtn = document.getElementById('trainerWatch');
  if (sigBtn && watBtn){
    sigBtn.classList.toggle('active', !!curMark && curMark.kind === 'signal');
    watBtn.classList.toggle('active', !!curMark && curMark.kind === 'watch');
    sigBtn.disabled = watBtn.disabled = !inRun;
  }
  renderLiveCurve();
}

// Display completed positions, not individual exit operations.
function currentPracticeStats(){
  const st=TR_EMPTY();
  st.exits=0;
  for(const s of tsLoad()){
    if(s.cond!=='完成'&&s.cond!=='爆仓')continue;
    st.sessions++;
    st.exits+=s.trades.length;
    for(const p of PracticeCore.positions(s.trades).filter(p=>p.closed)){
      st.orders++;
      if(p.pnl>0)st.wins++;
      if(p.pnl>=0)st.grossWin+=p.pnl;else st.grossLoss+=p.pnl;
      st.pnlSum+=p.pnl;
      st.pnlPctSum+=p.capital>0?p.pnl/p.capital*100:0;
      const ps=st.perSymbol[p.symbol]||(st.perSymbol[p.symbol]={orders:0,wins:0,pnl:0});
      ps.orders++;if(p.pnl>0)ps.wins++;ps.pnl+=p.pnl;
    }
  }
  return st;
}
function trSessionRow(cond){
  const cap0=TR.capital0||INIT_CAPITAL;
  const endCapital=cond==='进行中'?acctEquity():(TR.endCapital??acctBal());
  return { id:TR.archiveId??Date.now(), ts:TR.startWallTs||Date.now(),
    sym:TR.sym, tf:TR.tf, startTs:TR.startTs, steps:TR.step, maxSteps:TR.maxStep,
    capital:cap0, capital0:cap0, endCapital:+endCapital.toFixed(2), pnl:+(endCapital-cap0).toFixed(2),
    cond, endReason:TR.endReason, drill:TR.drill||'', desc:TR.desc||'', transition:TR.transition||'',
    durSec:TR.durSec||0, marks:(TR.marks||[]).slice(), curve:(TR.curve||[]).slice(),
    addGain:TR.addGain||0, ddActual:TR.ddActual||0, ddCf:TR.ddCf||0, hadAdds:!!TR.hadAdds,
    flag:false, trades:TR.session.slice(), research:TR.research||null,
    openPositions:cond==='进行中'?positions:[],
  };
}
function archiveSession(cond){
  if(!TR.sym)return;
  PracticeStore.put(trSessionRow(cond));
  renderTrainerSessions();
  renderTrainerStats();
}

// 工具栏"已练 N 局": 统计全部存档(含放弃), tooltip 展示完成/爆仓/放弃明细
function updatePracticeCount(){
  const arr = tsLoad();
  const n = c => arr.filter(s => s.cond === c).length;
  const el = document.getElementById('practiceCount');
  if (el){
    el.innerHTML = `已练 <b>${arr.length}</b> 局`;
    el.title = `已练习局数(含放弃): 完成 ${n('完成')} · 爆仓 ${n('爆仓')} · 放弃 ${n('放弃')} · 中断 ${n('中断')}`;
  }
  const badge = document.getElementById('sideTabPracBadge');
  if (badge){
    badge.textContent = arr.length ? String(arr.length) : '';
    badge.classList.toggle('on', arr.length > 0);
  }
}
let prHistoryLimit=40;
function renderTrainerSessions(){
  updatePracticeCount();
  const box = document.getElementById('trHistory');
  if (!box) return;
  if(prEl('practiceBatch')?.open&&!trLocked())prRenderBatch();
  const only = !!document.getElementById('trOnlyFlagged')?.checked;
  let arr = tsLoad();
  if (only) arr = arr.filter(s => s.flag);
  if (!arr.length){
    box.innerHTML = `<div class="ts-empty">${only ? '暂无错题。' : '暂无历史练习局。'}</div>`;
    return;
  }
  box.innerHTML = arr.slice(0,prHistoryLimit).map(s => {
    const condCls = s.cond === '爆仓' ? 'ts-cond-bad' : (s.cond === '放弃' || s.cond === '中断') ? 'ts-cond-muted' : 'ts-cond-ok';
    const condTag = `<span class="ts-cond ${condCls}">${prEscape(s.cond)}</span>`;
    const col = s.pnl >= 0 ? 'var(--up)' : 'var(--down)';
    const drillTag = s.drill ? `<span class="ts-badge">${prEscape(s.drill)}</span>` : '';
    const flagTag = s.flag ? '<span class="ts-flag" title="已标记错题"> ⭐</span>' : '';
    const addTag = s.hadAdds ? `<span class="ts-badge" title="加仓贡献 vs 不加仓对照(同出场时点)">加仓 ${(s.addGain || 0) >= 0 ? '+' : ''}${(s.addGain || 0).toFixed(0)}U</span>` : '';
    const ddTxt = (s.curve && s.curve.length > 1) ? ` · 最大回撤 ${(s.ddActual || 0).toFixed(1)}%` : '';
    const curveRow = (s.curve && s.curve.length > 1)
      ? `<div class="ts-curve-row">
           ${equitySparkSvg(s.curve, 180, 34, s.capital)}
           <span class="ts-curve-lbl">净值曲线${s.hadAdds ? '<br>灰虚线=不加仓对照' : ''}</span>
         </div>` : '';
    const addRow = s.hadAdds
      ? `<div class="ts-meta-line">加仓效果：贡献 <b class="tnum" style="color:${(s.addGain || 0) >= 0 ? 'var(--up)' : 'var(--down)'}">${(s.addGain || 0) >= 0 ? '+' : ''}${(s.addGain || 0).toFixed(2)}U</b>（实际 vs 仅首仓·同出场时点） · 最大回撤 实际 <b>${(s.ddActual || 0).toFixed(1)}%</b> vs 不加仓 <b>${(s.ddCf || 0).toFixed(1)}%</b></div>`
      : '';
    const hasR = s.trades.some(t => t.max_r != null);
    const table = s.trades.length
      ? '<table class="j-table"><thead><tr><th>时间</th><th>方向</th><th>入场→出场</th><th>比例</th><th class="num">盈亏U</th>' + (s.hadAdds ? '<th class="num">加仓</th>' : '') + (hasR ? '<th class="num">R/最大收盘</th>' : '') + '</tr></thead><tbody>' +
        s.trades.map(t => `<tr><td>${prEscape(t.exit_time)}</td><td>${prEscape(t.side)}${t.adds > 0 ? '+'+t.adds : ''}</td><td class="tnum">${t.entry}→${t.exit_price}</td><td class="tnum">${t.partial ? (t.ratio*100)+'%' : '100%'}</td><td class="num tnum" style="color:${t.pnl_u >= 0 ? 'var(--up)' : 'var(--down)'}">${t.pnl_u >= 0 ? '+' : ''}${(+t.pnl_u).toFixed(1)}</td>` +
          (s.hadAdds ? `<td class="num tnum" style="color:${(t.add_pnl || 0) >= 0 ? 'var(--up)' : 'var(--down)'}">${(t.add_pnl || 0) >= 0 ? '+' : ''}${(+t.add_pnl || 0).toFixed(1)}</td>` : '') +
          (hasR ? `<td class="num tnum" style="color:${(t.r_final ?? 0) >= 0 ? 'var(--up)' : 'var(--down)'}">${t.r_final != null ? (t.r_final >= 0 ? '+' : '') + t.r_final.toFixed(1) : '—'} / ${t.max_r != null ? (t.max_r >= 0 ? '+' : '') + t.max_r.toFixed(1) : '—'}</td>` : '') + '</tr>').join('') + '</tbody></table>'
      : '<div class="ts-muted">本局未开单。</div>';
    const descRow = `<div class="ts-meta-line">题目: <b>${prEscape(s.desc || '—')}</b>${s.transition ? ` · 趋势转换: <b>${prEscape(s.transition)}</b>` : (s.desc ? ' · 无趋势转换' : '')} · 用时 <b>${fmtDur(s.durSec)}</b></div>`;
    const im = markImpact(s);
    const avgTxt = (v, n) => n ? `平均 <b class="tnum" style="color:${v >= 0 ? 'var(--up)' : 'var(--down)'}">${v >= 0 ? '+' : ''}${v.toFixed(1)}U</b>(${n}笔)` : '—';
    const markRow = (im.sigCount || im.watchCount)
      ? `<div class="ts-meta-line">信号K <b>${im.sigCount}</b> 个（${im.sigFollow} 个 10 根内开仓 / ${im.sigMiss} 未开仓） · 观察 <b>${im.watchCount}</b> 个（${im.watchFollow} 开仓 / ${im.watchMiss} 未开仓）</div>
         <div class="ts-meta-line">信号后开仓 ${avgTxt(im.avgWith, im.nWith)} vs 无信号开仓 ${avgTxt(im.avgWithout, im.nWithout)}</div>
         <div class="ts-muted">${im.rows.map(r => `T+${r.m.step} ${r.m.kind === 'signal' ? '⚡' : '👁'} → ${r.tr ? `T+${prEscape(r.tr.entries?.[0]?.step ?? '?')} 开仓 ${prEscape(r.tr.side)} ${r.tr.pnl_u >= 0 ? '+' : ''}${(+r.tr.pnl_u).toFixed(1)}U` : '未开仓'}`).join(' · ')}</div>`
      : '';
    const dt = new Date(s.ts).toLocaleString('zh-CN', {hour12:false, month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'});
    const nPos = PracticeCore.positions(s.trades).filter(p=>p.closed).length;
    return `<div class="ts-item" data-tsid="${s.id}">
      <div class="ts-head" data-toggle="1">
        <div class="ts-head-top">
          <span class="ts-sym">${dt} · <b>${prEscape(s.sym || '?')}</b> ${prEscape(s.tf)}${drillTag}${flagTag}</span>
          <span>${addTag}${condTag}</span>
        </div>
        <div class="ts-head-sub">
          <span class="ts-muted">${nPos} 个完整持仓 / ${s.trades.length} 次平仓 · 回撤 ${(s.ddActual || 0).toFixed(1)}%</span>
          <b class="tnum ts-pnl" style="color:${col}">${s.pnl >= 0 ? '+' : ''}${s.pnl.toFixed(1)}U (${s.pnl >= 0 ? '+' : ''}${(s.pnl / Math.max(s.capital, 1) * 100).toFixed(1)}%)</b>
        </div>
      </div>
      <div class="ts-detail" style="display:none;">
        <div class="ts-muted">本金 ${s.capital.toLocaleString()}U → 终值 <b>${s.endCapital.toLocaleString()}U</b> · ${s.pnl >= 0 ? '+' : ''}${s.pnl.toFixed(1)}U (${s.pnl >= 0 ? '+' : ''}${(s.pnl/s.capital*100).toFixed(2)}%) · 步数 ${s.steps}${ddTxt}</div>
        ${descRow}
        ${curveRow}
        ${addRow}
        ${markRow}
        ${prSessionReview(s)}
        ${table}
        <div class="ts-actions">
          <button class="btn ts-mini" data-act="retry" title="用同一品种/级别/起点再盲练一局">🔄 同区间重练</button>
          <button class="btn ts-mini" data-act="flag">${s.flag ? '☆ 取消错题' : '⭐ 标记错题'}</button>
        </div>
      </div>
    </div>`;
  }).join('')+(arr.length>prHistoryLimit?'<button class="btn btn-outline ts-more" data-history-more>查看更多历史</button>':'');
}

function renderTrainerStats(){
  const g = document.getElementById('trStatsGrid');
  if (!g) return;
  const st = currentPracticeStats();
  document.getElementById('trStatMode').textContent = `共 ${st.sessions} 局`;
  const perSymEl = document.getElementById('trPerSym');
  if (!st.orders){
    g.innerHTML = '<div class="ts-empty ts-empty-span">暂无已平仓数据，开始第一次盲测吧</div>';
    perSymEl.innerHTML = '';
    return;
  }
  const avgU = st.pnlSum / st.orders;
  const avgPct = st.pnlPctSum / st.orders;
  const pf = st.grossLoss < 0 ? (st.grossWin / Math.abs(st.grossLoss)).toFixed(2) : '—';
  const cell = (k,v) => `<div class="kpi-cell"><span class="k">${k}</span><span class="v tnum">${v}</span></div>`;
  g.innerHTML =
    cell('完整持仓', `<strong>${st.orders}</strong> · ${st.exits}次`) +
    cell('胜率', `<strong>${(st.wins/st.orders*100).toFixed(1)}%</strong>`) +
    cell('平均盈亏', `<strong style="color:${avgU >= 0 ? 'var(--up)' : 'var(--down)'}">${avgU >= 0 ? '+' : ''}${avgU.toFixed(1)}U</strong>`) +
    cell('平均收益', `<strong>${avgPct >= 0 ? '+' : ''}${avgPct.toFixed(2)}%</strong>`) +
    cell('累计净利', `<strong style="color:${st.pnlSum >= 0 ? 'var(--up)' : 'var(--down)'}">${st.pnlSum >= 0 ? '+' : ''}${st.pnlSum.toFixed(0)}U</strong>`) +
    cell('盈亏比', `<strong>${st.grossLoss < 0 ? (st.grossWin/Math.abs(st.grossLoss)).toFixed(2) : '—'}</strong>`) +
    cell('盈利因子PF', `<strong>${pf}</strong>`) +
    cell('总盈/总亏', `<strong>${st.grossWin.toFixed(0)} / ${st.grossLoss.toFixed(0)}</strong>`);
  perSymEl.innerHTML = Object.entries(st.perSymbol)
    .sort((a,b) => b[1].orders - a[1].orders)
    .map(([s,v]) =>
      `<span class="sym-pill"><b>${prEscape(s)}</b> ${v.orders}笔 · 胜${v.orders ? (v.wins/v.orders*100).toFixed(0) : 0}% · <span class="tnum" style="color:${v.pnl >= 0 ? 'var(--up)' : 'var(--down)'}">${v.pnl >= 0 ? '+' : ''}${v.pnl.toFixed(0)}U</span></span>`
    ).join('');
}

function setupTrainer(){
  document.getElementById('trainerBtn').addEventListener('click', () => {
    if (TR.active) return;
    // 点击后先弹配置面板(品种/市场/级别/步数, 留空随机), 再开始
    document.getElementById('trainerConfig').style.display = 'flex';
  });
  // 品种下拉: 从主切换下拉克隆(单一数据源); 与市场互斥
  const symSel = document.getElementById('trSymSel');
  if (symSel){
    symSel.innerHTML = '<option value="">随机</option>' +
      [...document.querySelectorAll('#symSelect option')]
        .map(o => `<option value="${o.value}">${o.textContent}</option>`).join('');
    symSel.addEventListener('change', () => {
      const m = document.getElementById('trMktSel');
      if (m) m.disabled = !!symSel.value;
    });
  }
  // 历史练习明细: 点击行展开/收起; 行内按钮=标记错题 / 同区间重练(委托挂在静态父容器上)
  document.getElementById('trHistory')?.addEventListener('click', e => {
    if(e.target.closest('[data-history-more]')){prHistoryLimit+=40;renderTrainerSessions();return;}
    const act = e.target.closest('[data-act]');
    if (act){
      const item = act.closest('.ts-item');
      const arr = tsLoad();
      const s = arr.find(x => x.id === +item?.dataset.tsid);
      if (!s) return;
      if (act.dataset.act === 'flag'){
        PracticeStore.put({...s,flag:!s.flag}); renderTrainerSessions();
      } else if (act.dataset.act === 'retry'){
        if (TR.active){ alert('当前有进行中的练习, 请先结束再重练'); return; }
        if (positions.length){ alert('当前有未平仓持仓, 请先平仓再开始练习'); return; }
        const steps = s.maxSteps || s.steps;
        TR.maxStep = Math.min(500, Math.max(20, +steps || 200));
        enterTrainer({ repeat: { sym: s.sym, tf: s.tf, startTs: s.startTs, steps, capital0: s.capital0 || s.capital, id:s.id, mode:s.research?.mode, rule:s.research?.rule } });
      }
      return;
    }
    const head = e.target.closest('.ts-head');
    if (!head) return;
    const d = head.parentElement.querySelector('.ts-detail');
    if (d) d.style.display = d.style.display === 'none' ? '' : 'none';
  });
  document.getElementById('trOnlyFlagged')?.addEventListener('change', renderTrainerSessions);
  document.getElementById('trCfgStart').addEventListener('click', () => {
    const steps = Math.min(500, Math.max(20, +document.getElementById('trStepsInput').value || 200));
    TR.maxStep = steps;
    document.getElementById('trainerConfig').style.display = 'none';
    enterTrainer();
  });
  document.getElementById('trCfgCancel').addEventListener('click', () => {
    document.getElementById('trainerConfig').style.display = 'none';
  });
  document.getElementById('trainerNext').addEventListener('click', trainerStep);
  document.getElementById('trainerSignal').addEventListener('click', () => toggleTrainerMark('signal'));
  document.getElementById('trainerWatch').addEventListener('click', () => toggleTrainerMark('watch'));
  document.getElementById('trainerReveal').addEventListener('click', () => revealTrainer(false));
  document.getElementById('trainerQuit').addEventListener('click', quitTrainer);
  document.getElementById('trainerEnd').addEventListener('click', () => {
    document.getElementById('trainerBanner').style.display = 'none';
    endTrainer();
  });
  document.getElementById('trainerAgain').addEventListener('click', () => {
    document.getElementById('trainerBanner').style.display = 'none';
    enterTrainer();
  });
  document.getElementById('trStatsClear').addEventListener('click', () => {
    if (confirm('确认清零全部练习战绩与历史明细? 此操作不可恢复')){
      if(trLocked())return;
      localStorage.removeItem(TRKEY);
      PracticeStore.clear();
      renderTrainerStats();
      renderTrainerSessions();
    }
  });
  // 步进快捷键: → 或空格(输入框聚焦时不触发, 避免打断填写开单逻辑)
  window.addEventListener('keydown', e => {
    if (!trLocked()||prDraft) return;
    const tag = document.activeElement && document.activeElement.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (e.key === 'ArrowRight' || e.key === ' '){ e.preventDefault(); trainerStep(); }
  });
  window.addEventListener('beforeunload', e => {
    if (trLocked()){ e.preventDefault(); e.returnValue = ''; }   // 防误关丢失本局
  });
  setupPracticeResearch();
  renderTrainerStats();
  renderTrainerSessions();
  PracticeStore.ready.then(()=>{renderTrainerStats();renderTrainerSessions();});
}
