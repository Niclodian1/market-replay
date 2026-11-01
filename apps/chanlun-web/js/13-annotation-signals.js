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
  function detectDivergence(candles, series, metric, options={}) {
    const lookback=options.lookback ?? 60, minGap=options.minGap ?? 5;
    const values=series[metric], out=[];
    for(let i=Math.max(lookback,34);i<candles.length;i++) {
      // Strict fresh low relative to the preceding full window. No future pivot.
      let ref=i-lookback;
      for(let j=ref+1;j<i;j++) if(candles[j].low<=candles[ref].low) ref=j;
      if(ref<34 || i-ref<minGap || candles[i].low>=candles[ref].low) continue;
      if(!Number.isFinite(values[i]) || !Number.isFinite(values[ref])
        || values[i]>=0 || values[ref]>=0 || values[i]<=values[ref]) continue;
      out.push({i,side:'buy',tag:metric==='dif'?'创新低·DIF背离候选':'创新低·负柱背离候选',
        evidence:{type:'macd_new_low_divergence_v1',metric,lookback,minGap,referenceIndex:ref,
          referenceTime:candles[ref].time,referenceLow:candles[ref].low,currentLow:candles[i].low,
          referenceValue:values[ref],currentValue:values[i],confirmedTime:candles[i].time,
          definition:`当根低价低于此前${lookback}根最低价；间隔至少${minGap}根；参照点已完成35根预热；两处指标均低于零且本次指标抬高。创新低当根收盘观察，非反转确认。`}});
    }
    return out;
  }
  function divergence(candles, metric) { return detectDivergence(candles,macd(candles),metric); }
  root.AnnotationSignals={bodyBreakouts,macd,detectDivergence,divergence};
  if(typeof module!=='undefined') module.exports=root.AnnotationSignals;
})(typeof window!=='undefined'?window:globalThis);
