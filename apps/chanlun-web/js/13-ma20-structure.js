/* MA20 entry candidates. Causal, symbol-independent, no annotation/date lookup. */
(function(root){
  'use strict';
  const VERSION='MA20-STRUCTURE-V3-2026-11-03';
  const PARAMETERS=Object.freeze({ma:20,atr:14,bodyATR:0.3,confirmationWindow:3,confirmationBodyATR:0.5});
  function compute(candles,volumes=[]){
    const cs=candles.map(c=>Array.isArray(c)?{time:c[0],open:c[1],high:c[2],low:c[3],close:c[4]}:c);
    const rows=[],signals=[],trs=[];let lastCross=-1,used=false;
    for(let i=0;i<cs.length;i++){
      const c=cs[i],prev=cs[i-1];
      const tr=prev?Math.max(c.high-c.low,Math.abs(c.high-prev.close),Math.abs(c.low-prev.close)):c.high-c.low;
      trs.push(tr);
      const ma20=i>=19?cs.slice(i-19,i+1).reduce((s,b)=>s+b.close,0)/20:null;
      const atr=i>=13?trs.slice(i-13,i+1).reduce((s,v)=>s+v,0)/14:null;
      const bodyATR=atr>0?(c.close-c.open)/atr:null;
      const cross=i>=20&&c.close>ma20&&prev.close<=rows[i-1].ma20;
      let selected=false,family=null,reasons=[];
      if(cross){
        lastCross=i;used=false;
        if(c.close>c.open&&bodyATR>=PARAMETERS.bodyATR){
          selected=true;family='reclaim';reasons=['收盘从 MA20 下方或线上重新站上','阳线实体 ≥ 0.3 × ATR14'];
        }
      }else if(lastCross>=0&&i-lastCross<=PARAMETERS.confirmationWindow&&!used
        &&rows[lastCross].bodyATR!==null&&rows[lastCross].bodyATR<PARAMETERS.confirmationBodyATR
        &&c.close>ma20&&bodyATR>=PARAMETERS.confirmationBodyATR&&c.close>prev.high){
        selected=true;family='confirmation';used=true;
        reasons=['最近一次上穿的实体 < 0.5 × ATR14','上穿后 1–3 根内首次强确认：实体 ≥ 0.5 × ATR14','收盘高于 MA20 且超过上一根最高价'];
      }
      const priorVolume=i>=20?volumes.slice(i-20,i):[];
      const meanVolume=priorVolume.length===20&&priorVolume.every(Number.isFinite)?priorVolume.reduce((s,v)=>s+v,0)/20:null;
      const row={i,time:c.time,selected,family,reasons,cross,ma20,atr,bodyATR,
        distATR:atr>0&&ma20!==null?(c.close-ma20)/atr:null,
        riskPct:c.close>0?(c.close-c.low)/c.close:null,
        volRatio:meanVolume>0&&Number.isFinite(volumes[i])?volumes[i]/meanVolume:null,
        entry:c.close,stop:c.low,confirmationOf:family==='confirmation'?cs[lastCross].time:null};
      rows.push(row);if(selected)signals.push(row);
    }
    return {version:VERSION,parameters:PARAMETERS,rows,signals};
  }
  const api={VERSION,PARAMETERS,compute};
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.MA20Structure=api;
})(typeof globalThis==='object'?globalThis:this);
