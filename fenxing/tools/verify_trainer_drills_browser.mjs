// 桌面端练习模式新功能回归:
//   1) 专项训练: 突破失败 / 震荡反复止损 / 趋势持有 / 加仓后回撤 的窗口分类与选题;
//   2) 净值曲线: 练习中记录实际/不加仓对照曲线, 揭示横幅与历史明细展示;
//   3) 加仓效果复盘: 实际 vs 仅首仓(同出场时点)的贡献与回撤对比;
//   4) 错题重练: 标记错题、只看错题、同区间重练。
// 用法: 先启动静态服务(cd fenxing && python3 -m http.server 8877),
//       再启动 Chrome: --headless=new --remote-debugging-port=9336,
//       然后: node tools/verify_trainer_drills_browser.mjs
// 环境变量: DESKTOP_TEST_URL(默认 http://127.0.0.1:8877), CDP_PORT(默认 9336)。
import assert from 'node:assert/strict';
const origin = process.env.DESKTOP_TEST_URL || 'http://127.0.0.1:8877';
const cdpPort = process.env.CDP_PORT || '9336';
const tabs = await (await fetch(`http://127.0.0.1:${cdpPort}/json/list`)).json();
const page = tabs.find(t => t.type === 'page');
if (!page) throw new Error('no CDP page: ' + JSON.stringify(tabs));
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise(r => { ws.onopen = r; ws.onerror = r; });
let id = 0;
const pending = new Map();
const errors = [];
ws.onmessage = e => {
  const m = JSON.parse(e.data);
  if (m.id){
    const p = pending.get(m.id);
    pending.delete(m.id);
    m.error ? p.reject(m.error) : p.resolve(m.result);
  } else if (m.method === 'Runtime.exceptionThrown'){
    errors.push((m.params.exceptionDetails.exception?.description || '').split('\n')[0].slice(0, 200));
  }
};
const call = (method, params = {}) => new Promise((resolve, reject) => {
  const n = ++id; pending.set(n, { resolve, reject }); ws.send(JSON.stringify({ id: n, method, params }));
});
const ev = async expression => {
  const r = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 400));
  return r.result.value;
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const until = async (expression, timeout = 30000) => {
  const start = Date.now();
  while (Date.now() - start < timeout){ if (await ev(expression)) return; await sleep(150); }
  throw new Error('Timed out: ' + expression);
};
const resetTrainer = () => ev(`(() => {
  positions.length = 0;
  TR.active = false; TR.revealed = false; TR.step = 0; TR.session = [];
  TR.curve = []; TR.addPnlRealized = 0; TR.hadAdds = false; TR.drill = '';
  applyBlind(false); trainerChartLock(false);
  document.getElementById('trainerBanner').style.display = 'none';
  if (inReplay()) exitReplay();
  updateTrainerUI();
})()`);

await call('Runtime.enable');
await call('Page.enable');
await call('Network.enable');
await call('Network.setCacheDisabled', { cacheDisabled: true });
await call('Emulation.setDeviceMetricsOverride', { width: 1680, height: 1000, deviceScaleFactor: 1, mobile: false });
await call('Page.navigate', { url: origin + '/index.html' });
await until(`location.href.startsWith(${JSON.stringify(origin)}) && window.__INIT_OK === true`);
await sleep(1000);
await ev(`(() => {
  localStorage.removeItem('chan_trainer_stats_v1');
  localStorage.removeItem('chan_trainer_sessions_v1');
  localStorage.removeItem('chan_trainer_recent_v1');
  localStorage.removeItem('chan_journal_v1');
  localStorage.removeItem('chan_account_v2');
  positions.length = 0;
  window.alert = m => { window.__alert = String(m); };
  window.__alert = null;
  window.confirm = () => true;
})()`);

// ---- 1) 配置项齐全 ----
assert.deepEqual(
  await ev(`[...document.querySelectorAll('#trDrillSel option')].map(o => o.value)`),
  ['', '突破失败', '震荡反复止损', '趋势持有', '加仓后回撤']);
assert.equal(await ev(`!!document.getElementById('trAvoidRecent')`), true);
assert.equal(await ev(`!!document.getElementById('trOnlyFlagged')`), true);
assert.equal(await ev(`document.getElementById('practiceCount').textContent`), '已练 0 局', '初始已练局数应为 0');

// ---- 2) 专项分类器: 构造分段行情, 验证各类窗口识别与选题 ----
const cls = await ev(`(() => {
  const cs = []; let t = 1600000000, px = 103;
  const push = c => { const o = px; px = c; cs.push({ time:t, open:o, high:Math.max(o,c)+0.8, low:Math.min(o,c)-0.8, close:c }); t += 86400; };
  // 0-59 基准区间 ~[102,107] (103↔106)
  for (let i=0;i<60;i++) push(103 + (i%20<10 ? 0 : 3));
  // 60-119 突破失败: 先冲高 115.5(越过基准高点), 再回落 104
  for (let i=0;i<60;i++) push(i<20 ? 103 + (12.5/19)*i : 115.5 - (11.5/39)*(i-20));
  // 120-179 震荡: 围绕 104.5 ±2.5, 高频穿越
  for (let i=0;i<60;i++) push(104.5 + (i%2 ? 2.5 : -2.5));
  // 180-239 单边上涨 104.5 → 164.5
  for (let i=0;i<60;i++) push(104.5 + i);
  // 240-299 上涨后回撤: 164.5 → 174.5, 峰值后回撤到 157.5
  for (let i=0;i<60;i++) push(i<30 ? 164.5 + (10/29)*i : 174.5 - (17/29)*(i-30));
  // 300-359 平坦
  for (let i=0;i<60;i++) push(157.5);
  const fin = x => Number.isFinite(x) ? x : null;
  return {
    fail: drillScore(cs, 60, 60, '突破失败'),
    failFlat: fin(drillScore(cs, 300, 60, '突破失败')),
    chop: drillScore(cs, 120, 60, '震荡反复止损'),
    chopTrend: drillScore(cs, 180, 60, '震荡反复止损'),
    trend: drillScore(cs, 180, 60, '趋势持有'),
    trendChop: drillScore(cs, 120, 60, '趋势持有'),
    pull: drillScore(cs, 240, 60, '加仓后回撤'),
    pullTrend: fin(drillScore(cs, 180, 60, '加仓后回撤')),
    pickFail: pickStartByDrill(cs, 60, 300, 60, '突破失败', null),
    pickChop: pickStartByDrill(cs, 60, 300, 60, '震荡反复止损', null),
    pickTrend: pickStartByDrill(cs, 60, 300, 60, '趋势持有', null),
    pickPull: pickStartByDrill(cs, 60, 300, 60, '加仓后回撤', null),
    pickFailScore: fin(drillScore(cs, pickStartByDrill(cs, 60, 300, 60, '突破失败', null), 60, '突破失败')),
    pickChopScore: fin(drillScore(cs, pickStartByDrill(cs, 60, 300, 60, '震荡反复止损', null), 60, '震荡反复止损')),
    pickTrendScore: fin(drillScore(cs, pickStartByDrill(cs, 60, 300, 60, '趋势持有', null), 60, '趋势持有')),
    pickPullScore: fin(drillScore(cs, pickStartByDrill(cs, 60, 300, 60, '加仓后回撤', null), 60, '加仓后回撤')),
  };
})()`);
assert.ok(cls.fail > 0, '突破失败窗口应识别: ' + cls.fail);
assert.equal(cls.failFlat, null, '平坦窗口不算突破失败');
assert.ok(cls.chop > cls.chopTrend, `震荡窗口分数应高于趋势窗口: ${cls.chop} vs ${cls.chopTrend}`);
assert.ok(cls.trend > cls.trendChop, `趋势窗口分数应高于震荡窗口: ${cls.trend} vs ${cls.trendChop}`);
assert.ok(cls.pull > -Infinity, '加仓后回撤窗口应识别: ' + cls.pull);
assert.equal(cls.pullTrend, null, '单边趋势无回撤不算加仓后回撤');
assert.ok(cls.pickFailScore > 0, '突破失败选题窗口应满足定义: ' + cls.pickFailScore);
assert.ok(cls.pickChopScore !== null, '震荡选题窗口应满足定义: ' + cls.pickChopScore);
assert.ok(cls.pickTrendScore !== null && cls.pickTrendScore > cls.trendChop, '趋势选题窗口应满足定义: ' + cls.pickTrendScore);
assert.ok(cls.pickPullScore !== null, '回撤选题窗口应满足定义: ' + cls.pickPullScore);
assert.ok(cls.pickFail >= 55 && cls.pickFail <= 300, '突破失败选题应在候选范围内: ' + cls.pickFail);

// ---- 3) 专项端到端: BTC/USD 1D, 趋势持有, 80 步 ----
await ev(`(() => {
  const opt = [...document.querySelectorAll('#trSymSel option')].find(o => o.value.includes('BTC'));
  document.getElementById('trSymSel').value = opt ? opt.value : '';
  document.getElementById('trMktSel').value = '';
  document.getElementById('trTfSel').value = '1D';
  document.getElementById('trDrillSel').value = '趋势持有';
  document.getElementById('trStepsInput').value = 80;
  document.getElementById('trainerBtn').click();
  document.getElementById('trCfgStart').click();
})()`);
await until(`TR.active === true && TR.sym != null && TR.maxStep === 80`, 40000);
const e2e = await ev(`(() => ({
  sym: TR.sym, tf: TR.tf, drill: TR.drill, idx: TR.startIdx,
  score: drillScore(DATA.levels[TR.tf].candles.map(c => ({ time:c[0], open:c[1], high:c[2], low:c[3], close:c[4] })), TR.startIdx, TR.maxStep, '趋势持有'),
  recent: JSON.parse(localStorage.getItem('chan_trainer_recent_v1') || '[]').length,
}))()`);
assert.equal(e2e.drill, '趋势持有');
assert.equal(e2e.tf, '1D');
assert.ok(e2e.score > -Infinity, '端到端选题应满足专项形态: ' + e2e.score);
assert.ok(e2e.recent >= 1, '应记录最近练过的区间');
// 练习模式重点色(橙): body class + 模式徽标 + 按钮高亮, 且不叠加复盘标识
assert.equal(await ev(`document.body.classList.contains('mode-trainer')`), true, '练习模式应加 mode-trainer');
assert.equal(await ev(`document.body.classList.contains('mode-replay')`), false, '练习中不应叠加复盘标识');
assert.ok((await ev(`document.getElementById('modeBadge').textContent`)).includes('练习'), '练习徽标文本: ' + await ev(`document.getElementById('modeBadge').textContent`));
assert.equal(await ev(`document.getElementById('trainerBtn').classList.contains('active')`), true, '练习按钮应高亮');
await resetTrainer();
assert.equal(await ev(`document.body.classList.contains('mode-trainer')`), false, '结束后应清除练习标识');
assert.equal(await ev(`document.getElementById('modeBadge').textContent`), '', '结束后徽标应清空');

// ---- 3b) 15F 分片模式专项可用 ----
await ev(`(() => {
  document.getElementById('trTfSel').value = '15F';
  document.getElementById('trDrillSel').value = '震荡反复止损';
  document.getElementById('trStepsInput').value = 60;
  document.getElementById('trainerBtn').click();
  document.getElementById('trCfgStart').click();
})()`);
await until(`TR.active === true && TR.tf === '15F'`, 90000);
const e2e15 = await ev(`(() => {
  const w = ChunkStore.win;
  if (!w) return null;
  const ts = TR.startTs;
  let lo = 0, hi = w.times.length - 1, ans = 0;
  while (lo <= hi){ const m = (lo + hi) >> 1; if (w.times[m] <= ts){ ans = m; lo = m + 1; } else hi = m - 1; }
  const s = drillScore(w.candles, ans, TR.maxStep, '震荡反复止损');
  return { drill: TR.drill, score: Number.isFinite(s) ? s : null };
})()`);
assert.equal(e2e15.drill, '震荡反复止损');
assert.ok(e2e15.score !== null, '15F 专项选题应满足形态: ' + e2e15.score);
await resetTrainer();

// ---- 4) 自定义本金 + 净值曲线 + 加仓效果 + 用时/题目描述 + 信号K标记 ----
await ev(`(() => {
  document.getElementById('trTfSel').value = '1D';
  document.getElementById('trDrillSel').value = '';
  document.getElementById('trStepsInput').value = 40;
  document.getElementById('trCapitalInput').value = 50000;
  document.getElementById('trainerBtn').click();
  document.getElementById('trCfgStart').click();
})()`);
await until(`TR.active === true && TR.step === 0 && TR.curve.length >= 1`, 40000);
assert.equal(await ev('TR.capital0'), 50000, '练习本金应使用配置值');
assert.equal(await ev('acctBal()'), 50000, '账户余额应重置为配置本金');
assert.ok((await ev(`document.getElementById('trainerElapsed').textContent`)).match(/^\d{2}:\d{2}$/), '练习中应显示用时');
// 开仓方向按"第5根相对起点"的已知走势选择(测试可窥视数据, 确保浮盈可加仓); 出场点选与加仓价不同的根
const plan = await ev(`(() => {
  const arr = DATA.levels[TR.tf].candles, b = TR.startIdx;
  const c0 = arr[b][4], c5 = arr[b + 5][4];
  let j = 10; while (j < 35 && arr[b + j][4] === c5) j++;
  return { dir: c5 >= c0 ? '多' : '空', steps: j, move: Math.abs(c5 - c0) };
})()`);
// 开仓 1000U×10, 带止损(放在本局窗口极值之外, 保证测试期间不触发; 按实际风险计算 R 基准)
const entryPx = await ev('curBar().close');
const risk = await ev(`(() => {
  const arr = DATA.levels[TR.tf].candles, b = TR.startIdx, n = ${plan.steps};
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i <= n; i++){ lo = Math.min(lo, arr[b+i][3]); hi = Math.max(hi, arr[b+i][2]); }
  return { lo, hi };
})()`);
const stopPx = plan.dir === '多' ? risk.lo * 0.995 : risk.hi * 1.005;
const expectedR0 = Math.abs(entryPx - stopPx) / entryPx * 10 * 1000;
await ev(`(() => {
  const c = s => document.querySelector(s).click();
  c('#jLogicTags .logic-tag[data-group="ma120dir"][data-val="MA120向上"]');
  c('#jLogicTags .logic-tag[data-group="pricepos"][data-val="价格在MA120以上"]');
  c('#jLogicTags .logic-tag[data-cat="entry"][data-val="突破MA60"]');
  document.getElementById('jCapital').value = 1000;
  document.getElementById('jLeverage').value = 10;
  document.getElementById('jSide').value = ${JSON.stringify(plan.dir)};
  document.getElementById('jEntry').value = ${entryPx};
  document.getElementById('jStop').value = ${stopPx};
  document.getElementById('jLogic').value = '曲线测试';
  document.getElementById('jOpen').click();
})()`);
assert.equal(await ev('positions.length'), 1, '练习中开仓成功');
assert.ok(Math.abs(await ev('positions[0].r0') - expectedR0) < 0.5, `R 基准应为止损亏损金额 ≈${expectedR0.toFixed(0)}U`);
assert.ok((await ev(`document.querySelector('[data-r]').textContent`)).includes('最大'), '持仓卡应显示 R 与最大 R');
assert.match(await ev(`document.querySelector('[data-plev]').textContent`), /x$/, '持仓卡应显示对净值杠杆');
assert.ok((await ev(`document.querySelector('[data-alev]').textContent`)).match(/x$/), '账户行应显示持仓杠杆');
assert.equal(await ev(`!!document.getElementById('jCloseAll')`), true, '有持仓时应在单子上方显示一键全平');
assert.equal(await ev(`document.getElementById('jCloseAll').disabled`), false, '有持仓时一键全平可用');
assert.ok((await ev(`document.getElementById('jCloseAll').getBoundingClientRect().height`)) >= 30, '一键全平按钮应为大按钮');
assert.equal(await ev(`document.getElementById('jCloseAll').getBoundingClientRect().bottom <= document.querySelector('.pos-card').getBoundingClientRect().top + 1`), true, '一键全平应位于持仓卡上方');
for (let i = 0; i < 5; i++){ await ev('trainerStep()'); await sleep(60); }
assert.ok(await ev('pnlOf(positions[0], curBar().close)') > 0, '第5根应为浮盈(方向已按走势选择)');
// 浮盈加仓 100U
await ev(`(() => {
  const card = document.querySelector('.pos-card');
  card.querySelector('[data-act="add-toggle"]').click();
  card.querySelector('.inp-add-cap').value = 100;
  card.querySelector('.inp-add-logic').value = '浮盈加仓';
  card.querySelector('[data-act="add-ok"]').click();
})()`);
assert.equal(await ev('positions[0].entries.length'), 2, '加仓成功');
assert.equal(await ev('TR.hadAdds'), true);
// 信号K标记: 当前K线 → 再点取消 → 再点恢复; 图表出现 ⚡ 标记
await ev(`document.getElementById('trainerSignal').click()`);
assert.equal(await ev(`TR.marks.length`), 1, '信号K标记成功');
assert.equal(await ev(`TR.marks[0].kind`), 'signal');
assert.equal(await ev(`TR.marks[0].step`), 5);
assert.ok(await ev(`buildMarkers('raw').some(m => m.text === '⚡')`), '信号K应显示在图上');
await ev(`document.getElementById('trainerSignal').click()`);
assert.equal(await ev(`TR.marks.length`), 0, '再点取消信号K');
await ev(`document.getElementById('trainerSignal').click()`);
assert.equal(await ev(`TR.marks.length`), 1, '恢复信号K');
// 第6根开第二笔(信号后 10 根内开仓), 第7根标记"观察"(未开仓)
await ev('trainerStep()');
await sleep(60);
await ev(`(() => {
  document.getElementById('jCapital').value = 1000;
  document.getElementById('jLogic').value = '信号后开仓';
  document.getElementById('jOpen').click();
})()`);
assert.equal(await ev('positions.length'), 2, '信号后开仓成功');
await ev('trainerStep()');
await sleep(60);
await ev(`document.getElementById('trainerWatch').click()`);
assert.equal(await ev(`TR.marks.length`), 2, '观察标记成功');
assert.equal(await ev(`TR.marks.find(m => m.kind === 'watch').step`), 7);
for (let i = 7; i < plan.steps; i++){ await ev('trainerStep()'); await sleep(60); }
// 一键全平 + 揭示
await ev(`document.getElementById('jCloseAll').click()`);
assert.equal(await ev('positions.length'), 0, '一键全平应平掉全部持仓');
await ev(`revealTrainer(false)`);
await sleep(400);
assert.equal(await ev(`!!document.getElementById('jCloseAll')`), false, '无持仓时不应显示一键全平');
assert.equal(await ev(`document.getElementById('modeBadge').textContent`), '练习 · 已揭示', '揭示后徽标应更新');
const res = await ev(`(() => {
  const s = tsLoad()[0];
  const rec = s.trades.find(t => t.adds > 0) || s.trades[0];
  const im = markImpact(s);
  return {
    curveLen: s.curve.length,
    hasCf: s.curve.every(p => typeof p.cf === 'number' && typeof p.eq === 'number'),
    cfDiff: s.curve.some(p => Math.abs(p.cf - p.eq) > 0.01),
    addGain: s.addGain, hadAdds: s.hadAdds,
    ddActual: s.ddActual, ddCf: s.ddCf,
    recAdd: rec.add_pnl, recNoAdd: rec.no_add_pnl, recPnl: rec.pnl_u,
    recR0: rec.r0, recRFinal: rec.r_final, recMaxR: rec.max_r,
    capital: s.capital, capital0: s.capital0,
    desc: s.desc, transition: s.transition, durSec: s.durSec,
    marks: s.marks, markSteps: s.marks.map(m => m.step).sort((a,b) => a-b),
    im,
    banner: document.getElementById('trainerBanner').textContent,
    bannerHtml: document.getElementById('trainerBanner').innerHTML,
    live: document.getElementById('trCurveLive').innerHTML.includes('<svg'),
    histHtml: document.getElementById('trHistory').innerHTML,
  };
})()`);
assert.ok(res.curveLen >= 10, '净值曲线应覆盖全部步数: ' + res.curveLen);
assert.ok(res.hasCf, '曲线每点都应有不加仓对照值');
assert.ok(res.cfDiff, '加仓后对照曲线应出现差异');
assert.equal(res.hadAdds, true);
assert.ok(Math.abs(res.recPnl - (res.recAdd + res.recNoAdd)) < 0.011,
  `加仓贡献 + 不加仓对照 = 实际盈亏: ${res.recAdd}+${res.recNoAdd} vs ${res.recPnl}`);
assert.ok(Math.abs(res.addGain - res.recAdd) < 0.011, '会话加仓贡献应等于逐笔加仓贡献之和');
assert.ok(Math.abs(res.recR0 - expectedR0) < 0.5, '存档 R 基准 ≈ 止损亏损金额: ' + res.recR0);
assert.ok(Math.abs(res.recRFinal - res.recPnl / expectedR0) < 0.05, `r_final = 盈亏/R基准: ${res.recRFinal} vs ${res.recPnl}/${expectedR0.toFixed(0)}`);
assert.ok(res.recMaxR >= res.recRFinal - 0.02, `最大 R 应 ≥ 最终 R: ${res.recMaxR} vs ${res.recRFinal}`);
assert.ok(res.ddActual >= 0 && res.ddCf >= 0, '回撤指标应存在');
assert.equal(res.capital, 50000, '存档本金应为配置值');
assert.equal(res.capital0, 50000);
assert.ok(res.desc && res.desc.length >= 2, '题目描述应生成: ' + res.desc);
assert.ok(res.durSec >= 1, '应记录答题用时: ' + res.durSec);
assert.equal(res.marks.length, 2, '信号K与观察标记应存档');
assert.deepEqual(res.markSteps, [5, 7]);
assert.equal(res.im.sigCount, 1); assert.equal(res.im.watchCount, 1);
assert.equal(res.im.sigFollow, 1, '信号后 10 根内开仓应被统计');
assert.equal(res.im.watchMiss, 1, '观察后未开仓应被统计');
assert.equal(res.im.nWith, 1, '有信号开仓 1 笔');
assert.equal(res.im.nWithout, 1, '无信号开仓 1 笔');
assert.ok(res.banner.includes('净值') && res.banner.includes('加仓效果'), '答案横幅应展示净值与加仓效果');
assert.ok(res.banner.includes('题目') && res.banner.includes('用时'), '答案横幅应展示题目描述与用时');
assert.ok(res.bannerHtml.includes('<svg'), '答案横幅应含净值 sparkline');
assert.ok(res.live, '练习中工具条应显示实时净值曲线');
assert.ok(res.histHtml.includes('同区间重练') && res.histHtml.includes('不加仓对照'), '历史明细应含重练与加仓对照');
assert.ok(res.histHtml.includes('回撤') && res.histHtml.includes('%'), '历史头部应含最终盈亏百分比与最大回撤');
assert.ok(res.histHtml.includes('题目:') && res.histHtml.includes('信号K'), '历史明细应含题目描述与信号K统计');
assert.ok(res.histHtml.includes('R/最大'), '历史明细应含 R/最大 列');

// ---- 5) 错题标记 / 只看错题 / 同区间重练 ----
await ev(`document.getElementById('trainerEnd').click()`);   // 结束本局(保留存档)
await sleep(200);
assert.ok((await ev(`document.getElementById('practiceCount').textContent`)).includes('1'), '存档后已练局数应更新: ' + await ev(`document.getElementById('practiceCount').textContent`));
assert.ok((await ev(`document.getElementById('practiceCount').title`)).includes('完成 1'), 'tooltip 应含完成明细: ' + await ev(`document.getElementById('practiceCount').title`));
await ev(`document.querySelector('#trHistory .ts-item [data-act="flag"]').click()`);
assert.equal(await ev(`tsLoad()[0].flag`), true, '标记错题应持久化');
await ev(`(() => {
  const c = document.getElementById('trOnlyFlagged');
  c.checked = true; c.dispatchEvent(new Event('change', { bubbles: true }));
})()`);
assert.ok((await ev(`document.getElementById('trHistory').textContent`)).includes('⭐'), '只看错题应过滤出该局');
const retryTs = await ev(`tsLoad()[0].startTs`);
await ev(`document.getElementById('trCapitalInput').value = 80000`);   // 重练应沿用原局本金, 不受当前配置影响
await ev(`document.querySelector('#trHistory .ts-item [data-act="retry"]').click()`);
await until(`TR.active === true && TR.startTs === ${retryTs}`, 40000);
assert.equal(await ev(`TR.maxStep`), 40, '重练步数应与原局一致');
assert.equal(await ev(`TR.capital0`), 50000, '重练应沿用原局本金');
assert.equal(await ev(`TR.drill`), '', '重练不套用专项(按原区间盲练)');
assert.equal(await ev(`document.getElementById('trOnlyFlagged').checked`), true, '重练不影响错题筛选');
await resetTrainer();

// 收尾: 清理测试数据
await ev(`(() => {
  localStorage.removeItem('chan_trainer_stats_v1');
  localStorage.removeItem('chan_trainer_sessions_v1');
  localStorage.removeItem('chan_trainer_recent_v1');
  localStorage.removeItem('chan_journal_v1');
  localStorage.removeItem('chan_account_v2');
})()`);
assert.deepEqual(errors, []);
console.log('PASS trainer: 专项分类/选题, 自定义本金, 用时/题目描述/趋势转换, 信号K标记与影响分析, 净值曲线(实际+不加仓对照), 加仓效果复盘, 持仓杠杆/一键全平/R倍数, 错题标记与同区间重练');
ws.close();
process.exit(0);
