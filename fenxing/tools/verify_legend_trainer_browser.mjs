// 桌面端两项回归:
//   1) 图例"距最新"读数: 十字光标价位与最新收盘的距离, 实时跟随光标(上正下负);
//   2) 练习答案横幅: 净盈亏率必须对本局初始本金(100,000U), 而非逐笔保证金收益率加总。
// 用法: 先启动静态服务(如 cd fenxing && python3 -m http.server 8877),
//       再启动 Chrome: --headless=new --remote-debugging-port=9336,
//       然后: node tools/verify_legend_trainer_browser.mjs
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
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 300));
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
await call('Emulation.setDeviceMetricsOverride', { width: 1680, height: 1000, deviceScaleFactor: 1, mobile: false });
await call('Page.navigate', { url: origin + '/index.html' });
await until(`location.href.startsWith(${JSON.stringify(origin)}) && document.readyState === 'complete'`);
await until('window.__INIT_OK === true && typeof candleSeries === "object" && candleSeries !== null');
await sleep(800);
errors.length = 0;

// ---- 1) 图例"距最新" ----
// 光标放在主图上/下两个位置: 读数符号应与"光标价 vs 最新收盘"一致, 数值一致
const probe = async yFrac => {
  const r = await ev('document.getElementById("chart").getBoundingClientRect().toJSON()');
  await call('Input.dispatchMouseEvent', {
    type: 'mouseMoved', x: r.x + r.width * 0.5, y: r.y + r.height * yFrac, button: 'none', buttons: 0,
  });
  await sleep(260);
  const text = await ev('document.getElementById("distTag").textContent');
  const vis = await ev(`(() => {
    const el = document.getElementById('distTag');
    const r = el.getBoundingClientRect();
    const cb = document.querySelector('.chart-box').getBoundingClientRect();
    return { display: getComputedStyle(el).display, rightGap: Math.round(cb.right - r.right) };
  })()`);
  const expected = await ev(`(() => {
    const r = document.getElementById('chart').getBoundingClientRect();
    const px = candleSeries.coordinateToPrice(${(r.height * yFrac).toFixed(2)});
    const cs = candlesFor(activeView);
    const last = cs[cs.length - 1].close;
    return { px, dp: px - last, pp: (px - last) / last * 100 };
  })()`);
  return { text, vis, expected };
};
const top = await probe(0.08);
assert.match(top.text, /^-?[\d,.]+ · 距最新 \+[\d,.]+ \(\+\d+\.\d{2}%\)( · \d+根)?$/, '顶部光标应显示价格与正距离: ' + top.text);
assert.equal(top.vis.display, 'block', '读数应显示在右侧价格栏');
assert.ok(Math.abs(top.vis.rightGap) <= 4, '读数应贴右侧价格栏: gap=' + top.vis.rightGap);
const bottom = await probe(0.92);
assert.match(bottom.text, /^-?[\d,.]+ · 距最新 -[\d,.]+ \(-\d+\.\d{2}%\)( · \d+根)?$/, '底部光标应显示价格与负距离: ' + bottom.text);
for (const [name, p] of [['top', top], ['bottom', bottom]]){
  const shown = Number((p.text.match(/距最新 ([+-][\d,.]+)/) || [])[1]?.replace(/,/g, ''));
  const tol = Math.max(0.2, Math.abs(p.expected.dp) * 0.005);
  assert.ok(Math.abs(shown - p.expected.dp) <= tol,
    `${name} 距离数值应与计算一致: 显示 ${shown} vs 计算 ${p.expected.dp.toFixed(2)}`);
  // 光标价格: 按价格轴每像素跨度换算容差(CDP 鼠标坐标取整误差 ≤ ~2px)
  const perPx = await ev(`Math.abs(candleSeries.coordinateToPrice(0) - candleSeries.coordinateToPrice(document.getElementById('chart').clientHeight)) / document.getElementById('chart').clientHeight`);
  const priceShown = Number((p.text.match(/^(-?[\d,.]+) ·/) || [])[1]?.replace(/,/g, ''));
  const ptol = Math.max(0.5, perPx * 2);
  assert.ok(Math.abs(priceShown - p.expected.px) <= ptol,
    `${name} 光标价格应与计算一致(±${ptol.toFixed(0)}): 显示 ${priceShown} vs 计算 ${p.expected.px.toFixed(2)}`);
}
// 光标移出图表 → 读数隐藏; 左上角图例不再承载该读数
await call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 1600, y: 700, button: 'none', buttons: 0 });
await sleep(300);
assert.equal(await ev(`getComputedStyle(document.getElementById('distTag')).display`), 'none', '移出图表应隐藏读数');
assert.equal(await ev(`!!document.getElementById('legendDist')`), false, '左上角图例不应再有距最新读数');

// ---- 1b) 左上角 MA 图例: 颜色与周期一一对应 ----
const maLegend = await ev(`[...document.querySelectorAll('#legendMa .ma-item')].map(e => ({ t: e.textContent, c: getComputedStyle(e.querySelector('i')).backgroundColor }))`);
assert.deepEqual(maLegend.map(x => x.t), ['MA20', 'MA60', 'MA120'], 'MA 图例周期');
assert.deepEqual(maLegend.map(x => x.c), ['rgb(41, 98, 255)', 'rgb(255, 152, 0)', 'rgb(156, 39, 176)'], 'MA 图例颜色');
await ev(`document.querySelector('#overlayToggle button[data-ov="ma"]').click()`);
assert.equal(await ev(`document.getElementById('legendMa').textContent`), '', 'MA 关闭时图例应隐藏');
await ev(`document.querySelector('#overlayToggle button[data-ov="ma"]').click()`);
assert.equal(await ev(`document.querySelectorAll('#legendMa .ma-item').length`), 3, 'MA 恢复后图例应回来');

// ---- 2) 答案横幅: 净盈亏率对本金 ----
// 造两笔逐笔收益率加总 = -15% 的局: 净 -300U / 本金 100,000U 应显示 -0.3%
const banner = await ev(`(() => {
  const bak = { sym: TR.sym, tf: TR.tf, step: TR.step, startTs: TR.startTs, endReason: TR.endReason, session: TR.session };
  TR.sym = DATA.meta.symbol; TR.tf = activeTF; TR.step = 12;
  TR.startTs = candlesFor(activeView)[10].time;
  TR.endReason = '手动揭示';
  TR.session = [
    { pnl_u: -500, pnl_pct_capital: -25 },   // 逐笔保证金口径: 亏 25%
    { pnl_u: 200, pnl_pct_capital: 10 },     // 逐笔保证金口径: 盈 10%
  ];
  showAnswerBanner();
  const txt = document.getElementById('trainerBanner').textContent;
  document.getElementById('trainerBanner').style.display = 'none';
  Object.assign(TR, bak);
  return txt;
})()`);
assert.ok(banner.includes('-300.0U (-0.3%)'),
  '净率应为对本金 -0.3%, 而非逐笔加总 -15%: ' + banner);

assert.equal(errors.length, 0, '运行期异常: ' + errors.join(' | '));
console.log('✓ 图例距最新: 顶部', top.text, '| 底部', bottom.text);
console.log('✓ 答案横幅:', banner.replace(/\s+/g, ' ').slice(0, 140));
ws.close();
