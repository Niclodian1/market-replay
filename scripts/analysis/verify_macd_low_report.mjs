// Isolated headless Chrome CDP only; never touches the user's chart or storage.
import assert from 'node:assert/strict';
import {writeFileSync} from 'node:fs';
const port=process.env.CDP_PORT||'9356';
const origin=process.env.REPORT_ORIGIN||'http://127.0.0.1:8896';
const tab=await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,{method:'PUT'})).json();
const ws=new WebSocket(tab.webSocketDebuggerUrl);
await new Promise((ok,bad)=>{ws.onopen=ok;ws.onerror=bad;});
let id=0;const pending=new Map(),errors=[];
ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result);}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
const call=(method,params={})=>new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});ws.send(JSON.stringify({id:n,method,params}));});
const ev=async expression=>{const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
const until=async expr=>{for(let n=0;n<100;n++){if(await ev(expr))return;await new Promise(r=>setTimeout(r,150));}throw Error('timeout '+expr);};
try{
 await call('Runtime.enable');await call('Page.enable');
 await call('Emulation.setDeviceMetricsOverride',{width:1600,height:1050,deviceScaleFactor:1,mobile:false});
 await call('Page.navigate',{url:origin+'/analysis/macd-low-breakeven-2026-11-11.html'});
 await until('document.getElementById("scope")?.textContent.includes("414 个有效入场")');
 assert.match(await ev('document.getElementById("scope").textContent'),/398 个可评估/);
 assert.equal(await ev('document.querySelectorAll("#assets tbody tr").length'),39);
 assert.equal(await ev('document.querySelectorAll("#trades tbody tr").length'),40);
 assert.deepEqual(await ev('summary(current).loss'),[224,226]);
 assert.deepEqual(await ev('summary(current).negative'),[238,243]);
 assert.ok(!(await ev('document.body.textContent')).match(/NaN|undefined/));
 await ev('document.getElementById("next").click()');assert.match(await ev('document.getElementById("pageInfo").textContent'),/第 2/);
 await ev('document.querySelector("#assets tr[data-sym=BTC]").click()');assert.match(await ev('document.getElementById("scope").textContent'),/14 个有效入场/);
 await ev('document.getElementById("sym").value="";document.getElementById("period").value="all";update()');
 assert.match(await ev('document.getElementById("scope").textContent'),/1714 个有效入场/);
 await ev('document.getElementById("period").value="last5y";document.getElementById("horizon").value="120";update()');
 assert.match(await ev('document.getElementById("scope").textContent'),/372 个可评估/);
 for(const tf of ['4H','1H','15F']){
  await ev(`document.getElementById('tf').value=${JSON.stringify(tf)};update()`);
  assert.ok((await ev('current.length'))>100);assert.ok(!(await ev('document.body.textContent')).match(/NaN|undefined/));
 }
 assert.equal(await ev(`(()=>{for(const period of ['all','last5y'])for(const tf of ['1D','4H','1H','15F'])for(const h of ['20','60','120','end']){
 document.getElementById('period').value=period;document.getElementById('tf').value=tf;document.getElementById('horizon').value=h;update();
 const a=summary(current),ss=DATA.series.filter(s=>s.tf===tf).map(s=>s.periods[period].summary[h]);
 if(a.total!==ss.reduce((t,s)=>t+s.n,0)||a.loss.some((n,i)=>n!==ss.reduce((t,s)=>t+(s.loss_stop?.[i]||0),0)))return false;
 }return true;})()`),true,'all aggregate counts agree with source summaries');
 await ev('document.getElementById("market").value="贵金属";update()');assert.equal(await ev('current.length'),0);
 await ev('document.getElementById("market").value="";document.getElementById("tf").value="1D";document.getElementById("horizon").value="60";update();window.scrollTo(0,0)');
 if(process.argv[2]){const r=await call('Page.captureScreenshot',{format:'png'});writeFileSync(process.argv[2],Buffer.from(r.data,'base64'));}
 await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 assert.ok(!(await ev('document.body.textContent')).match(/NaN|undefined/));
 const index=await (await fetch(origin+'/index.html')).text();
 assert.ok(index.includes('href="analysis/macd-low-breakeven-2026-11-11.html"'));
 await call('Page.navigate',{url:new URL('../../apps/chanlun-web/analysis/macd-low-breakeven-2026-11-11.html',import.meta.url).href});
 await until('document.getElementById("scope")?.textContent.includes("414 个有效入场")');
 assert.deepEqual(errors,[]);
 console.log('PASS default / all 32 aggregate cohorts / symbol-click / pagination / all timeframes / empty filter / mobile / menu link / offline file / no JS errors');
}finally{ws.close();}
