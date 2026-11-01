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
const lows=Array.from({length:110},(_,i)=>bar(i));lows[40].low=80;lows[100].low=75;
const dif=Array(110).fill(-6),hist=Array(110).fill(-3);dif[40]=-10;dif[100]=-5;hist[40]=-4;hist[100]=-2;
for(const metric of ['dif','hist']){
 const result=engine.detectDivergence(lows,{dif,hist},metric);
 assert.equal(result.length,1);assert.equal(result[0].i,100);assert.equal(result[0].evidence.referenceIndex,40);
 const falling={dif:[...dif],hist:[...hist]};falling[metric][100]=-20;
 assert.equal(engine.detectDivergence(lows,falling,metric).length,0,'stronger downward momentum excluded');
}
const equal=lows.map(c=>({...c}));equal[100].low=80;assert.equal(engine.detectDivergence(equal,{dif,hist},'dif').length,0,'equal low excluded');
const adjacent=lows.map(c=>({...c}));adjacent[99].low=76;assert.ok(!engine.detectDivergence(adjacent,{dif,hist},'dif').some(s=>s.i===100),'adjacent low at100 excluded; earlier new low99 remains valid');
for(const file of ['data.js','data_eth.js']){
 const ctx={window:{}};vm.runInNewContext(fs.readFileSync(new URL('../data-view/'+file,import.meta.url),'utf8'),ctx);
 const cs=ctx.window.MARKET_DATA.levels['1D'].candles.map(c=>({time:c[0],open:c[1],high:c[2],low:c[3],close:c[4]}));
 const modes=[...[[20,60],[20,120],[60,120]].map(p=>[p.join('+'),c=>engine.bodyBreakouts(c,p)]),...['dif','hist'].map(m=>[m,c=>engine.divergence(c,m)])];
 for(const [name,build] of modes){const full=build(cs);for(const n of [250,900,1800,3000])assert.deepEqual(build(cs.slice(0,n)),full.filter(s=>s.i<n),`${file} ${name}: prefix parity`);console.log(file,name,full.length);}
}
console.log('PASS: body vs wick, single vs dual cross, lower/equal/adjacent lows, weaker/stronger MACD, BTC/ETH prefix causality');
