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
const TSKEY = 'chan_trainer_sessions_v1';
const tsLoad = () => { try { return JSON.parse(localStorage.getItem(TSKEY)) || []; } catch { return []; } };
const tsSave = arr => localStorage.setItem(TSKEY, JSON.stringify(arr));
const TR_EMPTY = () => ({ sessions:0, orders:0, wins:0, grossWin:0, grossLoss:0, pnlSum:0, pnlPctSum:0, perSymbol:{} });
const trStatsLoad = () => { try { return { ...TR_EMPTY(), ...(JSON.parse(localStorage.getItem(TRKEY)) || {}) }; } catch { return TR_EMPTY(); } };
const trStatsSave = s => localStorage.setItem(TRKEY, JSON.stringify(s));
const randInt = n => Math.floor(Math.random() * n);

function applyBlind(on){
  document.body.classList.toggle('trainer-blind', on);
  document.querySelector('.legend-symbol').textContent = on ? '???' : DATA.meta.symbol;
  document.getElementById('symbolBadge').textContent =
    on ? `盲测中 · ${activeTF}` : `${DATA.meta.symbol} · ${activeTF}`;
  document.title = on ? '盘感练习 · 盲测中' : DATA.meta.symbol + ' · 缠论多级别联动';
  document.getElementById('replayCtl').style.display = 'none';   // 练习中禁用普通复盘控件(防跳步作弊)
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
let _lvLocking = false;
function lockViewport(){
  if (!trLocked()) return;
  const n = candlesFor(activeView).length;
  if (!n) return;
  const maxW = TR.LOOKBACK + TR.step + 3;                       // 练习窗口总宽 + 缓冲
  const w = Math.min(TR.LOOKBACK + 5, maxW);
  _lvLocking = true;
  chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - w), to: n + 2 });
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
  const right = candlesFor('raw').length + 2;
  // 强制吸附精确练习窗口: 任何视口变化(缩放/平移/误按键/数据长度突变)都立即弹回
  // (旧版"越界才修正"无法覆盖零宽视口等退化场景 → barSpacing 被打爆卡在放大态)
  if (Math.abs(r.from - left) > 0.5 || Math.abs(r.to - right) > 0.5){
    _lvLocking = true;
    chart.timeScale().setVisibleLogicalRange({ from: left, to: right });
    requestAnimationFrame(() => { _lvLocking = false; });
  }
}
chart.timeScale().subscribeVisibleLogicalRangeChange(enforcePracticeBounds);

async function enterTrainer(){
  if (positions.length){ alert('当前有未平仓持仓, 请先平仓再开始练习'); return; }
  if (inReplay()) exitReplay();
  stopPlay();
  const btn = document.getElementById('trainerBtn');
  btn.disabled = true; btn.textContent = '随机定位中…';
  try {
    const syms = [...document.querySelectorAll('#symSelect option')].map(o => o.value);
    const need = TR.LOOKBACK + TR.maxStep + 5;
    // 市场×级别预过滤: 选了级别且非1D → 只有加密有分钟/小时级数据
    const symSel = document.getElementById('trSymSel')?.value || '';
    const mktSel = document.getElementById('trMktSel')?.value || '';
    const tfSel = document.getElementById('trTfSel')?.value || '';
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
      const sym = pool[randInt(pool.length)];
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
      TR.startWallTs = Date.now();
      TR.endCapital = INIT_CAPITAL;
      acctReset();   // 每局练习独立初始本金(100,000U); 练习中途资金不再跨局累计
      if (CHUNK_TFS.includes(tf)){
        // 分钟级: 从 manifest 随机取绝对下标, 只装载练习窗口(前200+后205根)
        const man = ChunkStore.manifests[`${sym}/${TF_DIR[tf]}`];
        const startAbs = TR.LOOKBACK + randInt(man.total - TR.LOOKBACK - TR.maxStep - 2);
        const tStart = manifestIndexAt(man, startAbs);
        const okL = await ChunkStore.loadWindow(sym, tf,
          tStart - TR.LOOKBACK * man.sec, tStart + (TR.maxStep + 5) * man.sec);
        if (!okL) continue;
        // 质量门控: 缺口数据(交易所维护等)不适合练习 —— 窗口内大缺口/回看不足/右侧余量不足 → 重抽
        const w0 = ChunkStore.win;
        const t0 = w0.times;
        const di = lowerBoundTime(t0, tStart);
        let maxGap = 0;
        for (let i = 1; i < t0.length; i++){ const g = t0[i] - t0[i-1]; if (g > maxGap) maxGap = g; }
        const lookOk = di >= TR.LOOKBACK;                        // 回看足 200 根
        const fwdOk = t0.length - 1 - di >= TR.maxStep;          // 右侧余量足 maxStep 根
        const gapOk = maxGap <= 15 * man.sec;                    // 窗口内无 ≥15 根当量的缺口
        if (!lookOk || !fwdOk || !gapOk) continue;
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
        const len = DATA.levels[activeTF].candles.length;
        TR.sym = sym; TR.tf = activeTF;
        TR.startIdx = TR.LOOKBACK + randInt(len - TR.LOOKBACK - TR.maxStep - 2);
        TR.startTs = DATA.levels[activeTF].candles[TR.startIdx][0];
        enterReplay(DATA.levels[activeTF].candles[TR.startIdx][0]);
      }
      applyBlind(true);
      trainerChartLock(true);
      document.getElementById('trainerBanner').style.display = 'none';
      updateTrainerUI();
      lockViewport();
      return;
    }
    alert(`随机定位失败(已尝试 ${tries} 次)。若选了分钟级, 请在面板把「市场」改为「加密」重试——分钟级分片目前仅 BTC 已生成。`);
    TR.active = false;   // 失败路径恢复按钮态(避免卡在"练习中")
    updateTrainerUI();
  } catch (e){
    alert('练习器启动失败: ' + e.message + '\n' + (String(e.stack || '').split('\n')[1] || '').trim());
    console.error(e);
    updateTrainerUI();
  }
}

function trainerStep(){
  if (!trLocked()) return;
  closePosPopup();   // 步进时关闭图上弹层, 不遮挡最新K线
  stopPlay();
  stepReplay(1);
  TR.step++;
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
  while (positions.length) closePosObj(positions[0], 1, auto ? '步数走满, 揭示强平' : '主动揭示, 按现价强平', undefined, true);
  const endCapital = acctBal();   // 全部平仓后 = 本局最终净值(revealed 置位前抓取, 模式仍为 trainer)
  // 结束原因: 三条揭示路径各自标注, 存档与横幅可见, 避免争议
  TR.endReason = (note && String(note).includes('爆仓')) ? '账户爆仓'
    : (auto ? '步数走满' : '手动揭示');
  TR.revealed = true;
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
  recordSession();
  archiveSession(TR.endReason === '账户爆仓' ? '爆仓' : '完成');
  renderJournal();
  refreshAll();
  chart.timeScale().fitContent();
  showAnswerBanner();
  updateTrainerUI();
}

function endTrainer(){
  TR.active = false; TR.revealed = false; TR.session = []; TR.startTs = null;
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
  for (const p of positions) acctAdj(posCapital(p));   // 弃局退还未平仓保证金(不计盈亏)
  positions = [];
  TR.endCapital = acctBal();   // 已平仓单盈亏已结算进余额
  archiveSession('放弃');
  endTrainer();
  const b = document.getElementById('trainerBanner');
  b.innerHTML = '<span>已放弃本次练习（未计入统计）</span>';
  b.style.display = 'flex';
  setTimeout(() => { if (!TR.active) b.style.display = 'none'; }, 2500);
}

function showAnswerBanner(extra){
  const el = document.getElementById('trainerBanner');
  const n = TR.session.length;
  const tot = TR.session.reduce((a,r) => a + r.pnl_u, 0);
  const pct = TR.session.reduce((a,r) => a + (r.pnl_pct_capital || 0), 0);
  el.innerHTML =
    `<b>本局答案：${DATA.meta.symbol} · ${TR.tf}</b>` +
    `<span>起点 <b>${fmtTimeReal(TR.startTs)}</b> · 走了 ${TR.step} 根</span>` +
    `<span>结束: <b>${TR.endReason || '—'}</b></span>` +
    `<span>开单 <b>${n}</b> 笔 · 净 <b style="color:${tot >= 0 ? 'var(--up)' : 'var(--down)'}">${tot >= 0 ? '+' : ''}${tot.toFixed(1)}U (${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%)</b></span>` +
    `<span><button class="btn" id="trJumpBtn" title="跳回本局练习的K线区间">📍 定位起点</button></span>` +
    (extra ? `<span style="color:var(--down); font-weight:700;">${extra}</span>` : '');
  el.style.display = 'flex';
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
    `${TR.step}/${TR.maxStep} · 本局 ${TR.session.length} 笔`;
  document.getElementById('trainerNext').disabled = !inRun;
  document.getElementById('trainerReveal').style.display = inRun ? '' : 'none';
  document.getElementById('trainerQuit').style.display = inRun ? '' : 'none';
  document.getElementById('trainerEnd').style.display = (TR.active && TR.revealed) ? '' : 'none';
  document.getElementById('trainerAgain').style.display = (TR.active && TR.revealed) ? '' : 'none';
  const btn = document.getElementById('trainerBtn');
  btn.disabled = TR.active;
  btn.textContent = !TR.active ? '开始练习' : (TR.revealed ? '练习完成' : '练习中…');
}

// 本局战绩入账(只计已平仓单) + 渲染持久化统计卡
function recordSession(){
  const st = trStatsLoad();
  st.sessions++;
  for (const r of TR.session){
    st.orders++;
    if (r.pnl_u > 0) st.wins++;
    if (r.pnl_u >= 0) st.grossWin += r.pnl_u; else st.grossLoss += r.pnl_u;
    st.pnlSum += r.pnl_u;
    st.pnlPctSum += r.pnl_pct_capital || 0;
    const ps = st.perSymbol[r.symbol] || (st.perSymbol[r.symbol] = { orders:0, wins:0, pnl:0 });
    ps.orders++; if (r.pnl_u > 0) ps.wins++; ps.pnl += r.pnl_u;
  }
  trStatsSave(st);
  renderTrainerStats();
}

// 本局整机存档(独立本金+逐笔明细): 揭晓/揭示时调用; 放弃局另走 cond='放弃'
function archiveSession(cond){
  if (!TR.sym) return;
  const cap0 = INIT_CAPITAL;
  const endCap = typeof TR.endCapital === 'number' ? TR.endCapital : acctBal();
  const s = {
    id: Date.now(),
    ts: TR.startWallTs || Date.now(),
    sym: TR.sym, tf: TR.tf,
    startTs: TR.startTs,
    steps: TR.step,
    capital: cap0,
    endCapital: +endCap.toFixed(2),
    pnl: +(endCap - cap0).toFixed(2),
    cond,                     // '完成' | '爆仓' | '放弃'
    trades: TR.session.slice(),
  };
  let arr = tsLoad();
  arr.unshift(s);
  if (arr.length > 100) arr = arr.slice(0, 100);
  tsSave(arr);
  renderTrainerSessions();
}

function renderTrainerSessions(){
  const box = document.getElementById('trHistory');
  if (!box) return;
  const arr = tsLoad();
  if (!arr.length){
    box.innerHTML = '<div class="note" style="margin-top:6px;">暂无历史练习局。</div>';
    return;
  }
  box.innerHTML = arr.map(s => {
    const condTag = s.cond === '爆仓' ? '<b style="color:var(--down)">爆仓</b>'
      : s.cond === '放弃' ? '<span style="color:var(--muted)">放弃</span>'
      : '<span style="color:var(--up)">完成</span>';
    const col = s.pnl >= 0 ? 'var(--up)' : 'var(--down)';
    return `<div class="ts-item" data-tsid="${s.id}">
      <div class="ts-head" data-toggle="1" style="display:flex; justify-content:space-between; gap:8px; cursor:pointer; padding:2px 0;">
        <span style="opacity:.75">${new Date(s.ts).toLocaleString('zh-CN', {hour12:false, month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'})} · ${s.sym || '?'} ${s.tf || ''}</span>
        <span>${s.trades.length}笔 · <b style="color:${col}">${s.pnl >= 0 ? '+' : ''}${s.pnl.toFixed(1)}U</b> ${condTag}</span>
      </div>
      <div class="ts-detail" style="display:none; font-size:11px; padding:2px 0 6px 8px; line-height:1.8;">
        <div style="opacity:.7">本金 ${s.capital.toLocaleString()}U → 终值 <b>${s.endCapital.toLocaleString()}U</b> · ${s.pnl >= 0 ? '+' : ''}${s.pnl.toFixed(1)}U (${s.pnl >= 0 ? '+' : ''}${(s.pnl/s.capital*100).toFixed(2)}%) · 步数 ${s.steps}</div>
        ${s.trades.length ? '<table style="width:100%; border-collapse:collapse; margin-top:2px;"><tr style="opacity:.6"><td>时间</td><td>方向</td><td>入场→出场</td><td>比例</td><td style="text-align:right">盈亏U</td></tr>' +
          s.trades.map(t => `<tr><td>${t.exit_time || ''}</td><td>${t.side}${t.adds > 0 ? '+'+t.adds : ''}</td><td>${t.entry}→${t.exit_price}</td><td>${t.partial ? (t.ratio*100)+'%' : '100%'}</td><td style="text-align:right; color:${t.pnl_u >= 0 ? 'var(--up)' : 'var(--down)'}">${t.pnl_u >= 0 ? '+' : ''}${(+t.pnl_u).toFixed(1)}</td></tr>`).join('') + '</table>'
          : '<div style="opacity:.6">本局未开单。</div>'}
      </div>
    </div>`;
  }).join('');
}

function renderTrainerStats(){
  const g = document.getElementById('trStatsGrid');
  if (!g) return;
  const st = trStatsLoad();
  document.getElementById('trStatMode').textContent = `共 ${st.sessions} 局`;
  const perSymEl = document.getElementById('trPerSym');
  if (!st.orders){
    g.innerHTML = '<span class="k">暂无数据</span><span class="v">开始第一次盲测吧</span>';
    perSymEl.innerHTML = '';
    return;
  }
  const avgU = st.pnlSum / st.orders;
  const avgPct = st.pnlPctSum / st.orders;
  const pf = st.grossLoss < 0 ? (st.grossWin / Math.abs(st.grossLoss)).toFixed(2) : '—';
  const cell = (k,v) => `<span class="k">${k}</span><span class="v">${v}</span>`;
  g.innerHTML =
    cell('开单', `<strong>${st.orders}</strong> 笔`) +
    cell('胜率', `<strong>${(st.wins/st.orders*100).toFixed(1)}%</strong>`) +
    cell('平均盈亏', `<strong style="color:${avgU >= 0 ? 'var(--up)' : 'var(--down)'}">${avgU >= 0 ? '+' : ''}${avgU.toFixed(1)}U</strong>`) +
    cell('平均收益', `${avgPct >= 0 ? '+' : ''}${avgPct.toFixed(2)}%`) +
    cell('累计净利', `${st.pnlSum >= 0 ? '+' : ''}${st.pnlSum.toFixed(0)}U`) +
    cell('盈亏比', st.grossLoss < 0 ? (st.grossWin/Math.abs(st.grossLoss)).toFixed(2) : '—') +
    cell('盈利因子PF', pf) +
    cell('总盈/总亏', `${st.grossWin.toFixed(0)} / ${st.grossLoss.toFixed(0)}`);
  perSymEl.innerHTML = Object.entries(st.perSymbol)
    .sort((a,b) => b[1].orders - a[1].orders)
    .map(([s,v]) =>
      `<span style="display:inline-block; margin-right:10px;">${s}: ${v.orders}笔 · 胜${v.orders ? (v.wins/v.orders*100).toFixed(0) : 0}% · ${v.pnl >= 0 ? '+' : ''}${v.pnl.toFixed(0)}U</span>`
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
  // 历史练习明细: 点击行展开/收起(委托挂在静态父容器上)
  document.getElementById('trHistory')?.addEventListener('click', e => {
    const head = e.target.closest('.ts-head');
    if (!head) return;
    const d = head.parentElement.querySelector('.ts-detail');
    if (d) d.style.display = d.style.display === 'none' ? '' : 'none';
  });
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
      trStatsSave(TR_EMPTY());
      tsSave([]);
      renderTrainerStats();
      renderTrainerSessions();
    }
  });
  // 步进快捷键: → 或空格(输入框聚焦时不触发, 避免打断填写开单逻辑)
  window.addEventListener('keydown', e => {
    if (!trLocked()) return;
    const tag = document.activeElement && document.activeElement.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (e.key === 'ArrowRight' || e.key === ' '){ e.preventDefault(); trainerStep(); }
  });
  window.addEventListener('beforeunload', e => {
    if (trLocked()){ e.preventDefault(); e.returnValue = ''; }   // 防误关丢失本局
  });
  renderTrainerStats();
  renderTrainerSessions();
}
