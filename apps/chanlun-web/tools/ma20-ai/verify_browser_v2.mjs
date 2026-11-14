// Run against an isolated browser profile/origin; never clears existing annotation storage.
import assert from 'node:assert/strict';
const origin=process.env.COVER67_TEST_URL||'http://127.0.0.1:8878';
const port=process.env.CDP_PORT||'9349';
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
 await call('Runtime.enable');await call('Page.enable');await call('Network.enable');await call('Network.setCacheDisabled',{cacheDisabled:true});errors.length=0;
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await call('Page.navigate',{url:origin+'/index.html?ma20ai=1'});await until('window.__INIT_OK===true && typeof ma20AIData === "function"');
 assert.equal(await ev('MA20_AI_SOURCE'),'ma20_ai_v2');assert.equal(await ev('MA20_AI.mode'),'blind');assert.equal(await ev('ma20AIRows().length'),20);assert.equal(await ev('ma20AIReference().confirmed.size'),81);assert.equal(await ev('MA20AIModel.gatePassed'),false);
 const saved=await ev('JSON.stringify(ANN.decisions)');
 try{
  assert.ok(await ev('ma20AIRows().every(r=>!MA20AIModel.fitTimes.includes(r.time))'));
  assert.ok(await ev('ma20AIRows("high").every(r=>!MA20_AI_RESERVED.has(r.time))'));assert.ok(await ev('ma20AIRows("low").every(r=>!MA20_AI_RESERVED.has(r.time))'));
  await ev('MA20_AI.showScores=true;ma20AIRefresh();ma20AISelect(0);ma20AIReview()');await until('ANN.on && ANN.sigs.length===20');
  assert.equal(await ev('document.querySelectorAll("[data-assessment]").length'),19);
  assert.ok(!(await ev('document.getElementById("annBody").textContent')).includes('当前偏好'));
  assert.ok(await ev('ma20AIMarkers("raw").every(m=>m.time<=candlesFor("raw")[ANN.sigs[ANN.idx].i].time)'));
  const time=await ev('candlesFor("raw")[ANN.sigs[ANN.idx].i].time');
  await ev(`ANN.decisions['BTC|1D|ma20_ai_v1|'+${time}]={d:'no',note:'v1-sentinel'};annToggleReason('调整时间不足');annMark('no')`);
  let out=await ev('annExportData()'),rec=out.items.find(x=>x.source==='ma20_ai_v2'&&x.features.time===time);
  assert.ok(rec);assert.equal(rec.algorithm.evaluationRole,'reserved_blind_review');assert.equal(rec.algorithm.blindReview.firstDecision,'no');assert.equal(rec.algorithm.featureVersion,'ma20-ai-features/2');assert.equal(Object.keys(rec.algorithm.features).length,33);assert.ok(rec.algorithm.version.startsWith('MA20-AI-V2-2026-11-04-'));
  assert.ok((await ev('document.getElementById("annBody").textContent')).includes('当前偏好'));
  await ev('annStartChartPick("advanceLow");annHandleChartPick({time:candlesFor("raw")[ANN.sigs[ANN.idx].i-60].time});annStartChartPick("advancePeak");annHandleChartPick({time:candlesFor("raw")[ANN.sigs[ANN.idx].i-30].time});annMark("yes")');
  out=await ev('annExportData()');rec=out.items.find(x=>x.source==='ma20_ai_v2'&&x.features.time===time);assert.equal(rec.algorithm.blindReview.firstDecision,'no');assert.equal(rec.algorithm.blindReview.revisedAfterExposure,true);assert.ok(rec.assessment.advanceLow);assert.ok(rec.assessmentEvidence.derived.advanceBars>0);
  await ev(`annImportData(${JSON.stringify(JSON.stringify({symbol:'BTC',tf:'1D',items:[rec]}))})`);assert.equal(await ev('annDecision(ANN.sigs[ANN.idx].i).algorithm.blindReview.firstDecision'),'no');assert.equal(await ev('!!annDecision(ANN.sigs[ANN.idx].i).manual'),false);assert.equal(await ev(`ANN.decisions['BTC|1D|ma20_ai_v1|'+${time}].note`),'v1-sentinel');
  await ev('ma20AIContext(ANN.sigs[ANN.idx].i)');assert.ok(await ev('ma20AIMarkers("raw").some(m=>m.text==="参考高B")'));assert.ok(await ev('ma20AIMarkers("raw").every(m=>m.time<=candlesFor("raw")[ANN.sigs[ANN.idx].i].time)'));
  await ev('annSetOpen(false);MA20_AI.mode="previous_low";MA20_AI.open=true;ma20AIRefresh()');assert.equal(await ev('ma20AIRows().length'),4);
  await ev('ma20AISelect(0);ma20AIReview()');assert.ok((await ev('document.getElementById("annBody").textContent')).includes('上轮你的判定'));
  await ev('annSetOpen(false);ma20AINextBatch()');assert.ok(await ev('MA20_AI.batch.every(r=>!MA20_AI_RESERVED.has(r.time))'));
  await ev('MA20_AI.mode="all";ma20AIRefresh()');assert.equal(await ev('ma20AIRows().length'),await ev('ma20StructureData().signals.length'));
  await ev('switchSymbol("ETH")');await until('currentSym==="ETH"');assert.equal(await ev('ma20AIRows().length'),0);
  await ev('switchSymbol("BTC")');await until('currentSym==="BTC"');await ev('MA20_AI.mode="blind";MA20_AI.open=true;MA20_AI.showScores=false;ma20AIRefresh();candleSeries.setMarkers(buildMarkers(activeView))');
  await call('Page.captureScreenshot',{format:'png'}).then(async r=>{const fs=await import('node:fs');fs.writeFileSync('/tmp/ma20-ai-v2-ui.png',Buffer.from(r.data,'base64'));});
 }finally{await ev(`ANN.decisions=${saved};annSave();`);}
 await call('Page.navigate',{url:origin+'/docs/ma20-ai-v2-2026-11-04.html'});await until('document.readyState==="complete" && document.querySelectorAll("table").length>=4');assert.ok((await ev('document.body.textContent')).includes('整段行情'));
 await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});assert.ok(await ev('document.documentElement.scrollWidth<=innerWidth'));assert.deepEqual(errors,[]);
 console.log('PASS V2 blind-group exclusion; locked score/filter/colors; first decision/revision evidence; 33 features; 19 optional form fields/chart anchors; v1 storage; 4 positives; context markers; BTC scope; report');
}finally{ws.close();}
