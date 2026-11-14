import assert from 'node:assert/strict';
import fs from 'node:fs';import vm from 'node:vm';import{createRequire}from'node:module';
const require=createRequire(import.meta.url), engine=require('../js/13-annotation-signals.js');
const bar=(i,close=100)=>({time:1700000000+i*86400,open:close,high:close+2,low:close-2,close});
const cs=Array.from({length:150},(_,i)=>bar(i));cs[149]={...bar(149),open:90,close:110,high:112,low:88};
for(const pair of [[20,60],[20,120],[60,120]])assert.equal(engine.bodyBreakouts(cs,pair).at(-1).i,149);
const wick=cs.map(c=>({...c}));wick[149]={...wick[149],open:105,close:110};
assert.equal(engine.bodyBreakouts(wick,[20,60]).length,0,'wick crossing without body crossing is not a signal');
wick[149]={...wick[149],open:110,close:90};assert.equal(engine.bodyBreakouts(wick,[20,60]).length,0,'red candle excluded');
const one=Array.from({length:150},(_,i)=>bar(i,i<130?50:100));one[149]={...bar(149),open:80,close:110};
assert.equal(engine.bodyBreakouts(one,[20,60]).length,0,'crossing only MA20 does not count as a pair');

// 底背离：比的是两段负柱各自的【最低价那根】，标记必须落在最低价上。
const mk=path=>{let t=1700000000;return path.map(p=>{const c={time:t,open:p,high:p+1,low:p-1,close:p};t+=86400;return c;});};
const seg=(n,a,b)=>Array.from({length:n},(_,i)=>a+(b-a)*i/(n-1));
const base=seg(80,100,100), down1=seg(12,100,70), up1=seg(14,70,95), up2=seg(16,85,100);
const weak=mk([...base,...down1,...up1,...seg(12,95,85),...up2]);
const steep=mk([...base,...down1,...up1,...seg(12,95,50),...up2]);
const peakSig=engine.peakDivergence(weak);
assert.equal(peakSig.length,1,'histogram-only divergence still fires when price stops above the previous low');
assert.equal(peakSig[0].evidence.type,'macd_peak_divergence_v1');
assert.equal(peakSig[0].i,peakSig[0].evidence.peakIndex+1,'peak variant signals one bar after the histogram extreme');
const sig=engine.bottomDivergence(weak);
assert.equal(sig.length,0,'pullback that stops above the previous low is not a divergence');
const lower=mk([...base,...down1,...up1,...seg(12,95,68),...up2]);
const sig2=engine.bottomDivergence(lower);
assert.equal(sig2.length,1,'new price low plus weaker histogram is a divergence');
const ev=sig2[0].evidence;
assert.equal(ev.type,'macd_low_divergence_v1');
assert.equal(sig2[0].i,ev.lowIndex,'marker sits on the low bar');
assert.equal(lower[ev.lowIndex].low,Math.min(...lower.slice(ev.segment[0],ev.lowIndex+1).map(c=>c.low)),'marker is the segment lowest low');
assert.equal(lower[ev.referenceIndex].low,Math.min(...lower.slice(ev.referenceSegment[0],ev.referenceSegment[1]+1).map(c=>c.low)),'reference is the previous segment lowest low');
assert.ok(ev.lowPrice<ev.referenceLow,'current low undercuts the reference low');
assert.ok(ev.currentHist>ev.referenceHist&&ev.currentHist<0&&ev.referenceHist<0,'both histograms negative and current shallower');
assert.ok(ev.confirmIndex>ev.lowIndex,'confirmation comes after the low');
assert.ok(ev.confirmTime>ev.lowTime);
assert.ok(ev.referenceEndTime<ev.segmentStartTime,'reference band lies entirely before the current one');
assert.ok(ev.strength>0&&ev.strength<1);
assert.equal(engine.bottomDivergence(steep).length,0,'deep second dip is not a divergence');
assert.equal(engine.bottomDivergence(weak,{lowTolerance:0.5}).length>=0,true);
assert.equal(engine.bottomDivergence(lower,{minStrength:0.99}).length,0,'minStrength filters weak divergence');

for(const file of ['data.js','data_eth.js']){
 const ctx={window:{}};vm.runInNewContext(fs.readFileSync(new URL('../data-view/'+file,import.meta.url),'utf8'),ctx);
 const cs=ctx.window.MARKET_DATA.levels['1D'].candles.map(c=>({time:c[0],open:c[1],high:c[2],low:c[3],close:c[4]}));
 const modes=[...[[20,60],[20,120],[60,120]].map(p=>[p.join('+'),c=>engine.bodyBreakouts(c,p)]),['low',c=>engine.bottomDivergence(c)],['peak',c=>engine.peakDivergence(c)]];
 for(const [name,build] of modes){const full=build(cs);for(const n of [250,900,1800,3000])assert.deepEqual(build(cs.slice(0,n)),full.filter(s=>s.i<n),`${file} ${name}: prefix parity`);console.log(file,name,full.length);}
}
console.log('PASS: body vs wick, single vs dual cross, two-low divergence (marker on lowest low), shallow vs deep histogram, BTC/ETH prefix causality');
