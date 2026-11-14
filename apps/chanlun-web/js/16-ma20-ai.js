/* Preference-learning pilot: advisory only, no automatic deletion of V3 signals. */
'use strict';
const MA20_AI_SOURCE='ma20_ai_v2';
const MA20_AI_ENGINE=MA20AIStructure;
const MA20_AI_BLIND=new Set(MA20AIModel.blindReviewTimes||[]);
const MA20_AI_RESERVED=new Set(MA20AIModel.reservedAllTimes||[]);
const MA20_AI={on:false,open:false,mode:'blind',showScores:false,time:null,contextTime:null,cache:null,batch:null,market:''};
function ma20AIActive(){return ANN.source===MA20_AI_SOURCE;}
function ma20AISupported(){return currentSym===MA20AIModel.scope.symbol&&activeTF===MA20AIModel.scope.tf;}
function ma20AIData(){
 if(!ma20AISupported())return {rows:[],signals:[]};
 const cs=candlesFor('raw'),ref=chunkedActive()?ChunkStore.win:L().candles,key=`${currentSym}|${activeTF}|${cs.length}|${JSON.stringify(cs.at(-1))}|${volArrFor('raw').at(-1)}`;
 if(MA20_AI.cache?.ref===ref&&MA20_AI.cache.key===key)return MA20_AI.cache.data;
 const data=MA20_AI_ENGINE.compute(cs,volArrFor('raw')),base=ma20StructureData(),model=MA20AIModel.models[MA20AIModel.selected];
 const rows=data.rows.map(r=>{const score=MA20_AI_ENGINE.score(r,model),linear=MA20_AI_ENGINE.score(r,MA20AIModel.models.logistic),tree=MA20_AI_ENGINE.score(r,MA20AIModel.models.gbdt);return {...r,selected:!!base.rows[r.i]?.selected,score,linear,tree,band:score>=MA20AIModel.threshold?'high':score>=MA20AIModel.lowThreshold?'borderline':'low'};});
 const result={rows,signals:rows.filter(r=>r.selected)};MA20_AI.cache={ref,key,data:result};return result;
}
function ma20AIReference(){const ref=ma20StructureReference();for(const [t,v]of Object.entries(MA20AIModel.labelReference||{}))if(v.y===1&&!ref.withdrawn.has(Number(t)))ref.confirmed.add(Number(t));return ref;}
function ma20AIAnnotated(time){const d=annDecisionOf(time,MA20_AI_SOURCE);return !!d?.d;}
function ma20AINextBatch(){
 const ref=ma20AIReference(),pool=ma20AIData().signals.filter(r=>!MA20_AI_RESERVED.has(r.time)&&!ref.confirmed.has(r.time)&&!ma20AIAnnotated(r.time)&&MA20AIModel.labelReference[r.time]?.status!=='new_review_confirmed');
 const chosen=[],seen=new Set(),add=(list,n,reason)=>{if(n<=0)return;let count=0;for(const r of list){if(seen.has(r.time))continue;chosen.push({...r,queueReason:reason});seen.add(r.time);if(++count>=n)break;}};
 add(pool.filter(r=>MA20AIModel.labelReference[r.time]?.status==='legacy_negative_weak').sort((a,b)=>b.score-a.score),6,'旧轮次标注需要复核');
 add([...pool].sort((a,b)=>Math.abs(a.score-MA20AIModel.threshold)-Math.abs(b.score-MA20AIModel.threshold)),6,'接近验证段门槛');
 add([...pool].sort((a,b)=>Math.abs(b.linear-b.tree)-Math.abs(a.linear-a.tree)),4,'两个模型意见差异较大');
 const hash=t=>{let x=(t^20261104)>>>0;x=Math.imul(x^(x>>>16),0x45d9f3b);return(x^(x>>>16))>>>0;};
 add([...pool].sort((a,b)=>hash(a.time)-hash(b.time)),4,'固定随机对照');
 add([...pool].sort((a,b)=>a.time-b.time),20-chosen.length,'补齐复核批次');
 MA20_AI.batch=chosen.slice(0,20).sort((a,b)=>a.time-b.time).map(r=>({time:r.time,reason:r.queueReason}));return MA20_AI.batch;
}
function ma20AIRows(mode=MA20_AI.mode,visible=false){
 if(!ma20AISupported())return [];
 const d=ma20AIData(),ref=ma20AIReference();let rows;
 if(mode==='blind'){const bytime=new Map(d.rows.map(r=>[r.time,r]));rows=[...MA20_AI_BLIND].map(t=>bytime.has(t)?{...bytime.get(t),queueReason:'整组未入训练 · 待盲检'}:null).filter(Boolean);
 }else if(mode==='batch'){
  if(!MA20_AI.batch)ma20AINextBatch();const bytime=new Map(d.rows.map(r=>[r.time,r]));rows=MA20_AI.batch.map(b=>bytime.has(b.time)?{...bytime.get(b.time),queueReason:b.reason}:null).filter(Boolean);
 }else if(mode==='previous'||mode==='previous_low'){const bytime=new Map(d.rows.map(r=>[r.time,r]));rows=MA20AIModel.report.feedbackRows.filter(x=>mode==='previous'||(x.decision==='yes'&&x.oldScore<MA20AIModel.previousThreshold)).map(x=>({...bytime.get(x.time),queueReason:'上轮已用于开发 · 回看'}));
 }else if(mode==='outside')rows=d.rows.filter(r=>ref.confirmed.has(r.time)&&!r.selected);
 else if(mode==='rejected')rows=d.signals.filter(r=>ref.confirmed.has(r.time)&&r.score<MA20AIModel.threshold);
 else if(mode==='high')rows=d.signals.filter(r=>(!MA20_AI_RESERVED.has(r.time)||ma20AIAnnotated(r.time))&&r.score>=MA20AIModel.threshold);
 else if(mode==='low')rows=d.signals.filter(r=>(!MA20_AI_RESERVED.has(r.time)||ma20AIAnnotated(r.time))&&r.score<MA20AIModel.threshold);
 else rows=d.signals;
 const cutoff=visible&&ANN.on&&ANN.hideFuture?ANN.sigs[ANN.idx]?.i:null;
 return cutoff==null?rows:rows.filter(r=>r.i<=cutoff);
}
ANN_SOURCES[MA20_AI_SOURCE]={name:'AI偏好 v2 · 当前复核组',group:'我的规则',build(){return ma20AIRows().map(r=>({i:r.i,side:'buy',tag:'AI偏好复核',kind:'ai_preference'}));}};
function ma20AIMeta(i,decision=null){
 const r=ma20AIData().rows[i],c=candlesFor('raw')[i];if(!r||!c)return null;
 const blind=MA20_AI_RESERVED.has(r.time),prev=annDecisionOf(r.time,MA20_AI_SOURCE)?.algorithm?.blindReview;
 const firstDecision=prev?.firstDecision??decision,firstAt=prev?.firstJudgedAt??(decision?Date.now():null);
 return {version:MA20AIModel.version,featureVersion:MA20_AI_ENGINE.VERSION,task:'human_preference_review',model:MA20AIModel.selected,score:r.score,scoreMeaning:'uncalibrated_preference_not_win_probability',threshold:MA20AIModel.threshold,band:r.band,gatePassed:MA20AIModel.gatePassed,
  reviewMode:MA20_AI.mode,queueReason:MA20_AI_BLIND.has(r.time)?'整组留出盲检':MA20_AI.batch?.find(x=>x.time===r.time)?.reason||null,firstStageSelected:r.selected,
  historicalStage:blind?'reserved_group_blind_not_prospective':'development_refit_not_out_of_sample',evaluationRole:MA20_AI_BLIND.has(r.time)?'reserved_blind_review':blind?'reserved_context':'development',episode:MA20AIModel.groupReference?.[r.time]??null,
  blindReview:MA20_AI_BLIND.has(r.time)?{firstDecision,firstJudgedAt:firstAt,firstModelVersion:prev?.firstModelVersion??MA20AIModel.version,firstScore:prev?.firstScore??r.score,firstThreshold:prev?.firstThreshold??MA20AIModel.threshold,scoreHiddenUntilFirstDecision:true,revisedAfterExposure:!!prev?.firstDecision&&decision!==null&&decision!==prev.firstDecision}:null,trainingSnapshot:MA20AIModel.report.inputs.annotationsSha256,feedbackSnapshot:MA20AIModel.report.inputs.feedbackSha256,features:Object.fromEntries(MA20_AI_ENGINE.NAMES.map((n,j)=>[n,r.values[j]])),
  execution:{entryBasis:'selected_bar_close',entry:c.close,stop:c.low,stopChecksBegin:'next_bar'},labelCollection:{source:MA20_AI_SOURCE,unmarkedPolicy:'unknown'}};
}
function ma20AICard(i){
 const r=ma20AIData().rows[i],meta=ma20AIMeta(i);if(!r||!meta)return '';
 const esc=ma20V3Escape,locked=MA20_AI_RESERVED.has(r.time)&&!ma20AIAnnotated(r.time),stage=MA20_AI_RESERVED.has(r.time)?'整组已从训练和开发检查中排除；这是新标签盲检，仍非新行情前瞻':'开发样本已参与拟合；分数不可当作独立验证';
 const pct=v=>(100*v).toFixed(0)+'分',f=v=>Number.isFinite(v)?v.toFixed(2):'—',previous=MA20AIModel.report.feedbackRows.find(x=>x.time===r.time);
 return `<section class="ann-card"><div class="ann-sec">AI偏好 v2 · 辅助复核</div><p class="ann-line"><b>${r.selected?'原V3候选':'原候选池漏选'}</b> · 先按图形给出你的判断，再查看模型意见。</p>
  <p class="ann-line">开发检查仍未达标，新一轮盲检待完成；本分数只辅助复核，不表示盈利概率。${stage}。</p>
  ${locked?'<p class="ann-line">请先独立判断；盲检点提交判定后才能查看模型意见。</p>':`<details ${MA20_AI.showScores?'open':''}><summary>查看模型意见 · 偏好分不是胜率</summary><p class="ann-line">当前偏好 ${pct(r.score)} · ${r.score>=MA20AIModel.threshold?'高于验证门槛':'低于验证门槛'}</p><div class="ann-grid"><span>逻辑回归对照</span><b>${pct(r.linear)}</b><span>浅层树</span><b>${pct(r.tree)}</b><span>验证段门槛</span><b>${pct(MA20AIModel.threshold)}</b><span>实体 / ATR</span><b>${f(r.values[0])}</b><span>距MA20 / ATR</span><b>${f(r.values[5])}</b><span>此前上穿次数</span><b>${f(r.values[15])}</b></div></details>`}
  <p class="ann-line">收盘入场 ${fmt(meta.execution.entry)} · 初始止损 ${fmt(meta.execution.stop)}。只查看当根及以前的图形；理由请按你的判断填写。</p>
  ${locked?'':`<details><summary>线性对照模型的主要加减分因素</summary><p class="ann-line">这是逻辑回归相对训练均值的贡献，不是浅层树的原因，也不代表因果。</p>${MA20_AI_ENGINE.explain(r,MA20AIModel.models.logistic).map(x=>`<p class="ann-line">${esc(x.label)}：${x.contribution>=0?'+':''}${x.contribution.toFixed(2)}（当前 ${f(x.value)}）</p>`).join('')}</details>`}
  ${previous?`<details><summary>上轮你的判定与备注</summary><p class="ann-line">${previous.decision==='yes'?'会做':'不做'} · ${esc(previous.note||previous.reasons.join('、')||'未填写理由')}</p><p class="ann-line">此点已用于开发；当前分数不是独立检验。</p></details>`:''}
  <details><summary>自动参照结构（本版评分仍采用原26特征）</summary><p class="ann-line">新增7特征的向前检查没有优于简单模型，暂只展示研究值。自动段不等于你手选的整理区间。</p><div class="ann-grid"><span>距60根低点涨幅</span><b>${Number.isFinite(r.values[26])?(r.values[26]*100).toFixed(1)+'%':'—'}</b><span>前段上涨幅度</span><b>${Number.isFinite(r.values[27])?(r.values[27]*100).toFixed(1)+'%':'—'}</b><span>回撤/前段上涨</span><b>${Number.isFinite(r.values[28])?(r.values[28]*100).toFixed(1)+'%':'—'}</b><span>调整/上涨时间</span><b>${f(r.values[29])} 倍</b><span>调整后/前半波幅</span><b>${f(r.values[30])}</b><span>DIF/ATR</span><b>${f(r.values[31])}</b><span>底部整理/下跌时间</span><b>${f(r.values[32])} 倍</b></div><p class="ann-line">回撤比超过100%表示跌破该参照上涨起点，不是直接判定不做；波幅比未知可能因为调整不足6根。</p><button class="btn" onclick="ma20AIContext(${i})">图上查看自动参考高低点</button></details>
  <details><summary>复核填写示例</summary><p class="ann-line">例如：不做。区域仍在高位来回穿越，调整时间不足；本根虽强，但不是我等待的整理后启动。可选表单中标出调整开始、结束与参照高点。</p></details>
 </section>`;
}
function ma20AIContext(i){const row=ma20AIData().rows[i];if(!row)return;MA20_AI.contextTime=row.time;MA20_AI.on=true;candleSeries.setMarkers(buildMarkers(activeView));const apply=()=>chart.timeScale().setVisibleLogicalRange({from:Math.max(0,(row.anchors?.advanceLow?.i??i-90)-10),to:i+20});apply();requestAnimationFrame(apply);}
function ma20AIMarkers(view){
 if(!MA20_AI.on||!ma20AISupported())return [];
 const cs=candlesFor(view),groups=new Map();
 for(const r of ma20AIRows(MA20_AI.mode,true)){const i=cover67IndicatorBar(r.time,cs);if(i<0)continue;const time=cs[i].time;if(!groups.has(time))groups.set(time,r);}
 const points=[...groups].map(([time,r])=>({time,id:'ma20ai:'+r.time,position:'belowBar',shape:'circle',color:MA20_AI_RESERVED.has(r.time)&&!ma20AIAnnotated(r.time)?'#38bdf8':!MA20_AI.showScores?'#38bdf8':r.band==='high'?'#22c55e':r.band==='borderline'?'#f59e0b':'#94a3b8',text:'AI',size:r.time===MA20_AI.time?1.5:1}));
 const context=ma20AIData().rows.find(r=>r.time===MA20_AI.contextTime);const cutoff=ANN.on&&ANN.hideFuture?ANN.sigs[ANN.idx]?.i:null;if(context&&context.anchors&&(cutoff==null||context.i<=cutoff)){for(const [key,label,pos,color]of [['advanceLow','参考低A','belowBar','#22c55e'],['peak','参考高B','aboveBar','#f59e0b'],['postPeakLow','段内低C','belowBar','#a78bfa']]){const a=context.anchors[key];if(!a)continue;const j=cover67IndicatorBar(a.time,cs);if(j>=0)points.push({time:cs[j].time,position:pos,color,shape:'square',text:label,size:1});}}return points;
}
function ma20AISelect(index){
 const r=ma20AIRows(MA20_AI.mode,true)[index];if(!r)return;MA20_AI.time=r.time;MA20_AI.contextTime=null;ma20AIRefresh();candleSeries.setMarkers(buildMarkers(activeView));
 const move=()=>{if(MA20_AI.time!==r.time)return;const i=cover67IndicatorBar(r.time,candlesFor(activeView));if(i>=0)chart.timeScale().setVisibleLogicalRange({from:i-90,to:i+20});};move();requestAnimationFrame(move);setTimeout(move,160);
}
function ma20AIReview(time=MA20_AI.time){
 if(!ma20AISupported())return;
 MA20_AI.open=false;if(activeView!=='raw')setView('raw');ANN.source=MA20_AI_SOURCE;manualReviewSource=MA20_AI_SOURCE;ANN.idx=0;ANN.hideFuture=true;ANN._counts={};document.getElementById('annHide').checked=true;annSetOpen(true);
 const ix=ANN.sigs.findIndex(s=>candlesFor('raw')[s.i].time===time);if(ix>=0)annGo(ix);ma20AIRefresh();
}
function ma20AIRefresh(){
 const panel=document.getElementById('ma20AIPanel');if(!panel)return;
 const market=`${currentSym}|${activeTF}`;if(market!==MA20_AI.market){MA20_AI.market=market;MA20_AI.batch=null;MA20_AI.time=null;MA20_AI.contextTime=null;}
 const supported=ma20AISupported(),rows=ma20AIRows(MA20_AI.mode,true),toggle=document.getElementById('ma20AIToggle');toggle.setAttribute('aria-expanded',String(MA20_AI.open));toggle.classList.toggle('active',MA20_AI.on);panel.hidden=!MA20_AI.open;
 if(!MA20_AI.open)return;panel.style.top=Math.ceil(document.querySelector('header').getBoundingClientRect().bottom+8)+'px';
 panel.querySelector('[data-ai-status]').textContent=supported?`BTC 1D · ${rows.length} 条${ANN.on&&ANN.hideFuture?' · 仅当前标注及以前':''}`:'当前仅支持 BTC 日线；其他品种和周期尚未学习你的标签。';
 for(const el of panel.querySelectorAll('select,[data-ai-nextbatch],[data-ai-review]'))el.disabled=!supported;
 panel.querySelector('[data-ai-mode]').value=MA20_AI.mode;
 const blindDone=[...MA20_AI_BLIND].filter(t=>ma20AIAnnotated(t)).length;panel.querySelector('[data-ai-blind]').textContent=`盲检已标 ${blindDone}/${MA20_AI_BLIND.size}；该批整组不参与训练。`;
 panel.querySelector('[data-ai-entries]').innerHTML=rows.map((r,k)=>{const d=annDecisionOf(r.time,MA20_AI_SOURCE);return `<button class="c67-entry" data-ai-index="${k}" aria-current="${r.time===MA20_AI.time}"><span>${k+1}. ${ma20V3Escape(ManualEvidence.stamp(candlesFor('raw')[r.i],activeTF))}</span><b>${MA20_AI_RESERVED.has(r.time)&&!d?.d?'盲检未判':MA20_AI.showScores?(r.score*100).toFixed(0)+'分':d?.d?'已判':'待复核'}</b><small>${ma20V3Escape(r.queueReason||(MA20_AI_RESERVED.has(r.time)&&!d?.d?'整组未入训练':({high:'偏好较高',borderline:'接近门槛',low:'偏好较低'}[r.band])))} · ${d?.d?({yes:'会做',no:'不做',skip:'跳过'}[d.d]||'已标'):'本轮未标'}</small></button>`;}).join('')||'<p class="note">本组没有条目。</p>';
 const ix=rows.findIndex(r=>r.time===MA20_AI.time);panel.querySelector('[data-ai-position]').textContent=ix<0?'点击条目定位':`${ix+1} / ${rows.length}`;panel.querySelector('[data-ai-prev]').disabled=!supported||!rows.length||ix===0;panel.querySelector('[data-ai-next]').disabled=!supported||!rows.length||ix===rows.length-1;
}
(function(){
 const button=document.createElement('button');button.id='ma20AIToggle';button.textContent='AI买点';button.setAttribute('aria-expanded','false');document.getElementById('indicatorToggle').appendChild(button);
 const panel=document.createElement('section');panel.id='ma20AIPanel';panel.className='c67-indicator-panel';panel.hidden=true;panel.innerHTML=`<div class="c67-panel-head"><b>AI学习你的买点 · v2</b><button class="btn" data-ai-close>×</button></div>
  <p class="note" data-ai-status></p><p class="note">已合并上一轮20条反馈；开发检查仍未达标。本轮20条用于独立新标签盲检，整组已从训练排除；默认隐藏分数，避免影响判断；开启后绿色/橙色/灰色表示偏好分段，不是胜率。原V3候选可完整查看。</p>
  <p class="note"><a href="docs/ma20-ai-v2-2026-11-04.html" target="_blank" rel="noopener">查看训练结果、止损率和运行空间 ↗</a></p>
  <label class="ann-form-field">复核范围<select data-ai-mode><option value="blind">本轮20条 · 整组留出盲检</option><option value="batch">开发分歧 · 继续补标</option><option value="previous_low">上轮4个低分正例 · 开发回看</option><option value="previous">上轮20条 · 开发回看</option><option value="all">全部原V3候选</option><option value="high">模拟保留 · 高于验证门槛</option><option value="low">模拟过滤 · 低于验证门槛</option><option value="rejected">我的买点 · 被模型低估</option><option value="outside">我的买点 · 原候选池漏选</option></select></label>
  <p class="note" data-ai-blind></p><label class="note"><input type="checkbox" data-ai-scores>查看模型分数与分段颜色（建议先判断）</label><button class="btn" data-ai-nextbatch>生成开发复核20条</button><div class="c67-panel-nav"><button class="btn" data-ai-prev>上一条</button><span data-ai-position></span><button class="btn" data-ai-next>下一条</button></div><div class="c67-entries" data-ai-entries></div><button class="btn" data-ai-review>进入本组标注</button>`;
 document.body.appendChild(panel);
 button.onclick=()=>{MA20_AI.open=!MA20_AI.open;MA20_AI.on=MA20_AI.open;if(MA20_AI.open){MA20_V3.open=false;MA20_V3.on=false;ma20StructureRefresh();COVER67_INDICATOR.open=false;cover67IndicatorRefresh();}ma20AIRefresh();candleSeries.setMarkers(buildMarkers(activeView));};
 panel.querySelector('[data-ai-scores]').onchange=e=>{MA20_AI.showScores=e.target.checked;ma20AIRefresh();candleSeries.setMarkers(buildMarkers(activeView));if(ma20AIActive()&&ANN.on)annRender();};
 panel.querySelector('[data-ai-close]').onclick=()=>{MA20_AI.open=false;ma20AIRefresh();};
 panel.querySelector('[data-ai-mode]').onchange=e=>{if(ma20AIActive()&&ANN.on)annSetOpen(false);MA20_AI.mode=e.target.value;MA20_AI.time=null;ma20AIRefresh();candleSeries.setMarkers(buildMarkers(activeView));};
 panel.querySelector('[data-ai-nextbatch]').onclick=()=>{if(ma20AIActive()&&ANN.on)annSetOpen(false);ma20AINextBatch();MA20_AI.mode='batch';MA20_AI.time=null;ma20AIRefresh();candleSeries.setMarkers(buildMarkers(activeView));};
 panel.querySelector('[data-ai-entries]').onclick=e=>{const b=e.target.closest('[data-ai-index]');if(b)ma20AISelect(Number(b.dataset.aiIndex));};
 panel.querySelector('[data-ai-review]').onclick=()=>ma20AIReview();
 for(const [key,step]of [['prev',-1],['next',1]])panel.querySelector(`[data-ai-${key}]`).onclick=()=>{const rows=ma20AIRows(MA20_AI.mode,true),i=rows.findIndex(r=>r.time===MA20_AI.time);ma20AISelect(i<0?0:i+step);};
 chart.subscribeClick(p=>{if(annChartPickBusy())return;const id=p.hoveredObjectId;if(typeof id==='string'&&id.startsWith('ma20ai:'))ma20AIReview(Number(id.slice(7)));});
 window.addEventListener('resize',ma20AIRefresh);manualReviewSource=MA20_AI_SOURCE;annSourceMenuRender();ma20AIRefresh();
 if(new URLSearchParams(location.search).get('ma20ai')==='1')button.click();
})();
