// 桌面端乖离档位回归: 全品种持仓卡显示(MA20/MA60乖离 + 滚动分位) + 触发态 + 因果性(replayT 截断)。
// 用法: 先启动静态服务(cd fenxing && python3 -m http.server 8877),
//       再启动 Chrome: --headless=new --remote-debugging-port=9336,
//       然后: node tools/verify_bias_levels_browser.mjs
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

await call('Runtime.enable');
await call('Page.enable');
await call('Network.enable');
await call('Network.setCacheDisabled', { cacheDisabled: true });
await call('Page.navigate', { url: origin + '/index.html' });
await until(`location.href.startsWith(${JSON.stringify(origin)}) && window.__INIT_OK === true`);
await call('Page.bringToFront');
await call('Emulation.setFocusEmulationEnabled', { enabled: true });
await sleep(800);

await ev(`(() => {
  localStorage.removeItem('chan_journal_v1');
  localStorage.removeItem('chan_account_v2');
  positions.length = 0;
  window.alert = m => { window.__alert = String(m); };
})()`);

// 1) 扫描出四种状态的历史时点(与 biasSnapshot 同口径: 滚动MA20 + 窗口分位)
const cases = await ev(`(() => {
  const cs = candlesFor('raw');
  const n = cs.length;
  const lb = Math.min(n, BIAS_CFG.lookback[activeTF] || 365);
  const b20s = new Array(n).fill(null);
  let s = 0;
  for (let i = 0; i < n; i++){
    s += cs[i].close;
    if (i >= 20) s -= cs[i-20].close;
    if (i >= 19) b20s[i] = (cs[i].close/(s/20)-1)*100;
  }
  const out = {};
  for (let i = 60; i < n; i += 3){
    const b20 = b20s[i];
    let cnt = 0, le = 0;
    for (let j = Math.max(19, i-lb+1); j <= i; j++){ cnt++; if (b20s[j] <= b20) le++; }
    const p = cnt ? le/cnt : null;
    const hit2 = b20 >= BIAS_CFG.l2 || (p != null && p >= BIAS_CFG.p2);
    const hit1 = !hit2 && (b20 >= BIAS_CFG.l1 || (p != null && p >= BIAS_CFG.p1));
    const panic = b20 <= BIAS_CFG.rb || (p != null && p <= BIAS_CFG.pr);
    const key = hit2 ? 'two' : hit1 ? 'one' : (panic ? 'panic' : 'none');
    if (!out[key]) out[key] = { t: cs[i].time, b20: +b20.toFixed(2) };
    if (out.two && out.one && out.panic && out.none) break;
  }
  return out;
})()`);
assert.ok(cases.none && cases.one && cases.two && cases.panic,
  '扫描到 无档/一档/二档/恐慌 四种状态: ' + JSON.stringify(cases));
console.log('状态时点:', JSON.stringify(cases));

// 2) 复盘开多: 持仓卡出现乖离行(none 态)
await ev(`enterReplay(${cases.none.t})`);
assert.equal(await ev('inReplay()'), true, '进入复盘');
await ev(`(() => {
  const c = s => document.querySelector(s).click();
  c('#jLogicTags .logic-tag[data-group="ma120dir"][data-val="MA120向上"]');
  c('#jLogicTags .logic-tag[data-group="pricepos"][data-val="价格在MA120以上"]');
  c('#jLogicTags .logic-tag[data-cat="entry"][data-val="突破MA60"]');
  document.getElementById('jCapital').value = 1000;
  document.getElementById('jLeverage').value = 5;
  document.getElementById('jSide').value = '多';
  document.getElementById('jLogic').value = '乖离档位回归';
  document.getElementById('jOpen').click();
})()`);
assert.equal(await ev('positions.length'), 1, '开仓成功');
assert.equal(await ev(`!!document.querySelector('[data-bias]')`), true, '持仓卡含乖离行');
let txt = await ev(`document.querySelector('[data-bias]').textContent`);
assert.ok(txt.includes('乖离 MA20') && /P\d+/.test(txt), '乖离行含 MA20 乖离与分位: ' + txt);
assert.ok(txt.includes('距一档') && txt.includes('距回补区'), 'none 态显示距一档/距回补区: ' + txt);

// 3) 各触发态随 replayT 实时刷新
for (const [k, want] of [['one', '一档触发'], ['two', '二档触发'], ['panic', '恐慌回补区']]){
  await ev(`setReplayT(${cases[k].t})`);
  await sleep(120);
  txt = await ev(`document.querySelector('[data-bias]').textContent`);
  assert.ok(txt.includes(want), `${k} 态应显示「${want}」: ` + txt);
}

// 4) 因果性: biasSnapshot 与截断数据的独立复算一致(无未来信息)
await ev(`setReplayT(${cases.two.t})`);
const indep = await ev(`(() => {
  const cs = candlesFor('raw');
  const n = cs.length;
  let s20 = 0, s60 = 0;
  for (let i = n-60; i < n; i++){ s60 += cs[i].close; if (i >= n-20) s20 += cs[i].close; }
  const snap = biasSnapshot();
  return { b20: (cs[n-1].close/(s20/20)-1)*100, b60: (cs[n-1].close/(s60/60)-1)*100,
           s20: snap.b20, s60: snap.b60 };
})()`);
assert.ok(Math.abs(indep.b20 - indep.s20) < 1e-9 && Math.abs(indep.b60 - indep.s60) < 1e-9,
  `独立复算一致: ${JSON.stringify(indep)}`);

// 5) 周期切换: 异布局显示切回提示, 切回后恢复乖离行
await ev(`setTF('4H')`);
await sleep(400);
txt = await ev(`document.querySelector('[data-bias]').textContent`);
assert.ok(txt.includes('切回 BTC 1D'), '切到4H后显示切回提示: ' + txt);
await ev(`setTF('1D')`);
await sleep(400);
txt = await ev(`document.querySelector('[data-bias]').textContent`);
assert.ok(txt.includes('乖离 MA20'), '切回1D恢复乖离行: ' + txt);

// 6) 全品种: 切到 SPX, 新布局持仓卡同样显示乖离行
await ev(`switchSymbol('SPX', true)`);
await until(`currentSym === 'SPX' && !!biasSnapshot()`, 30000);
await ev(`(() => {
  positions.length = 0;
  positions.push({ id: 91, sym: currentSym, tf: activeTF, side: '多', stop: null,
    entries: [{ t: curBar().time, price: curBar().close, capital: 1000, leverage: 5, logic: '全品种回归' }] });
  renderJournal();
})()`);
txt = await ev(`document.querySelector('[data-bias]').textContent`);
assert.ok(txt.includes('乖离 MA20') && /P\d+/.test(txt), 'SPX 持仓卡显示乖离行: ' + txt);

// 收尾: 清理测试数据
await ev(`(() => {
  positions.length = 0;
  localStorage.removeItem('chan_journal_v1');
  localStorage.removeItem('chan_account_v2');
})()`);
assert.deepEqual(errors, []);
console.log('PASS bias levels: 全品种持仓卡乖离档位(触发态/分位/因果性/周期提示/跨品种)');
ws.close();
process.exit(0);
