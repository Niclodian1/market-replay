/* Structured human evidence. No values are inferred from algorithm grades. */
(function(root){
'use strict';
const VERSION='ma20-assessment/1';
const FIELDS={
 region:['买点区域','成立','不成立','不确定'],
 trigger:['本根触发','足够强','力度不足','不确定'],
 regime:['行情类型','趋势回调','整理突破','下跌反弹','高位震荡','不确定'],
 confidence:['判断把握','高','中','低'],
 primaryReason:['最主要的依据','调整充分且突破有效','趋势回调重新走强','区域成立但触发不足','调整幅度不足','调整时间不足','震荡未结束','高位震荡','下跌未结束','止损或盈亏比不合适','看不懂','其他'],
 depth:['调整幅度','充分','不足','不确定','不适用'],
 duration:['调整时间','充分','不足','不确定','不适用'],
 contraction:['波动收敛','明显收窄','没有收窄','不确定','不适用'],
 referenceStatus:['结构参照范围','可以明确标出','不确定','不适用'],
 preferredEntry:['更合适的触发位置','就是当前根','等待后续更强信号','此前已有更好买点','不确定','不适用']
};
const ANCHORS={adjustStart:'调整开始K线',adjustEnd:'调整结束K线',referencePeak:'参照高点K线',earlierEntry:'此前更好买点K线'};
const stamp=(bar,tf)=>new Date(bar.time*1000).toISOString().slice(0,tf==='1D'?10:16).replace('T',' ');
function inspect(a={},cs=[],i,tf){
 const errors=[];const anchors={};
 for(const [key,[name,...options]]of Object.entries(FIELDS)){
  if(key==='preferredEntry'&&!a[key])continue;
  if(!options.includes(a[key]))errors.push('请选择'+name);
 }
 for(const [key,label]of Object.entries(ANCHORS)){
  const value=(a[key]||'').trim();if(!value)continue;
  const matches=cs.map((c,index)=>({c,index})).filter(({c})=>stamp(c,value.length>10?'intraday':tf)===value);
  const available=matches.filter(x=>x.index<=i);
  if(available.length>1){errors.push(label+'当天有多根K线，请填写完整UTC日期和时分');continue;}
  const index=(available[0]||matches[0])?.index??-1;
  if(index<0){errors.push(label+'必须对应当前周期的实际K线（UTC）');continue;}
  if(index>i){errors.push(label+'不能在当前信号之后');continue;}
  anchors[key]={time:cs[index].time,index,high:cs[index].high,low:cs[index].low,close:cs[index].close};
 }
 if(a.referenceStatus==='可以明确标出'&&(!a.adjustStart||!a.adjustEnd))errors.push('请填写调整起止K线，或把结构参照范围选为不确定／不适用');
 if(a.preferredEntry==='此前已有更好买点'&&!a.earlierEntry)errors.push('请标出此前更好买点K线，或选择不确定');
 if(anchors.adjustStart&&anchors.adjustEnd&&anchors.adjustStart.index>anchors.adjustEnd.index)errors.push('调整开始不能晚于调整结束');
 const box={};
 for(const k of ['boxHigh','boxLow'])if(a[k]!==undefined&&a[k]!==''){
  const n=Number(a[k]);if(!Number.isFinite(n)||n<=0)errors.push('箱体边界必须是正数');else box[k]=n;
 }
 if(box.boxHigh!==undefined&&box.boxLow!==undefined&&box.boxHigh<=box.boxLow)errors.push('箱体上沿必须高于下沿');
 if((box.boxHigh===undefined)!==(box.boxLow===undefined))errors.push('箱体上下沿请成对填写，或都留空');
 if((a.primaryReason==='其他')&&!(a.explanation||'').trim())errors.push('主要依据选其他时，请补充一句说明');
 const derived={};
 if(anchors.adjustStart&&anchors.adjustEnd&&anchors.adjustStart.index<=anchors.adjustEnd.index){
  const segment=cs.slice(anchors.adjustStart.index,anchors.adjustEnd.index+1);
  derived.adjustmentBars=segment.length;derived.adjustmentHigh=Math.max(...segment.map(c=>c.high));derived.adjustmentLow=Math.min(...segment.map(c=>c.low));
  derived.adjustmentRangePct=derived.adjustmentHigh>0?(derived.adjustmentHigh-derived.adjustmentLow)/derived.adjustmentHigh:null;
 }
 if(anchors.referencePeak){const h=anchors.referencePeak.high;derived.drawdownFromReferencePeak=h>0?1-cs[i].close/h:null;}
 if(box.boxHigh!==undefined&&box.boxLow!==undefined){derived.boxWidthPct=(box.boxHigh-box.boxLow)/box.boxLow;derived.signalBoxPosition=(cs[i].close-box.boxLow)/(box.boxHigh-box.boxLow);}
 return {errors,evidence:{schema:VERSION,valid:errors.length===0,validationErrors:[...errors],signalTime:cs[i]?.time,timeframe:tf,timeBasis:'UTC',anchors,box,derived}};
}
const api={VERSION,FIELDS,ANCHORS,stamp,inspect};root.AnnotationForm=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
