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
 await call('Runtime.enable');await call('Page.enable');await call('Network.enable');await call('Network.setCacheDisabled',{cacheDisabled:true});
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 errors.length=0; // Runtime.enable may replay errors from the previous document.
 await call('Page.navigate',{url:origin+'/index.html'});await until('window.__INIT_OK===true && typeof ma20StructureData === "function"');
 assert.equal(await ev('ma20StructureData().signals.length'),171);
 assert.equal(await ev('ma20StructureRows("diff").length'),109);assert.equal(await ev('ma20StructureRows("missed").length'),6);
 const saved=await ev('JSON.stringify(ANN.decisions)');
 try{
  await ev('document.getElementById("ma20StructureList").click()');
  assert.equal(await ev('document.querySelectorAll("[data-v3-index]").length'),109);
  await ev('ma20StructureSelect(3);ma20StructureReview()');
  await until('ANN.on && ANN.sigs.length===109');
  assert.equal(await ev('activeView'),'raw');
  assert.ok((await ev('document.getElementById("annBody").textContent')).includes('本根收盘'));
  assert.equal(await ev('document.querySelectorAll("[data-assessment]").length'),17);
  assert.ok(await ev('ma20StructureMarkers("raw").every(m=>m.time<=candlesFor("raw")[ANN.sigs[ANN.idx].i].time)'));
  await ev('annStartChartPick("adjustStart");annHandleChartPick({time:candlesFor("raw")[ANN.sigs[ANN.idx].i-5].time})');
  assert.ok(await ev('!!annDecision(ANN.sigs[ANN.idx].i).assessment.adjustStart'));
  await ev('annToggleReason("力度不够");annMark("no")');
  let out=await ev('annExportData()'),rec=out.items.find(x=>x.source==='ma20_structure_v3'&&x.decision==='no');
  assert.ok(rec);assert.equal(rec.algorithm.version,'MA20-STRUCTURE-V3-2026-11-03');assert.equal(rec.algorithm.execution.entryBasis,'selected_bar_close');assert.equal(rec.algorithm.reference.status,'unknown');
  const time=rec.features.time;
  await ev(`ANN.source="ma20_structure_v3";annBuildSigs();annGo(ANN.sigs.findIndex(s=>candlesFor("raw")[s.i].time===${time}))`);
  assert.equal(await ev('annDecision(ANN.sigs[ANN.idx].i).d'),'no','all/diff share decisions');
  await ev('annMark("yes")');out=await ev('annExportData()');rec=out.items.find(x=>x.source==='ma20_structure_v3'&&x.features.time===time);
  await ev(`annImportData(${JSON.stringify(JSON.stringify({items:[rec]}))})`);
  assert.equal(await ev('!!annDecision(ANN.sigs[ANN.idx].i).manual'),false,'V3 positive must not turn into a manual buy on import');
  await ev('ma20StructureVolumeOpen(ANN.sigs[ANN.idx].i)');assert.ok((await ev('document.getElementById("ma20V3Volume").textContent')).includes('不含本根'));
  await ev('annGo(0)');assert.equal(await ev('!!document.getElementById("ma20V3Volume")'),false);
  await ev(`(()=>{const t=MA20ReviewReference.confirmedTimes[0];ANN.decisions['BTC|1D|manual_buy_v1|'+t]={d:'no',manual:{}};})()`);
  assert.equal(await ev('ma20StructureReference().confirmed.size'),67);
  await ev(`delete ANN.decisions['BTC|1D|manual_buy_v1|'+MA20ReviewReference.confirmedTimes[0]];annSetOpen(false);MA20_V3.open=true;MA20_V3.mode='missed';ma20StructureRefresh();`);
  assert.equal(await ev('document.querySelectorAll("[data-v3-index]").length'),6);
  await call('Page.captureScreenshot',{format:'png'}).then(async r=>{const fs=await import('node:fs');fs.writeFileSync('/tmp/ma20-v3-missed.png',Buffer.from(r.data,'base64'));});
  await ev('switchSymbol("ETH")');await until('currentSym==="ETH" && ma20StructureData().signals.length===169');
  assert.equal(await ev('ma20StructureReference().confirmed.size'),0,'BTC reference cannot leak into ETH');
  assert.ok((await ev('document.querySelector("[data-v3-status]").textContent')).includes('ETH'));
  assert.equal(await ev('ma20StructureRows("diff").length'),169);
 }finally{await ev(`ANN.decisions=${saved};annSave();`);}
 await call('Page.navigate',{url:origin+'/docs/ma20-review-2026-11-03.html'});await until('document.querySelectorAll("#points [data-i]").length===68');
 await ev('document.getElementById("scope").value="extra";list()');assert.equal(await ev('document.querySelectorAll("#points [data-i]").length'),109);
 await ev('document.getElementById("scope").value="missed";list()');assert.equal(await ev('document.querySelectorAll("#points [data-i]").length'),6);
 await ev('document.getElementById("cohort").value="algorithm";renderStats()');assert.ok((await ev('document.getElementById("horizons").textContent')).includes('121/170'));
 await call('Page.captureScreenshot',{format:'png'}).then(async r=>{const fs=await import('node:fs');fs.writeFileSync('/tmp/ma20-v3-report.png',Buffer.from(r.data,'base64'));});
 await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 assert.ok(await ev('document.documentElement.scrollWidth<=window.innerWidth'),'report should fit mobile viewport');
 assert.deepEqual(errors,[]);console.log('PASS V3 browser: counts, blind markers, chart-picked reference, save/export/import, version metadata, same-source review scopes, reference overrides, ETH isolation, report filters, mobile layout');

}finally{ws.close();}
