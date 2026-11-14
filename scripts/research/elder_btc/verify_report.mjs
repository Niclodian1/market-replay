// Isolated CDP profile only; downloads intercepted in-memory, no user's storage touched.
import assert from 'node:assert/strict';
import {writeFileSync} from 'node:fs';
const port=process.env.CDP_PORT||'9357',origin=process.env.REPORT_ORIGIN||'http://127.0.0.1:8897';
const tab=await(await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,{method:'PUT'})).json();
const ws=new WebSocket(tab.webSocketDebuggerUrl);await new Promise((ok,bad)=>{ws.onopen=ok;ws.onerror=bad;});
let id=0;const pending=new Map(),errors=[];
ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result);}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const call=(method,params={})=>new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});ws.send(JSON.stringify({id:n,method,params}));});
const ev=async expression=>{const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
const until=async expr=>{for(let n=0;n<150;n++){if(await ev(expr))return;await new Promise(r=>setTimeout(r,200));}throw Error('timeout '+expr);};
try{
 await call('Runtime.enable');await call('Page.enable');await call('Emulation.setDeviceMetricsOverride',{width:1600,height:1080,deviceScaleFactor:1,mobile:false});
 await call('Page.navigate',{url:origin+'/analysis/elder-btc-1d-2026-11-12.html'});
 await until('document.querySelectorAll("#cards .metric").length===8 && document.getElementById("chart")._fullLayout!=null');
 assert.equal(await ev('document.getElementById("model").value'),'impulse_direct');
 assert.match(await ev('document.getElementById("scope").textContent'),/671 个自然日/);
 assert.match(await ev('document.getElementById("cards").textContent'),/0.62%/);
 assert.equal(await ev('document.querySelectorAll("#models tbody tr").length'),28);
 assert.equal(await ev('document.querySelectorAll("#contrasts tbody tr").length'),16);
 assert.equal(await ev('document.querySelectorAll("#trades tbody tr").length'),30);
 await ev('document.getElementById("next").click()');assert.match(await ev('document.getElementById("page").textContent'),/2 \/ 3/);
 await ev('document.querySelector("tr[data-name=value_impulse]").click()');assert.match(await ev('document.getElementById("cards").textContent'),/6.85%/);
 await ev('document.getElementById("segment").value="train";update()');assert.match(await ev('document.getElementById("scope").textContent'),/1763 个自然日/);
 await ev('document.getElementById("group").value="primary";renderModels()');assert.equal(await ev('document.querySelectorAll("#models tbody tr").length'),12);
 await ev('document.getElementById("group").value="reference";renderModels()');assert.equal(await ev('document.querySelectorAll("#models tbody tr").length'),1);
 await ev('document.getElementById("segment").value="test";document.getElementById("model").value="kangaroo";update()');assert.match(await ev('document.getElementById("page").textContent'),/0 笔/);
 assert.ok(!(await ev('document.getElementById("cards").textContent')).match(/NaN|undefined/));
 await ev('window.__blobs=[];URL.createObjectURL=b=>{window.__blobs.push(b);return "blob:test"};URL.revokeObjectURL=()=>{};HTMLAnchorElement.prototype.click=function(){window.__downloadName=this.download};document.querySelector("[data-dl=code]").click()');
 assert.equal(await ev('window.__downloadName'),'final_strategy.py');assert.ok((await ev('window.__blobs[0].text()')).includes('FROZEN_CONFIG='));
 const index=await(await fetch(origin+'/index.html')).text();assert.ok(index.includes('href="analysis/elder-btc-1d-2026-11-12.html"'));
 await ev('document.getElementById("model").value="impulse_direct";document.getElementById("group").value="all";update();window.scrollTo(0,0)');
 if(process.argv[2]){const shot=await call('Page.captureScreenshot',{format:'png'});writeFileSync(process.argv[2],Buffer.from(shot.data,'base64'));}
 await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await ev('window.dispatchEvent(new Event("resize"))');
 assert.ok(await ev('document.getElementById("chart")._fullLayout!=null'));
 await call('Page.navigate',{url:new URL('../../../apps/chanlun-web/analysis/elder-btc-1d-2026-11-12.html',import.meta.url).href});
 await until('document.querySelectorAll("#cards .metric").length===8');assert.deepEqual(errors,[]);
 console.log('PASS default metrics, Plotly, 28 configs, 16 contrasts, model/period filters, empty sample, pagination, downloads, menu, mobile, offline loading, no JS errors');
}finally{ws.close();}
