// 桌面端画线(趋势线/水平线/斐波那契/折线)回归验证。
// 用法: 先启动静态服务(如 cd fenxing && python3 -m http.server 8877),
//       再启动 Chrome: --headless=new --remote-debugging-port=9336,
//       然后: node tools/verify_draw_browser.mjs
// 环境变量: DESKTOP_TEST_URL(默认 http://127.0.0.1:8877), CDP_PORT(默认 9336)。
// 注意: 脚本会清空该 origin 的 localStorage 后重建画线数据, 请用独立测试端口。
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
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 300));
  return r.result.value;
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const until = async (expression, timeout = 20000) => {
  const start = Date.now();
  while (Date.now() - start < timeout){ if (await ev(expression)) return; await sleep(150); }
  throw new Error('Timed out: ' + expression);
};
const mouse = (type, x, y, clickCount = 1) =>
  call('Input.dispatchMouseEvent', { type, x, y, button: 'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount });
const click = async (x, y) => { await mouse('mousePressed', x, y); await mouse('mouseReleased', x, y); await sleep(160); };

await call('Runtime.enable');
await call('Page.enable');
await call('Network.enable');
await call('Network.setCacheDisabled', { cacheDisabled: true });
await call('Emulation.setDeviceMetricsOverride', { width: 1680, height: 1000, deviceScaleFactor: 1, mobile: false });
// 模拟首次升级: 在新文档任何脚本执行前写入 v1 旧数据(并清掉 v2), 验证自动迁移
const { identifier } = await call('Page.addScriptToEvaluateOnNewDocument', { source: `(() => {
  try {
    localStorage.clear();
    localStorage.setItem('chan_drawings_v1', JSON.stringify([
      { type: 'trend', t1: 1700259200, p1: 109.3, t2: 1705961600, p2: 93.5, color: '#4a90d9' },
      { type: 'sandr', t: null, p: 100, color: '#f5a623' },
    ]));
  } catch (e) {}
})()` });
await call('Page.navigate', { url: origin + '/index.html' });
await until(`location.href.startsWith(${JSON.stringify(origin)}) && document.readyState === 'complete'`);
await until('window.__INIT_OK === true && typeof drawingMgr === "object" && drawingMgr !== null');
await sleep(800);
await call('Page.removeScriptToEvaluateOnNewDocument', { identifier });
errors.length = 0;

// 组件加载 + 工具条
assert.equal(await ev('typeof LightweightChartsDrawing.DrawingManager'), 'function');
// 磁吸必须关闭: 落点/拖拽完全跟手, 不吸附 OHLC
assert.equal(await ev('drawingMgr.magnet().enabled'), false);
assert.deepEqual(
  await ev('[...document.querySelectorAll("#drawToggle button")].map(b => b.dataset.tool)'),
  ['none', 'trend', 'sandr', 'fib', 'poly']);
// v1 迁移
assert.equal(await ev('drawingMgr.drawings().length'), 2);
assert.equal(await ev('drawingMgr.drawings().map(d => d.kind).join(",")'), 'trend-line,horizontal-line');
assert.ok(await ev('!!localStorage.getItem("chan_drawings_v2")'));

// 斐波那契: 两次点击
const cr = await ev('document.getElementById("chart").getBoundingClientRect().toJSON()');
await ev('document.querySelector("#drawToggle button[data-tool=fib]").click()');
assert.equal(await ev('drawingMgr.tool()'), 'fib-retracement');
await click(cr.x + cr.width * 0.25, cr.y + cr.height * 0.30);
await click(cr.x + cr.width * 0.55, cr.y + cr.height * 0.70);
assert.equal(await ev('drawingMgr.tool()'), null);   // 落点后自动回选择态
assert.equal(await ev('drawingMgr.drawings().filter(d => d.kind === "fib-retracement").length'), 1);

// 折线: 三次点击 + 双击结束
await ev('document.querySelector("#drawToggle button[data-tool=poly]").click()');
await click(cr.x + cr.width * 0.35, cr.y + cr.height * 0.60);
await click(cr.x + cr.width * 0.45, cr.y + cr.height * 0.40);
await click(cr.x + cr.width * 0.55, cr.y + cr.height * 0.55);
await mouse('mousePressed', cr.x + cr.width * 0.55, cr.y + cr.height * 0.55, 2);
await mouse('mouseReleased', cr.x + cr.width * 0.55, cr.y + cr.height * 0.55, 2);
await sleep(300);
assert.equal(await ev('drawingMgr.drawings().filter(d => d.kind === "polyline").length'), 1);

// 右键取消: 进行中的落点被清掉并退出工具, 且不新增画线
const nBeforeCancel = await ev('drawingMgr.drawings().length');
await ev('document.querySelector("#drawToggle button[data-tool=trend]").click()');
await click(cr.x + cr.width * 0.30, cr.y + cr.height * 0.50);
assert.equal(await ev('drawingMgr.tool()'), 'trend-line');
assert.equal(await ev(`(() => {
  const e2 = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
  document.getElementById('chart').dispatchEvent(e2);
  return e2.defaultPrevented;
})()`), true, '右键在画线中应拦截原生菜单');
assert.equal(await ev('drawingMgr.tool()'), null, '右键取消后退出工具');
assert.equal(await ev('document.querySelector("#drawToggle button[data-tool=none]").classList.contains("active")'), true);
assert.equal(await ev('drawingMgr.drawings().length'), nBeforeCancel, '取消不产生半成品画线');

// 右键取消选中; 空闲态右键不拦截(保留原生菜单)
await ev('drawingMgr.select([drawingMgr.drawings()[0].id])');
assert.ok(await ev('drawingMgr.selection().length') > 0);
await ev(`document.getElementById('chart').dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }))`);
assert.equal(await ev('drawingMgr.selection().length'), 0, '右键取消选中');
assert.equal(await ev(`(() => {
  const e2 = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
  document.getElementById('chart').dispatchEvent(e2);
  return e2.defaultPrevented;
})()`), false, '空闲态保留原生右键菜单');

// 选中 + 拖动斐波那契锚点
const pt = await ev(`(() => {
  const d = drawingMgr.drawings().find(x => x.kind === 'fib-retracement');
  return { x: Math.round(chart.timeScale().timeToCoordinate(d.points[0].time)),
           y: Math.round(candleSeries.priceToCoordinate(d.points[0].price)) };
})()`);
const before = await ev('JSON.stringify(drawingMgr.drawings().find(x => x.kind === "fib-retracement").points)');
await mouse('mousePressed', cr.x + pt.x, cr.y + pt.y);
await mouse('mouseMoved', cr.x + pt.x + 40, cr.y + pt.y - 30, 0);
await mouse('mouseReleased', cr.x + pt.x + 40, cr.y + pt.y - 30);
await sleep(300);
assert.notEqual(await ev('JSON.stringify(drawingMgr.drawings().find(x => x.kind === "fib-retracement").points)'), before);

// 持久化: 刷新后一致
await sleep(400);
const all = await ev('JSON.stringify(drawingMgr.drawings())');
await ev('window.__RELOAD_MARK = true');   // 标记旧文档, 防止 until 在刷新前的旧页上提前通过
await call('Page.reload');
await until('window.__INIT_OK === true && typeof drawingMgr === "object" && drawingMgr !== null && window.__RELOAD_MARK === undefined');
await sleep(800);
assert.equal(await ev('JSON.stringify(drawingMgr.drawings())'), all);

// 清线立即落盘
await ev('document.getElementById("drawClear").click()');
assert.equal(await ev('drawingMgr.drawings().length'), 0);
assert.equal(await ev('JSON.parse(localStorage.getItem("chan_drawings_v2") || "[]").length'), 0);

assert.deepEqual(errors, []);
console.log('PASS desktop drawing: migrate v1, trend/hline/fib/poly, right-click cancel, drag anchor, persist, clear');
ws.close();
process.exit(0);
