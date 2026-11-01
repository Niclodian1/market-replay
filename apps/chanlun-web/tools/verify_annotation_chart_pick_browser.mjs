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
 await call('Emulation.setDeviceMetricsOverride',{width:1600,height:1100,deviceScaleFactor:1,mobile:false});
 await call('Page.navigate',{url:origin+'/index.html'});await until('window.__INIT_OK===true && typeof annStartChartPick==="function"');
 await ev('annSetOpen(true);annGo(25);chart.subscribeClick(p=>window.__pickLastClick={time:p.time,point:p.point})');const saved=await ev('JSON.stringify(ANN.decisions)');
 try{
  await ev('document.querySelector(".ann-reference-details").open=true');
  assert.equal(await ev('document.querySelectorAll("[data-ann-pick]").length'),4);
  for(const [field,offset]of [['adjustStart',10],['adjustEnd',1],['referencePeak',20],['earlierEntry',5]]){
   await ev(`document.querySelector('[data-ann-pick="${field}"]').scrollIntoView({block:'nearest'})`);
   await new Promise(r=>setTimeout(r,150));
   const button=await ev(`(()=>{let b=document.querySelector('[data-ann-pick="${field}"]').getBoundingClientRect();return {x:b.left+b.width/2,y:b.top+b.height/2};})()`);
   await call('Input.dispatchMouseEvent',{type:'mouseMoved',...button});
   await call('Input.dispatchMouseEvent',{type:'mousePressed',...button,button:'left',clickCount:1});
   await call('Input.dispatchMouseEvent',{type:'mouseReleased',...button,button:'left',clickCount:1});
   assert.equal(await ev('ANN_CHART_PICK.target'),field);
   assert.equal(await ev('document.getElementById("annChartPickBanner").hidden'),false);
   await new Promise(r=>setTimeout(r,250));
   const point=await ev(`(()=>{const c=candlesFor('raw')[ANN.sigs[ANN.idx].i-${offset}],b=document.getElementById('chart').getBoundingClientRect();return {x:b.left+chart.timeScale().timeToCoordinate(c.time),y:b.top+b.height*.55};})()`);
   await call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});await new Promise(r=>setTimeout(r,100));
   const adjusted=await ev(`(()=>{const c=candlesFor('raw')[ANN.sigs[ANN.idx].i-${offset}],b=document.getElementById('chart').getBoundingClientRect();return {x:b.left+chart.timeScale().timeToCoordinate(c.time),y:b.top+b.height*.55};})()`);
   await call('Input.dispatchMouseEvent',{type:'mousePressed',...adjusted,button:'left',clickCount:1});await call('Input.dispatchMouseEvent',{type:'mouseReleased',...adjusted,button:'left',clickCount:1});
   await new Promise(r=>setTimeout(r,400));
   if(await ev('ANN_CHART_PICK.target!==null')){
    console.log('PICK DEBUG',field,adjusted,await ev('({last:window.__pickLastClick,rect:document.getElementById("chart").getBoundingClientRect().toJSON(),range:chart.timeScale().getVisibleLogicalRange(),status:document.getElementById("annChartPickBanner").textContent})'));
    throw Error('Chart click not received for '+field);
   }
   assert.equal(await ev(`annDecision(ANN.sigs[ANN.idx].i).assessmentEvidence.anchors.${field}.time`),await ev(`candlesFor('raw')[ANN.sigs[ANN.idx].i-${offset}].time`));
   assert.equal(await ev('document.getElementById("annChartPickBanner").hidden'),true);
  }
  await ev('annMark("skip")');let rec=(await ev('annExportData()')).items.find(r=>r.source==='ma20_simple_v2'&&r.assessmentEvidence?.anchors?.adjustStart);
  assert.ok(rec);assert.equal(rec.assessmentEvidence.derived.adjustmentBars,10);
  await ev('annStartChartPick("adjustEnd");annHandleChartPick({time:candlesFor("raw")[ANN.sigs[ANN.idx].i+1].time})');
  assert.equal(await ev('ANN_CHART_PICK.target'),'adjustEnd');assert.ok((await ev('document.getElementById("annChartPickBanner").textContent')).includes('之后'));
  await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape'});assert.equal(await ev('ANN_CHART_PICK.target'),null);assert.equal(await ev('ANN.on'),true);
  await ev('annStartChartPick("adjustStart");annGo(26)');assert.equal(await ev('ANN_CHART_PICK.target'),null);assert.equal(await ev('ANN_CHART_PICK.selected'),null);
  await ev('annStartChartPick("adjustStart");switchSymbol("ETH")');await until('currentSym==="ETH"');assert.equal(await ev('ANN_CHART_PICK.target'),null);
  await ev('setView("merged");annStartChartPick("referencePeak")');assert.equal(await ev('activeView'),'raw');
  await ev('document.getElementById("annChartPickBanner").querySelector("button").click()');assert.equal(await ev('ANN_CHART_PICK.target'),null);
  await ev('annStartChartPick("referencePeak");annSetOpen(false)');assert.equal(await ev('document.getElementById("annChartPickBanner").hidden'),true);
 }finally{await ev(`ANN.decisions=${saved};annSave();`);}
 assert.deepEqual(errors,[]);console.log('PASS chart date picking: four real chart clicks, exact stored anchors, range derivation/export, future rejection, Esc, cancel, navigation/asset cleanup and merged-to-raw precision');
}finally{ws.close();}
