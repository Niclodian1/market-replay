import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url), engine=require('../js/13-cover67-legacy.js');
const base=new URL('../',import.meta.url), golden=JSON.parse(fs.readFileSync(new URL('fixtures/cover67-golden.json',import.meta.url)));
assert.equal(engine.VERSION,golden.version);assert.deepEqual(engine.RULES,golden.rules);
for(const [sym,file] of [['BTC','data.js'],['ETH','data_eth.js']]){
 const ctx={window:{}};vm.runInNewContext(fs.readFileSync(new URL('data-view/'+file,base),'utf8'),ctx);
 const l=ctx.window.MARKET_DATA.levels['1D'];
 const cs=l.candles.map(c=>({time:c[0],open:c[1],high:c[2],low:c[3],close:c[4]}));
 const result=engine.compute(cs,l.volume),g=golden[sym];
 assert.deepEqual(result.signals.filter(s=>s.time<=g.end).map(s=>s.time),g.times,`${sym}: Python signal parity`);
 assert.equal(result.crosses.length,g.crosses);
 for(const expected of g.sample)for(const [k,v] of Object.entries(expected))if(k!=='i')assert.ok(Math.abs(result.rows[expected.i][k]-v)<1e-8,`${sym} ${expected.i} ${k}`);
 for(const end of [400,1500,2500,cs.length]){
  const prefix=engine.compute(cs.slice(0,end),l.volume.slice(0,end));
  assert.deepEqual(prefix.signals.map(s=>s.time),result.signals.filter(s=>s.i<end).map(s=>s.time));
 }
 const st=engine.stats(result.trades);
 if(sym==='BTC'){assert.equal(result.signals.length,70);assert.equal(st.n,69);assert.equal(st.wins,26);assert.equal(st.pending,1);}
 const first=result.signals[0].i;assert.equal(engine.stats(result.trades,first).n,0,'no future outcomes in blind history');
 for(const grade of ['high','normal','low']){
  const s=engine.stats(result.trades,Infinity,grade);
  if(s.n&&s.avgWin!==null&&s.avgLoss!==null)assert.ok(Math.abs(s.mean-(s.winRate*s.avgWin+(1-s.winRate)*s.avgLoss))<1e-12);
 }
 console.log(`${sym}: ${result.signals.length} signals, ${st.n} completed, ${(st.winRate*100).toFixed(1)}% net win; parity/prefix/history passed`);
}
assert.equal(engine.grade(.9),'normal');assert.equal(engine.grade(1.2),'high');assert.equal(engine.grade(NaN),'unknown');
