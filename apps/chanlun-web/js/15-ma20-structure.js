/* Versioned MA20 candidates and human comparison. Reference labels never enter the engine. */
'use strict';
const MA20_V3_SOURCES={all:'ma20_structure_v3',diff:'ma20_structure_v3_diff',missed:'ma20_structure_v3_missed'};
function ma20StructureDecisionSource(source){return source===MA20_V3_SOURCES.diff?MA20_V3_SOURCES.all:source;}
const MA20_V3={on:false,open:false,mode:'diff',time:null,market:'',cache:null};
const ma20V3Escape=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ma20StructureActive=()=>Object.values(MA20_V3_SOURCES).includes(ANN.source);
function ma20StructureData(){
  const cs=candlesFor('raw'),volumes=volArrFor('raw'),ref=chunkedActive()?ChunkStore.win:L().candles;
  const key=`${currentSym}|${activeTF}|${cs.length}|${JSON.stringify(cs.at(-1))}|${volumes.at(-1)}`;
  if(!MA20_V3.cache||MA20_V3.cache.ref!==ref||MA20_V3.cache.key!==key)MA20_V3.cache={ref,key,data:MA20Structure.compute(cs,volumes)};
  return MA20_V3.cache.data;
}
function ma20StructureReference(){
  const snapshot=window.MA20ReviewReference;
  const applicable=snapshot?.symbol===currentSym&&snapshot?.tf===activeTF;
  const confirmed=new Set(applicable?snapshot.confirmedTimes:[]),drafts=new Set(applicable?snapshot.draftTimes:[]),withdrawn=new Set();
  const prefix=`${currentSym}|${activeTF}|${MANUAL_SOURCE}|`;
  for(const [key,record]of Object.entries(ANN.decisions)){
    if(!key.startsWith(prefix))continue;
    const time=Number(key.split('|')[3]);
    if(record?.manual?.archived||['no','skip'].includes(record?.d)){confirmed.delete(time);drafts.delete(time);withdrawn.add(time);}
    else if(record?.d==='yes'){confirmed.add(time);drafts.delete(time);}
    else if(record?.manual&&!confirmed.has(time))drafts.add(time);
  }
  return {confirmed,drafts,withdrawn,snapshotVersion:applicable?snapshot.version:null};
}
function ma20StructureRows(mode='all',visible=false){
  const data=ma20StructureData(),reference=ma20StructureReference();
  let rows=mode==='missed'?data.rows.filter(r=>reference.confirmed.has(r.time)&&!r.selected):data.signals;
  if(mode==='diff')rows=rows.filter(r=>!reference.confirmed.has(r.time));
  const cutoff=visible&&ANN.on&&ANN.hideFuture?ANN.sigs[ANN.idx]?.i:null;
  return cutoff==null?rows:rows.filter(r=>r.i<=cutoff);
}
function ma20StructureBuild(mode){
  return ma20StructureRows(mode).map(r=>({i:r.i,side:'buy',tag:mode==='missed'?'V3漏选·我的买点':'MA20结构V3',kind:mode==='missed'?'missed':'candidate'}));
}
function ma20StructureMeta(i){
  const r=ma20StructureData().rows[i],reference=ma20StructureReference(),bar=candlesFor('raw')[i];if(!r||!bar)return null;
  const status=reference.confirmed.has(r.time)?'confirmed_manual':reference.drafts.has(r.time)?'manual_draft':reference.withdrawn.has(r.time)?'manual_withdrawn':'unknown';
  return {version:MA20Structure.VERSION,selected:!!r.selected,family:r.family||null,reasons:r.reasons||[],
    metrics:{bodyATR:r.bodyATR,distATR:r.distATR,riskPct:r.riskPct,volRatio:r.volRatio},
    reference:{status,snapshotVersion:reference.snapshotVersion,unmarkedPolicy:'unknown',snapshotSha256:reference.snapshotVersion?window.MA20ReviewReference.sourceSha256:null},
    execution:{entryBasis:'selected_bar_close',entry:bar.close,stop:bar.low,stopChecksBegin:'next_bar'}};
}
function ma20StructureVolumeColor(v){return !Number.isFinite(v)?'#94a3b8':v>=1.2?'#38bdf8':v>=.9?'#f59e0b':'#c084fc';}
function ma20StructureVolumeOpen(i){
  document.getElementById('ma20V3Volume')?.remove();
  const row=ma20StructureData().rows[i];if(!row)return;
  const el=document.createElement('section');el.id='ma20V3Volume';el.className='ann-card';
  el.style.cssText='position:fixed;left:20px;bottom:28px;z-index:95;max-width:min(340px,80vw);padding:16px;background:var(--panel,#272b35);box-shadow:0 8px 30px #0006';
  const n=Number.isFinite(row.volRatio)?row.volRatio.toFixed(2):'未知';
  el.innerHTML=`<button class="btn" style="float:right" onclick="this.parentElement.remove()">关闭</button><b>${annDate(row.time)} · V3量能</b><p style="color:${ma20StructureVolumeColor(row.volRatio)}">本根量比：${n}</p><p>量比 = 本根成交量 ÷ 前20根成交量的平均值（不含本根）。</p><p>蓝 ≥1.2；橙 ≥0.9 且 &lt;1.2；紫 &lt;0.9；缺量为灰色。量能不决定是否入选，颜色不代表胜率。</p>`;
  document.body.appendChild(el);
}
function referenceReason(time){const r=window.MA20ReviewReference;return r?.symbol===currentSym&&r?.tf===activeTF?r.reasonsByTime?.[time]||'':'';}
function ma20StructureCard(i){
  if(!ma20StructureActive())return '';
  const r=ma20StructureData().rows[i],m=ma20StructureMeta(i);if(!m)return '';
  const n=v=>Number.isFinite(v)?v.toFixed(2):'—',pct=v=>Number.isFinite(v)?(v*100).toFixed(2)+'%':'—';
  const manualReason=annDecisionOf(r.time,MANUAL_SOURCE)?.manual?.reason||(referenceReason(r.time));
  const label={confirmed_manual:'与你已确认的买点重合',manual_draft:'手选草稿，尚未确认',manual_withdrawn:'本机已撤回／否定的手选位置',unknown:'你的已确认买点中没有此点，等待复核'}[m.reference.status];
  return `<section class="ann-card ma20-v3-card"><div class="ann-sec">MA20 结构识别 V3 · ${ma20V3Escape(currentSym)} ${ma20V3Escape(activeTF)}</div>
    <p class="ann-line"><b>${r.selected?'算法候选':'算法未选入的手选买点'}</b> · ${label}</p>
    <p class="ann-line">${(r.reasons||[]).map(ma20V3Escape).join('；')||'未满足当前规则。请记录你认可它的结构依据。'}</p>
    <div class="ann-grid"><span>入场 · 本根收盘</span><b>${fmt(m.execution.entry)}</b><span>止损 · 本根最低</span><b>${fmt(m.execution.stop)}</b><span>初始风险距离</span><b>${pct(r.riskPct)}</b><span>实体 / ATR</span><b>${n(r.bodyATR)}</b><span>距 MA20 / ATR</span><b>${n(r.distATR)}</b></div>
    <button class="btn" onclick="ma20StructureVolumeOpen(${i})">在图表查看量能</button><p class="ann-line">买入后从下一根开始统计止损。当前仅显示入场时信息；未标注为未知，不自动判“不做”。</p>
    ${manualReason?`<details><summary>手选买入依据</summary><p class="ann-line" style="white-space:pre-wrap">${ma20V3Escape(manualReason)}</p></details>`:''}<p class="ann-line">差异复核：会做可补充买入依据；不做请选原因或写备注，例如“区域不合适”“触发太弱”“调整未结束”“止损距离过大”。</p>
  </section>`;
}
for(const [mode,source]of Object.entries(MA20_V3_SOURCES))ANN_SOURCES[source]={
  name:{all:'MA20 结构 V3 · 全部候选',diff:'MA20 结构 V3 · 额外候选待复核',missed:'MA20 结构 V3 · 我的漏选'}[mode],group:'我的规则',build(){return ma20StructureBuild(mode);}
};
manualReviewSource=MA20_V3_SOURCES.diff;
function ma20StructureMarkers(view){
  if(!MA20_V3.on)return [];
  const reference=ma20StructureReference(),cs=candlesFor(view),groups=new Map();
  for(const r of ma20StructureRows(MA20_V3.mode,true)){
    const i=cover67IndicatorBar(r.time,cs);if(i<0)continue;
    const time=cs[i].time;if(!groups.has(time))groups.set(time,[]);groups.get(time).push(r);
  }
  return [...groups].map(([time,rows])=>({time,id:'ma20v3:'+rows[0].time,position:'belowBar',shape:'circle',
    color:ma20StructureVolumeColor(rows[0].volRatio),
    size:rows.some(r=>r.time===MA20_V3.time)?1.5:1,text:rows.length>1?`V3 ×${rows.length}`:reference.confirmed.has(rows[0].time)?'V3✓':'V3'}));
}
function ma20StructureRefresh(){
  const panel=document.getElementById('ma20StructurePanel');if(!panel)return;
  const market=`${currentSym}|${activeTF}`;if(MA20_V3.market!==market){MA20_V3.market=market;MA20_V3.time=null;document.getElementById('ma20V3Volume')?.remove();}
  const toggle=document.getElementById('ma20StructureToggle');toggle.classList.toggle('active',MA20_V3.on);toggle.setAttribute('aria-pressed',String(MA20_V3.on));
  if(!MA20_V3.on&&!MA20_V3.open){toggle.textContent='结构V3';panel.hidden=true;return;}
  const rows=ma20StructureRows(MA20_V3.mode,true),reference=ma20StructureReference();
  if(!rows.some(r=>r.time===MA20_V3.time))MA20_V3.time=null;
  toggle.textContent=MA20_V3.on?`结构V3 ${rows.length}`:'结构V3';panel.hidden=!MA20_V3.open;
  panel.style.top=Math.ceil(document.querySelector('header').getBoundingClientRect().bottom+8)+'px';
  if(MA20_V3.open){
    panel.querySelector('[data-v3-status]').textContent=`${currentSym} ${activeTF} · ${rows.length} 条${ANN.on&&ANN.hideFuture?' · 仅当前标注及以前':''}`;
    panel.querySelector('[data-v3-mode]').value=MA20_V3.mode;
    panel.querySelector('[data-v3-reference]').textContent=reference.snapshotVersion?`对照：${reference.snapshotVersion} 上传的手选买点＋本机手选更新。`:'对照：当前品种／周期的本机已确认手选买点；未标注位置为未知。';
    const ix=rows.findIndex(r=>r.time===MA20_V3.time);
    panel.querySelector('[data-v3-position]').textContent=ix<0?'点击条目定位':`${ix+1} / ${rows.length}`;
    panel.querySelector('[data-v3-prev]').disabled=!rows.length||ix===0;
    panel.querySelector('[data-v3-next]').disabled=!rows.length||ix===rows.length-1;
    panel.querySelector('[data-v3-entries]').innerHTML=rows.map((r,k)=>{
      const status=reference.confirmed.has(r.time)?'手选已确认':reference.drafts.has(r.time)?'手选草稿':'待复核';
      const d=annDecisionOf(r.time,MA20_V3_SOURCES[MA20_V3.mode]);
      return `<button class="c67-entry" data-v3-index="${k}" aria-current="${r.time===MA20_V3.time}"><span>${k+1}. ${ma20V3Escape(ManualEvidence.stamp(candlesFor('raw')[r.i],activeTF))}</span><b>${fmt(candlesFor('raw')[r.i].close)}</b><small>${status} · ${d?.d?({yes:'本轮会做',no:'本轮不做',skip:'本轮跳过'}[d.d]||'已标'):'本轮未标'}</small></button>`;
    }).join('')||'<p class="note">当前条件下没有条目。</p>';
  }
}
function ma20StructureSelect(index){
  const r=ma20StructureRows(MA20_V3.mode,true)[index];if(!r)return;
  MA20_V3.time=r.time;ma20StructureRefresh();candleSeries.setMarkers(buildMarkers(activeView));
  const position=()=>{if(MA20_V3.time!==r.time)return;const i=cover67IndicatorBar(r.time,candlesFor(activeView));if(i>=0)chart.timeScale().setVisibleLogicalRange({from:i-65,to:i+25});};
  position();requestAnimationFrame(position);setTimeout(position,160);
  document.querySelector('[data-v3-entries] [aria-current="true"]')?.scrollIntoView({block:'nearest'});
}
function ma20StructureReview(mode=MA20_V3.mode,time=MA20_V3.time){
  MA20_V3.open=false;if(activeView!=='raw')setView('raw');ANN.source=MA20_V3_SOURCES[mode];manualReviewSource=ANN.source;ANN.idx=0;ANN.hideFuture=true;
  document.getElementById('annHide').checked=true;annSetOpen(true);
  const idx=ANN.sigs.findIndex(s=>candlesFor('raw')[s.i].time===time);if(idx>=0)annGo(idx);
  ma20StructureRefresh();
}
(function(){
  const toolbar=document.getElementById('indicatorToggle');
  const toggle=document.createElement('button');toggle.id='ma20StructureToggle';toggle.textContent='结构V3';toggle.setAttribute('aria-pressed','false');toggle.title='按新规则显示MA20候选';toolbar.appendChild(toggle);
  const list=document.createElement('button');list.id='ma20StructureList';list.textContent='V3差异复核';toolbar.appendChild(list);
  const panel=document.createElement('section');panel.id='ma20StructurePanel';panel.className='c67-indicator-panel ma20-v3-panel';panel.hidden=true;
  panel.innerHTML=`<div class="c67-panel-head"><b>MA20 结构识别 V3</b><button class="btn" data-v3-close>×</button></div>
    <p class="note"><a href="docs/ma20-review-2026-11-03.html" target="_blank" rel="noopener">查看 BTC 买点统计与规则说明 ↗</a></p><p class="note" data-v3-status></p><p class="note">点色：蓝 ≥1.2 倍量 · 橙 0.9–1.2 · 紫 &lt;0.9 · 灰未知；✓ 与手选买点重合。量能只提示，不筛选。</p><p class="note" data-v3-reference></p>
    <label class="ann-form-field">查看范围<select data-v3-mode><option value="diff">算法额外候选 · 待复核</option><option value="all">全部算法候选</option><option value="missed">我的买点 · 算法漏选</option></select></label>
    <div class="c67-panel-nav"><button class="btn" data-v3-prev>上一条</button><span data-v3-position></span><button class="btn" data-v3-next>下一条</button></div>
    <div class="c67-entries" data-v3-entries></div><button class="btn" data-v3-review>进入本组标注</button>`;
  document.body.appendChild(panel);
  toggle.onclick=()=>{MA20_V3.on=!MA20_V3.on;if(!MA20_V3.on)MA20_V3.open=false;ma20StructureRefresh();candleSeries.setMarkers(buildMarkers(activeView));};
  list.onclick=()=>{MA20_V3.on=true;MA20_V3.open=!MA20_V3.open;if(MA20_V3.open){COVER67_INDICATOR.open=false;cover67IndicatorRefresh();}ma20StructureRefresh();candleSeries.setMarkers(buildMarkers(activeView));};
  panel.querySelector('[data-v3-close]').onclick=()=>{MA20_V3.open=false;ma20StructureRefresh();};
  panel.querySelector('[data-v3-mode]').onchange=e=>{MA20_V3.mode=e.target.value;MA20_V3.time=null;ma20StructureRefresh();candleSeries.setMarkers(buildMarkers(activeView));};
  panel.querySelector('[data-v3-entries]').onclick=e=>{const b=e.target.closest('[data-v3-index]');if(b)ma20StructureSelect(Number(b.dataset.v3Index));};
  for(const [attr,step]of [['prev',-1],['next',1]])panel.querySelector(`[data-v3-${attr}]`).onclick=()=>{const rows=ma20StructureRows(MA20_V3.mode,true),i=rows.findIndex(r=>r.time===MA20_V3.time);ma20StructureSelect(i<0?0:i+step);};
  panel.querySelector('[data-v3-review]').onclick=()=>ma20StructureReview();
  chart.subscribeClick(p=>{if(annChartPickBusy())return;const id=p.hoveredObjectId;if(typeof id==='string'&&id.startsWith('ma20v3:')){const time=Number(id.slice(7));ma20StructureReview(MA20_V3.mode,time);}});
  window.addEventListener('resize',ma20StructureRefresh);
  if(typeof MA20Structure!=='undefined'){annSourceMenuRender();ma20StructureRefresh();}
})();
