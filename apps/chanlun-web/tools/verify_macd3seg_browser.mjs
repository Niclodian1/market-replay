// 三段背离图层浏览器验证（独立 profile；不清理任何本地存储）
// 用法: M3S_TEST_URL=http://127.0.0.1:8879 CDP_PORT=9338 node tools/verify_macd3seg_browser.mjs [screenshot.png]
import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
const origin = process.env.M3S_TEST_URL || 'http://127.0.0.1:8879';
const port = process.env.CDP_PORT || '9338';
const shot = process.argv[2] || '';
const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const page = pages.find(p => p.type === 'page');
assert.ok(page, 'test tab');
const ws = new WebSocket(page.webSocketDebuggerUrl); await new Promise((ok, bad) => { ws.onopen = ok; ws.onerror = bad; });
let id = 0; const pending = new Map(), errors = [];
ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(m.error) : p.resolve(m.result); } else if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text); };
const call = (method, params = {}) => new Promise((resolve, reject) => { const n = ++id; pending.set(n, { resolve, reject }); ws.send(JSON.stringify({ id: n, method, params })); });
const ev = async expression => { const r = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value; };
const until = async expr => { for (let n = 0; n < 150; n++) { if (await ev(expr)) return; await new Promise(r => setTimeout(r, 200)); } throw Error('timeout ' + expr); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
try {
  await call('Runtime.enable'); await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await call('Page.navigate', { url: origin + '/index.html?macd3seg=1' });
  await until('window.__INIT_OK===true && typeof macd3segRefresh==="function"');
  assert.equal(await ev('currentSym + "|" + activeTF'), 'BTC|1D');
  assert.equal(await ev('MACD3SEG.on'), true);
  const entries = await ev('macd3segEntries().map(e=>e.date+":"+e.kind)');
  const gt = ['2018-02-03:buy', '2018-02-06:buy', '2018-06-25:buy', '2019-12-18:buy', '2023-06-06:buy', '2023-06-15:buy', '2024-05-03:buy', '2025-04-09:buy',
    '2020-02-10:sell', '2020-02-13:sell', '2020-06-02:sell', '2021-04-16:sell', '2024-01-11:sell', '2024-10-31:sell', '2025-05-23:sell'];
  for (const g of gt) assert.ok(entries.includes(g), '前端缺少 ' + g);
  assert.ok(await ev('macd3segMarkers(activeView).some(m=>m.text==="次日损")'), '次日止损标记');
  assert.equal(await ev('document.getElementById("macd3segToggle").textContent'), `三段背离 ${entries.length}`);
  // 列表 + 选中 2023-06-15 二次入场
  await ev('document.getElementById("macd3segList").click()');
  await ev(`document.querySelector('[data-m3s-ptab="entries"]').click()`);
  assert.equal(await ev('document.querySelectorAll("[data-m3s-id]").length'), entries.length);
  const tid = await ev('macd3segEntries().find(e=>e.date==="2023-06-15").trade_id');
  await ev(`document.querySelector('[data-m3s-id="${tid}"]').click()`);
  assert.equal(await ev('MACD3SEG.sel'), tid);
  assert.ok((await ev('MACD3SEG.popup.textContent')).includes('二次入场'));
  // 复盘截断: 2023-06-10 之前不应出现 06-15 信号
  await ev('replayT = Date.UTC(2023,5,10)/1000; macd3segRefresh();');
  assert.ok(await ev('macd3segEntries().every(e=>e.time<=replayT)'), '复盘截断泄漏');
  assert.ok(await ev('!macd3segEntries().some(e=>e.date==="2023-06-15")'));
  await ev('replayT = null; macd3segRefresh();');
  // 4H
  await ev('setTF("4H")'); await until('activeTF==="4H"');
  assert.ok(await ev('macd3segEntries().length') > 20, '4H 信号');
  await ev('setTF("1D")'); await until('activeTF==="1D"');
  await ev(`macd3segSelect(${tid})`); await sleep(600);
  if (shot) { const r = await call('Page.captureScreenshot', { format: 'png' }); writeFileSync(shot, Buffer.from(r.data, 'base64')); }
  assert.deepEqual(errors, [], '页面异常: ' + errors.join('\n'));
  console.log(`OK · 前端 1D 入场 ${entries.length} 个（基准 15/15）· 截断/4H/列表/详情卡通过`);
} finally { ws.close(); }
