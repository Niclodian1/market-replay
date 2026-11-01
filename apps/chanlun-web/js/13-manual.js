// 从图表主动收集正样本。未选位置是未知；算法复核沿用独立信号源。
const MANUAL_SOURCE = 'manual_buy_v1';
let manualReviewSource = 'ma20_simple_v2';
let manualUndo = null;
const manualActive = () => ANN.source === MANUAL_SOURCE;
const manualEscape = value => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function manualBuild(){
  const prefix=`${currentSym}|${activeTF}|${MANUAL_SOURCE}|`;
  const indices=new Map(candlesFor('raw').map((bar,i)=>[bar.time,i]));
  return Object.entries(ANN.decisions).filter(([key,rec])=>key.startsWith(prefix)&&rec?.manual&&!rec.manual.archived)
    .map(([key,rec])=>({i:indices.get(Number(key.split('|')[3])),side:'buy',tag:rec.d==='yes'?'我的买点':'买点草稿'}))
    .filter(s=>s.i!==undefined).sort((a,b)=>a.i-b.i);
}
function manualModeUI(){
  document.getElementById('annDrawer')?.classList.toggle('manual-mode',manualActive());
  for(const button of document.querySelectorAll('[data-ann-mode]')){
    button.classList.toggle('active',(button.dataset.annMode==='manual')===manualActive());
    button.setAttribute('aria-pressed',String((button.dataset.annMode==='manual')===manualActive()));
  }
}
function manualSetMode(mode){
  annCancelChartPick(true);
  if(mode==='manual'){
    if(!manualActive())manualReviewSource=ANN.source;
    ANN.source=MANUAL_SOURCE;
  }else ANN.source=manualReviewSource;
  ANN.hideFuture=mode!=='manual';
  document.getElementById('annHide').checked=ANN.hideFuture;
  ANN.idx=0; ANN._counts={};
  annBuildSigs();annSourceMenuRender();annApplyBlind();annRender();annDraw();
  if(!manualActive()&&ANN.sigs.length)annGo(0);
}
function manualStartPick(){
  if(!manualActive())manualSetMode('manual');
  if(!ANN.on)annSetOpen(true);
  if(activeView!=='raw'){
    const range=chart.timeScale().getVisibleRange();setView('raw');
    if(range)chart.timeScale().setVisibleRange(range);
  }
  if(typeof drawingMgr!=='undefined'&&drawingMgr){drawingMgr.setTool(null);paintDrawButtons('none');}
  if(typeof cover67VolumeClose==='function')cover67VolumeClose();
  annCancelChartPick(true);
  ANN_CHART_PICK.target='manualEntry';ANN_CHART_PICK.context=annChartPickContext();
  const banner=document.getElementById('annChartPickBanner');
  banner.querySelector('span').textContent='点击你会买入的K线，按当根收盘价记录；不限指标（Esc取消）';
  banner.hidden=false;document.getElementById('chart').classList.add('ann-picking');
  annApplyBlind();annDraw();
}
function manualHandlePick(param){
  const target=ANN_CHART_PICK.target;
  if(!target||!manualActive())return false;
  if(ANN_CHART_PICK.context!==annChartPickContext()||activeView!=='raw'){annCancelChartPick(true);return true;}
  const cs=candlesFor('raw'),i=cs.findIndex(c=>c.time===param.time);
  const status=document.querySelector('#annChartPickBanner span');
  if(i<0){status.textContent='请点击实际K线所在列；Esc取消';return true;}
  if(target==='manualEntry'){
    const key=[currentSym,activeTF,MANUAL_SOURCE,cs[i].time].join('|');
    const previous=ANN.decisions[key];
    if(!previous?.manual){
      ANN.decisions[key]={d:null,reasons:[],source:MANUAL_SOURCE,tag:'买点草稿',ts:Date.now(),
        manual:{schema:ManualEvidence.VERSION,reason:'',pattern:'free',rangeStart:'',rangeEnd:'',
          collectionMode:inReplay()?'replay_context':'historical_review',selectedAt:new Date().toISOString(),
          execution:{basis:'selected_bar_close',price:cs[i].close},archived:false}};
      annSave();
    }else if(previous.manual.archived){
      previous.manual.archived=false;annSave();
    }
    annCancelChartPick(true);ANN._counts={};annBuildSigs();
    ANN.idx=ANN.sigs.findIndex(s=>s.i===i);
    ANN_CHART_PICK.suppressUntil=Date.now()+200;
    annSourceMenuRender();annApplyBlind();annRender();annDraw();
    document.querySelector('[data-manual="reason"]')?.focus();
  }else{
    const sg=ANN.sigs[ANN.idx];
    if(!sg||i>=sg.i){status.textContent='整理区间请选择买入K线之前的K线；Esc取消';return true;}
    const input=document.querySelector(`[data-manual="${target}"]`);
    if(!input){annCancelChartPick(true);return true;}
    const date=ManualEvidence.stamp(cs[i],activeTF);
    const duplicate=cs.slice(0,sg.i).filter(c=>ManualEvidence.stamp(c,activeTF)===date).length>1;
    input.value=ManualEvidence.stamp(cs[i],duplicate?'intraday':activeTF);
    manualSave(false);ANN_CHART_PICK.suppressUntil=Date.now()+200;
    annCancelChartPick();annDraw();
  }
  return true;
}
function manualStartRangePick(field){
  manualSave(false);
  if(activeView!=='raw')setView('raw');
  if(typeof drawingMgr!=='undefined'&&drawingMgr){drawingMgr.setTool(null);paintDrawButtons('none');}
  annCancelChartPick();ANN_CHART_PICK.target=field;ANN_CHART_PICK.context=annChartPickContext();
  const banner=document.getElementById('annChartPickBanner');
  banner.querySelector('span').textContent=`点击整理区间${field==='rangeStart'?'开始':'结束'}的K线（买点之前；Esc取消）`;
  banner.hidden=false;document.getElementById('chart').classList.add('ann-picking');
}
function manualSave(commit){
  const sg=ANN.sigs[ANN.idx];if(!manualActive()||!sg)return;
  const key=annKey(sg.i),rec=ANN.decisions[key],form={...rec.manual};
  for(const el of document.querySelectorAll('[data-manual]'))form[el.dataset.manual]=el.value;
  const check=ManualEvidence.inspect(form,candlesFor('raw'),volArrFor('raw'),sg.i,activeTF);
  form.execution=check.evidence.execution;
  const completed=(commit||rec.d==='yes')&&check.errors.length===0;
  ANN.decisions[key]={...rec,d:completed?'yes':null,tag:completed?'我的买点':'买点草稿',ts:Date.now(),manual:form,
    manualEvidence:check.evidence,features:{time:check.evidence.decisionTime,price:check.evidence.price}};
  sg.tag=completed?'我的买点':'买点草稿';
  const option=document.getElementById('manualList')?.options[ANN.idx];
  if(option)option.textContent=`${ManualEvidence.stamp(candlesFor('raw')[sg.i],activeTF)} · ${completed?'已保存':'草稿'}`;
  const saved=annSave();annSumRender();
  const evidence=document.getElementById('manualAutoEvidence');if(evidence)evidence.innerHTML=manualEvidenceHTML(check.evidence);
  const status=document.getElementById('manualStatus');
  if(status){
    status.textContent=!saved?'本地保存失败，请立即导出备份；刷新可能丢失本次修改':commit&&check.errors.length?check.errors.join('；'):completed?'买点已保存 · 按当根收盘价记录':'草稿已保存；填写理由后点击“保存买点”';
    status.classList.toggle('manual-error',!saved||!!(commit&&check.errors.length));
  }
  annDraw();return check;
}
function manualEvidenceHTML(evidence){
  if(!evidence)return '';
  const v=evidence.volume,iv=evidence.interval;
  const n=value=>Number.isFinite(value)?value.toLocaleString(undefined,{maximumFractionDigits:2}):'数据不足';
  const r=value=>Number.isFinite(value)?value.toFixed(2)+' 倍':'数据不足';
  const pct=value=>Number.isFinite(value)?(value*100).toFixed(2)+'%':'数据不足';
  const volumeText=Number.isFinite(v.vsPrevious20)
    ? `本根成交量较前20根均量${v.vsPrevious20>=1?'增加':'减少'} ${(Math.abs(v.vsPrevious20-1)*100).toFixed(2)}%。`
    : '前20根成交量不足或缺失，暂不比较放量／缩量。';
  return `<section class="ann-card"><div class="ann-sec">自动量能分析 · 截至买点收盘</div>
    <div class="ann-grid"><span>本根成交量</span><b>${n(v.current)}</b><span>本根 / 前20根均量</span><b>${r(v.vsPrevious20)}</b>
    <span>本根 / 前一根成交量</span><b>${r(v.vsPreviousBar)}</b>${iv?`<span>本根 / 所选区间均量</span><b>${r(iv.breakoutVolumeRatio)}</b>`:''}</div>
    <p class="ann-line">${volumeText}前20根均量不含买入当根；不足20根或成交量缺失时不补零、不作判断。</p></section>
    ${iv?`<section class="ann-card"><div class="ann-sec">你选定的整理区间</div><div class="ann-grid">
    <span>持续时间</span><b>${iv.bars} 根</b><span>区间最高 / 最低</span><b>${n(iv.high)} / ${n(iv.low)}</b>
    <span>区间振幅（相对最低价）</span><b>${pct(iv.rangePct)}</b>
    <span>后段 / 前段平均真实波幅</span><b>${r(iv.lastVsFirstTrueRange)}</b><span>后段 / 前段均量</span><b>${r(iv.lastVsFirstVolume)}</b>
    <span>买点收盘高于区间最高价</span><b>${iv.closeAboveIntervalHigh?'是':'否'}</b></div>
    <p class="ann-line">前后各取 ${iv.comparisonBars} 根，中间多出一根时略去；比值小于1表示后段更小。仅描述数据，不替你判定形态成立。</p></section>`:''}`;
}
function manualSummary(){
  let saved=0,drafts=0;
  for(const sg of ANN.sigs){if(annDecision(sg.i)?.d==='yes')saved++;else drafts++;}
  document.getElementById('annSum').textContent=`已保存 ${saved} 个买点 · 草稿 ${drafts} · 未选位置为未知`;
  document.getElementById('annBadge').textContent=`我的买点 · ${saved} 已保存 / ${drafts} 草稿`;
}
function manualRender(){
  const body=document.getElementById('annBody'),sg=ANN.sigs[ANN.idx],rec=sg?annDecision(sg.i):null;
  const form=rec?.manual||{},cs=candlesFor('raw'),bar=sg?cs[sg.i]:null;
  document.getElementById('annPos').textContent=sg?`${ANN.idx+1}/${ANN.sigs.length}`:'0/0';
  const footer=document.getElementById('manualActions');
  footer.querySelectorAll('button').forEach(b=>b.disabled=!sg);
  document.getElementById('manualPrev').disabled=!sg||ANN.idx===0;
  document.getElementById('manualNext').disabled=!sg||ANN.idx===ANN.sigs.length-1;
  const undo=manualUndo?.startsWith(`${currentSym}|${activeTF}|${MANUAL_SOURCE}|`)?'<button class="lab-btn" id="manualUndo">撤销移除上一个买点</button>':'';
  const intro=`<p class="ann-line">先在图上找出你会买的位置，再写买入理由。不限指标，默认按当根K线收盘价买入；未选位置不会记为“不做”。</p>
    <button class="lab-btn manual-add" id="manualAdd">＋ 在图上添加我的买点</button>${undo}`;
  if(!bar){body.innerHTML=intro+'<div class="manual-empty">还没有选中的买点。点击上方按钮，再点击图中的K线。</div>';}
  else{
    const check=ManualEvidence.inspect(form,cs,volArrFor('raw'),sg.i,activeTF);
    const options=Object.entries(ManualEvidence.PATTERNS).map(([key,label])=>`<option value="${key}"${form.pattern===key?' selected':''}>${label}</option>`).join('');
    body.innerHTML=intro+`<label class="ann-form-field"><span>我的买点清单（当前品种／周期）</span><select id="manualList">${ANN.sigs.map((s,k)=>`<option value="${k}"${k===ANN.idx?' selected':''}>${ManualEvidence.stamp(cs[s.i],activeTF)} · ${annDecision(s.i)?.d==='yes'?'已保存':'草稿'}</option>`).join('')}</select></label>
      <section class="ann-card"><div class="ann-date"><b>${manualEscape(ManualEvidence.stamp(bar,activeTF))} UTC</b><span class="ann-tag">手选买点</span></div>
      <div class="ann-grid"><span>默认买入价 · 当根收盘</span><b>${bar.close.toLocaleString()}</b></div>
      <p class="ann-line">${form.collectionMode==='replay_context'?'在复盘窗口中选取。':'历史回看标注：选择时可能已见后续走势，不能作为盲测结果。'}</p></section>
      <label class="ann-form-field"><span>为什么在这里买入？ *</span><textarea data-manual="reason" rows="4" maxlength="6000" placeholder="写清你的判断，例如：前面持续缩量、波动逐渐收窄，这根放量突破整理上沿，所以在收盘买入。">${manualEscape(form.reason)}</textarea></label>
      <label class="ann-form-field"><span>买点类型（用于展开对应描述）</span><select data-manual="pattern">${options}</select></label>
      <p class="ann-line">理由涉及收敛／盘整突破时，选择对应类型并标出整理区间；其他买点可自由描述。</p>
      <label class="ann-form-field"><span>参考指标或关键价位（可选）</span><input data-manual="references" value="${manualEscape(form.references)}" placeholder="例如 MA20、MACD、箱体上沿；也可只看价格"></label>
      <div id="manualRangeFields"${!ManualEvidence.needsRange(form.pattern)&&!form.rangeStart&&!form.rangeEnd?' hidden':''}>
      <p class="ann-line">标出你认为的收敛／盘整区间，起止均在买入K线之前。日期按UTC，也可在图上逐个点选。</p>
      ${[['rangeStart','区间开始'],['rangeEnd','区间结束']].map(([key,label])=>`<label class="ann-form-field"><span>${label} *</span><span class="ann-date-pick-row"><input data-manual="${key}" value="${manualEscape(form[key])}" placeholder="${activeTF==='1D'?'2025-06-01':'2025-06-01 08:00'}"><button class="lab-btn" data-manual-pick="${key}">图上选择</button></span></label>`).join('')}</div>
      <button class="lab-btn" id="manualRangeToggle">${ManualEvidence.needsRange(form.pattern)?'整理区间为必填':'添加／显示参考区间（可选）'}</button>
      <p class="ann-line" id="manualStatus" role="status">${rec.d==='yes'?'买点已保存':'草稿自动保存；填写买入理由后点击“保存买点”'}</p>
      <div id="manualAutoEvidence">${manualEvidenceHTML(check.evidence)}</div>
      <button class="lab-btn" id="manualArchive">移除这个买点（可撤销）</button>`;
  }
  document.getElementById('manualAdd').onclick=manualStartPick;
  document.getElementById('manualUndo')?.addEventListener('click',()=>{
    ANN.decisions[manualUndo].manual.archived=false;manualUndo=null;annSave();annBuildSigs();annRender();annDraw();
  });
  document.getElementById('manualList')?.addEventListener('change',e=>annGo(Number(e.target.value)));
  for(const el of body.querySelectorAll('[data-manual]')){
    el.addEventListener('input',()=>manualSave(false));
    el.addEventListener('change',()=>{manualSave(false);if(el.dataset.manual==='pattern')manualRender();});
  }
  for(const button of body.querySelectorAll('[data-manual-pick]'))button.onclick=()=>manualStartRangePick(button.dataset.manualPick);
  document.getElementById('manualRangeToggle')?.addEventListener('click',()=>{document.getElementById('manualRangeFields').hidden=false;});
  document.getElementById('manualArchive')?.addEventListener('click',()=>{
    annCancelChartPick(true);manualUndo=annKey(sg.i);ANN.decisions[manualUndo].manual.archived=true;
    annSave();ANN._counts={};annBuildSigs();annRender();annApplyBlind();annDraw();
  });
  manualSummary();
}
function manualDrawInterval(ctx,chartRef,width,height){
  const sg=ANN.sigs[ANN.idx];if(!sg||!manualActive()||activeView!=='raw')return;
  const rec=annDecision(sg.i),ev=rec?.manualEvidence;
  if(!ev?.interval)return;
  const x1=chartRef.timeScale().timeToCoordinate(ev.interval.startTime),x2=chartRef.timeScale().timeToCoordinate(ev.interval.endTime);
  if(x1===null||x2===null)return;
  const left=Math.max(0,x1-4),right=Math.min(width,x2+4);
  if(right<=left)return;
  ctx.fillStyle='rgba(34,211,238,.08)';ctx.fillRect(left,0,right-left,height);
  ctx.strokeStyle='#22d3ee';ctx.setLineDash([3,4]);ctx.strokeRect(left,1,right-left,height-2);ctx.setLineDash([]);
  ctx.fillStyle='#22d3ee';ctx.font='11px system-ui';ctx.fillText('我的整理区间',left+6,48);
}
