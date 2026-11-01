/* Frozen MA20-COVER67. Rules calibrated on BTC; never refit per symbol. */
(function(root) {
"use strict";
const VERSION = "MA20-COVER67-2026-10-30";
const RULES = [[{"col":"rsi_change3","op":"<","value":0},{"col":"hist_change3_prev","op":">","value":-0.1}],[{"col":"rsi_change3_prev","op":"<","value":-5},{"col":"hist_change3_prev","op":">","value":-0.1}],[{"col":"dist20","op":"<","value":0.005},{"col":"adx","op":">","value":25}],[{"col":"drawdown60_prev","op":">","value":0.1},{"col":"hist_norm","op":"<","value":-0.1}],[{"col":"rsi_prev","op":">","value":55}],[{"col":"rsi_low_lift","op":">","value":10}],[{"col":"dist_atr","op":"<","value":0.2},{"col":"range5_20_prev","op":">","value":0.7}],[{"col":"dist_atr","op":"<","value":1},{"col":"dist20","op":">","value":0.05}],[{"col":"drawdown60_prev","op":"<","value":0.05}],[{"col":"close_pos","op":"<","value":0.8},{"col":"dist120","op":"<","value":-0.2}],[{"col":"risk_atr","op":"<","value":0.75},{"col":"neg_energy_delta","op":"<","value":-0.1}],[{"col":"dist120","op":">","value":0.1},{"col":"atr5_20_prev","op":">","value":1.2}],[{"col":"slope20","op":">","value":0},{"col":"rsi_prev","op":"<","value":45}],[{"col":"close_pos","op":"<","value":0.7},{"col":"dist120","op":"<","value":-0.1}],[{"col":"close_pos","op":">","value":0.9},{"col":"rsi_low_lift","op":">","value":5}],[{"col":"drawdown20_prev","op":"<","value":0.05},{"col":"rsi_change3_prev","op":"<","value":-5}],[{"col":"hist_change3_prev","op":">","value":0.1},{"col":"neg_energy_delta","op":"<","value":-0.1}],[{"col":"close_pos","op":"<","value":0.35}],[{"col":"body_atr","op":"<","value":0.5},{"col":"atrpct","op":"<","value":0.02}],[{"col":"body_atr","op":">","value":1},{"col":"slope60","op":"<","value":-0.5}],[{"col":"body_atr","op":">","value":1.5},{"col":"dist120","op":">","value":0.1}],[{"col":"dist20","op":">","value":0.05},{"col":"rsi_prev","op":"<","value":40}],[{"col":"slope20","op":"<","value":-0.5},{"col":"drawdown60_prev","op":"<","value":0.2}],[{"col":"dist_atr","op":"<","value":0.3},{"col":"dist20","op":">","value":0.02}],[{"col":"body_atr","op":"<","value":0.5},{"col":"hist_change3","op":">","value":0.2}],[{"col":"dist60","op":"<","value":0},{"col":"dist120","op":">","value":0.15}]];
const finite = Number.isFinite;
function mean(a, i, n) { if (i<n-1) return NaN; let s=0; for(let j=i-n+1;j<=i;j++){if(!finite(a[j]))return NaN;s+=a[j];}return s/n; }
function extreme(a,i,n,max) { if(i<n-1)return NaN; return Math[max?'max':'min'](...a.slice(i-n+1,i+1)); }
function ema(a,n) { let p;const k=2/(n+1);return a.map(v => p=p===undefined?v:k*v+(1-k)*p); }
function wilder(a,n=14) { const out=[];for(let i=0;i<a.length;i++){out[i]=finite(out[i-1])?(out[i-1]*(n-1)+a[i])/n:mean(a,i,n);}return out; }
function grade(v) { return !finite(v)?'unknown':v>=1.2?'high':v>=.9?'normal':'low'; }
function compute(cs, volumes) {
 const C=cs.map(c=>c.close),H=cs.map(c=>c.high),L=cs.map(c=>c.low),N=C.length;
 const V=cs.map((c,i)=>Number(volumes?.[i]??c.volume));
 const ma={};for(const n of [20,60,120])ma[n]=C.map((_,i)=>mean(C,i,n));
 const tr=C.map((_,i)=>Math.max(H[i]-L[i],i?Math.abs(H[i]-C[i-1]):0,i?Math.abs(L[i]-C[i-1]):0));
 const atr=tr.map((_,i)=>mean(tr,i,14)), aw=wilder(tr);
 const delta=C.map((v,i)=>i?v-C[i-1]:NaN);
 const gain=wilder(delta.map(v=>Math.max(v,0))),loss=wilder(delta.map(v=>Math.max(-v,0)));
 const rsi=C.map((_,i)=>loss[i]===0?(gain[i]>0?100:NaN):100-100/(1+gain[i]/loss[i]));
 const plus=[],minus=[];for(let i=0;i<N;i++){const u=i?H[i]-H[i-1]:NaN,d=i?L[i-1]-L[i]:NaN;plus[i]=u>d&&u>0?u:0;minus[i]=d>u&&d>0?d:0;}
 const wp=wilder(plus),wm=wilder(minus);
 const dx=C.map((_,i)=>{const p=100*wp[i]/aw[i],m=100*wm[i]/aw[i];return 100*Math.abs(p-m)/(p+m);});
 const adx=wilder(dx),fast=ema(C,12),slow=ema(C,26),dif=C.map((_,i)=>fast[i]-slow[i]),dea=ema(dif,9),hist=dif.map((v,i)=>v-dea[i]),neg=hist.map(v=>Math.max(-v,0));
 const rows=[],signals=[],crosses=[];
 for(let i=0;i<N;i++){
  const c=cs[i],a=atr[i];const row={i,time:c.time,ma20:ma[20][i],body_atr:(c.close-c.open)/a,dist_atr:(c.close-ma[20][i])/a,risk_atr:(c.close-c.low)/a,close_pos:(c.close-c.low)/(c.high-c.low),atrpct:a/c.close,adx:adx[i],rsi:rsi[i],rsi_prev:rsi[i-1],rsi_change3:rsi[i]-rsi[i-3],rsi_change3_prev:rsi[i-1]-rsi[i-4],hist_norm:hist[i]/a,hist_change3:(hist[i]-hist[i-3])/a,hist_change3_prev:(hist[i-1]-hist[i-4])/atr[i-1],rsi_low_lift:extreme(rsi,i-1,5,false)-extreme(rsi,i-6,5,false),atr5_20_prev:mean(tr,i-1,5)/mean(tr,i-1,20),range5_20_prev:(extreme(H,i-1,5,true)-extreme(L,i-1,5,false))/(extreme(H,i-1,20,true)-extreme(L,i-1,20,false)),neg_energy_delta:(mean(neg,i-6,5)-mean(neg,i-1,5))/atr[i-1],volratio:V[i]/mean(V,i-1,20)};
  for(const n of [20,60,120]){row['dist'+n]=c.close/ma[n][i]-1;row['slope'+n]=(ma[n][i]/ma[n][i-5]-1)/5*100;}
  for(const n of [20,60])row['drawdown'+n+'_prev']=1-C[i-1]/extreme(H,i-1,n,true);
  row.cross=i>0&&finite(ma[20][i-1])&&C[i-1]<=ma[20][i-1]&&c.close>ma[20][i];
  row.excluded=RULES.flatMap((r,k)=>r.every(x=>finite(row[x.col])&&(x.op==='<'?row[x.col]<x.value:row[x.col]>x.value))?[k+1]:[]);
  row.selected=row.cross&&c.close>c.open&&!row.excluded.length;
  row.complete=RULES.every(r=>r.every(x=>finite(row[x.col])));
  row.grade=grade(row.volratio);rows.push(row);
  if(row.cross)crosses.push(row);if(row.selected)signals.push(row);
 }
 const trades=signals.map(s=>{
  const e=s.i+1;if(e>=N)return {i:s.i,grade:s.grade,status:'pending'};
  const entry=cs[e].open,stop=cs[s.i].low;
  if(entry<=stop)return {i:s.i,grade:s.grade,status:'cancelled'};
  for(let j=e;j<N;j++){
   let price=null;
   if(j>e&&C[j-1]<ma[20][j-1])price=cs[j].open;
   else if(L[j]<=stop)price=Math.min(cs[j].open,stop);
   if(price!==null){const net=price*.999/(entry*1.001)-1;return {i:s.i,entry_i:e,exit_i:j,grade:s.grade,status:'closed',net,R:net/((entry-stop)/entry)};}
  }
  return {i:s.i,entry_i:e,grade:s.grade,status:'pending'};
 });
 return {rows,signals,crosses,trades};
}
function stats(trades, cutoff=Infinity, g=null) {
 const prior=trades.filter(t=>t.i<cutoff&&(!g||t.grade===g));
 const closed=prior.filter(t=>t.status==='closed'&&t.exit_i<cutoff),n=closed.length;
 const wins=closed.filter(t=>t.net>0),losses=closed.filter(t=>t.net<=0);
 const avg=a=>a.length?a.reduce((s,t)=>s+t.net,0)/a.length:null;
 const p=n?wins.length/n:null,z=1.96,center=n?(p+z*z/(2*n))/(1+z*z/n):null,half=n?z*Math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n):null;
 return {n,wins:wins.length,winRate:p,mean:avg(closed),avgWin:avg(wins),avgLoss:avg(losses),low:n?center-half:null,high:n?center+half:null,pending:prior.filter(t=>t.status!=='cancelled'&&!(t.status==='closed'&&t.exit_i<cutoff)).length,cancelled:prior.filter(t=>t.status==='cancelled').length};
}
root.Cover67={VERSION,RULES,compute,stats,grade};
if(typeof module!=='undefined')module.exports=root.Cover67;
})(typeof window!=='undefined'?window:globalThis);
