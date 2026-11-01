// Run against an isolated browser profile/origin; never clears existing annotation storage.
import assert from 'node:assert/strict';
const origin=process.env.COVER67_TEST_URL||'http://127.0.0.1:8878';
const port=process.env.CDP_PORT||'9337';
const pages=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const page=pages.find(p=>p.type==='page'&&(p.url==='about:blank'||p.url.startsWith(origin)));
assert.ok(page,'isolated test tab');
const ws=new WebSocket(page.webSocketDebuggerUrl);await new Promise((ok,bad)=>{ws.onopen=ok;ws.onerror=bad;});
let id=0;const pending=new Map(),errors=[];
ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result);}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const call=(method,params={})=>new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});ws.send(JSON.stringify({id:n,method,params}));});
const ev=async expression=>{const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
const until=async expr=>{for(let n=0;n<150;n++){if(await ev(expr))return;await new Promise(r=>setTimeout(r,200));}throw Error('timeout '+expr);};
try{
 await call('Runtime.enable');await call('Page.enable');await call('Network.enable');await call('Network.setCacheDisabled',{cacheDisabled:true});
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await call('Page.navigate',{url:origin+'/index.html'});await until('window.__INIT_OK===true && typeof annSetOpen === "function"');
 assert.equal(await ev('Cover67.VERSION'),'MA20-SIMPLE-V2-2026-10-30');
 await ev('annSetOpen(true)');assert.equal(await ev('ANN.source'),'ma20_simple_v2');assert.equal(await ev('ANN.sigs.length'),181);
 assert.equal(await ev('Object.keys(ANN_SOURCES).some(s=>s.startsWith("cover67"))'),false);
 const saved=await ev('JSON.stringify(ANN.decisions)');
 try {
  await ev(`(()=>{ const s=ANN.sigs[0],t=candlesFor('raw')[s.i].time;ANN.decisions['BTC|1D|cover67_v1|'+t]={d:'yes',note:'legacy sentinel'};annRender();})()`);
  assert.equal(await ev('annDecision(ANN.sigs[ANN.idx].i)?.d || null'),null,'legacy decision must not fill new review');
  await ev(`(()=>{ const el=document.querySelector('[data-assessment="region"]');el.value='成立';el.dispatchEvent(new Event('change'));const t=document.querySelector('[data-assessment="trigger"]');t.value='力度不足';t.dispatchEvent(new Event('change'));for(const [k,v] of Object.entries({regime:'趋势回调',confidence:'中',primaryReason:'区域成立但触发不足',depth:'不确定',duration:'不确定',contraction:'不确定',referenceStatus:'不确定'})){document.querySelector('[data-assessment="'+k+'"]').value=v;}annSaveAssessment();annToggleReason('力度不够');annMark('no');})()`);
  let out=await ev('annExportData()');let rec=out.items.find(x=>x.source==='ma20_simple_v2'&&x.decision==='no');
  assert.equal(rec.assessment.region,'成立');assert.equal(rec.assessment.trigger,'力度不足');assert.equal(rec.algorithm.version,'MA20-SIMPLE-V2-2026-10-30');
  await ev(`(()=>{delete ANN.decisions[annKey(ANN.sigs[0].i)];annImportData(${JSON.stringify(JSON.stringify({items:[rec]}))});})()`);
  assert.equal(await ev('annDecision(ANN.sigs[0].i).assessment.trigger'),'力度不足');
  await ev('document.getElementById("cover67VolumeOnChart").click()');
  assert.ok((await ev('document.getElementById("cover67VolumePopup").textContent')).includes('0 笔'),'blind history excludes future');
  await ev('document.getElementById("cover67Review").click()');assert.equal(await ev('ANN.sigs.length'),185);
  await ev('switchSymbol("ETH")');await until('currentSym==="ETH" && annCoverContext().signals.length===170');
  await ev('ANN.source="ma20_simple_v2";annBuildSigs();annGo(ANN.sigs.findIndex(s=>annDate(candlesFor("raw")[s.i].time)==="2025-07-02"))');
  assert.equal(await ev('annCoverContext().rows[ANN.sigs[ANN.idx].i].confirmationCount'),2);
  assert.equal(await ev('document.querySelectorAll("[data-assessment]").length'),17);
  await ev('document.getElementById("cover67VolumeOnChart").click()');
  assert.ok((await ev('document.getElementById("cover67VolumePopup").textContent')).includes('共 2 项确认'));
  assert.ok((await ev('document.getElementById("cover67VolumePopup").textContent')).includes('相同确认项数量'));
  await ev('document.getElementById("annHide").click();annSetOpen(false);cover67IndicatorEnable(true);document.getElementById("cover67IndicatorList").click()');
  assert.equal(await ev('COVER67_INDICATOR.rows.length'),170);
  assert.ok((await ev('document.getElementById("cover67IndicatorEntries").textContent')).includes('项确认'));
  assert.equal(await ev('annCoverContext().signals.some(r=>r.confirmationCount===0)'),true);
  await call('Page.captureScreenshot',{format:'png'}).then(async r=>{const fs=await import('node:fs');fs.writeFileSync('/tmp/ma20-simple-v2.png',Buffer.from(r.data,'base64'));});
 } finally {await ev(`ANN.decisions=${saved};annSave();`);}
 assert.deepEqual(errors,[]);console.log('PASS simple v2 browser: candidate counts, blank versioned review, labels save/import/export, legacy preservation, blind history, ETH July 2, chart popup/list');
}finally{ws.close();}
