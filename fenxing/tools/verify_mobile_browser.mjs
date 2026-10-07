// Start tools/serve_mobile.py (or a server on 8876) and Chrome with --remote-debugging-port=9336.
// Use a dedicated temporary Chrome profile: this test clears only the local test origin.
import assert from 'node:assert/strict';
import fs from 'node:fs';
const origin = process.env.MOBILE_TEST_URL || 'http://127.0.0.1:8876';
const tabs = await (await fetch('http://127.0.0.1:9336/json/list')).json();
const ws = new WebSocket(tabs.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise(r => ws.onopen = r);
let id = 0;
const pending = new Map(), errors = [];
ws.onmessage = e => {
  const m = JSON.parse(e.data);
  if (m.id) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(m.error) : p.resolve(m.result); }
  if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails);
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
const until = async (expression, timeout = 15000) => {
  const start = Date.now();
  while (Date.now() - start < timeout) { if (await ev(expression)) return; await sleep(100); }
  throw new Error('Timed out: ' + expression);
};
const size = async (width, height) => { await call('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: true }); await sleep(150); };
const shot = async name => fs.writeFileSync(`/tmp/fenxing-${name}.png`, Buffer.from((await call('Page.captureScreenshot')).data, 'base64'));
try {
  await call('Runtime.enable'); await call('Page.enable'); await call('Network.enable');
  await call('Page.navigate', { url: 'about:blank' });
  await sleep(300);
  await call('Network.setCacheDisabled', { cacheDisabled: true });
  await call('Storage.clearDataForOrigin', { origin, storageTypes: 'all' });
  await size(390, 844);
  await call('Page.navigate', { url: origin + '/' });
  await until("typeof offlineReady !== 'undefined' && offlineReady && !document.getElementById('prefetchBtn').disabled");
  await ev("document.getElementById('trSymSel').value='BTC';document.getElementById('trTfSel').value='1D';document.getElementById('trStepsInput').value='20';document.getElementById('trainerBtn').click()");
  await until("TR.active && !document.getElementById('loading').hidden === false && chart && !!savedPractice?.tr");
  await ev("document.getElementById('jCapital').value='10000';document.getElementById('jLeverage').value='1';document.getElementById('jOpen').click();trainerStep();trainerStep();drawRecs=[{type:'sandr',p:curBar().close,color:'#f5a623'}];addDrawnLine(drawRecs[0]);savePractice();document.getElementById('themeBtn').click();document.getElementById('maPeriods').value='10,30';document.getElementById('maPeriods').dispatchEvent(new Event('change'));document.querySelector('[data-ov=ma]').click();setTradeSheet(false)");
  const before = await ev('({step:TR.step,eq:acctEquity(),pos:positions,draw:drawRecs})');
  assert.equal(before.step, 2); assert.equal(before.pos.length, 1);
  await ev('persistQueue');
  await shot('practice-v10');
  await call('Page.reload');
  await until("typeof savedPractice !== 'undefined' && savedPractice?.tr && !document.getElementById('resumeCard').hidden");
  assert.equal(await ev('TR.active'), false);
  assert.equal(await ev('themeKey()'), 'light');
  assert.equal(await ev("document.getElementById('maPeriods').value"), '10,30');
  await ev('resumePractice()');
  assert.deepEqual(await ev('({step:TR.step,eq:acctEquity(),pos:positions,draw:drawRecs})'), before);
  assert.equal(await ev('maSeries.every(m=>!m.series.options().visible)'), true);
  await ev('trainerStep()');
  assert.equal(await ev('maSeries.every(m=>!m.series.options().visible)'), true);
  console.log('PASS resume: cursor, account, positions, drawings, theme and MA preferences');
  for (const [w,h] of [[844,390],[667,375],[320,568],[390,844]]) {
    await size(w,h);
    const geometry = await ev(`({height:innerHeight,width:innerWidth,scrollWidth:document.documentElement.scrollWidth,footer:document.getElementById('trainerCtl').getBoundingClientRect().toJSON(),chart:document.getElementById('chart').getBoundingClientRect().toJSON(),paneWidths:[chart,volumeChart,macdChart].map((c,i)=>document.getElementById(['chart','volume','macd'][i]).clientWidth-c.priceScale('right').width())})`);
    assert.ok(geometry.footer.bottom <= h + 1, JSON.stringify(geometry));
    assert.ok(geometry.chart.height >= 70, JSON.stringify(geometry));
    assert.ok(geometry.scrollWidth <= w, JSON.stringify(geometry));
    assert.ok(Math.max(...geometry.paneWidths)-Math.min(...geometry.paneWidths)<=1, JSON.stringify(geometry));
    await shot(`layout-${w}-${h}`);
  }
  await ev("document.getElementById('chartSettingsBtn').click()");
  assert.equal(await ev("[...document.querySelectorAll('.seg button')].every(b=>b.getBoundingClientRect().height>=44)"), true);
  await ev("document.getElementById('chartSettingsBtn').click();setTradeSheet(true)");
  assert.equal(await ev("document.getElementById('newOrderDetails').open"), false);
  await shot('trade-v10');
  await size(390,400);
  assert.ok(await ev("document.querySelector('.sheet-panel').getBoundingClientRect().height <= 400"));
  await size(390,844);
  await ev("setTradeSheet(false);closeAllPositions();while(trLocked()) trainerStep()");
  await ev('persistQueue');
  assert.equal(await ev('tsLoad().length'), 1);
  assert.equal(await ev('!!savedPractice?.tr'), false);
  assert.ok(await ev('Math.abs(tsLoad()[0].pnl - tsLoad()[0].trades.reduce((n,r)=>n+r.pnl_u,0))<.02'));
  await ev('endTrainer()');
  await ev('openHistoryReview(tsLoad()[0].id,0)');
  assert.equal(await ev('!!reviewSession && TR.revealed && replayT===null'), true);
  assert.ok(await ev("/^\\d{4}-/.test(chart.options().timeScale.tickMarkFormatter(TR.startTs))"));
  assert.equal(await ev('maxDrawdown([{eq:100},{eq:120},{eq:90}])'),25);
  assert.equal(await ev("document.getElementById('tradeToggle').hidden"), true);
  assert.ok(await ev("document.getElementById('trainerBanner').innerText.includes('入场')"));
  await shot('review-v10');
  await ev('endTrainer()');
  await ev("(()=>{const sample=tsLoad()[0];const history=Array.from({length:105},(_,i)=>({...sample,id:'test-'+i}));tsSave(history);renderTrainerSessions()})()");
  await ev('persistQueue');
  await call('Page.reload');
  await until("typeof savedHistory !== 'undefined' && tsLoad().length===105 && offlineReady");
  assert.equal(await ev("document.querySelectorAll('.ts-item').length"),20);
  await ev("document.getElementById('historyMore').click()");
  assert.equal(await ev("document.querySelectorAll('.ts-item').length"),40);
  assert.equal(await ev("buildTrainerCsv().includes('test-104')"),true);
  console.log('PASS review, complete-session accounting, 105-session persistence and full CSV export');
  await ev("document.getElementById('offlineMarket').value='贵金属';prefetchAll()");
  assert.equal(await ev('failedDownloads.length'),0);
  assert.ok(await ev("offlineItems.get('XAUUSD') && offlineItems.get('XAGUSD')"));
  await ev("document.getElementById('offlineMarket').value='港股';window.realFetch=window.fetch;window.fetch=(url,opts)=>String(url).includes('9988_hk')?Promise.reject(new Error('test failure')):realFetch(url,opts);prefetchAll()");
  assert.equal(await ev('failedDownloads.length'),1);
  await ev('window.fetch=window.realFetch;prefetchAll(true)');
  assert.equal(await ev('failedDownloads.length'),0);
  await ev("document.getElementById('offlineMarket').value='A股';window.fetch=(url,opts)=>new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))));void prefetchAll()");
  await until('!!downloadController');
  await ev("document.getElementById('cancelDownload').click()");
  await until('!downloadController');
  await ev('window.fetch=window.realFetch');
  assert.ok(await ev("document.getElementById('downloadStatus').textContent.includes('已取消')"));
  await shot('offline-v10');
  await call('Network.emulateNetworkConditions', { offline: true, latency:0, downloadThroughput:0, uploadThroughput:0 });
  await call('Page.reload');
  await until("typeof offlineReady !== 'undefined' && offlineReady && tsLoad().length===105");
  await ev("document.getElementById('trSymSel').value='XAUUSD';document.getElementById('trTfSel').value='1D';enterTrainer()");
  assert.equal(await ev('TR.active'),true);
  console.log('PASS verified downloads, failure retry, cancellation, offline restart and practice');
  await call('Network.emulateNetworkConditions', { offline:false, latency:0, downloadThroughput:-1, uploadThroughput:-1 });
  await ev(`(async()=>{
    endTrainer(); clearPracticeSnapshot();
    const sample=tsLoad()[0];
    await idbSet(HISTORY_KEY,null); localStorage.removeItem(HISTORY_KEY);
    localStorage.setItem(TSKEY,JSON.stringify([sample])); await idbSet(TSKEY,[sample]);
    trStatsSave({...TR_EMPTY(),sessions:150,orders:150});persistFlush();await persistQueue;
  })()`);
  await call('Page.reload');
  await until("typeof savedHistory !== 'undefined' && tsLoad().length===1 && offlineReady");
  assert.equal(await ev('trStatsLoad().orders'),150);
  await ev('(async()=>{trStatsSave(TR_EMPTY());await tsSave([]);persistFlush();await persistQueue})()');
  await call('Page.reload');
  await until("typeof offlineReady !== 'undefined' && offlineReady");
  assert.equal(await ev('tsLoad().length'),0);
  assert.equal(await ev('trStatsLoad().orders'),0);
  await ev(`(async()=>{
    const setItem=Storage.prototype.setItem;
    Storage.prototype.setItem=function(){throw new DOMException('test quota','QuotaExceededError')};
    try { await tsSave([{id:'quota-test',sym:'BTC',tf:'1D',ts:Date.now(),startTs:1,steps:20,capital:100000,endCapital:100000,pnl:0,cond:'完成',trades:[],curve:[]}]); }
    finally { Storage.prototype.setItem=setItem; }
  })()`);
  await call('Page.reload');
  await until("typeof savedHistory !== 'undefined' && tsLoad()[0]?.id==='quota-test' && offlineReady");
  console.log('PASS legacy history migration, clear without resurrection, and IndexedDB fallback when localStorage is full');
  assert.deepEqual(errors, []);
  console.log('PASS no uncaught browser exceptions');
} finally {
  await call('Network.emulateNetworkConditions', { offline:false, latency:0, downloadThroughput:-1, uploadThroughput:-1 });
  ws.close();
}
