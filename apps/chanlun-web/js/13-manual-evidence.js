// 手选买点的因果特征：只读取所选K线及此前数据，不生成买卖建议。
(function(root){
'use strict';
const VERSION = 'manual-buy/1';
const PATTERNS = { free: '自由描述', contraction: '收敛后突破', range: '盘整后突破', pullback: '趋势回调', rebound: '超跌反弹', other: '其他' };
const needsRange = pattern => pattern === 'contraction' || pattern === 'range';
const stamp = (bar, tf) => new Date(bar.time * 1000).toISOString().slice(0, tf === '1D' ? 10 : 16).replace('T', ' ');
const mean = values => values.length && values.every(v => Number.isFinite(v) && v >= 0)
  ? values.reduce((a,b) => a+b,0) / values.length : null;
const ratio = (a,b) => Number.isFinite(a) && Number.isFinite(b) && b > 0 ? a/b : null;
function inspect(form, candles, volumes, index, tf){
  const errors = [], c = candles[index];
  if (!Number.isInteger(index) || index < 0 || !c) return { errors:['请先在图上选择实际K线'], evidence:null };
  // 先截断，再做全部统计，追加/修改未来K线不会改变证据。
  const cs = candles.slice(0,index+1);
  const vs = cs.map((bar,i) => {
    const value = volumes?.[i] ?? bar.volume;
    if(value === null || value === undefined || value === '')return null;
    const number=Number(value);return Number.isFinite(number)&&number>=0?number:null;
  });
  const reason = String(form.reason || '').trim();
  if (!reason) errors.push('请明确填写为什么在这根K线收盘买入');
  if (!Object.hasOwn(PATTERNS, form.pattern || 'free')) errors.push('买点类型无效');
  const anchors = {};
  for (const field of ['rangeStart','rangeEnd']){
    const text = String(form[field] || '').trim();
    if (!text) continue;
    const matches = cs.map((bar,i) => ({bar,i})).filter(({bar}) => stamp(bar,text.length>10?'intraday':tf) === text);
    if (matches.length !== 1){ errors.push(`${field==='rangeStart'?'区间开始':'区间结束'}必须唯一对应买点之前的实际K线（UTC）`); continue; }
    const {bar,i} = matches[0];
    if (i >= index){ errors.push('整理区间必须在买入K线之前结束，不包含突破当根'); continue; }
    anchors[field] = {time:bar.time,index:i};
  }
  if (needsRange(form.pattern) && (!anchors.rangeStart || !anchors.rangeEnd)) errors.push('收敛／盘整后突破需标明区间开始和结束');
  if (!!form.rangeStart !== !!form.rangeEnd) errors.push('区间起止请成对填写');
  if (anchors.rangeStart && anchors.rangeEnd && anchors.rangeStart.index > anchors.rangeEnd.index) errors.push('区间开始不能晚于结束');
  const previous = vs.slice(Math.max(0,index-20),index);
  const average20 = previous.length===20 ? mean(previous) : null;
  const volume = {current:vs[index],previous20Count:previous.length,previous20Mean:average20,
    vsPrevious20:ratio(vs[index],average20),vsPreviousBar:ratio(vs[index],vs[index-1])};
  let interval = null;
  if (anchors.rangeStart && anchors.rangeEnd && anchors.rangeStart.index <= anchors.rangeEnd.index){
    const start=anchors.rangeStart.index,end=anchors.rangeEnd.index;
    const segment=cs.slice(start,end+1),iv=vs.slice(start,end+1),half=Math.floor(segment.length/2);
    const high=Math.max(...segment.map(b=>b.high)),low=Math.min(...segment.map(b=>b.low));
    const tr = i => Math.max(cs[i].high-cs[i].low,i?Math.abs(cs[i].high-cs[i-1].close):0,i?Math.abs(cs[i].low-cs[i-1].close):0);
    const first=Array.from({length:half},(_,k)=>start+k),last=Array.from({length:half},(_,k)=>end-half+1+k);
    interval={startTime:cs[start].time,endTime:cs[end].time,bars:segment.length,high,low,
      rangePct:low>0?(high-low)/low:null,volumeMean:mean(iv),breakoutVolumeRatio:ratio(vs[index],mean(iv)),
      comparisonBars:half,lastVsFirstTrueRange:half>=2?ratio(mean(last.map(tr)),mean(first.map(tr))):null,
      lastVsFirstVolume:half>=2?ratio(mean(last.map(i=>vs[i])),mean(first.map(i=>vs[i]))):null,
      closeAboveIntervalHigh:c.close>high,closeVsIntervalHigh:ratio(c.close,high)===null?null:c.close/high-1};
  }
  return {errors,evidence:{schema:VERSION,valid:errors.length===0,timeBasis:'UTC',decisionTime:c.time,
    execution:{basis:'selected_bar_close',price:c.close},anchors,volume,interval,
    price:{open:c.open,high:c.high,low:c.low,close:c.close,bodyPct:c.open>0?(c.close-c.open)/c.open:null}}};
}
const api={VERSION,PATTERNS,needsRange,stamp,inspect};
root.ManualEvidence=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
