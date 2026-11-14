/* Causal feature extraction shared by offline training and browser inference. */
(function(root){
'use strict';
const VERSION='ma20-ai-features/1';
const NAMES=['bodyATR','closePosition','upperWickATR','riskATR','riskPct','distance20ATR','distance60ATR','slope20ATR','slope60ATR','drawdown60','position120','trContraction5_20','trContraction20_60','volumeRatio','volumeContraction','crosses20','below20Fraction','barsSincePeak60','efficiency20','rsi14','macdHistATR','histChange3ATR','range20ATR','range60ATR','return20ATR','negativeEnergyChange'];
const LABELS=['实体/ATR','收盘位置','上影线/ATR','风险距离/ATR','初始风险比例','距MA20/ATR','距MA60/ATR','MA20斜率','MA60斜率','距此前60根高点回撤','120根区间位置','前5/20根波幅比','前20/60根波幅比','本根量比','前5/20根量比','此前20根上穿次数','此前20根均线下方比例','距此前60根高点根数','20根走势效率','RSI14','MACD柱/ATR','MACD柱3根变化','此前20根区间/ATR','此前60根区间/ATR','20根涨跌/ATR','下跌动能消化'];
function compute(candles,volumes=[]){
 const cs=candles.map(c=>Array.isArray(c)?{time:c[0],open:c[1],high:c[2],low:c[3],close:c[4]}:c),c=cs.map(x=>x.close),h=cs.map(x=>x.high),l=cs.map(x=>x.low),tr=[],atr=[],m20=[],m60=[],hist=[],rsi=[],cross=[],rows=[];
 const avg=(a,start,end)=>start>=0&&end-start>0&&a.slice(start,end).every(Number.isFinite)?a.slice(start,end).reduce((s,v)=>s+v,0)/(end-start):null;
 const ratio=(a,b)=>Number.isFinite(a)&&Number.isFinite(b)&&b!==0?a/b:null;
 let e12=null,e26=null,e9=null,gain=0,loss=0;
 for(let i=0;i<cs.length;i++){
  const b=cs[i],delta=i?c[i]-c[i-1]:0;
  tr.push(i?Math.max(h[i]-l[i],Math.abs(h[i]-c[i-1]),Math.abs(l[i]-c[i-1])):h[i]-l[i]);atr.push(avg(tr,i-13,i+1));m20.push(avg(c,i-19,i+1));m60.push(avg(c,i-59,i+1));
  e12=e12===null?c[i]:e12+2/13*(c[i]-e12);e26=e26===null?c[i]:e26+2/27*(c[i]-e26);const dif=e12-e26;e9=e9===null?dif:e9+2/10*(dif-e9);hist.push(dif-e9);
  if(i===1){gain=Math.max(delta,0);loss=Math.max(-delta,0);}else if(i>1){gain=(gain*13+Math.max(delta,0))/14;loss=(loss*13+Math.max(-delta,0))/14;}
  rsi.push(i<14?null:loss===0?(gain===0?50:100):100-100/(1+gain/loss));
  cross.push(i>=20&&c[i]>m20[i]&&c[i-1]<=m20[i-1]?1:0);
  const high60=i>=60?Math.max(...h.slice(i-60,i)):null,low120=i>=120?Math.min(...l.slice(i-120,i)):null,high120=i>=120?Math.max(...h.slice(i-120,i)):null;
  const range=n=>i>=n?Math.max(...h.slice(i-n,i))-Math.min(...l.slice(i-n,i)):null;
  const peak60=i>=60?h.slice(i-60,i).lastIndexOf(high60):null;
  let travel=0;if(i>=21)for(let j=i-20;j<i;j++)travel+=Math.abs(c[j]-c[j-1]);
  const negAvg=(start,end)=>start>=0?hist.slice(start,end).reduce((s,v)=>s+Math.max(-v,0),0)/(end-start):null;
  const values=[ratio(c[i]-b.open,atr[i]),ratio(c[i]-l[i],h[i]-l[i]),ratio(h[i]-Math.max(b.open,c[i]),atr[i]),ratio(c[i]-l[i],atr[i]),ratio(c[i]-l[i],c[i]),
   m20[i]===null?null:ratio(c[i]-m20[i],atr[i]),m60[i]===null?null:ratio(c[i]-m60[i],atr[i]),
   i>=24?ratio(m20[i]-m20[i-5],atr[i]):null,i>=69?ratio(m60[i]-m60[i-10],atr[i]):null,
   high60?1-c[i-1]/high60:null,high120!==null?ratio(c[i]-low120,high120-low120):null,
   ratio(avg(tr,i-5,i),avg(tr,i-20,i)),ratio(avg(tr,i-20,i),avg(tr,i-60,i)),ratio(volumes[i],avg(volumes,i-20,i)),ratio(avg(volumes,i-5,i),avg(volumes,i-20,i)),
   i>=40?cross.slice(i-20,i).reduce((a,b)=>a+b,0):null,i>=40?c.slice(i-20,i).filter((v,k)=>v<=m20[i-20+k]).length/20:null,peak60===null?null:(60-peak60)/60,
   i>=21?ratio(Math.abs(c[i-1]-c[i-21]),travel):null,rsi[i],ratio(hist[i],atr[i]),i>=3?ratio(hist[i]-hist[i-3],atr[i]):null,
   ratio(range(20),atr[i-1]),ratio(range(60),atr[i-1]),i>=20?ratio(c[i]-c[i-20],atr[i]):null,i>=10?ratio(negAvg(i-10,i-5)-negAvg(i-5,i),atr[i-1]):null];
  rows.push({i,time:b.time,values:values.map(v=>Number.isFinite(v)?v:null)});
 }
 return {version:VERSION,names:NAMES,labels:LABELS,rows};
}
function vector(row,model){return (model.indices?model.indices.map(k=>row.values[k]):row.values).map((v,j)=>{v=Number.isFinite(v)?v:model.medians[j];return Math.max(-6,Math.min(6,(v-model.means[j])/model.scales[j]));});}
function raw(row,model){const x=vector(row,model);if(model.type==='logistic')return model.intercept+x.reduce((s,v,j)=>s+v*model.coefficients[j],0);
 const xf=x.map(Math.fround);let s=model.intercept;for(const t of model.trees){let i=0;while(t.left[i]!==-1)i=xf[t.feature[i]]<=t.threshold[i]?t.left[i]:t.right[i];s+=model.learningRate*t.value[i];}return s;}
function score(row,model){return 1/(1+Math.exp(-Math.max(-35,Math.min(35,raw(row,model)))));}
function explain(row,model){if(model.type!=='logistic')return [];const x=vector(row,model);return x.map((v,j)=>({name:NAMES[j],label:LABELS[j],contribution:v*model.coefficients[j],value:row.values[j]})).sort((a,b)=>Math.abs(b.contribution)-Math.abs(a.contribution)).slice(0,3);}
const api={VERSION,NAMES,LABELS,compute,vector,raw,score,explain};if(typeof module==='object'&&module.exports)module.exports=api;else root.MA20AI=api;
})(typeof globalThis==='object'?globalThis:this);
