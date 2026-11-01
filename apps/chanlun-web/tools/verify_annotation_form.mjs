import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),f=require('../js/13-annotation-form.js');
const cs=Array.from({length:10},(_,i)=>({time:Date.UTC(2025,0,i+1)/1000,open:100+i,high:110+i,low:90+i,close:105+i}));
const base={region:'成立',trigger:'足够强',regime:'趋势回调',confidence:'中',primaryReason:'调整充分且突破有效',depth:'充分',duration:'充分',contraction:'明显收窄',referenceStatus:'可以明确标出',adjustStart:'2025-01-02',adjustEnd:'2025-01-04',referencePeak:'2025-01-01',boxHigh:'120',boxLow:'90'};
let r=f.inspect(base,cs,6,'1D');assert.deepEqual(r.errors,[]);assert.equal(r.evidence.derived.adjustmentBars,3);assert.equal(r.evidence.derived.adjustmentHigh,113);assert.equal(r.evidence.derived.adjustmentLow,91);assert.equal(r.evidence.anchors.adjustStart.time,cs[1].time);
assert.ok(f.inspect({...base,adjustEnd:'2025-01-08'},cs,6,'1D').errors.some(e=>e.includes('之后')));
assert.ok(f.inspect({...base,adjustStart:'2025-01-05'},cs,6,'1D').errors.some(e=>e.includes('晚于')));
assert.ok(f.inspect({...base,boxLow:'130'},cs,6,'1D').errors.some(e=>e.includes('上沿')));
assert.ok(f.inspect({...base,adjustEnd:'2025-01-99'},cs,6,'1D').errors.some(e=>e.includes('实际K线')));
assert.ok(f.inspect({...base,primaryReason:'其他',explanation:''},cs,6,'1D').errors.some(e=>e.includes('说明')));
const uncertain={...base,referenceStatus:'不确定',adjustStart:'',adjustEnd:'',referencePeak:'',boxHigh:'',boxLow:''};assert.deepEqual(f.inspect(uncertain,cs,6,'1D').errors,[]);
assert.ok(f.inspect({...base,preferredEntry:'此前已有更好买点'},cs,6,'1D').errors.some(e=>e.includes('此前更好')));
const intraday=cs.map(c=>({...c,time:c.time+8*3600}));r=f.inspect({...uncertain,referencePeak:'2025-01-01 08:00'},intraday,6,'4H');assert.deepEqual(r.errors,[]);assert.equal(r.evidence.anchors.referencePeak.time,intraday[0].time);
console.log('PASS structured assessment: required labels, uncertain references, actual UTC bars, no future anchors, ordered range, box bounds, intraday timestamps and derived evidence');

const duplicate=[...cs,{...cs[6],time:cs[6].time+16*3600}];
assert.ok(f.inspect({...uncertain,referencePeak:'2025-01-07'},duplicate,10,'1D').errors.some(e=>e.includes('多根')));
assert.equal(f.inspect({...uncertain,referencePeak:'2025-01-07 16:00'},duplicate,10,'1D').evidence.anchors.referencePeak.time,duplicate[10].time);
