// 桌面端两级入场标签回归:
//   一级: 市场环境(必填: MA120方向 + 价格位置, 同组单选) / 入场信号(必填: MA突破/MA回踩/动能/裸K, 任选其一) /
//         形态(选填); 点开一级标签 → 选二级标签。
//   记录格式: 「市场: .. | 入场: .. | 动能: .. | 裸K: .. | 形态: ..」+ 可选补充文字。
// 用法: 先启动静态服务(cd fenxing && python3 -m http.server 8877),
//       再启动 Chrome: --headless=new --remote-debugging-port=9336,
//       然后: node tools/verify_logic_tags_browser.mjs
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
const until = async (expression, timeout = 20000) => {
  const start = Date.now();
  while (Date.now() - start < timeout){ if (await ev(expression)) return; await sleep(150); }
  throw new Error('Timed out: ' + expression);
};
const click = sel => ev(`document.querySelector(${JSON.stringify(sel)}).click()`);

await call('Runtime.enable');
await call('Page.enable');
await call('Network.enable');
await call('Network.setCacheDisabled', { cacheDisabled: true });
await call('Page.navigate', { url: origin + '/index.html' });
await until(`location.href.startsWith(${JSON.stringify(origin)}) && window.__INIT_OK === true`);
await sleep(1000);
await ev(`(() => {
  localStorage.removeItem('chan_journal_v1');
  localStorage.removeItem('chan_account_v2');
  positions.length = 0;
  window.alert = m => { window.__alert = String(m); };
  window.__alert = null;
})()`);

// 一级分类齐全(动能/裸K 已并入入场信号)
assert.deepEqual(
  await ev(`[...document.querySelectorAll('#jLogicTags .lt-cat')].map(b => b.dataset.cat)`),
  ['market', 'entry', 'pattern']);

await ev(`enterReplay(candlesFor(activeView)[50].time)`);
assert.equal(await ev('inReplay()'), true);
// 复盘模式重点色(蓝): body class + 模式徽标
assert.equal(await ev(`document.body.classList.contains('mode-replay')`), true, '复盘模式应加 mode-replay');
assert.equal(await ev(`document.body.classList.contains('mode-trainer')`), false);
assert.equal(await ev(`document.getElementById('modeBadge').textContent`), '复盘中', '复盘徽标文本');
assert.equal(await ev(`document.getElementById('modeBadge').classList.contains('replay')`), true, '复盘徽标应带 replay 样式');

// 点开一级 → 二级选项齐全(入场信号内含 MA突破/MA回踩/动能/裸K 四组)
await click('#jLogicTags .lt-cat[data-cat="market"]');
assert.equal(await ev(`getComputedStyle(document.querySelector('#jLogicTags .lt-panel[data-panel="market"]')).display`), 'flex', '点一级标签应展开二级');
assert.deepEqual(
  await ev(`[...document.querySelectorAll('#jLogicTags .lt-panel[data-panel="market"] .logic-tag')].map(b => b.dataset.val)`),
  ['MA120向上', 'MA120向下', 'MA120走平', '价格在MA120以上', '价格在MA120以下']);
assert.deepEqual(
  await ev(`[...document.querySelectorAll('#jLogicTags .lt-panel[data-panel="entry"] .lt-glabel')].map(b => b.textContent)`),
  ['MA突破', 'MA回踩', '动能', '裸K']);
assert.ok(await ev(`!!document.querySelector('#jLogicTags .lt-panel[data-panel="entry"] .logic-tag[data-val="MACD背离"]')`), '动能应在入场信号内');
assert.ok(await ev(`!!document.querySelector('#jLogicTags .lt-panel[data-panel="entry"] .logic-tag[data-val="假突破"]')`), '裸K应在入场信号内');

// 必填拦截: 全空 → 缺 MA120方向
await ev(`document.getElementById('jOpen').click()`);
assert.ok(String(await ev('window.__alert')).includes('MA120方向'), '缺市场环境应拦截: ' + await ev('window.__alert'));
assert.equal(await ev('positions.length'), 0, '拦截后未开仓');

// 只选市场环境 → 仍缺入场信号
await ev(`(() => {
  window.__alert = null;
  document.querySelector('#jLogicTags .logic-tag[data-group="ma120dir"][data-val="MA120向上"]').click();
  document.querySelector('#jLogicTags .logic-tag[data-group="pricepos"][data-val="价格在MA120以上"]').click();
  document.getElementById('jOpen').click();
})()`);
assert.ok(String(await ev('window.__alert')).includes('入场信号'), '缺入场信号应拦截: ' + await ev('window.__alert'));

// 市场环境同组单选: 点向下 → 向上取消
await click('#jLogicTags .logic-tag[data-group="ma120dir"][data-val="MA120向下"]');
assert.equal(await ev(`document.querySelector('#jLogicTags .logic-tag[data-group="ma120dir"][data-val="MA120向上"]').classList.contains('active')`), false, '同组单选应取消前一个');
assert.equal(await ev(`document.querySelector('#jLogicTags .logic-tag[data-group="ma120dir"][data-val="MA120向下"]').classList.contains('active')`), true);

// 入场信号"任选其一即可": 只选动能 MACD背离 即满足必填
await ev(`(() => {
  const c = s => document.querySelector(s).click();
  c('#jLogicTags .lt-cat[data-cat="entry"]');
  c('#jLogicTags .logic-tag[data-cat="entry"][data-group="momentum"][data-val="MACD背离"]');
})()`);
assert.deepEqual(await ev(`ltMissing()`), [], '只选动能应满足入场信号必填');

// 补齐其余信号 + 形态 → 开仓成功, 记录结构化字符串
await ev(`(() => {
  window.__alert = null;
  const c = s => document.querySelector(s).click();
  c('#jLogicTags .logic-tag[data-cat="entry"][data-val="突破MA60"]');
  c('#jLogicTags .logic-tag[data-cat="entry"][data-val="回踩MA20"]');
  c('#jLogicTags .logic-tag[data-cat="entry"][data-group="candle"][data-val="假突破"]');
  c('#jLogicTags .lt-cat[data-cat="pattern"]');
  c('#jLogicTags .logic-tag[data-cat="pattern"][data-val="蓄势"]');
  document.getElementById('jCapital').value = 1000;
  document.getElementById('jLogic').value = '补充文本';
  document.getElementById('jOpen').click();
})()`);
assert.equal(await ev('window.__alert'), null, '必填齐后应可开仓');
assert.equal(await ev('positions.length'), 1, '标签开仓成功');
const logic = await ev('positions[0].entries[0].logic');
assert.ok(logic.includes('市场: MA120向下·价格在MA120以上'), '市场环境段: ' + logic);
assert.ok(logic.includes('入场: 突破MA60、回踩MA20'), '入场信号段: ' + logic);
assert.ok(logic.includes('动能: MACD背离') && logic.includes('裸K: 假突破') && logic.includes('形态: 蓄势'), '动能/裸K/形态段: ' + logic);
assert.ok(logic.endsWith('· 补充文本'), '补充文字应合并: ' + logic);

// 计数徽标(动能/裸K 计入入场信号)与面板收起
assert.equal(await ev(`document.querySelector('#jLogicTags [data-count="market"]').textContent`), '2');
assert.equal(await ev(`document.querySelector('#jLogicTags [data-count="entry"]').textContent`), '4');
await click('#jLogicTags .lt-cat[data-cat="pattern"]');   // 再点一级(当前已展开) → 收起
assert.equal(await ev(`getComputedStyle(document.querySelector('#jLogicTags .lt-panel[data-panel="pattern"]')).display`), 'none', '再点一级应收起');

// 止损预估: 输入止损价实时展示预计亏损金额
await ev(`(() => {
  document.getElementById('jCapital').value = 10000;
  document.getElementById('jLeverage').value = 10;
  document.getElementById('jSide').value = '多';
  document.getElementById('jEntry').value = 100;
  document.getElementById('jStop').value = 95;
  document.getElementById('jStop').dispatchEvent(new Event('input', { bubbles: true }));
})()`);
let sl = await ev(`document.getElementById('jStopLoss').textContent`);
assert.ok(sl.includes('-5000.00U') && sl.includes('50.0%'), '多单止损预估应为 -5000U/50%: ' + sl);
await ev(`(() => { document.getElementById('jStop').value = 105; document.getElementById('jStop').dispatchEvent(new Event('input', { bubbles: true })); })()`);
sl = await ev(`document.getElementById('jStopLoss').textContent`);
assert.ok(sl.includes('不会触发止损'), '多单止损在上方应提示方向错误: ' + sl);
await ev(`(() => { document.getElementById('jSide').value = '空'; document.getElementById('jSide').dispatchEvent(new Event('change', { bubbles: true })); })()`);
sl = await ev(`document.getElementById('jStopLoss').textContent`);
assert.ok(sl.includes('-5000.00U'), '空单上方止损应预估亏损: ' + sl);
await ev(`(() => { document.getElementById('jStop').value = ''; document.getElementById('jStop').dispatchEvent(new Event('input', { bubbles: true })); })()`);
sl = await ev(`document.getElementById('jStopLoss').textContent`);
assert.ok(sl.includes('输入止损价'), '未填止损应显示提示: ' + sl);

// 收尾: 清理本次测试数据
await ev(`(() => {
  localStorage.removeItem('chan_journal_v1');
  localStorage.removeItem('chan_account_v2');
  positions.length = 0;
})()`);
assert.deepEqual(errors, []);
console.log('PASS logic tags: 两级标签(市场环境必填 + 入场信号必填: MA突破/MA回踩/动能/裸K 任选其一, 形态选填), 一级展开二级, 结构化记录, 止损预估');
ws.close();
process.exit(0);
