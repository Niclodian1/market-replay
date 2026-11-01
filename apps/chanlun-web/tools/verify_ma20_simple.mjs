import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),engine=require('../js/13-cover67.js');
assert.equal(engine.VERSION,'MA20-SIMPLE-V2-2026-10-30');assert.equal(engine.RULES.length,0);
for(const [symbol,file,n,counts] of [['BTC','data.js',181,[57,71,53]],['ETH','data_eth.js',170,[51,76,43]]]){
 const context={window:{}};vm.runInNewContext(fs.readFileSync(new URL('../data-view/'+file,import.meta.url),'utf8'),context);
 const l=context.window.MARKET_DATA.levels['1D'];const cs=l.candles.map(c=>({time:c[0],open:c[1],high:c[2],low:c[3],close:c[4]}));
 const result=engine.compute(cs,l.volume);assert.equal(result.signals.length,n);
 const oracle=[];
 const avg=(i,n,field)=>cs.slice(i-n+1,i+1).reduce((a,c)=>a+c[field],0)/n;
 for(let i=20;i<cs.length;i++)if(cs[i-1].close<=avg(i-1,20,'close')&&cs[i].close>avg(i,20,'close')&&cs[i].close>cs[i].open)oracle.push(cs[i].time);
 assert.deepEqual(result.signals.map(r=>r.time),oracle);
 for(let g=0;g<=2;g++)assert.equal(result.signals.filter(r=>r.confirmationCount===g).length,counts[g]);
 for(const end of [300,900,2200,3000]){
  const prefix=engine.compute(cs.slice(0,end),l.volume.slice(0,end));
  assert.deepEqual(prefix.signals,result.signals.filter(r=>r.i<end),'causal candidate and context parity');
 }
 for(const row of result.signals){
  assert.equal(row.selected,true);assert.deepEqual(row.excluded,[]);
  assert.equal(row.confirmationCount,Number(row.body_atr>=.5)+Number(row.volratio>=1.2));
 }
 for(const grade of ['0','1','2']){
  const t=result.trades.filter(t=>t.strengthGrade===grade&&t.status==='closed');
  const s=engine.stats(result.trades,Infinity,grade,'strengthGrade');assert.equal(s.n,t.length);
 }
 assert.equal(engine.stats(result.trades,result.signals[0].i).n,0);
 if(symbol==='ETH')assert.equal(result.signals.find(r=>new Date(r.time*1000).toISOString().slice(0,10)==='2025-07-02').confirmationCount,2);
 const unavailable=engine.compute(cs,[]); // absent volume must not exclude price candidates
 assert.equal(unavailable.signals.length,n);assert.ok(unavailable.signals.every(r=>r.confirmationCount===null));
 console.log(symbol,n,'candidates: independent trigger, confirmation buckets, causal contexts, missing volume and blind statistics passed');
}
