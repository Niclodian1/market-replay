import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),engine=require('../js/13-ma20-structure.js');
const read=s=>{const t=fs.readFileSync(new URL('../data-view/'+(s==='BTC'?'data.js':'data_eth.js'),import.meta.url),'utf8');return JSON.parse(t.slice(t.indexOf('{')).trim().replace(/;$/,'')).levels['1D'];};
const expected=JSON.parse(fs.readFileSync(new URL('../../../reports/ma20-new-review-2026-11-03/engine_expected.json',import.meta.url),'utf8'));
for(const sym of ['BTC','ETH']){
 const d=read(sym),full=engine.compute(d.candles,d.volume);
 assert.deepEqual(full.signals.map(r=>r.time),expected[sym].times,`${sym} independent Python parity`);
 assert.deepEqual(full.signals.map(r=>r.family),expected[sym].family);
 // Every signal must be the same using data available at its own close.
 for(const r of full.signals){const prefix=engine.compute(d.candles.slice(0,r.i+1),d.volume.slice(0,r.i+1));assert.deepEqual(prefix.rows.at(-1),r,`${sym} prefix ${r.i}`);}
 const altered=engine.compute(d.candles,d.volume.map(v=>v*99));
 assert.deepEqual(altered.signals.map(r=>r.time),expected[sym].times,'volume is not a gate');
 console.log(`${sym}: ${full.signals.length} signals, Python parity and prefix invariance PASS`);
}
assert.equal(engine.compute([]).signals.length,0);
assert.equal(engine.compute(Array.from({length:30},(_,i)=>[i,100,100,100,100])).signals.length,0,'flat zero ATR');
assert.equal(engine.compute(read('BTC').candles).rows.at(-1).volRatio,null,'no volume is unknown');
console.log('PASS MA20 V3 engine');
