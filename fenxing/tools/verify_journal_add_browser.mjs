// 桌面端加仓便利功能回归: 练习模式理由留空默认「浮盈加仓」+ ⚡ 一键加满全部浮盈。
// 用法: 先启动静态服务(如 cd fenxing && python3 -m http.server 8877),
//       再启动 Chrome: --headless=new --remote-debugging-port=9336,
//       然后: node tools/verify_journal_add_browser.mjs
// 环境变量: DESKTOP_TEST_URL(默认 http://127.0.0.1:8877), CDP_PORT(默认 9336)。
// 注意: 脚本会清空该 origin 的账户/日志数据后重建持仓, 请用独立测试端口。
// 说明: 练习(盲测)态用直接置 TR.active/revealed 模拟, 免去练习器随机数据装载流程。
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

await call('Runtime.enable');
await call('Page.enable');
await call('Network.enable');
await call('Network.setCacheDisabled', { cacheDisabled: true });
await call('Page.navigate', { url: origin + '/index.html' });
await until(`location.href.startsWith(${JSON.stringify(origin)}) && window.__INIT_OK === true`);
await sleep(1000);
// 干净起点 + 拦截 alert 避免阻塞
await ev(`(() => {
  localStorage.removeItem('chan_journal_v1');
  localStorage.removeItem('chan_account_v2');
  positions.length = 0;
  window.alert = m => { window.__alert = String(m); };
  window.__alert = null;
})()`);

// 找一段确定上涨 ≥3% 的窗口(数据后半段), 在窗口起点入复盘/开多, 终点获得浮盈
const pair = await ev(`(() => {
  const cs = candlesFor(activeView);
  const start = Math.floor(cs.length * 0.4), end = cs.length - 2;
  for (let i0 = start; i0 < end - 5; i0++){
    for (let j = i0 + 2; j <= Math.min(i0 + 30, end); j++){
      if (cs[j].close >= cs[i0].close * 1.03) return { i0, j, t0: cs[i0].time, t1: cs[j].time };
    }
  }
  return null;
})()`);
assert.ok(pair, '找到 3% 上涨窗口');
await ev(`enterReplay(${pair.t0})`);
assert.equal(await ev('inReplay()'), true);
await ev(`(() => {
  const c = s => document.querySelector(s).click();
  c('#jLogicTags .logic-tag[data-group="ma120dir"][data-val="MA120向上"]');
  c('#jLogicTags .logic-tag[data-group="pricepos"][data-val="价格在MA120以上"]');
  c('#jLogicTags .logic-tag[data-cat="entry"][data-val="突破MA60"]');
  document.getElementById('jCapital').value = 1000;
  document.getElementById('jLeverage').value = 10;
  document.getElementById('jSide').value = '多';
  document.getElementById('jLogic').value = '测试开仓';
  document.getElementById('jOpen').click();
})()`);
assert.equal(await ev('positions.length'), 1, '开仓成功');
await ev(`setReplayT(${pair.t1})`);
await sleep(400);
const avail = await ev('addBudget(positions[0]).avail');
assert.ok(avail > 0, '存在可加浮盈');

// 复盘模式: 加仓理由留空仍必填(保持规则沉淀), 且不产生半成品加仓
await ev(`document.querySelector('.pos-card [data-act="add-toggle"]').click()`);
await ev(`(() => { document.querySelector('.pos-card .inp-add-logic').value = ''; document.querySelector('.pos-card [data-act="add-ok"]').click(); })()`);
assert.ok(String(await ev('window.__alert')).includes('必填'), '复盘模式理由留空应拦截');
assert.equal(await ev('positions[0].entries.length'), 1, '拦截后未加仓');

// 练习(盲测)模式: 标签提示默认值 + 一键加满按钮显示可加金额
await ev(`TR.active = true; TR.revealed = false; TR.LOOKBACK = 200; TR.step = 0; TR.startIdx = 100; TR.session = []; renderJournal();`);
assert.ok((await ev(`document.querySelector('.pos-card').textContent`)).includes('留空=浮盈加仓'), '练习模式标签提示默认值');
assert.ok((await ev(`document.querySelector('[data-full]').textContent`)).includes('U'), '一键加满显示可加金额');

// 一键加满: 理由留空 → 默认「浮盈加仓」; 本金 = 全部可加浮盈; 加满后余额归零
const availBefore = await ev('addBudget(positions[0]).avail');
await ev(`(() => { document.querySelector('.pos-card .inp-add-logic').value = ''; document.querySelector('.pos-card [data-act="add-full"]').click(); })()`);
assert.equal(await ev('positions[0].entries.length'), 2, '一键加满新增一笔');
const last = await ev('positions[0].entries.at(-1)');
assert.equal(last.logic, '浮盈加仓', '理由默认值');
assert.ok(Math.abs(last.capital - availBefore) < 1e-6, `本金应等于全部浮盈 ${availBefore} → ${last.capital}`);
assert.ok(Math.abs(await ev('addBudget(positions[0]).avail')) < 1e-6, '加满后还可加归零');

// 收尾: 清理本次测试数据
await ev(`(() => { localStorage.removeItem('chan_journal_v1'); localStorage.removeItem('chan_account_v2'); })()`);
assert.deepEqual(errors, []);
console.log('PASS journal add: 练习理由留空默认「浮盈加仓」+ 一键加满全部浮盈 (加满 ' + availBefore.toFixed(2) + 'U)');
ws.close();
process.exit(0);
