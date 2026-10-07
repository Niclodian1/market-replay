import assert from 'node:assert/strict';
const origin = process.env.MOBILE_TEST_URL || 'http://127.0.0.1:8876';
const tabs = await (await fetch('http://127.0.0.1:9336/json/list')).json();
const page = tabs.find(t => t.type === 'page');
if (!page) throw new Error('no CDP page: ' + JSON.stringify(tabs));
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise(r => { ws.onopen = r; ws.onerror = r; });
let id = 0;
const pending = new Map();
ws.onmessage = e => {
  const m = JSON.parse(e.data);
  if (m.id) {
    const p = pending.get(m.id);
    pending.delete(m.id);
    m.error ? p.reject(m.error) : p.resolve(m.result);
  }
};
const call = (method, params = {}) => new Promise((resolve, reject) => {
  const n = ++id; pending.set(n, { resolve, reject }); ws.send(JSON.stringify({ id: n, method, params }));
});
const ev = async expression => {
  const r = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result.value;
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const until = async (expression, timeout = 20000) => {
  const start = Date.now();
  while (Date.now() - start < timeout) { if (await ev(expression)) return; await sleep(100); }
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
await ev("document.getElementById('trSymSel').value='AAPL';document.getElementById('trTfSel').value='1D';document.getElementById('trStepsInput').value='20';document.getElementById('trainerBtn').click()");
await until('TR.active && document.getElementById("loading").hidden && !!chart');

await ev('setChartSettingsOpen(true)');
assert.equal(await ev("document.getElementById('chartSettingsBtn').textContent"), '收起');
assert.equal(await ev('document.getElementById("chartSettings").hidden'), false);
const btn = await ev("document.getElementById('chartSettingsBtn').getBoundingClientRect().toJSON()");
const panel = await ev("document.getElementById('chartSettings').getBoundingClientRect().toJSON()");
assert.ok(btn.bottom <= panel.top + 1, JSON.stringify({ btn, panel }));
await ev('setChartSettingsOpen(false)');
assert.equal(await ev("document.getElementById('chartSettingsBtn').textContent"), '图表工具');
assert.equal(await ev('document.getElementById("chartSettings").hidden'), true);

await ev("setDrawTool('fib'); setChartSettingsOpen(false)");
assert.equal(await ev('drawTool'), 'fib');
assert.equal(await ev('document.getElementById("drawDock").hidden'), false);
assert.equal(await ev('document.getElementById("chartSettings").hidden'), true);
await ev(`(() => {
  const el = document.getElementById('chart');
  const r = el.getBoundingClientRect();
  const x = r.left + r.width * 0.35, y = r.top + r.height * 0.45;
  el.dispatchEvent(new PointerEvent('pointerdown', { clientX: x, clientY: y, pointerId: 1, bubbles: true }));
  el.dispatchEvent(new PointerEvent('pointermove', { clientX: x + 12, clientY: y - 18, pointerId: 1, bubbles: true }));
})()`);
assert.equal(await ev('!!window._drawCursor && Number.isFinite(window._drawCursor.p)'), true);
assert.equal(await ev('drawPending == null'), true);

await ev(`(() => {
  const el = document.getElementById('chart');
  const r = el.getBoundingClientRect();
  legendFromPointer(r.left + r.width * 0.4, r.top + r.height * 0.4);
})()`);
assert.equal(await ev("document.getElementById('legendDist').textContent.includes('距最新')"), true);
assert.equal(await ev("document.getElementById('legendDist').textContent.includes('%')"), true);

await ev(`(() => {
  const cs = candlesFor();
  const a = cs[Math.max(2, cs.length - 18)];
  const b = cs[cs.length - 6];
  drawRecs.push({ type: 'fib', t1: a.time, p1: a.low, t2: b.time, p2: b.high, color: '#f5a623' });
  drawRecs.push({ type: 'poly', points: [
    { t: cs[3].time, p: cs[3].high },
    { t: cs[8].time, p: cs[8].low },
    { t: cs[12].time, p: cs[12].close },
  ], color: '#4a90d9' });
  syncDrawOverlay();
})()`);
assert.equal(await ev("drawRecs.filter(r => r.type === 'fib').length >= 1"), true);
assert.equal(await ev("drawRecs.filter(r => r.type === 'poly').length >= 1"), true);
assert.equal(await ev("Math.abs(fibPrice(100,200,0.5) - 150) < 1e-9"), true);

console.log('PASS draw panel collapse, live % distance, fib + polyline');
ws.close();
process.exit(0);
