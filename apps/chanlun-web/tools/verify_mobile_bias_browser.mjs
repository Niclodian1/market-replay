// 手机版乖离档位回归: 持仓卡显示(MA20/MA60乖离 + 滚动分位) + 触发态 + 因果性(练习窗口截断)。
// 用法: 先启动静态服务(cd ~/kpractice && python3 -m http.server 8876),
//       再启动 Chrome: --headless=new --remote-debugging-port=9336,
//       然后: node tools/verify_mobile_bias_browser.mjs
// 环境变量: MOBILE_TEST_URL(默认 http://127.0.0.1:8876), CDP_PORT(默认 9336)。
import assert from 'node:assert/strict';
const origin = process.env.MOBILE_TEST_URL || 'http://127.0.0.1:8876';
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
  while (Date.now() - start < timeout){ if (await ev(expression)) return; await sleep(100); }
  throw new Error('Timed out: ' + expression);
};

await call('Runtime.enable');
await call('Page.enable');
await call('Network.enable');
await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
await call('Page.navigate', { url: 'about:blank' });
await sleep(200);
await call('Network.setCacheDisabled', { cacheDisabled: true });
await call('Storage.clearDataForOrigin', { origin, storageTypes: 'all' });
await call('Page.navigate', { url: origin + '/' });
await until("typeof offlineReady !== 'undefined' && offlineReady && typeof TR !== 'undefined'");
await call('Page.bringToFront');
await call('Emulation.setFocusEmulationEnabled', { enabled: true });

// 启动一局 BTC 1D 练习并开仓(先拦截 alert: 必填标签缺失时弹窗会阻塞无头渲染)
await ev("document.getElementById('trSymSel').value='BTC';document.getElementById('trTfSel').value='1D';document.getElementById('trStepsInput').value='20';document.getElementById('trCapitalInput').value='50000';document.getElementById('trainerBtn').click()");
await until('TR.active && document.getElementById("loading").hidden && !!chart');
await ev(`window.alert = m => { window.__alert = String(m); };`);
await ev(`(() => {
  const c = s => document.querySelector(s).click();
  c('#jLogicPicks .chip[data-cat="market"][data-group="ma120dir"][data-val="MA120向上"]');
  c('#jLogicPicks .chip[data-cat="market"][data-group="pricepos"][data-val="价格在MA120以上"]');
  c('#jLogicPicks .chip[data-cat="entry"][data-val="突破MA60"]');
  document.getElementById('jCapital').value = '10000';
  document.getElementById('jLeverage').value = '10';
  document.getElementById('jOpen').click();
})()`);
assert.equal(await ev('positions.length'), 1, '开仓成功: ' + (await ev('window.__alert || ""')));
assert.equal(await ev(`!!document.querySelector('[data-bias]')`), true, '持仓卡含乖离行');
let txt = await ev(`document.querySelector('[data-bias]').textContent`);
assert.ok(txt.includes('乖离 MA20') && /P\d+/.test(txt), '乖离行含 MA20 乖离与分位: ' + txt);

// 扫描四种状态的历史时点(盲测态: startIdx + replayT 截断到该根)
const cases = await ev(`(() => {
  const cs = L().candles;
  const out = {};
  const savedIdx = TR.startIdx, savedStep = TR.step, savedRT = replayT;
  for (let i = 60; i < cs.length; i += 7){
    TR.startIdx = i; TR.step = 0; replayT = cs[i][0];
    const b = biasSnapshot();
    const hit2 = b.b20 >= BIAS_CFG.l2 || (b.p20 != null && b.p20 >= BIAS_CFG.p2);
    const hit1 = !hit2 && (b.b20 >= BIAS_CFG.l1 || (b.p20 != null && b.p20 >= BIAS_CFG.p1));
    const panic = b.b20 <= BIAS_CFG.rb || (b.p20 != null && b.p20 <= BIAS_CFG.pr);
    const key = hit2 ? 'two' : hit1 ? 'one' : (panic ? 'panic' : 'none');
    if (!out[key]) out[key] = { idx: i, b20: +b.b20.toFixed(2) };
    if (out.two && out.one && out.panic && out.none) break;
  }
  TR.startIdx = savedIdx; TR.step = savedStep; replayT = savedRT;
  return out;
})()`);
assert.ok(cases.none && cases.one && cases.two && cases.panic,
  '扫描到 无档/一档/二档/恐慌 四种状态: ' + JSON.stringify(cases));
console.log('状态时点:', JSON.stringify(cases));

// 各触发态: 定位窗口后重渲染持仓卡
for (const [k, want] of [['two', '二档触发'], ['one', '一档触发'], ['panic', '恐慌回补区'], ['none', '距一档']]){
  await ev(`TR.startIdx = ${cases[k].idx}; TR.step = 0; replayT = L().candles[${cases[k].idx}][0]; renderJournal();`);
  txt = await ev(`document.querySelector('[data-bias]').textContent`);
  assert.ok(txt.includes(want), `${k} 态应显示「${want}」: ` + txt);
}

// 因果性: biasSnapshot 与截断数据的独立复算一致
await ev(`TR.startIdx = ${cases.two.idx}; TR.step = 0; replayT = L().candles[${cases.two.idx}][0];`);
const indep = await ev(`(() => {
  const lv = L(); const n = displayRange().hi;
  let s20 = 0, s60 = 0;
  for (let i = n-60; i < n; i++){ s60 += lv.candles[i][4]; if (i >= n-20) s20 += lv.candles[i][4]; }
  const snap = biasSnapshot();
  return { b20: (lv.candles[n-1][4]/(s20/20)-1)*100, b60: (lv.candles[n-1][4]/(s60/60)-1)*100,
           s20: snap.b20, s60: snap.b60 };
})()`);
assert.ok(Math.abs(indep.b20 - indep.s20) < 1e-9 && Math.abs(indep.b60 - indep.s60) < 1e-9,
  `独立复算一致: ${JSON.stringify(indep)}`);

assert.deepEqual(errors, []);
console.log('PASS mobile bias levels: 持仓卡乖离档位(触发态/分位/因果性)');
ws.close();
process.exit(0);
