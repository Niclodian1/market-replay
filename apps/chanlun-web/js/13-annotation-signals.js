/* Causal annotation candidates: candle-body MA combinations and MACD divergence. */
(function(root) {
  'use strict';
  function sma(values, n) {
    let sum = 0;
    return values.map((v,i) => { sum += v; if(i >= n) sum -= values[i-n]; return i >= n-1 ? sum/n : null; });
  }
  function ema(values, n) {
    let prev; const alpha = 2/(n+1);
    return values.map(v => prev = prev === undefined ? v : alpha*v+(1-alpha)*prev);
  }
  function macd(candles) {
    const c = candles.map(x=>x.close), fast = ema(c,12), slow = ema(c,26);
    const dif = c.map((_,i)=>fast[i]-slow[i]), dea = ema(dif,9);
    return {dif,dea,hist:dif.map((v,i)=>v-dea[i])};
  }
  function bodyBreakouts(candles, periods) {
    const closes=candles.map(c=>c.close), mas=periods.map(p=>sma(closes,p)), out=[];
    for(let i=Math.max(...periods)-1;i<candles.length;i++) {
      const c=candles[i], values=mas.map(m=>m[i]);
      if(c.close>c.open && c.open<=Math.min(...values) && c.close>Math.max(...values)) {
        out.push({i,side:'buy',tag:`阳线穿 ${periods.join('+')}`,evidence:{type:'ma_body_combo_v1',periods,
          maValues:values,open:c.open,close:c.close,
          definition:'本根阳线开盘不高于两条均线，收盘高于两条均线（使用当根SMA）'}});
      }
    }
    return out;
  }
  // ---- 柱极值背离（与后端 app/core/divergence_detectors 同口径）----
  // MACD 用 SMA 播种（对齐后端 _ema），DIF=EMA12-EMA26, hist=DIF-DEA（不乘 2）。
  function emaSeeded(values, period) {
    const n=values.length, out=new Array(n).fill(null);
    if(n<period) return out;
    let sum=0; for(let i=0;i<period;i++) sum+=values[i];
    out[period-1]=sum/period;
    const k=2/(period+1);
    for(let i=period;i<n;i++) out[i]=values[i]*k+out[i-1]*(1-k);
    return out;
  }
  function macdHist(closes) {
    const fast=emaSeeded(closes,12), slow=emaSeeded(closes,26);
    const dif=closes.map((_,i)=>(fast[i]!=null&&slow[i]!=null)?fast[i]-slow[i]:null);
    const first=dif.findIndex(v=>v!=null);
    const dea=new Array(closes.length).fill(null);
    if(first>=0){
      const sig=emaSeeded(dif.slice(first),9);
      for(let j=0;j<sig.length;j++) dea[first+j]=sig[j];
    }
    return closes.map((_,i)=>(dif[i]!=null&&dea[i]!=null)?dif[i]-dea[i]:null);
  }
  // 柱极值底背离（动能口径）。
  //
  // 与下面 bottomDivergence 的区别：这个不看价格，只比两段负柱各自的【最深一根】，
  // 信号落在「极值之后第一根缩柱」，标记回指柱极值那根。
  //   1. 按 hist 符号切柱段，底背离取负柱段。
  //   2. 参照段 = 往前穿过 >=minGap 根反向柱的前一个同向段，取其 |hist| 最大那根。
  //   3. 当前段维护到当前为止的 |hist| 最大处 best；最近 shrinkBars 根连续缩短、
  //      且缩短起点正好是 best 时，才在缩短确认那一根出信号（极值已在它前面，不看未来）。
  //   4. strength = 1 - hist[best] / hist[参照极值]。
  function peakDivergence(candles, options={}) {
    const minGap=options.minGap ?? 3;
    const shrinkBars=Math.max(1, options.shrinkBars ?? 1);
    const minStrength=options.minStrength ?? 0;
    const n=candles.length, hist=macdHist(candles.map(c=>c.close));
    if(n<60) return [];
    const segs=[]; let start=0, sign=0;
    for(let i=0;i<=n;i++){
      const h=i<n?hist[i]:null;
      const now=h==null?0:(h>0?1:-1);
      if(i<n && now===sign) continue;
      if(sign!==0 && i>start) segs.push({sign,start,end:i-1});
      sign=now; start=i;
    }
    const out=[];
    for(let si=1;si<segs.length;si++){
      const S=segs[si];
      if(S.sign!==-1) continue;
      let prev=null;
      for(let pj=si-1;pj>=0;pj--){
        if(segs[pj].sign!==S.sign) continue;
        if(S.start-segs[pj].end-1>=minGap){ prev=segs[pj]; break; }
      }
      if(!prev) continue;
      let prevExt=prev.start;
      for(let k=prev.start+1;k<=prev.end;k++) if(Math.abs(hist[k]||0)>Math.abs(hist[prevExt]||0)) prevExt=k;
      const hp=hist[prevExt];
      if(hp==null || hp===0) continue;
      let best=S.start;
      for(let i=S.start+1;i<=S.end;i++){
        if(hist[i]==null || hist[i-1]==null) continue;
        if(Math.abs(hist[i])>Math.abs(hist[best])) best=i;
        if(i-S.start<shrinkBars) continue;
        let shrinking=true;
        for(let k=i-shrinkBars+1;k<=i;k++){
          if(!(Math.abs(hist[k])<Math.abs(hist[k-1]))){ shrinking=false; break; }
        }
        if(!shrinking || best!==i-shrinkBars) continue;
        const peak=i-shrinkBars;
        const strength=1-hist[peak]/hp;
        if(!(strength>minStrength)) continue;
        const tier=strength>=0.7?'强':strength>=0.4?'中':'弱';
        let segArea=0; for(let k=S.start;k<=i;k++) segArea+=Math.abs(hist[k]||0);
        let refArea=0; for(let k=prev.start;k<=prev.end;k++) refArea+=Math.abs(hist[k]||0);
        out.push({i,side:'buy',tag:`柱极值背离·${tier} ${(strength*100).toFixed(0)}%`,
          grade:tier==='强'?'A':tier==='中'?'B':'C',score:Math.round(strength*100),
          evidence:{type:'macd_peak_divergence_v1',minGap,shrinkBars,
            segment:[S.start,i],referenceSegment:[prev.start,prev.end],
            segmentStartTime:candles[S.start].time,
            referenceStartTime:candles[prev.start].time,referenceEndTime:candles[prev.end].time,
            peakIndex:peak,peakTime:candles[peak].time,peakHist:hist[peak],
            referenceIndex:prevExt,referenceTime:candles[prevExt].time,referenceHist:hp,
            alertIndex:i,alertTime:candles[i].time,
            confirmIndex:Math.min(i+3,n-1),confirmTime:candles[Math.min(i+3,n-1)].time,
            strength,tier,segmentBars:i-S.start+1,referenceBars:prev.end-prev.start+1,
            areaRatio:refArea?segArea/refArea:null,
            definition:`按负柱段比：当前负柱段最深一根若比前一个负柱段（中间隔>=${minGap}根反向柱）浅，`
              +`即动能减弱（只看动能，不看价格是否新低）。信号在${shrinkBars===1?'极值后第一根缩柱':'极值后第'+shrinkBars+'根缩柱'}发出。`}});
        break;                                        // 一段只出一个信号
      }
    }
    return out;
  }

  // 底背离（两个「价格低点」口径）。
  //
  // 之前按 MACD 柱极值定位，标记经常落不到真正的最低点上；现在比较的是两段负柱里的
  // 【最低价那根】，标记、连线、强度全部对着这两个价格低点：
  //   1. 按 hist 符号切柱段，底背离取负柱段。
  //   2. 参照低点 = 上一个负柱段（中间隔 >=minGap 根反向柱）里最低那根的价格与 hist。
  //   3. 本次低点 = 当前负柱段到某一根为止的最低那根。
  //   4. 条件：本次低点价格 < 参照低点价格（创新低，可放宽 lowTolerance），
  //            且 |hist[本次低点]| < |hist[参照低点]|（动能更弱）。
  //   5. 因果：低点本身要等「当前段第一次缩柱」那根才知道，信号在缩柱根发出、标记回指低点。
  function bottomDivergence(candles, options={}) {
    const minGap=options.minGap ?? 3;
    const lowTolerance=options.lowTolerance ?? 0;
    const minStrength=options.minStrength ?? 0;
    const n=candles.length, hist=macdHist(candles.map(c=>c.close));
    if(n<60) return [];
    const segs=[]; let start=0, sign=0;
    for(let i=0;i<=n;i++){
      const h=i<n?hist[i]:null;
      const now=h==null?0:(h>0?1:-1);
      if(i<n && now===sign) continue;
      if(sign!==0 && i>start) segs.push({sign,start,end:i-1});
      sign=now; start=i;
    }
    const out=[];
    for(let si=1;si<segs.length;si++){
      const S=segs[si];
      if(S.sign!==-1) continue;
      let prev=null;
      for(let pj=si-1;pj>=0;pj--){
        if(segs[pj].sign!==S.sign) continue;
        if(S.start-segs[pj].end-1>=minGap){ prev=segs[pj]; break; }
      }
      if(!prev) continue;
      let refLow=prev.start;
      for(let k=prev.start+1;k<=prev.end;k++) if(candles[k].low<candles[refLow].low) refLow=k;
      const refHist=hist[refLow];
      if(refHist==null || refHist>=0) continue;
      let runLow=S.start, last=null;
      for(let i=S.start+1;i<=S.end;i++){
        if(candles[i].low<candles[runLow].low) runLow=i;
        if(hist[i]==null || hist[i-1]==null) continue;
        if(!(Math.abs(hist[i])<Math.abs(hist[i-1]))) continue;   // 这一段第一次缩柱 → 低点已可见
        if(runLow===i || last===runLow) continue;                // 当根就是低点，还没确认
        const curHist=hist[runLow];
        if(curHist==null || curHist>=0) continue;
        if(candles[runLow].low>candles[refLow].low*(1+lowTolerance)) continue;
        if(!(Math.abs(curHist)<Math.abs(refHist))) continue;
        const strength=1-curHist/refHist;
        if(!(strength>minStrength)) continue;
        const tier=strength>=0.7?'强':strength>=0.4?'中':'弱';
        let segArea=0; for(let k=S.start;k<=i;k++) segArea+=Math.abs(hist[k]||0);
        let refArea=0; for(let k=prev.start;k<=prev.end;k++) refArea+=Math.abs(hist[k]||0);
        out.push({i:runLow,side:'buy',tag:`两低点底背离·${tier} ${(strength*100).toFixed(0)}%`,
          grade:tier==='强'?'A':tier==='中'?'B':'C',score:Math.round(strength*100),
          evidence:{type:'macd_low_divergence_v1',minGap,lowTolerance,
            segment:[S.start,i],referenceSegment:[prev.start,prev.end],
            segmentStartTime:candles[S.start].time,
            referenceStartTime:candles[prev.start].time,referenceEndTime:candles[prev.end].time,
            lowIndex:runLow,lowTime:candles[runLow].time,lowPrice:candles[runLow].low,currentHist:curHist,
            referenceIndex:refLow,referenceTime:candles[refLow].time,
            referenceLow:candles[refLow].low,referenceHist:refHist,
            confirmIndex:i,confirmTime:candles[i].time,
            strength,tier,segmentBars:i-S.start+1,referenceBars:prev.end-prev.start+1,
            areaRatio:refArea?segArea/refArea:null,
            definition:`比的是两段负柱各自的【最低价那根】：本次低点必须低于参照低点（容差`
              +`${(lowTolerance*100).toFixed(0)}%），且该处 MACD 负柱更浅。`
              +` 低点要等本段第一次缩柱才可见，信号在缩柱根（${candles[i].time?new Date(candles[i].time*1000).toISOString().slice(0,10):''}）发出。`}});
        last=runLow;
      }
    }
    return out;
  }
  root.AnnotationSignals={bodyBreakouts,macd,peakDivergence,bottomDivergence,macdHist};
  if(typeof module!=='undefined') module.exports=root.AnnotationSignals;
})(typeof window!=='undefined'?window:globalThis);
