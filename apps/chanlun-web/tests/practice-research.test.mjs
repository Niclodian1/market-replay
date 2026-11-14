import test from 'node:test';
import assert from 'node:assert/strict';
import Core from '../js/06-practice-core.js';

test('盘中同时止损与冲高，MFE 使用确定值和可能值，不纳入止损后的涨幅', () => {
  const bars=[[1,99,114,95,100],[2,103,108,94,96],[3,96,180,95,170]];
  for(const p of Core.outcomes(bars,1,100,95,'多')){
    assert.deepEqual(p.mfePct,[3,8]);
    assert.deepEqual(p.mfeR,[0.6,1.6]);
    assert.equal(p.stopBars,1);
    assert.equal(p.simulatedStopFill,95);
    assert.equal(p.ambiguous,true);
  }
});

test('跳空穿越空头止损按开盘结算，入场根和跳空后的低点不计浮盈',()=>{
  const bars=[[1,100,105,85,100],[2,109,110,81,100]];
  const [p]=Core.outcomes(bars,1,100,105,'空');
  assert.equal(p.simulatedStopFill,109);
  assert.deepEqual(p.mfePct,[0,0]);
  assert.equal(p.ambiguous,false);
});

test('窗口不足标为未完成；精确触及初始止损按触发处理',()=>{
  const bars=[[1,99,101,95,100],[2,100,111,97,107]];
  assert.equal(Core.outcomes(bars,1,100,95,'多')[0].resolved,false);
  bars.push([3,106,116,95,104]);
  const [p]=Core.outcomes(bars,1,100,95,'多');
  assert.equal(p.stopBars,2);
  assert.equal(p.resolved,true);
  assert.deepEqual(p.targets[1],{r:3,certain:false,possible:true});
  assert.deepEqual(Core.outcomes(bars,1,100,null,'多'),[]);
});

test('所有特征只使用决策当根及以前，量比分母不含信号根',()=>{
  const bars=Array.from({length:150},(_,i)=>[i,100+i,102+i,99+i,101+i,10]);
  bars[130][5]=30;
  const a=Core.features(bars,130,{start:119,end:128});
  bars[131]=[131,1000,3000,1,2000,1000000];
  const b=Core.features(bars,130,{start:119,end:128});
  assert.deepEqual(a,b);
  assert.equal(a.volumeRatio20,3);
  assert.equal(a.range.bars,10);
  assert.equal(a.ma[120].value,171.5);
  assert.equal(Core.features(bars,130,{start:129,end:131}).range,null);
});

test('合并同一持仓的分批退出，保留未完整结束的持仓',()=>{
  const base={symbol:'BTC/USD',tf:'1D',entry_ts:10,side:'多'};
  const out=Core.positions([
    {...base,pid:1,partial:true,ratio:.5,pnl_u:20,capital:50},
    {...base,pid:1,partial:false,ratio:1,pnl_u:-10,capital:50},
    {...base,pid:2,partial:true,ratio:.5,pnl_u:6,capital:25},
  ]);
  assert.deepEqual(out.map(p=>[p.pnl,p.capital,p.rows,p.closed]),[[10,100,2,true],[6,25,1,false]]);
});

test('等待接续只形成一个机会，批次按来源隔离并按入场位置去重',()=>{
  const d={id:'1',opportunityId:'1',choice:'wait',barTime:20,side:'多'};
  const buy={...d,id:'2',choice:'buy',barTime:21};
  const s={sym:'BTC',tf:'1D',ts:1,cond:'完成',research:{cohort:'random',rule:'MA20-v1',decisions:[d,buy]}};
  assert.deepEqual(Core.opportunityRows(s),[buy]);
  const repeat={...s,ts:2,research:{...s.research,cohort:'repeat'}};
  assert.equal(Core.batchRows([s,{...s,ts:3},repeat],20,{cohort:'random'}).length,1);
  assert.equal(Core.batchRows([s,repeat],20,{cohort:'repeat'}).length,1);
});
