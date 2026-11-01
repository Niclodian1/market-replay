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
 await call('Runtime.enable'); await call('Page.enable');
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await call('Page.navigate',{url:origin+'/index.html'});
 await until('window.__INIT_OK===true && typeof cover67VolumeOpen==="function"');
 await new Promise(r=>setTimeout(r,1200));
 await ev('annSetOpen(false);cover67IndicatorEnable(true)');
 assert.equal(await ev('new Set(["high","normal","low"].map(cover67GradeColor)).size'),3);
 for(const grade of ['high','normal','low']){
  await ev(`(() => { const r=annCoverContext().signals.find(s=>s.grade===${JSON.stringify(grade)});COVER67_INDICATOR.time=r.time;cover67IndicatorRefresh();cover67VolumeOpen(r.time); })()`);
  assert.equal(await ev('cover67IndicatorMarkers(activeView).find(m=>m.time===COVER67_INDICATOR.time).color'),await ev(`cover67GradeColor(${JSON.stringify(grade)})`));
  assert.ok((await ev('document.getElementById("cover67VolumePopup").textContent')).includes('不含本根'));
 }
 await ev('cover67IndicatorSelect(25);cover67VolumeClose()');
 await new Promise(r=>setTimeout(r,200));
 const point=await ev('(() => {const r=COVER67_INDICATOR.rows[25],c=candlesFor("raw")[r.i],b=document.getElementById("chart").getBoundingClientRect();return {x:b.left+chart.timeScale().timeToCoordinate(r.time),y:b.top+candleSeries.priceToCoordinate(c.close)};})()');
 await call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});
 await new Promise(r=>setTimeout(r,100));
 const updatedPoint=await ev('(() => {const r=COVER67_INDICATOR.rows[25],c=candlesFor("raw")[r.i],b=document.getElementById("chart").getBoundingClientRect();return {x:b.left+chart.timeScale().timeToCoordinate(r.time),y:b.top+candleSeries.priceToCoordinate(c.close)};})()');
 await call('Input.dispatchMouseEvent',{type:'mouseMoved',...updatedPoint});
 await call('Input.dispatchMouseEvent',{type:'mousePressed',...updatedPoint,button:'left',clickCount:1});
 await call('Input.dispatchMouseEvent',{type:'mouseReleased',...updatedPoint,button:'left',clickCount:1});
 await new Promise(r=>setTimeout(r,300));
 await until('document.getElementById("cover67VolumePopup").hidden===false');
 await ev('document.getElementById("cover67VolumeClose").click()');
 assert.equal(await ev('document.getElementById("cover67VolumePopup").hidden'),true);
 await ev('cover67VolumeOpen(COVER67_INDICATOR.rows[25].time);switchSymbol("ETH")');await until('currentSym==="ETH"');
 assert.equal(await ev('document.getElementById("cover67VolumePopup").hidden'),true,'asset switch dismisses stale card');
 await ev('annSetOpen(true)');
 assert.equal(await ev('document.querySelector("#annBody .cover67-history")'),null);
 assert.ok(!(await ev('document.getElementById("annBody").textContent')).includes('量比 20'));
 await ev('document.getElementById("cover67VolumeOnChart").click()');
 assert.ok((await ev('document.getElementById("cover67VolumePopup").textContent')).includes('截至当前标注之前'));
 await ev('cover67IndicatorEnable(false)');
 assert.equal(await ev('document.getElementById("cover67VolumePopup").hidden'),true);
 assert.deepEqual(errors,[]);console.log('PASS: grade colors, formula, real chart click, close, asset cleanup, sidebar removal and blind statistics');
} finally {ws.close();}
