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
 assert.equal(await ev('ma20AISupported()'),true);assert.equal(await ev('MA20AIModel.gatePassed'),false);assert.equal(await ev('ma20AIRows().length'),20);
 const saved=await ev('JSON.stringify(ANN.decisions)');
 try{
  const batch=await ev('ma20AIRows().map(r=>r.time)');assert.equal(new Set(batch).size,20);assert.deepEqual([...batch].sort((a,b)=>a-b),batch);
  await ev('ma20AISelect(0);ma20AIReview()');await until('ANN.on && ma20AIActive() && ANN.sigs.length===20');
  assert.equal(await ev('document.querySelectorAll("[data-assessment]").length'),17);
  assert.ok((await ev('document.getElementById("annBody").textContent')).includes('不是盈利概率')||(await ev('document.getElementById("annBody").textContent')).includes('不表示盈利概率'));
  assert.ok(await ev('ma20AIMarkers("raw").every(m=>m.time<=candlesFor("raw")[ANN.sigs[ANN.idx].i].time)'));
  const time=await ev('candlesFor("raw")[ANN.sigs[ANN.idx].i].time');
  await ev(`ANN.decisions['BTC|1D|ma20_structure_v3|'+${time}]={d:'no',note:'old-version-sentinel'};annToggleReason('调整时间不足');annMark('no')`);
  let out=await ev('annExportData()'),rec=out.items.find(x=>x.source==='ma20_ai_v1'&&x.features.time===time);
  assert.ok(rec);assert.ok(rec.algorithm.version.startsWith('MA20-AI-V1-2026-11-04-'));assert.equal(rec.algorithm.task,'human_preference_review');assert.equal(rec.algorithm.execution.entryBasis,'selected_bar_close');assert.equal(rec.algorithm.scoreMeaning,'uncalibrated_preference_not_win_probability');assert.equal(Object.keys(rec.algorithm.features).length,26);assert.equal(rec.algorithm.gatePassed,false);
  await ev('annStartChartPick("adjustStart");annHandleChartPick({time:candlesFor("raw")[ANN.sigs[ANN.idx].i-4].time});annMark("yes")');
  out=await ev('annExportData()');rec=out.items.find(x=>x.source==='ma20_ai_v1'&&x.features.time===time);assert.ok(rec.assessment.adjustStart);
  await ev(`annImportData(${JSON.stringify(JSON.stringify({symbol:'BTC',tf:'1D',items:[rec]}))})`);
  assert.equal(await ev('!!annDecision(ANN.sigs[ANN.idx].i).manual'),false);assert.equal(await ev('annDecision(ANN.sigs[ANN.idx].i).d'),'yes');assert.equal(await ev(`ANN.decisions['BTC|1D|ma20_structure_v3|'+${time}].note`),'old-version-sentinel');
  await ev('annSetOpen(false);ma20AINextBatch()');assert.ok(!(await ev('MA20_AI.batch.map(r=>r.time)')).includes(time),'reviewed point is not queried again');
  await ev('MA20_AI.mode="rejected";MA20_AI.open=true;ma20AIRefresh()');assert.ok(await ev('ma20AIRows().length>0'));
  await ev('MA20_AI.mode="all";ma20AIRefresh()');assert.equal(await ev('ma20AIRows().length'),await ev('ma20StructureData().signals.length'));
  await ev('switchSymbol("ETH")');await until('currentSym==="ETH"');assert.equal(await ev('ma20AISupported()'),false);assert.equal(await ev('ma20AIRows().length'),0);assert.ok((await ev('document.querySelector("[data-ai-status]").textContent')).includes('当前仅支持 BTC'));
  await ev('switchSymbol("BTC")');await until('currentSym==="BTC" && ma20AISupported()');await ev('MA20_AI.mode="batch";MA20_AI.open=true;ma20AIRefresh()');
  await call('Page.captureScreenshot',{format:'png'}).then(async r=>{const fs=await import('node:fs');fs.writeFileSync('/tmp/ma20-ai-ui-final.png',Buffer.from(r.data,'base64'));});
 }finally{await ev(`ANN.decisions=${saved};annSave();`);}
 await call('Page.navigate',{url:origin+'/docs/ma20-ai-2026-11-04.html'});await until('document.readyState==="complete" && document.querySelectorAll("table").length>=3');assert.ok((await ev('document.body.textContent')).includes('70.6%'));
 await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});assert.ok(await ev('document.documentElement.scrollWidth<=innerWidth'),'report mobile width');
 assert.deepEqual(errors,[]);console.log('PASS AI: deterministic chronological batch20, blind markers, reasons/chart anchors, versioned export/import, legacy preservation, exclude reviewed points, complete candidate access, BTC-only scope, report');

}finally{ws.close();}
