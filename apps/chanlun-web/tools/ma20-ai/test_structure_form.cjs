const assert=require('assert/strict'),F=require('../../js/13-annotation-form.js');
const cs=Array.from({length:7},(_,i)=>({time:1577836800+86400*i,open:100,high:110,low:90,close:100}));cs[0].low=80;cs[2].high=120;
const valid=Object.fromEntries(Object.entries(F.FIELDS).map(([k,v])=>[k,v[1]]));valid.referenceStatus='不确定';valid.preferredEntry='就是当前根';valid.advanceLow='2020-01-01';valid.advancePeak='2020-01-03';
let r=F.inspect(valid,cs,5,'1D');assert.equal(r.evidence.derived.advanceBars,2);assert.equal(r.evidence.derived.precedingAdvancePct,.5);assert.equal(r.evidence.derived.priceRetracementFraction,.5);assert.equal(r.evidence.derived.adjustmentTimeRatio,1.5);
r=F.inspect({...valid,advancePeak:'2020-01-07'},cs,5,'1D');assert.ok(r.errors.some(e=>e.includes('不能在当前信号之后')));assert.equal(r.evidence.derived.advanceBars,undefined);
r=F.inspect({...valid,advanceLow:'2020-01-04'},cs,5,'1D');assert.ok(r.errors.some(e=>e.includes('起点必须早于终点')));
console.log('PASS optional advance anchors: known price/time ratios, future guard, chronological ordering');
