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
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
 await call('Page.navigate',{url:origin+'/index.html'});await until('window.__INIT_OK===true && typeof AnnotationForm!=="undefined"');
 await ev('annSetOpen(true)');const saved=await ev('JSON.stringify(ANN.decisions)');
 try{
  await ev('ANN.decisions={};annRender();annMark("yes")');assert.equal(await ev('annDecision(ANN.sigs[0].i)?.d||null'),null,'missing form prevents final decision');
  assert.ok((await ev('document.querySelector(".ann-assessment-status").textContent')).includes('请选择'));
  assert.ok((await ev('document.querySelector(".ann-assessment details").textContent')).includes('虚构案例'));
  await ev(`(()=>{const sg=ANN.sigs[0],cs=candlesFor('raw'),a={region:'成立',trigger:'足够强',regime:'趋势回调',confidence:'中',primaryReason:'调整充分且突破有效',depth:'充分',duration:'充分',contraction:'明显收窄',referenceStatus:'可以明确标出',adjustStart:AnnotationForm.stamp(cs[sg.i-5],activeTF),adjustEnd:AnnotationForm.stamp(cs[sg.i-1],activeTF),referencePeak:AnnotationForm.stamp(cs[sg.i-7],activeTF),boxHigh:'5000',boxLow:'2000',preferredEntry:'就是当前根',explanation:'区域成立，等待充分后强势突破。'};for(const [k,v]of Object.entries(a))document.querySelector('[data-assessment="'+k+'"]').value=v;annMark('yes');})()`);
  assert.equal(await ev('annDecision(ANN.sigs[0].i).d'),'yes');
  assert.equal(await ev('annDecision(ANN.sigs[0].i).assessmentEvidence.derived.adjustmentBars'),5);
  let out=await ev('annExportData()');assert.equal(out.items[0].assessmentEvidence.schema,'ma20-assessment/1');assert.equal(out.items[0].assessment.confidence,'中');
  await ev(`ANN.decisions={};annImportData(${JSON.stringify(JSON.stringify(out))})`);
  assert.equal(await ev('document.querySelector("[data-assessment=adjustStart]").value'),out.items[0].assessment.adjustStart);
  assert.equal(await ev('annDecision(ANN.sigs[0].i).assessmentEvidence.derived.adjustmentBars'),5);
  await ev('annGo(1);annMark("skip")');assert.equal(await ev('annDecision(ANN.sigs[1].i).d'),'skip');
  await ev(`(()=>{const e=document.querySelector('[data-assessment="explanation"]');e.value='<img src=x onerror="window.__bad=1">';e.dispatchEvent(new Event('input'));annRender();})()`);
  assert.equal(await ev('document.querySelector("[data-assessment=explanation]").value'),'<img src=x onerror="window.__bad=1">');assert.equal(await ev('window.__bad||null'),null);
  await ev('annGo(0)');
  await call('Page.captureScreenshot',{format:'png'}).then(async r=>{const fs=await import('node:fs');fs.writeFileSync('/tmp/ma20-assessment-form.png',Buffer.from(r.data,'base64'));});
 }finally{await ev(`ANN.decisions=${saved};annSave();`);}
 assert.deepEqual(errors,[]);console.log('PASS form browser: incomplete validation, example, full evidence round-trip, autosave, skip, safe text and record restoration');
}finally{ws.close();}
