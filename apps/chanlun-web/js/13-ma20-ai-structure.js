/* Seven causal context features. Anchors are historical proxies, never hand labels. */
(function(root){
'use strict';
const base=typeof module==='object'&&module.exports?require('./13-ma20-ai-engine.js'):root.MA20AI;
const EXTRA=['rebound60','precedingAdvance','priceRetracement','adjustmentTimeRatio','adjustmentContraction','difATR','baseToDeclineTime'];
const LABELS=['距此前60根低点涨幅','前段上涨幅度','回撤/前段上涨幅度','调整时间/前段上涨时间','调整后半/前半波幅','DIF/ATR','低点后整理/此前下跌时间'];
function compute(candles,volumes=[]){
 const cs=candles.map(c=>Array.isArray(c)?{time:c[0],open:c[1],high:c[2],low:c[3],close:c[4]}:c),d=base.compute(cs,volumes),tr=[];let e12=null,e26=null;
 const div=(a,b)=>Number.isFinite(a)&&Number.isFinite(b)&&b!==0?a/b:null;
 const avg=a=>a.length?a.reduce((s,v)=>s+v,0)/a.length:null;
 const extreme=(start,end,field,high)=>{let k=start;for(let j=start+1;j<end;j++)if(high?cs[j][field]>=cs[k][field]:cs[j][field]<=cs[k][field])k=j;return k;};
 const rows=cs.map((c,i)=>{
  tr.push(i?Math.max(c.high-c.low,Math.abs(c.high-cs[i-1].close),Math.abs(c.low-cs[i-1].close)):c.high-c.low);
  e12=e12===null?c.close:e12+2/13*(c.close-e12);e26=e26===null?c.close:e26+2/27*(c.close-e26);
  const atr=i>=13?avg(tr.slice(i-13,i+1)):null;
  let extra=Array(7).fill(null),anchors=null;
  if(i>=60){
   const p=extreme(i-60,i,'high',true),low60=extreme(i-60,i,'low',false);
   const a=p>0?extreme(Math.max(0,p-120),p,'low',false):null;
   const b=p+1<i?extreme(p+1,i,'low',false):null;
   let contraction=null;
   const segment=tr.slice(p+1,i);if(segment.length>=6){const half=Math.floor(segment.length/2);contraction=div(avg(segment.slice(-half)),avg(segment.slice(0,half)));}
   extra=[div(c.close,cs[low60].low)-1,a===null?null:div(cs[p].high,cs[a].low)-1,
    a===null?null:div(cs[p].high-c.close,cs[p].high-cs[a].low),a===null?null:div(i-p,p-a),contraction,div(e12-e26,atr),b===null?null:div(i-b,b-p)];
   anchors={peak:{i:p,time:cs[p].time,price:cs[p].high},advanceLow:a===null?null:{i:a,time:cs[a].time,price:cs[a].low},recentLow:{i:low60,time:cs[low60].time,price:cs[low60].low},postPeakLow:b===null?null:{i:b,time:cs[b].time,price:cs[b].low},adjustStart:p+1<i?cs[p+1].time:null,adjustEnd:i?cs[i-1].time:null};
  }
  return {...d.rows[i],values:[...d.rows[i].values,...extra.map(v=>Number.isFinite(v)?v:null)],anchors};
 });
 return {version:'ma20-ai-features/2',names:[...base.NAMES,...EXTRA],labels:[...base.LABELS,...LABELS],rows};
}
function explain(row,model){if(model.type!=='logistic')return [];const x=base.vector(row,model),names=[...base.NAMES,...EXTRA],labels=[...base.LABELS,...LABELS];return x.map((v,j)=>{const k=model.indices?model.indices[j]:j;return {name:names[k],label:labels[k],contribution:v*model.coefficients[j],value:row.values[k]};}).sort((a,b)=>Math.abs(b.contribution)-Math.abs(a.contribution)).slice(0,3);}
const api={VERSION:'ma20-ai-features/2',NAMES:[...base.NAMES,...EXTRA],LABELS:[...base.LABELS,...LABELS],compute,vector:base.vector,score:base.score,raw:base.raw,explain};if(typeof module==='object'&&module.exports)module.exports=api;else root.MA20AIStructure=api;
})(typeof globalThis==='object'?globalThis:this);
