// 练习「快速下单条」+ 开平仓修复 回归。
// 用法: 起静态服务(python3 -m http.server 8877 --directory apps/chanlun-web),
//       Chrome --headless=new --remote-debugging-port=9336, 再 node tools/verify_quick_order_browser.mjs
// 环境变量: DESKTOP_TEST_URL(默认 http://127.0.0.1:8877), CDP_PORT(默认 9336)
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
const origin = process.env.DESKTOP_TEST_URL || 'http://127.0.0.1:8877';
const cdpPort = process.env.CDP_PORT || '9336';
const shotDir = process.env.SHOT_DIR || '';
const tabs = await (await fetch(`http://127.0.0.1:${cdpPort}/json/list`)).json();
const page = tabs.find(t => t.type === 'page');
if (!page) throw new Error('no CDP page');
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise(r => { ws.onopen = r; });
let id = 0; const pending = new Map(); const errors = [];
ws.onmessage = e => {
  const m = JSON.parse(e.data);
  if (m.id){ const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(m.error) : p.resolve(m.result); }
  else if (m.method === 'Runtime.exceptionThrown') errors.push((m.params.exceptionDetails.exception?.description || '').split('\n')[0]);
};
const call = (method, params = {}) => new Promise((resolve, reject) => { const n = ++id; pending.set(n, { resolve, reject }); ws.send(JSON.stringify({ id: n, method, params })); });
const ev = async expression => {
  const r = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 400));
  return r.result.value;
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const until = async (expr, timeout = 30000) => { const s = Date.now(); while (Date.now() - s < timeout){ if (await ev(expr)) return; await sleep(150); } throw new Error('Timed out: ' + expr); };
const key = k => ev(`window.dispatchEvent(new KeyboardEvent('keydown', { key: ${JSON.stringify(k)}, bubbles: true }))`);
const saveShot = async name => {
  if (!shotDir) return;
  fs.mkdirSync(shotDir, { recursive: true });
  const { data } = await call('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(path.join(shotDir, name), Buffer.from(data, 'base64'));
  console.log('  📸 saved', name);
};

await call('Runtime.enable'); await call('Page.enable');
await call('Network.enable'); await call('Network.setCacheDisabled', { cacheDisabled: true });
await call('Emulation.setDeviceMetricsOverride', { width: 1500, height: 900, deviceScaleFactor: 2, mobile: false });
await ev(`try { window.onbeforeunload = null; if (typeof TR !== 'undefined') TR.active = false; } catch {}`);
await call('Page.navigate', { url: origin + '/index.html' });
await until(`window.__INIT_OK === true && typeof setupQuickOrder === 'function'`);
await sleep(800);
await ev(`(() => {
  ['chan_journal_v1','chan_account_v2','chanlun.quick.templates.v1','chanlun.journal.sizing.v1','chanlun.quick.min.v1'].forEach(k => localStorage.removeItem(k));
  positions.length = 0; QO.min = false;
  window.alert = m => { window.__alert = String(m); }; window.__alert = null;
  window.confirm = () => true;
  window.prompt = (q, d) => '突破回踩';
})()`);

// ===== A. 复盘模式下的开平仓修复 =====
const pair = await ev(`(() => {
  const cs = candlesFor(activeView);
  for (let i0 = Math.floor(cs.length * 0.4); i0 < cs.length - 40; i0++)
    for (let j = i0 + 2; j <= i0 + 30; j++) if (cs[j].close >= cs[i0].close * 1.05) return { t0: cs[i0].time, t1: cs[j].time, tPrev: cs[i0 - 3].time };
  return null;
})()`);
assert.ok(pair);
await ev(`enterReplay(${pair.t0})`);
// A1: 入场信号至少 1 个; 市场环境不选会自动补
await ev(`(() => {
  document.querySelectorAll('#jLogicTags .logic-tag.active').forEach(b => b.classList.remove('active'));
  document.getElementById('jCapital').value = 1000; document.getElementById('jLeverage').value = 5;
  document.getElementById('jSide').value = '多'; document.getElementById('jStop').value = '';
  document.getElementById('jOpen').click();
})()`);
assert.ok(String(await ev('window.__alert')).includes('入场信号'), '无信号应拦截: ' + await ev('window.__alert'));
assert.equal(await ev('positions.length'), 0);
await ev(`(() => { document.querySelector('#jLogicTags .logic-tag[data-cat="entry"][data-val="均线突破"]').click(); document.getElementById('jOpen').click(); })()`);
assert.equal(await ev('positions.length'), 1, '有信号即可开仓(市场环境自动判定)');
const p0 = await ev('({ tags: positions[0].tags, quick: positions[0].quick })');
assert.equal(p0.tags.market.length, 2, '市场环境已自动补齐: ' + JSON.stringify(p0.tags));
assert.equal(p0.quick.via, 'form');
assert.ok(Object.values(p0.quick.tagSource).every(v => v === 'auto'));
console.log('✓ A1 入场信号≥1, 市场环境自动补:', p0.tags.market.join(' / '));

// A2: 复盘回退到开仓前, 不检查止损、不允许平仓
await ev(`positions[0].stop = 1e-9; updatePositionStop(positions[0], candlesFor('raw').at(-1).high * 2, 't');`);   // 极端止损: 任何一根都会触发
await ev(`setReplayT(${pair.tPrev})`);
assert.equal(await ev('positions.length'), 1, '回退到开仓前不应触发止损');
await ev(`window.__alert = null; closePosObj(positions[0], 1, 'x')`);
assert.ok(String(await ev('window.__alert')).includes('早于'), '开仓前不允许平仓');
await ev(`updatePositionStop(positions[0], null, 'reset'); setReplayT(${pair.t0})`);
console.log('✓ A2 回退到开仓前: 不触发止损 / 禁止平仓');

// A3: 浮盈加仓不再凭空增加净值; 平仓后余额 = 初始 − 0 + 总盈亏
await ev(`setReplayT(${pair.t1})`); await sleep(200);
const before = await ev('({ eq: acctEquity(), bal: acctBal(), avail: addBudget(positions[0]).avail })');
assert.ok(before.avail > 0);
await ev(`(() => { document.querySelector('.pos-card [data-act="add-toggle"]').click(); const c = document.querySelector('.pos-card'); c.querySelector('.inp-add-logic').value = '测试加仓'; c.querySelector('[data-act="add-full"]').click(); })()`);
const after = await ev('({ eq: acctEquity(), n: positions[0].entries.length })');
assert.equal(after.n, 2);
assert.ok(Math.abs(after.eq - before.eq) < 1e-6, `加仓前后净值应不变: ${before.eq} → ${after.eq}`);
const pnlAll = await ev('pnlOf(positions[0], curBar().close)');
await ev(`closePosObj(positions[0], 1, '测试平仓')`);
const balEnd = await ev('acctBal()');
assert.ok(Math.abs(balEnd - (100000 + pnlAll)) < 1e-4, `平仓后余额 ${balEnd} 应为 100000 + 盈亏 ${pnlAll}`);
console.log('✓ A3 浮盈加仓: 净值不虚增, 平仓结算正确');

// A4: 开仓预期(短线/中线/长线) + 精简加仓/移止损/平仓预设(保本/止盈/保护利润) + 比例 chip
await ev(`(() => {
  if (typeof setSideTab === 'function') setSideTab('trade');
  document.getElementById('jExpect').value = '长线';
  document.querySelector('#jLogicTags .logic-tag[data-cat="entry"][data-val="均线突破"]').classList.add('active');
  document.getElementById('jOpen').click();
})()`);
assert.equal(await ev('positions[0].expect'), '长线', '开仓应保存预期: 长线');
assert.equal(await ev(`document.querySelector('.pos-card .pos-exp-tag').textContent.trim()`), '长线', '持仓卡应展示预期标签');
// 推进到浮盈处测试加仓抽屉、移止损抽屉、平仓预设抽屉
await ev(`setReplayT(${pair.t0}); positions[0].entries[0].price = curBar().close; setReplayT(${pair.t1}); renderJournal();`);
await sleep(220);
await ev(`(() => {
  const c = document.querySelector('.pos-card');
  c.querySelector('[data-act="add-toggle"]').click();
  c.querySelector('[data-act="add-pct"][data-val="50"]').click();
  c.querySelector('[data-act="add-chip"][data-val="突破"]').click();
})()`);
await sleep(120);
assert.ok(+(await ev(`document.querySelector('.pos-card .inp-add-cap').value`)) > 0, '50% 加仓按钮应自动填入金额');
assert.equal(await ev(`document.querySelector('.pos-card .inp-add-logic').value`), '突破', '加仓快捷标签应填入理由');
await saveShot('replay_02_pos_add_dark.png');

// 测试平仓抽屉：3 大常用预设（保本 / 止盈 / 保护利润）+ 比例分档 + 预计盈亏实时联动
const exitPresets = await ev(`[...document.querySelectorAll('.pos-card [data-act="exit-chip"]')].map(b => b.dataset.val)`);
assert.deepEqual(exitPresets, ['保本', '止盈', '保护利润'], '平仓预设应为 保本 / 止盈 / 保护利润');
await ev(`(() => {
  const c = document.querySelector('.pos-card');
  c.querySelector('[data-act="close-toggle"]').click();
  c.querySelector('[data-act="exit-chip"][data-val="止盈"]').click();
  c.querySelector('[data-act="exit-chip"][data-val="保护利润"]').click();
  c.querySelector('[data-act="ratio-chip"][data-val="50"]').click();
})()`);
await sleep(140);
assert.equal(await ev(`document.querySelector('.pos-card .inp-exit-logic').value`), '止盈、保护利润');
assert.equal(await ev(`document.querySelector('.pos-card .inp-exit-ratio').value`), '50');
assert.equal(await ev(`document.querySelector('.pos-card [data-exit-btn-r]').textContent`), '50%');
await saveShot('replay_03_pos_exit_dark.png');
// 浅色主题截图检查
await ev(`document.getElementById('themeBtn').click()`);
await sleep(200);
await saveShot('replay_04_pos_exit_light.png');
await ev(`document.getElementById('themeBtn').click()`);
await sleep(160);

await ev(`document.querySelector('.pos-card [data-act="close-ok"]').click()`);
assert.equal(await ev('jLoad().at(-1).exit_logic'), '止盈、保护利润');
assert.equal(await ev('jLoad().at(-1).expect'), '长线');
assert.equal(await ev('jLoad().at(-1).ratio'), 0.5);
// 点击持仓卡预期标签切换 + 保本预设 + 比例 0 拦截
await ev(`document.querySelector('.pos-card .pos-exp-tag').click()`);
assert.equal(await ev('positions[0].expect'), '短线', '点击预期标签应循环切换: 长线 → 短线');
await ev(`(() => { const c = document.querySelector('.pos-card'); c.querySelector('[data-act="close-toggle"]').click(); c.querySelector('[data-act="exit-chip"][data-val="保本"]').click(); const r = c.querySelector('.inp-exit-ratio'); r.value = '0'; window.__alert = null; c.querySelector('[data-act="close-ok"]').click(); })()`);
assert.ok(String(await ev('window.__alert')).includes('1%'), '比例 0 应拦截而非全平');
assert.equal(await ev('positions.length'), 1);
await ev(`closePosObj(positions[0], 1, '保本')`);
await ev('exitReplay()');
console.log('✓ A4 开仓预期(短/中/长线) + 精简加仓/平仓预设(保本/止盈/保护利润) + 比例 0 拦截');

// ===== B. 练习: 快速下单条 =====
await ev(`(() => {
  const opt = [...document.querySelectorAll('#trSymSel option')].find(o => o.value.includes('BTC'));
  document.getElementById('trSymSel').value = opt ? opt.value : '';
  document.getElementById('trMktSel').value = ''; document.getElementById('trTfSel').value = '1D';
  document.getElementById('trDrillSel').value = ''; document.getElementById('trStepsInput').value = 60;
  document.getElementById('prMode').value = 'classic';
  document.getElementById('trainerBtn').click(); document.getElementById('trCfgStart').click();
})()`);
await until(`TR.active === true && trLocked() && !!TR.research`, 40000);
for (let i = 0; i < 3; i++){ await ev('trainerStep()'); await sleep(80); }
for (let k = 0; k < 20; k++){
  const ok3 = await ev(`(() => { const s = QuickCore.stops(qoBars(), '多', curBar().close); return s.bar != null && s.swing != null && s.atr != null; })()`);
  if (ok3) break;
  await ev('trainerStep()'); await sleep(60);
}
await sleep(300);
await ev(`document.getElementById('jCapital').value = 2000; document.getElementById('jLeverage').value = 3;`);
await key('b');
assert.equal(await ev('QO.open'), true, 'B 打开快速下单条');
const st = await ev(`({ side: QO.side, stop: QO.stop, low: curBar().low, mk: QO.sel.market, autoE: [...QO.auto.entry], visible: !document.getElementById('quickOrder').hidden,
  margin: document.querySelector('.qo-margin').value, lev: document.querySelector('.qo-lev').value,
  stopPicked: QO.stopPicked, lines: QO.lines.map(l => ({ key: l.key, color: l.color, lineStyle: l.lineStyle, lineWidth: l.lineWidth, price: l.price })) })`);
assert.equal(st.side, '多'); assert.equal(st.visible, true);
assert.equal(st.stop, st.low, '默认止损 = 本根最低价');
assert.equal(st.stopPicked, false, '打开时未显式选中止损');
assert.equal(st.lines.length, 3, '应显示三条止损虚线');
assert.ok(st.lines.every(l => l.lineStyle === 2), '未选中时三条线都应为虚线(lineStyle=2)');
assert.equal(new Set(st.lines.map(l => l.color)).size, 3, '三条线颜色应互不相同');
assert.ok(st.mk.ma120dir && st.mk.pricepos, '市场环境自动判定');
assert.equal(st.margin, '2000'); assert.equal(st.lev, '3');
console.log('✓ B1 B 键打开: 三条彩色虚线, 默认本根止损, 环境自动判定, 沿用表单保证金/杠杆; 自动建议信号:', st.autoE.join(',') || '(无)');

// B2: 清空信号 → Enter 拦截
await ev(`QO.sel.entry.clear(); QO.sel.pattern.clear(); qoRender();`);
await key('Enter');
assert.equal(await ev('positions.length'), 0);
assert.ok((await ev(`document.querySelector('.qo-err').textContent`)).includes('至少'), '无信号不能开仓');
// B3: 切换止损方式 → 对应虚线变实线(其余保持虚线); 图上点击虚线吸附; 点空白新增自定义实线
await key('a');
assert.equal(await ev('QO.stopMethod'), 'atr');
assert.equal(await ev('QO.stopPicked'), true);
assert.ok(await ev('QO.stop < curBar().close'));
const atrLines = await ev(`QO.lines.map(l => ({ key: l.key, lineStyle: l.lineStyle, lineWidth: l.lineWidth }))`);
assert.equal(atrLines.length, 3);
assert.equal(atrLines.find(l => l.key === 'atr')?.lineStyle, 0, '选中的 ATR 线应变为实线(lineStyle=0)');
assert.equal(atrLines.find(l => l.key === 'atr')?.lineWidth, 2);
assert.ok(atrLines.filter(l => l.key !== 'atr').every(l => l.lineStyle === 2), '未选中的两条线保持虚线');
assert.equal(await ev(`document.querySelector('[data-qo-stop="atr"]').classList.contains('active')`), true, 'ATR 按钮应高亮');

// 确保三条候选线都存在且在像素上间隔开, 再测试鼠标直接点击图上 swing 虚线吸附与空白点选
for (let k = 0; k < 25; k++){
  const sepOk = await ev(`(() => {
    const ss = ['bar','swing','atr'].map(m => QO.stops[m]);
    if (ss.some(v => v == null)) return false;
    const ys = ss.map(v => candleSeries.priceToCoordinate(v));
    const h = document.getElementById('chart').getBoundingClientRect().height;
    return ys.every(y => y != null && y > 20 && y < h - 35) && Math.abs(ys[0]-ys[1]) > 14 && Math.abs(ys[1]-ys[2]) > 14 && Math.abs(ys[0]-ys[2]) > 14;
  })()`);
  if (sepOk) break;
  await ev('trainerStep()'); await sleep(60);
}
await key('h');   // 收起为单行, 确保主图下方空白区域不被表单遮挡
const swingTarget = await ev(`(() => {
  const b = document.getElementById('chart').getBoundingClientRect();
  const plotW = Math.min(chart.timeScale().width() - 40, b.width * 0.65);
  const py = candleSeries.priceToCoordinate(QO.stops.swing ?? QO.stops.bar);
  const targetKey = qoHitStopLine(py);
  return { x: b.left + plotW, y: b.top + py, targetKey };
})()`);
assert.ok(swingTarget.targetKey, '目标候选止损线可命中');
await call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: swingTarget.x, y: swingTarget.y });
await sleep(80);
assert.equal(await ev(`document.getElementById('chart').classList.contains('qo-line-hover')`), true, '悬停候选虚线应显示指针高亮');
await call('Input.dispatchMouseEvent', { type: 'mousePressed', x: swingTarget.x, y: swingTarget.y, button: 'left', clickCount: 1 });
await call('Input.dispatchMouseEvent', { type: 'mouseReleased', x: swingTarget.x, y: swingTarget.y, button: 'left', clickCount: 1 });
await sleep(120);
assert.equal(await ev('QO.stopMethod'), swingTarget.targetKey, '点击图上候选虚线应吸附选中 ' + swingTarget.targetKey);
assert.equal(await ev('QO.stopPicked'), true);
const swingLines = await ev(`QO.lines.map(l => ({ key: l.key, lineStyle: l.lineStyle }))`);
assert.equal(swingLines.find(l => l.key === swingTarget.targetKey)?.lineStyle, 0, '选中线应变为实线');
assert.ok(swingLines.filter(l => l.key !== swingTarget.targetKey).every(l => l.lineStyle === 2), '其余两条恢复虚线');

await sleep(550);
const blankPt = await ev(`(() => {
  const b = document.getElementById('chart').getBoundingClientRect();
  const plotW = Math.min(chart.timeScale().width() - 40, b.width * 0.65);
  const paneH = chart.paneSize().height - 12;
  const entryY = candleSeries.priceToCoordinate(curBar().close);
  for (let py = Math.round(entryY + 20); py < paneH; py += 6){
    const px = candleSeries.coordinateToPrice(py);
    if (Number.isFinite(px) && px > 0 && px < curBar().close
        && qoHitStopLine(py - 4) === null && qoHitStopLine(py) === null && qoHitStopLine(py + 4) === null){
      return { x: Math.round(b.left + plotW), y: Math.round(b.top + py) };
    }
  }
  return null;
})()`);
assert.ok(blankPt, '找到远离三条候选线的有效止损点位');
await call('Input.dispatchMouseEvent', { type: 'mouseMoved', ...blankPt });
await call('Input.dispatchMouseEvent', { type: 'mousePressed', ...blankPt, button: 'left', clickCount: 1 });
await call('Input.dispatchMouseEvent', { type: 'mouseReleased', ...blankPt, button: 'left', clickCount: 1 });
await sleep(120);
assert.equal(await ev('QO.stopMethod'), 'chart', '点空白有效价位应设为图上点选');
const customLines = await ev(`QO.lines.map(l => ({ key: l.key, lineStyle: l.lineStyle }))`);
assert.equal(customLines.length, 4, '自定义点选应保留 3 条候选虚线 + 新增 1 条自定义实线');
assert.equal(customLines.find(l => l.key === 'chart')?.lineStyle, 0);
assert.ok(customLines.filter(l => l.key !== 'chart').every(l => l.lineStyle === 2));
await key('h');   // 展开恢复
await key('l');
// B4: 校验 4 个新入场信号 + 选信号 + 存模板 + 应用模板 + Enter 开仓
const entryVals = await ev(`[...document.querySelectorAll('.qo-chip[data-qo-cat="entry"]')].map(b => b.dataset.qoVal)`);
assert.deepEqual(entryVals, ['均线突破', '大阳线后的顺势短线多', '大阴线后的顺势短线空', '震荡区间的反转'], '入场信号应精确为 4 项');
await ev(`QO.sel.entry.clear(); QO.sel.pattern.clear(); qoRender();`);
await ev(`document.querySelector('.qo-chip[data-qo-cat="entry"][data-qo-val="大阳线后的顺势短线多"]').click()`);
await ev(`document.querySelector('[data-qo-act="save-tpl"]').click()`);
assert.equal(await ev('qoTemplates().length'), 1);
await ev(`QO.sel.entry.clear(); qoRender();`);
await key('1');
assert.ok(await ev(`QO.sel.entry.has('大阳线后的顺势短线多') && QO.presetId === qoTemplates()[0].id`), '数字键套用模板');
await key('Enter');
assert.equal(await ev('positions.length'), 1, 'Enter 开仓');
const pos = await ev(`(() => { const p = positions[0], d = TR.research.decisions.at(-1); return { price: p.entries[0].price, close: curBar().close, cap: p.entries[0].capital, lev: p.entries[0].leverage, stop: p.stop, quick: p.quick, tags: p.tags, dq: d.quick, dtags: d.tags, choice: d.choice, open: QO.open }; })()`);
assert.equal(pos.price, pos.close); assert.equal(pos.cap, 2000); assert.equal(pos.lev, 3);
assert.equal(pos.quick.via, 'quick'); assert.equal(pos.quick.presetId != null, true);
assert.equal(pos.quick.tagSource['大阳线后的顺势短线多'] === 'manual' || pos.quick.tagSource['大阳线后的顺势短线多'] === 'auto-confirmed', true);
assert.ok(pos.quick.decisionMs >= 0 && pos.quick.keystrokes >= 2);
assert.equal(pos.choice, 'buy'); assert.ok(pos.dq && pos.dtags.entry.includes('大阳线后的顺势短线多'), '练习存档记录 quick + tags');
assert.equal(pos.open, false, '开仓后快速条关闭');
assert.equal(await ev('QO.lines.length'), 0, '下单关闭后三条止损线应全部清理');
assert.equal(await ev(`buildMarkers('raw').some(m => /^#\\d+/.test(String(m.text || '')))`), false, '开仓后主图不绘制 #编号 数字标记');
assert.equal(await ev(`Object.values(detailMap || {}).flat().some(d => /开仓|加仓|平仓/.test(d.title))`), false, '开仓后不生成遮挡 K 线的开平仓悬停浮窗');
console.log('✓ B4 模板 + Enter 开仓; quick 元数据:', JSON.stringify({ stopMethod: pos.quick.stopMethod, ms: pos.quick.decisionMs, keys: pos.quick.keystrokes, src: pos.quick.tagSource }));

// B5: 等待需要条件; 放弃一键记录
await ev('trainerStep()'); await sleep(300);
const nBefore = await ev('positions.length');   // 下一根可能已触发止损, 以此为基准
await key('s');
assert.equal(await ev('QO.side'), '空');
await key('w');
assert.equal(await ev(`document.activeElement.classList.contains('qo-note')`), true, 'W 且无条件时聚焦备注');
await ev(`document.querySelector('.qo-note').value = '等收盘跌破箱体下沿'`);
await ev(`document.querySelector('.qo-note').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))`);
const w = await ev('TR.research.decisions.at(-1)');
assert.equal(w.choice, 'wait'); assert.equal(w.condition, '等收盘跌破箱体下沿'); assert.equal(w.side, '空');
assert.equal(await ev('positions.length'), nBefore, '等待不开仓');
await ev(`document.activeElement.blur()`);
await key('b'); await key('x');
assert.equal(await ev('TR.research.decisions.at(-1).choice'), 'skip');
console.log('✓ B5 W 等待(需条件) / X 放弃');

// B6: 练习中自填更优开仓价无效(按收盘)
await ev(`(() => { document.getElementById('jEntry').value = 1; document.querySelector('#jLogicTags .logic-tag[data-cat="entry"][data-val="震荡区间的反转"]').classList.add('active'); document.getElementById('jStop').value=''; document.getElementById('jOpen').click(); })()`);
const p2 = await ev('positions.at(-1).entries[0].price === curBar().close');
assert.equal(p2, true, '练习中开仓价强制为收盘');
await ev(`closePosObj(positions.at(-1), 1, '测试平仓')`);
assert.ok(await ev('TR.session.length >= 1'), '平仓记录已写入本局');
assert.equal(await ev(`buildMarkers('raw').some(m => /^#\\d+/.test(String(m.text || '')))`), false, '平仓后主图不绘制 #编号/价格 数字标记');
assert.equal(await ev(`Object.values(detailMap || {}).flat().some(d => /开仓|加仓|平仓/.test(d.title))`), false, '平仓后不生成遮挡 K 线的开平仓悬停浮窗');
console.log('✓ B6 练习中自填开仓价被忽略, 按收盘成交; 开平仓后图上无数字标记遮挡');

// ===== C. 收起(H) / Esc 保留选择 / 复盘模式可用 / 旧表单隐藏 =====
assert.equal(await ev(`getComputedStyle(document.getElementById('journalForm')).display`), 'none', '旧版开仓表单默认隐藏');
await key('b');
await ev(`document.querySelector('.qo-chip[data-qo-cat="entry"][data-qo-val="震荡区间的反转"]').click()`);
await key('h');
assert.equal(await ev('QO.min'), true, 'H 收起');
const minBox = await ev(`(() => { const r = document.getElementById('quickOrder').getBoundingClientRect(); return { h: r.height, cls: document.getElementById('quickOrder').classList.contains('qo-min'), sig: document.querySelector('.qo-sig').textContent }; })()`);
assert.ok(minBox.cls && minBox.h < 70, '收起后只占一行: ' + minBox.h);
assert.ok(minBox.sig.includes('震荡区间的反转'), '收起条显示已选信号');
await key('Escape');
assert.equal(await ev('QO.open'), false);
assert.equal(await ev('QO.lines.length'), 0, 'Esc 关闭后三条止损线应清理');
await key('b');
assert.ok(await ev(`QO.sel.entry.has('震荡区间的反转') && QO.min === true`), 'Esc 后同一根K线再打开保留选择与收起状态');
await key('h');
assert.equal(await ev('QO.min'), false, '再按 H 展开');
await key('Escape');
await ev(`(() => { window.confirm = () => true; quitTrainer(); })()`);
await until('!TR.active');
const tR = await ev(`candlesFor('raw')[Math.floor(candlesFor('raw').length * 0.6)].time`);
await ev(`enterReplay(${tR})`);
await until(`!document.getElementById('quickOpenLong').disabled`, 3000);
await ev(`document.getElementById('quickOpenLong').click()`);
assert.equal(await ev('QO.open'), true, '复盘模式: 右侧「做多」按钮打开快速下单条');
assert.equal(await ev(`!!document.querySelector('.qo-entry') && !document.querySelector('[data-qo-act="wait"]')`), true, '复盘模式: 可自定开仓价, 无等待/放弃');
await ev(`document.querySelector('[data-qo-expect="短线"]').click()`);
assert.equal(await ev('QO.expect'), '短线', '快速下单条支持切换预期: 短线');
await sleep(140);
await saveShot('replay_01_quick_open.png');
const rp = await ev(`(() => { const b = curBar(); return { close: b.close, low: b.low }; })()`);
const limit = +(rp.close * 0.995).toPrecision(8);
await ev(`(() => { const i = document.querySelector('.qo-entry'); i.value = '${limit}'; i.dispatchEvent(new Event('input', { bubbles: true })); i.dispatchEvent(new Event('change', { bubbles: true }));
  const st = document.querySelector('.qo-stop'); st.value = '${+(limit * 0.97).toPrecision(8)}'; st.dispatchEvent(new Event('input', { bubbles: true })); st.dispatchEvent(new Event('change', { bubbles: true }));
  const sigChip = document.querySelector('.qo-chip[data-qo-cat="entry"][data-qo-val="均线突破"]'); if (sigChip && !sigChip.classList.contains('on')) sigChip.click(); })()`);
const nR = await ev('positions.length');
await key('Enter');
assert.equal(await ev('positions.length'), nR + 1, '复盘模式快速开仓');
assert.equal(await ev('positions.at(-1).expect'), '短线', '复盘快速开仓应保存所选预期: 短线');
assert.ok(Math.abs(await ev('positions.at(-1).entries[0].price') - limit) < 1e-6, '复盘模式按自定开仓价');
assert.equal(await ev('positions.at(-1).quick.stopMethod'), 'manual');
await ev(`closePosObj(positions.at(-1), 1, '清理')`);
await ev('exitReplay()');
console.log('✓ C H 收起/展开, Esc 保留选择, 复盘模式快速开仓(自定价), 旧表单默认隐藏');
assert.deepEqual(errors, [], '无页面异常: ' + errors.join(' | '));
console.log('PASS quick order + open/close fixes');
ws.close();
