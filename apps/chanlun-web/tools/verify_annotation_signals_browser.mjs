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
try {
 await call('Runtime.enable');await call('Page.enable');await call('Network.enable');await call('Network.setCacheDisabled',{cacheDisabled:true});
 await call('Page.navigate',{url:origin+'/index.html'});
 await until('window.__INIT_OK===true && typeof AnnotationSignals!=="undefined"');
 await ev('annSetOpen(true)');
 for(const src of ['k2060_v1','k20120_v1','k60120_v1','macd_low_dif_v1','macd_low_hist_v1']){
  await ev(`ANN.source=${JSON.stringify(src)};annBuildSigs();annSourceMenuRender();annGo(0)`);
  assert.ok(await ev('ANN.sigs.length>0'),src+' has signals');
  assert.ok((await ev('document.getElementById("annBody").textContent')).includes('信号判定依据'));
  assert.ok((await ev('document.getElementById("annSelLabel").textContent')).includes(src.startsWith('k')?'阳线同时突破':'底背离候选'));
  const backup=await ev('(()=>{const k=annKey(ANN.sigs[ANN.idx].i);return {key:k,old:ANN.decisions[k]||null}})()');
  await ev('annMark("yes")');
  const out=await ev('annExportData()');const row=out.items.find(x=>x.source===src&&x.decision==='yes');
  assert.ok(row?.signalEvidence);assert.equal(row.signalEvidence.type,src.startsWith('k')?'ma_body_combo_v1':'macd_new_low_divergence_v1');
  if(!src.startsWith('k'))assert.ok(row.signalEvidence.referenceTime<row.features.time);
  await ev(`(()=>{const b=${JSON.stringify(backup)};if(b.old)ANN.decisions[b.key]=b.old;else delete ANN.decisions[b.key];annSave()})()`);
  console.log(src,await ev('ANN.sigs.length'));
 }
 await ev('switchSymbol("ETH")');await until('currentSym==="ETH"');
 assert.ok(await ev('ANN.sigs.length>0'));
 assert.ok((await ev('document.getElementById("annBody").textContent')).includes('MACD柱'));
 assert.deepEqual(errors,[]);console.log('PASS: five sources, evidence cards, separate storage, evidence export and ETH switch');
} finally {ws.close();}
