// Practice decisions, evidence, and review. Future bars are attached only at the reveal boundary.
const PR_CHOICES={buy:'买入',wait:'等待',skip:'放弃机会',unsure:'不确定'};
const PR_COHORTS={random:'随机新题',special:'专项',repeat:'重练',legacy:'旧版',mixed:'包含已练区间'};
const prEl=id=>document.getElementById(id);
const prEscape=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const prNumber=(v,n=1)=>Number.isFinite(v)?v.toFixed(n):'—';
const prTime=(v,tf)=>Number.isFinite(v)&&Math.abs(v)<8.64e12 ? new Date(v*1000).toISOString().slice(0,tf==='1D'?10:16).replace('T',' ') : '—';
let prDraft=null, prPick=null, prSuppressPick=0;
const prChartPicking=()=>prPick!==null||Date.now()<prSuppressPick;
function prBars(from=-Infinity,to=Infinity){
  if(chunkedActive())return (ChunkStore.win?.candles||[]).filter(b=>b.time>=from&&b.time<=to)
    .map(b=>[b.time,b.open,b.high,b.low,b.close,b.volume??null]);
  const level=L(), result=[];
  for(let i=0;i<(level.candles||[]).length;i++){
    const b=level.candles[i];
    if(b[0]<from)continue;
    if(b[0]>to)break;
    const high=Math.max(b[2],b[3]),low=Math.min(b[2],b[3]);
    const clamp=v=>Math.max(low,Math.min(high,v));
    const vol=level.volume?.[i];
    result.push([b[0],clamp(b[1]),high,low,clamp(b[4]),Number.isFinite(vol)&&vol>=0?vol:null]);
  }
  return result;
}
function prBegin(rep, drill, avoid){
  const visible=prBars(-Infinity,replayT).slice(-TR.LOOKBACK-1);
  const meta=DATA.meta;
  const prior=PracticeStore.list().find(s=>s.sym===TR.sym&&s.tf===TR.tf&&Number.isFinite(s.startTs)
    &&s.startTs>=visible[0]?.[0] && s.startTs<=TR.startTs);
  const mode=rep?.mode||(prEl('prMode')?.value||'classic');
  TR.research={ schema:2, mode, cohort:rep?'repeat':drill?'special':prior?'mixed':'random',
    repeatOf:rep?.id??null, rule:(rep?.rule||prEl('prRule')?.value.trim()||'MA20-v1').slice(0,80),
    selection:{ drill:drill||null, avoidRecent:avoid, candidateSource:'free_replay' },
    market:Object.entries(MARKET_GROUPS).find(([,ss])=>ss.includes(TR.sym))?.[0]||null,
    data:{symbol:meta.symbol,source:meta.source??null,updated:meta.updated??meta.generatedAt??meta.generated_at??null,
      session:meta.session??meta.exchangeTimezone??null, timestampBasis:'UTC epoch seconds', adjustment:meta.adjustment??null},
    visibleFrom:visible[0]?.[0]??TR.startTs, visibleThrough:replayT, bars:visible, decisions:[], events:[] };
  TR.archiveId=Math.max(Date.now(),(PracticeStore.list(true)[0]?.id||0)+1);
  PracticeStore.active(TR.archiveId);
  prCheckpoint();
}
function prCheckpoint(){
  if(!trLocked()||!TR.research||replayT==null)return;
  const r=TR.research;
  if(r.visibleThrough!==replayT){
    if(replayT>r.visibleThrough)r.bars.push(...prBars(r.visibleThrough+1,replayT));
    r.visibleThrough=replayT;
  }
  PracticeStore.put(trSessionRow('进行中'));
}
function prVisibleContext(){
  return { view:activeView, stroke:strokeStd, pivot:pivotMode, layers:{...layers},
    maPeriods:prEl('maPeriods')?.value||'', overlays:[...document.querySelectorAll('button.active[data-ov]')].map(el=>el.dataset.ov),
    algorithmMarkers:{ maBreakout:prEl('cover67IndicatorToggle')?.getAttribute('aria-pressed')==='true',
      macd3Seg:typeof MACD3SEG!=='undefined'&&!!MACD3SEG.on } };
}
function prDefaultStop(bar,side,entry=bar.close){
  const stop=side==='空'?bar.high:bar.low;
  return stop>0&&(side==='空'?stop>entry:stop<entry)?stop:null;
}
function prEvent(action,pos,detail={}){
  if(!trLocked()||!TR.research)return;
  TR.research.events.push({action,positionId:pos.id,opportunityId:pos.opportunityId??null,
    barTime:replayT??TR.research.visibleThrough,step:TR.step,recordedAt:Date.now(),...detail});
}
function prAddDecision(choice, data, bar, side, entry, stop){
  const r=TR.research;
  const id=String(TR.archiveId)+'-'+(r.decisions.length+1);
  const last=data.parent ? PracticeCore.opportunityRows({research:r}).find(d=>d.opportunityId===data.parent&&d.choice==='wait') : null;
  const snapshot=r.bars.slice();
  if(bar.time>r.visibleThrough)snapshot.push(...prBars(r.visibleThrough+1,bar.time));
  const i=snapshot.findIndex(b=>b[0]===bar.time);
  const d={id,opportunityId:last?.opportunityId||id,previousDecisionId:last?.id??null,
    choice,side,barTime:bar.time,recordedAt:Date.now(),step:TR.step,
    reason:data.reason,reasonTag:data.reasonTag||null,note:data.note||null,kind:data.kind||null,condition:data.condition||null,invalid:data.invalid||null,
    confidence:data.confidence||null, anchors:data.anchors||{}, quick:data.quick||null,
    tags:{market:ltSelected('market'),entry:ltSelected('entry'),pattern:ltSelected('pattern')},
    visible:{through:bar.time,from:r.visibleFrom,revealed:false,...prVisibleContext()},
    rule:r.rule,features:PracticeCore.features(snapshot,i,data.anchors),
    reference:{price:entry,stop, basis:data.basis||'signal_close_and_extreme'}};
  r.decisions.push(d);
  return d;
}
function prRecordOrder(pos, bar, details, logic){
  if(!trLocked()||!TR.research)return;
  const leg=pos.entries[0];
  const d=prAddDecision('buy',details||{reason:logic,basis:'practice_order'},bar,pos.side,leg.price,pos.initialStop);
  pos.opportunityId=d.opportunityId;
  d.positionId=pos.id;
  prEvent('open',pos,{price:leg.price,initialStop:pos.initialStop,capital:leg.capital,leverage:leg.leverage,reason:logic});
}
function prFinish(){
  const r=TR.research;
  if(!r)return;
  // The reveal is about to expose the answers. No other call supplies future bars.
  const cutoff=replayT??r.visibleThrough;
  const latest=r.decisions.reduce((a,d)=>Math.max(a,d.barTime),0);
  const after=latest ? prBars(latest+1).slice(0,60) : [];
  const end=Math.max(cutoff,after.at(-1)?.[0]||0);
  const extra=prBars(r.bars.at(-1)?.[0]+1||r.visibleFrom,end);
  r.bars.push(...extra);
  r.visibleThrough=cutoff;
  r.resultsThrough=r.bars.at(-1)?.[0]??cutoff;
  r.revealedAt=Date.now();
  for(const d of r.decisions){
    d.outcomes=PracticeCore.outcomes(r.bars,d.barTime,d.reference.price,d.reference.stop,d.side);
  }
}
function prCloseDecision(){
  prDraft=null;prPick=null;
  const d=prEl('practiceDecision');
  if(d)d.hidden=true;
  if(prEl('practicePickHint'))prEl('practicePickHint').hidden=true;
}
function prOpenDecision(){
  if(!trLocked()||!TR.research)return;
  if(prDraft)return;
  const b=curBar();
  if(!b)return;
  prDraft={barTime:b.time,anchors:{}};
  const form=prEl('practiceDecisionForm');
  form.reset();
  prEl('prSide').value=prEl('jSide').value;
  const waits=PracticeCore.opportunityRows({research:TR.research}).filter(d=>d.choice==='wait');
  prEl('prParent').replaceChildren(new Option('新机会',''),...waits.map(d=>new Option(
    trLabel(d.barTime)+' · '+d.reason.slice(0,26),d.opportunityId)));
  prEl('prDecisionTime').textContent=trLabel(b.time);
  prEl('prDecisionError').textContent='';
  prEl('practiceDecision').hidden=false;
  prRenderAnchors();
  prDecisionFields();
  prEl('prChoice').focus();
}
function prRenderAnchors(){
  if(!prDraft)return;
  prEl('prAnchorText').textContent=['start','end','level'].map((k,i)=>{
    const v=prDraft.anchors[k], label=['起','止','关键'][i];
    return v==null ? label+' —' : label+' '+trLabel(typeof v==='object'?v.time:v)+(k==='level'?' @ '+prNumber(v.price,4):'');
  }).join(' · ');
}
function prStartPick(key){
  if(!prDraft||!trLocked())return;
  prPick=key;
  if(activeView!=='raw')setView('raw');
  if(typeof drawingMgr!=='undefined'&&drawingMgr){drawingMgr.setTool(null);paintDrawButtons('none');}
  prEl('practiceDecision').hidden=true;
  prEl('practicePickHint').hidden=false;
  prEl('practicePickHint').textContent='请点图上已显示的原始 K 线；Esc 取消选点。';
}
function prAcceptPoint(param){
  if(!prPick||!prDraft||!trLocked()||!Number.isFinite(param.time))return;
  const b=TR.research.bars.find(b=>b[0]===param.time);
  if(!b||b[0]>prDraft.barTime||(prPick!=='level'&&b[0]>=prDraft.barTime)){
    prEl('practicePickHint').textContent='请选当前判断之前的 K 线；Esc 取消选点。';return;
  }
  const p=param.point?candleSeries.coordinateToPrice(param.point.y):null;
  prDraft.anchors[prPick]=prPick==='level'?{time:b[0],price:Number.isFinite(p)&&p>0?p:b[4]}:b[0];
  prEndPick();
  prSuppressPick=Date.now()+160;
  prRenderAnchors();
}
function prEndPick(){
  prPick=null;
  prEl('practicePickHint').hidden=true;
  prEl('practiceDecision').hidden=false;
  prEl('prDecisionError').textContent='';
}
function prSubmitDecision(event){
  event.preventDefault();
  if(!prDraft||!trLocked()||curBar()?.time!==prDraft.barTime){prCloseDecision();return;}
  const value=id=>prEl(id).value.trim(), choice=value('prChoice'), side=value('prSide');
  const data={reason:[value('prReason'),value('prNote')].filter(Boolean).join(' · '),reasonTag:value('prReason'),note:value('prNote'),kind:value('prKind'),parent:value('prParent'),
    condition:value('prCondition'),invalid:value('prInvalid'),confidence:value('prConfidence'),anchors:{...prDraft.anchors}};
  const error=msg=>{prEl('prDecisionError').textContent=msg;};
  if(!data.reason&&choice!=='unsure')return error('请选一个主要理由，或写一句说明。');
  if(choice==='wait'&&!data.condition)return error('请填写等到什么条件再决定。');
  if(choice==='buy'&&!data.invalid)data.invalid=side==='空'?'价格触及当前 K 线高点':'价格触及当前 K 线低点';
  const parent=TR.research.decisions.find(d=>d.opportunityId===data.parent);
  if(parent&&parent.side!==side)return error('延续等待时请保持原方向，或另记为新机会。');
  const a=data.anchors;
  if((a.start==null)!==(a.end==null)||a.start>a.end)return error('请成对选择顺序正确的区间起止。');
  const bar=curBar(), stop=prDefaultStop(bar,side);
  if(choice==='buy'){
    if(stop==null)return error('此 K 线没有有效的初始止损距离。');
    if(!openJournalPosition({ details:data, side, entry:bar.close, stop,
      onError:error, logic:combineEntryLogic(selectedLogicTags(),data.reason) }))return;
  }else{
    prAddDecision(choice,data,bar,side,bar.close,stop);
    prCheckpoint();
  }
  prCloseDecision();updateTrainerUI();
}
// 买入时在弹窗内直接选入场信号/形态(与快速下单条共用同一组标签; 旧右侧表单已隐藏)
function prRenderSignals(){
  const box=prEl('prSignals'); if(!box)return;
  const buy=prEl('prChoice').value==='buy';
  box.hidden=!buy; if(!buy)return;
  const row=(cat,label)=>`<div class="pr-sig-row"><span>${label}</span>`+[...document.querySelectorAll(`#jLogicTags .logic-tag[data-cat="${cat}"]`)]
    .map(b=>`<button type="button" class="qo-chip${b.classList.contains('active')?' on':''}" data-pr-sig="${prEscape(b.dataset.val)}" data-cat="${cat}">${prEscape(b.dataset.val)}</button>`).join('')+'</div>';
  box.innerHTML=row('entry','入场信号 ≥1')+row('pattern','形态');
}
function prDecisionFields(){
  const choice=prEl('prChoice').value, side=prEl('prSide').value, buy=choice==='buy';
  prRenderSignals();
  prEl('prSubmit').textContent=buy?'保存并开仓':'锁定判断';
  prEl('prCondition').required=choice==='wait';
  const b=curBar();
  prEl('prDecisionHint').textContent=buy&&b ? '按原始 K 线收盘 '+prNumber(b.close,4)+' 入场 · 初始止损 '+
    prNumber(prDefaultStop(b,side),4)+' · 保证金/杠杆沿用快速下单设置；在下方选入场信号（至少 1 个）。'
    : '保存后保留当时判断，揭示答案后再计算假设结果。';
}
function prPathText(p){
  if(!p)return '无有效初始止损';
  if(!p.resolved)return '未完成（已观察 '+prNumber(p.observed,0)+' 根）';
  const interval=(xs,digits,suffix)=>prNumber(xs?.[0],digits)+
    (p.ambiguous?'～'+prNumber(xs?.[1],digits):'')+suffix;
  return (p.stopBars==null?'未止损':'第 '+prNumber(p.stopBars,0)+' 根触发止损')+
    ' · '+interval(p.mfePct,1,'%')+' / '+interval(p.mfeR,2,'R');
}
function prSessionReview(s){
  const r=s.research;
  if(!r)return '<p class="note">旧版练习：可查看交易；缺少当时的完整判断和价格路径。</p>';
  const decisions=r.decisions||[], events=r.events||[];
  const parts=decisions.map(d=>{
    return '<li><b>'+prEscape(PR_CHOICES[d.choice]||d.choice)+'</b> · '+prEscape(prTime(d.barTime,s.tf))+
      ' · '+prEscape(d.side)+' · '+prEscape(d.reason||'未填写')+
      (d.previousDecisionId?' · 延续等待':'')+
      (d.condition?' · 等待：'+prEscape(d.condition):'')+(d.invalid?' · 失效：'+prEscape(d.invalid):'')+
      (d.features?.range?' · 整理 '+prNumber(d.features.range.bars,0)+' 根':'')+
      ' · 初始止损 '+prNumber(d.reference?.stop,4)+
      '<div>'+((d.outcomes||[]).map(p=>p.horizon+' 根：'+prPathText(p)).join(' ｜ ')||'未评估')+'</div></li>';
  }).join('');
  const eventText=events.map(e=>'<li>'+prEscape(prTime(e.barTime,s.tf))+' · '+
    prEscape(({open:'开仓',add:'加仓',exit:'平仓',stop_change:'调整止损'})[e.action]||e.action)+
    ' · 持仓 #'+prNumber(e.positionId,0)+(e.action==='stop_change'?' · '+prNumber(e.from,4)+' → '+prNumber(e.to,4):'')+
    (e.reason?' · '+prEscape(e.reason):'')+'</li>').join('');
  return '<details class="pr-review"><summary>本局复盘 · '+decisions.length+' 次判断 / '+PracticeCore.opportunityRows(s).length+' 个机会 · '+
    prEscape(PR_COHORTS[r.cohort]||r.cohort)+' · '+prEscape(r.rule)+'</summary>'+
    '<div class="note">按原始入场和止损评估。未开仓项是假设结果；区间表示同一根 K 线内先后未知。</div>'+
    (parts?'<ul>'+parts+'</ul>':'<p class="note">没有记录判断。</p>')+
    (eventText?'<details><summary>实际操作时间线</summary><ul>'+eventText+'</ul></details>':'')+'</details>';
}
function prComparablePairs(rows){
  // Explain similar judgments on the same market and horizon. This is a review aid, not a performance test.
  const ref=rows.filter(r=>r.d.choice==='buy'&&r.d.features).slice(-24).reverse();
  const other=rows.filter(r=>['wait','skip'].includes(r.d.choice)&&r.d.features);
  const key=r=>[r.s.sym,r.s.tf,r.s.research.rule,r.d.side,r.d.kind||''].join('|');
  const parts=f=>[f.bodyATR,f.closeInRange,f.ma?.[20]?.distanceATR,f.recent20?.closeInRange];
  const used=new Set(), pairs=[];
  for(const a of ref){
    let best,score=Infinity;
    for(const b of other){
      if(used.has(b.d.id)||a.d.barTime===b.d.barTime||key(a)!==key(b))continue;
      const x=parts(a.d.features), y=parts(b.d.features), diffs=x.map((v,i)=>
        Number.isFinite(v)&&Number.isFinite(y[i])?Math.abs(v-y[i]):null).filter(v=>v!=null);
      if(diffs.length<3)continue;
      const d=diffs.reduce((n,v)=>n+v,0)/diffs.length;
      if(d<score){best=b;score=d;}
    }
    if(best&&score<=0.65){pairs.push([a,best]);used.add(best.d.id);}
    if(pairs.length===3)break;
  }
  return pairs;
}
function prRenderBatch(){
  const root=prEl('practiceBatchResults');
  if(!root)return;
  const list=PracticeStore.list();
  for(const [id,items] of [
    ['prFilterSym',list.filter(s=>s.research).map(s=>s.sym)],
    ['prFilterTF',list.filter(s=>s.research).map(s=>s.tf)],
    ['prFilterRule',list.map(s=>s.research?.rule)],
    ['prFilterKind',list.flatMap(s=>(s.research?.decisions||[]).map(d=>d.kind))],
  ]){
    const sel=prEl(id),v=sel.value;
    const opts=[...new Set(items.filter(x=>typeof x==='string'&&x))].sort();
    sel.replaceChildren(new Option('全部',''),...opts.map(x=>new Option(x,x)));
    if(opts.includes(v))sel.value=v;
  }
  const h=+prEl('prFilterHorizon').value;
  const filters={cohort:prEl('prFilterCohort').value,sym:prEl('prFilterSym').value,tf:prEl('prFilterTF').value,
    rule:prEl('prFilterRule').value,kind:prEl('prFilterKind').value};
  const rows=PracticeCore.batchRows(list,h,filters);
  if(!rows.length){root.textContent='这个筛选条件下还没有已揭示的判断。';return;}
  const median=xs=>{
    const s=[...xs].sort((a,b)=>a-b), i=Math.floor(s.length/2);
    return !s.length?null : s.length%2?s[i]:(s[i-1]+s[i])/2;
  };
  const pct=(n,d)=>d?prNumber(n/d*100)+'%':'—';
  const groups=new Map();
  for(const row of rows){
    const {s,d}=row;
    const key=JSON.stringify([s.sym,s.tf,s.research.rule,d.choice]);
    if(!groups.has(key))groups.set(key,[]);
    groups.get(key).push(row);
  }
  const compareLine=({s,d,path})=>prEscape(prTime(d.barTime,s.tf))+' · '+prEscape(PR_CHOICES[d.choice])+
    ' · '+prEscape(d.reason)+' · 实体 '+prNumber(d.features.bodyATR,2)+' ATR / MA20 距离 '+
    prNumber(d.features.ma?.[20]?.distanceATR,2)+' ATR · '+prPathText(path);
  const pairs=prComparablePairs(rows);
  root.innerHTML='<div class="note">同品种、周期、规则和同一入场点去重；相邻机会仍可能相关。最大运行盈利按保守值统计。等待、放弃和不确定显示假设结果。</div>'+
    '<div class="pr-table"><table><thead><tr><th>品种 / 周期 / 规则</th><th>判断</th><th>已评估 / 机会</th><th>止损率</th><th>最大运行盈利中位</th><th>先达 2R / 3R / 5R</th></tr></thead><tbody>'+
    [...groups].map(([key,rs])=>{
      const [sym,tf,rule,choice]=JSON.parse(key), ps=rs.map(r=>r.path).filter(p=>p?.resolved);
      return '<tr><td>'+prEscape([sym,tf,rule].join(' / '))+'</td><td>'+prEscape(PR_CHOICES[choice]||choice)+'</td><td>'+
        ps.length+' / '+rs.length+'</td><td>'+pct(ps.filter(p=>p.stopBars!=null).length,ps.length)+'</td><td>'+
        (ps.length?prNumber(median(ps.map(p=>p.mfePct[0])))+'% / '+prNumber(median(ps.map(p=>p.mfeR[0])),2)+'R':'—')+'</td><td>'+
        [2,3,5].map(r=>pct(ps.filter(p=>p.targets.some(t=>t.r===r&&t.certain)).length,ps.length)).join(' / ')+'</td></tr>';
    }).join('')+'</tbody></table></div>'+
    (pairs.length?'<details class="pr-review"><summary>相似案例对照</summary><p class="note">同品种、周期、规则与机会类型内，按当时价格特征寻找相似的买入和等待／放弃。</p><ul>'+
      pairs.map(([a,b])=>'<li>'+compareLine(a)+'<br>'+compareLine(b)+'</li>').join('')+'</ul></details>':'')+
    '<details class="pr-review"><summary>近期判断（按时间取最近 12 条）</summary><ul>'+
    rows.slice(-12).reverse().map(({s,d,path})=>'<li>'+prEscape(s.sym)+' · '+prEscape(PR_CHOICES[d.choice])+' · '+
      prEscape(d.reason)+' · '+prPathText(path)+'</li>').join('')+'</ul></details>';
}
function prExport(){
  if(trLocked())return;
  const json={format:'opportunity-practice-v2',exported_at:new Date().toISOString(),schema:2,sessions:PracticeStore.list(true)};
  const url=URL.createObjectURL(new Blob([JSON.stringify(json,null,2)],{type:'application/json'}));
  const a=document.createElement('a');
  a.href=url;a.download='practice-research-'+new Date().toISOString().slice(0,10)+'.json';a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function prReadImport(doc){
  const decode=v=>typeof v==='string'?JSON.parse(v):v;
  const source=doc?.format==='opportunity-practice-v2'?doc.sessions:
    doc?.format==='opportunity-practice-v1'?decode(doc.payload?.local?.chan_trainer_sessions_v1):
    Array.isArray(doc)?doc:null;
  if(!Array.isArray(source)||!source.length)throw new Error('没有找到练习局；请选择练习工作区或练习研究 JSON。');
  const num=v=>typeof v==='number'&&Number.isFinite(v)?v:0;
  const str=v=>typeof v==='string'?v:'';
  const arr=v=>Array.isArray(v)?v:[];
  return source.map(s=>{
    if(!s||!Number.isSafeInteger(s.id)||!Number.isFinite(s.ts)||!Array.isArray(s.trades))throw new Error('包含无效的练习局。');
    const out={...s, ts:s.ts, sym:str(s.sym),tf:str(s.tf),cond:str(s.cond),drill:str(s.drill),
      desc:str(s.desc),transition:str(s.transition),pnl:num(s.pnl),capital:num(s.capital),endCapital:num(s.endCapital),
      addGain:num(s.addGain),ddActual:num(s.ddActual),ddCf:num(s.ddCf),steps:num(s.steps),durSec:num(s.durSec),
      marks:arr(s.marks).filter(m=>m&&Number.isFinite(m.step)),curve:arr(s.curve).filter(p=>p&&Number.isFinite(p.eq)&&Number.isFinite(p.step)),
      trades:s.trades.filter(Boolean).map(t=>({...t,exit_time:str(t.exit_time),side:str(t.side),entry:num(t.entry),
        exit_price:num(t.exit_price),adds:num(t.adds),ratio:num(t.ratio),pnl_u:num(t.pnl_u),add_pnl:num(t.add_pnl),
        r_final:Number.isFinite(t.r_final)?t.r_final:null,max_r:Number.isFinite(t.max_r)?t.max_r:null,entries:arr(t.entries).filter(Boolean)}))};
    const r=s.research;
    if(r){
      if(r.schema!==2||!Array.isArray(r.decisions)||!Array.isArray(r.events)||!Array.isArray(r.bars)||
        !r.bars.every(PracticeCore.validBar)||!r.events.every(e=>e&&Number.isFinite(e.barTime))||
        !r.decisions.every(d=>d&&Object.hasOwn(PR_CHOICES,d.choice)&&Number.isFinite(d.barTime)&&
          Array.isArray(d.outcomes||[])&&(d.outcomes||[]).every(p=>p&&PracticeCore.HORIZONS.includes(p.horizon)&&
            Array.isArray(p.mfePct)&&Array.isArray(p.mfeR)&&Array.isArray(p.targets)&&p.targets.every(t=>t&&Number.isFinite(t.r)))))
        throw new Error('包含无效的练习研究数据。');
    }
    return out;
  });
}
function setupPracticeResearch(){
  prEl('trainerDecision').addEventListener('click',prOpenDecision);
  prEl('prCancel').addEventListener('click',prCloseDecision);
  prEl('prChoice').addEventListener('change',prDecisionFields);
  prEl('prSide').addEventListener('change',prDecisionFields);
  prEl('prParent').addEventListener('change',()=>{
    if(!prDraft||!TR.research)return;
    const parent=PracticeCore.opportunityRows({research:TR.research})
      .find(d=>d.opportunityId===prEl('prParent').value&&d.choice==='wait');
    if(!parent)return;
    prEl('prSide').value=parent.side;
    if(!prEl('prKind').value&&parent.kind)prEl('prKind').value=parent.kind;
    if(!Object.keys(prDraft.anchors).length)prDraft.anchors={...parent.anchors};
    prRenderAnchors();prDecisionFields();
  });
  prEl('practiceDecisionForm').addEventListener('submit',prSubmitDecision);
  prEl('prSignals')?.addEventListener('click',e=>{
    const c=e.target.closest('[data-pr-sig]'); if(!c)return;
    const b=document.querySelector(`#jLogicTags .logic-tag[data-cat="${c.dataset.cat}"][data-val="${c.dataset.prSig}"]`);
    if(b){b.classList.toggle('active'); if(typeof updateLtCounts==='function')updateLtCounts();}
    prRenderSignals();
  });
  document.querySelectorAll('[data-pr-pick]').forEach(el=>el.addEventListener('click',()=>prStartPick(el.dataset.prPick)));
  prEl('prClearAnchors').addEventListener('click',()=>{
    if(!prDraft)return;prDraft.anchors={};prPick=null;prEl('prDecisionError').textContent='';prRenderAnchors();
  });
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'&&prPick)prEndPick();
  });
  prEl('chart').addEventListener('pointerup',e=>{
    if(!prPick||e.button!==0||e.target.tagName!=='CANVAS')return;
    const plot=e.target.getBoundingClientRect(), x=e.clientX-plot.left, y=e.clientY-plot.top;
    if(x<0||x>=chart.timeScale().width())return;
    const time=chart.timeScale().coordinateToTime(x);
    if(!Number.isFinite(time)){
      prEl('practicePickHint').textContent='请点击有 K 线的位置。';
      return;
    }
    // Stop the separate price-line and annotation click handlers during an anchor pick.
    prAcceptPoint({time,point:{x,y}});
    e.stopPropagation();
  },true);
  prEl('prExport').addEventListener('click',prExport);
  prEl('prImport').addEventListener('change',async e=>{
    const file=e.target.files[0];e.target.value='';
    if(!file||trLocked())return;
    try{
      const rows=prReadImport(JSON.parse(await file.text()));
      await PracticeStore.ready;
      if(trLocked())return;
      const existing=new Set(PracticeStore.list(true).map(s=>s.id));
      let count=0;
      for(const s of rows){
        if(existing.has(s.id))continue;
        if(s.cond==='进行中')s.cond='中断';
        await PracticeStore.put(s);existing.add(s.id);count++;
      }
      prEl('practiceStorageStatus').textContent='已导入 '+count+' 局；已有记录按局号跳过。';
      renderTrainerSessions();renderTrainerStats();
    }catch(err){prEl('practiceStorageStatus').textContent='导入失败：'+err.message;}
  });
  prEl('practiceBatch').addEventListener('toggle',()=>{if(prEl('practiceBatch').open)prRenderBatch();});
  prEl('practiceBatch').addEventListener('change',prRenderBatch);
}
