import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {inspect}=require('../js/13-manual-evidence.js');
const candles=Array.from({length:40},(_,i)=>({time:Date.UTC(2025,0,i+1)/1000,open:100+i,high:103+i,low:98+i,close:102+i}));
const volumes=candles.map((_,i)=>i+10);
const free={reason:'价格突破我观察的压力位，在当根收盘买入',pattern:'free'};
const range={...free,pattern:'range',rangeStart:'2025-01-11',rangeEnd:'2025-01-20'};

test('任意K线均可作正样本，但必须给出买入理由，成交价固定为所选收盘价',()=>{
  assert.deepEqual(inspect(free,candles,volumes,30,'1D').errors,[]);
  assert.equal(inspect(free,candles,volumes,30,'1D').evidence.execution.price,candles[30].close);
  assert.equal(inspect(free,candles,volumes,30,'1D').evidence.execution.basis,'selected_bar_close');
  assert.ok(inspect({...free,reason:'  '},candles,volumes,30,'1D').errors.length);
  assert.equal(inspect(free,candles,volumes,0,'1D').evidence.volume.vsPrevious20,null);
});
test('收敛／盘整要求明确的真实区间，不能包含突破当根或未来',()=>{
  assert.deepEqual(inspect(range,candles,volumes,30,'1D').errors,[]);
  assert.ok(inspect({...free,pattern:'contraction'},candles,volumes,30,'1D').errors.length);
  for(const rangeEnd of ['2025-01-31','2025-02-01','2025-01-99']){
    assert.ok(inspect({...range,rangeEnd},candles,volumes,30,'1D').errors.length);
  }
  assert.ok(inspect({...range,rangeStart:'2025-01-22'},candles,volumes,30,'1D').errors.some(e=>e.includes('晚于')));
});
test('量比分母排除买入当根；区间均量、前后段波动按所选范围计算',()=>{
  const {evidence:e}=inspect(range,candles,volumes,30,'1D');
  assert.equal(e.volume.previous20Mean,29.5);
  assert.equal(e.volume.vsPrevious20,40/29.5);
  assert.equal(e.interval.bars,10);
  assert.equal(e.interval.volumeMean,24.5);
  assert.equal(e.interval.breakoutVolumeRatio,40/24.5);
  assert.equal(e.interval.comparisonBars,5);
  assert.equal(e.interval.lastVsFirstTrueRange,1);
  assert.equal(e.interval.lastVsFirstVolume,27/22);
});
test('完整后续数据或极端未来价格与量能不会改变决策证据',()=>{
  const prefix=inspect(range,candles.slice(0,31),volumes.slice(0,31),30,'1D');
  const future=candles.map((c,i)=>i<=30?c:{...c,high:1e9,close:1e9});
  const futureVolumes=volumes.map((v,i)=>i<=30?v:1e12);
  assert.deepEqual(inspect(range,future,futureVolumes,30,'1D'),prefix);
  assert.equal('forward' in prefix.evidence,false);
});
test('未知／负成交量不补零，真实零成交量保留；日内锚点精确到UTC时分',()=>{
  const missing=[...volumes];missing[12]=null;
  assert.equal(inspect(range,candles,missing,30,'1D').evidence.volume.vsPrevious20,null);
  missing[12]=-10;
  assert.equal(inspect(range,candles,missing,30,'1D').evidence.volume.vsPrevious20,null);
  const zero=[...volumes];zero[30]=0;
  assert.equal(inspect(free,candles,zero,30,'1D').evidence.volume.vsPrevious20,0);
  const hourly=candles.map((c,i)=>({...c,time:Date.UTC(2025,0,1,i*4)/1000}));
  assert.deepEqual(inspect({...range,rangeStart:'2025-01-02 16:00',rangeEnd:'2025-01-04 04:00'},hourly,volumes,30,'4H').errors,[]);
});
