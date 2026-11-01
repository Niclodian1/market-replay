// js/13-annotate.js — 标注模式: 把实验信号逐个过一遍, 人工判定"这单我会不会做"
// 目的: 用你的判断去收敛交易体系 —— 先看规则出的点, 再对你的裁决, 最后统计哪些条件真正有 edge。
// 数据存 localStorage(key = 品种|周期|实验|信号时间), 可导出 JSON。
"use strict";

const ANN = window.ANN = {
  on: false,
  source: 'ma20_simple_v2',
  ctx: 180,          // 信号左边留多少根做背景
  hideFuture: true,  // 遮住信号右边的 K 线(盲判)
  onlyUnmarked: false,
  sigs: [],
  idx: 0,
  decisions: {},
  _prim: null,
  _built: false,
};

const ANN_STORE_KEY = 'chanlun.ann.v1';
// 标签来自 BTC-1D MA20 那 184 笔标注里的真实说法(按出现频次排序), 旧的通用标签已删
const ANN_REASON_GROUPS = [
  { label: '不做', items: [
    '力度不够', '调整幅度不足', '调整时间不足', '高位震荡', '震荡没走完', '波动太大', '盈亏比差', '逆势/下跌未结束',
    '止损不好设', '相对高位', '不是有效突破', '阴线突破', '插针', '二次回调',
    '距MA60/120太远', '看不懂', '错过最佳买点',
  ] },
  { label: '会做', items: ['调整充分', '区域成立，触发不足', '有效突破', '超跌反弹', '第一次回调后突破', '震荡末端突破', '三线合一顺趋势'] },
];
const ANN_REASONS = ANN_REASON_GROUPS.reduce((a, g) => a.concat(g.items), []);

// ---------------------------------------------------------------- 数据源 -- //
// 单均线突破 / 双均线金叉 的通用工厂(周期可配: MA20/60/120)
function annMaSource(p) {
  return {
    name: `突破 MA${p}`, group: '单均线突破',
    build() {
      const C = candlesFor('raw').map(c => c.close);
      const M = mdSma(C, p);
      const out = [];
      for (let i = p; i < C.length; i++) {
        if (M[i - 1] == null || M[i] == null) continue;
        if (C[i - 1] <= M[i - 1] && C[i] > M[i]) out.push({ i, side: 'buy', tag: `MA${p}` });
      }
      return out;
    },
  };
}
function annCrossSource(f, s) {
  return {
    name: `金叉 MA${f}×MA${s}`, group: '双均线突破',
    build() {
      const C = candlesFor('raw').map(c => c.close);
      const F = mdSma(C, f), S = mdSma(C, s);
      const out = [];
      for (let i = s; i < C.length; i++) {
        if (F[i] == null || S[i] == null || F[i - 1] == null || S[i - 1] == null) continue;
        if (F[i - 1] <= S[i - 1] && F[i] > S[i]) out.push({ i, side: 'buy', tag: `${f}×${s}` });
      }
      return out;
    },
  };
}


// ---------------------------------------------------------------- ZZY-MA突破 -- //
// 从 184 笔人工标注里收敛出来的规则: 首次站上 MA20 + 阳线 + 贴均线 + 放量 +
// 上一笔向下 + 不在趋势末端。参数见 ANN_ZZY, 全部可调。
// v2: 用 47 笔复核结果收紧 —— 实体≥2%、必须离开 MA20 至少 1.5%(贴均线的假突破不要)
const ANN_ZZY = {
  maPeriod: 20,        // 突破基准均线
  minDist: 0.015,      // 收盘距 MA20 下限(低于此=贴着均线, 不算有效突破)
  maxDist: 0.05,       // 收盘距 MA20 上限(超过=追高, 盈亏比差)
  minBody: 0.02,       // 阳线实体下限(力度不够的一律不要)
  maxUpWick: null,     // 上影线上限(实测不区分好坏, 默认关闭; 想试 0.02)
  minVolRatio: 0.9,    // 量比 20 下限
  needDownStroke: true,// 要求最后一笔向下(超跌反弹), 关掉=允许追趋势
  maxDistMa120: 0.15,  // 距 MA120 上限(高位过滤)
  highZone: { gap60: 0.06, slope120: 0.4 }, // 距MA60>6% 且 MA120斜率>0.4%/根 → 排除
  minGap: 5,           // 与上一信号至少间隔 N 根
};

function annZzyBuild() {
  const cs = candlesFor('raw');
  const C = cs.map(c => c.close);
  const V = volArrFor('raw');
  const N = C.length;
  const M = mdSma(C, ANN_ZZY.maPeriod), M60 = mdSma(C, 60), M120 = mdSma(C, 120);
  const slopeOf = (arr, w) => {
    const out = new Array(arr.length).fill(null);
    for (let i = w; i < arr.length; i++) if (arr[i] != null && arr[i - w]) out[i] = (arr[i] - arr[i - w]) / w / arr[i - w] * 100;
    return out;
  };
  const s120 = slopeOf(M120, 5);
  const strokes = strokesFor('raw');
  const dirAt = (t) => {
    let d = 0;
    for (const x of strokes) { if (x[2] <= t) d = x[4]; else break; }
    return d;
  };
  const out = [];
  let last = -99;
  for (let i = Math.max(121, ANN_ZZY.maPeriod + 1); i < N; i++) {
    if (M[i] == null || M[i - 1] == null) continue;
    if (C[i - 1] > M[i - 1] || C[i] <= M[i]) continue;      // 首次站上
    const c = cs[i];
    if (c.close <= c.open) continue;                         // 阳线
    const dist = c.close / M[i] - 1;
    if (dist > ANN_ZZY.maxDist) continue;
    if (dist < ANN_ZZY.minDist) continue;                    // 贴着均线, 不是有效突破
    const body = (c.close - c.open) / c.open;
    if (body < ANN_ZZY.minBody) continue;                    // 力度不够
    const upWick = (c.high - c.close) / c.open;
    if (ANN_ZZY.maxUpWick != null && upWick > ANN_ZZY.maxUpWick) continue;
    let av = 0, cnt = 0;
    for (let k = Math.max(0, i - 20); k < i; k++) { if (V[k] > 0) { av += V[k]; cnt++; } }
    av = cnt ? av / cnt : 0;
    const vr = av ? V[i] / av : null;
    if (vr != null && vr < ANN_ZZY.minVolRatio) continue;
    const dir = dirAt(c.time);
    if (ANN_ZZY.needDownStroke && dir > 0) continue;
    if (M120[i] != null && c.close / M120[i] - 1 >= ANN_ZZY.maxDistMa120) continue;
    if (M60[i] != null && s120[i] != null
        && c.close / M60[i] - 1 > ANN_ZZY.highZone.gap60 && s120[i] > ANN_ZZY.highZone.slope120) continue;
    if (i - last < ANN_ZZY.minGap) continue;
    last = i;
    const closePos = c.high > c.low ? (c.close - c.low) / (c.high - c.low) : 0.5;
    const grade = (vr != null && vr >= 1.0 && closePos >= 0.7 && body >= 0.03) ? 'A' : 'B';
    out.push({ i, side: 'buy', tag: `ZZY-${grade}`, grade });
  }
  return out;
}

// Frozen algorithm: per-market calculations, no BTC statistics reused for ETH.
let annCoverCache = null;
function annCoverContext() {
  const cs = candlesFor('raw'), volumes = volArrFor('raw');
  const rawRef = chunkedActive() ? ChunkStore.win : L().candles;
  if (!annCoverCache || annCoverCache.rawRef !== rawRef || annCoverCache.sym !== currentSym || annCoverCache.tf !== activeTF
      || annCoverCache.lastTime !== cs.at(-1)?.time || annCoverCache.n !== cs.length) {
    annCoverCache = { sym: currentSym, tf: activeTF, rawRef, lastTime: cs.at(-1)?.time, n: cs.length,
      data: Cover67.compute(cs, volumes) };
  }
  return annCoverCache.data;
}
function annCoverBuild(all) {
  const data = annCoverContext();
  return (all ? data.crosses : data.signals).map(r => ({ i: r.i, side: 'buy',
    tag: r.selected ? 'MA20候选' : '非阳线上穿',
    kind: r.selected ? 'selected' : 'excluded', grade: r.grade, version: Cover67.VERSION }));
}
function annCoverActive() { return ['ma20_simple_v2','ma20_simple_check_v2'].includes(ANN.source); }
function annCoverMeta(i) {
  const r = annCoverContext().rows[i];
  return r ? { version: Cover67.VERSION, selected: r.selected, volumeGrade: r.grade,
    volRatio: Number.isFinite(r.volratio) ? r.volratio : null, excludedRules: r.excluded,
    confirmationCount:r.confirmationCount, strengthGrade:r.strengthGrade, bodyATR:Number.isFinite(r.body_atr)?r.body_atr:null,
    bodyConfirmed:r.bodyConfirmed, volumeConfirmed:r.volumeConfirmed,
    context:{drawdown60ATR:Number.isFinite(r.drawdown60_atr_prev)?r.drawdown60_atr_prev:null,pullbackBars:r.pullback_bars,
      efficiency20:Number.isFinite(r.efficiency20_prev)?r.efficiency20_prev:null,crosses20:Number.isFinite(r.crosses20_prev)?r.crosses20_prev:null,location60:Number.isFinite(r.location60_prev)?r.location60_prev:null,range5_20:Number.isFinite(r.range5_20_prev)?r.range5_20_prev:null},
    bullish: candlesFor('raw')[i].close > candlesFor('raw')[i].open, dataComplete: r.complete } : null;
}
function annCoverCard(i) {
  if (!annCoverActive()) return '';
  const data = annCoverContext(), r = data.rows[i];
  let yes=0,kept=0,missed=0;
  for (const row of data.crosses) {
    const d = annDecisionOf(row.time,'ma20_simple_check_v2') || annDecisionOf(row.time,'ma20_simple_v2');
    if (d && d.d==='yes') { yes++; if(row.selected) kept++; else missed++; }
  }
  const state = r.selected ? '阳线上穿候选 · 等待人工判断' : '非阳线上穿 · 查漏校核';
  const why = r.selected ? '前收盘≤前MA20，本收盘>本MA20，且收盘>开盘。' : '未满足阳线条件。';
  return `<section class="ann-card cover67-card">
    <div class="ann-sec">MA20 简化 v2 · ${currentSym} ${activeTF}</div>
    <div class="cover67-state ${r.selected?'selected':'excluded'}">${state}</div>
    <div class="ann-line">止损 ${candlesFor('raw')[i].low.toLocaleString()}</div>
    <div class="ann-line">${why}${r.complete?'':' · 指标预热不足'}</div>
    <div class="ann-line">不再使用26条排除规则；实体与量能仅作确认，不过滤候选。新一轮标注独立保存。</div>
    <div class="cover67-audit">已标会做 ${yes} · 已覆盖 ${kept} · 漏选 ${missed}</div>
    <button class="lab-btn" id="cover67Review">${ANN.source==='ma20_simple_v2'?'校核全部 MA20 上穿（查漏选）':'只看阳线上穿候选'}</button>
    <p class="ann-line">查漏选：在“全部上穿”中把算法未选入、但你会做的点标为会做，导出后即可对照。</p>
    ${r.selected ? '<button class="lab-btn" id="cover67VolumeOnChart">图上查看量能</button>' : ''}
    <details><summary>量化参考（不作为拒绝门槛）</summary><div class="ann-grid">
      <span>实体 / ATR</span><b>${Number.isFinite(r.body_atr)?r.body_atr.toFixed(2):'—'}</b>
      <span>距 MA20 / ATR</span><b>${Number.isFinite(r.dist_atr)?r.dist_atr.toFixed(2):'—'}</b>
      <span>收盘位置</span><b>${Number.isFinite(r.close_pos)?(r.close_pos*100).toFixed(0)+'%':'—'}</b>
      <span>前60根高点回撤 / ATR</span><b>${Number.isFinite(r.drawdown60_atr_prev)?r.drawdown60_atr_prev.toFixed(2):'—'}</b>
      <span>距60根内最近最高点</span><b>${r.pullback_bars===null?'—':r.pullback_bars+' 根'}</b>
      <span>距 MA60 / MA120</span><b>${Number.isFinite(r.dist60)?(r.dist60*100).toFixed(1)+'%':'—'} / ${Number.isFinite(r.dist120)?(r.dist120*100).toFixed(1)+'%':'—'}</b>
      <span>突破前20根涨跌</span><b>${Number.isFinite(r.return20_prev)?(r.return20_prev*100).toFixed(1)+'%':'—'}</b>
      <span>前60根区间位置</span><b>${Number.isFinite(r.location60_prev)?(r.location60_prev*100).toFixed(0)+'%':'—'}</b>
      <span>突破前20根路径效率 ER</span><b>${Number.isFinite(r.efficiency20_prev)?r.efficiency20_prev.toFixed(2):'—'}</b>
      <span>突破前20根上穿次数</span><b>${Number.isFinite(r.crosses20_prev)?r.crosses20_prev:'—'}</b>
      <span>突破前5根 / 20根振幅</span><b>${Number.isFinite(r.range5_20_prev)?r.range5_20_prev.toFixed(2):'—'}</b>
      <span>突破前 TR5 / TR20</span><b>${Number.isFinite(r.atr5_20_prev)?r.atr5_20_prev.toFixed(2):'—'}</b>
      <span>RSI14 / 近3根变化</span><b>${Number.isFinite(r.rsi)?r.rsi.toFixed(1)+' / '+r.rsi_change3.toFixed(1):'—'}</b>
      <span>MACD近3根变化 / ATR</span><b>${Number.isFinite(r.hist_change3)?r.hist_change3.toFixed(3):'—'}</b>
    </div><p class="ann-line">调整的幅度、时间、收窄程度分别观察；距高点根数不等于完整整理时长，区间位置偏高也不自动等于高位震荡。</p></details>
  </section>`;
}

const ANN_CHART_PICK={target:null,context:null,selected:null,suppressUntil:0};
function annChartPickContext(){return `${currentSym}|${activeTF}|${ANN.source}|${candlesFor('raw')[ANN.sigs[ANN.idx]?.i]?.time}`;}
function annChartPickBusy(){return !!ANN_CHART_PICK.target||Date.now()<ANN_CHART_PICK.suppressUntil;}
function annCancelChartPick(clear=false){
  ANN_CHART_PICK.target=null;
  if(clear){ANN_CHART_PICK.selected=null;ANN_CHART_PICK.context=null;}
  document.getElementById('annChartPickBanner')?.setAttribute('hidden','');
  document.getElementById('chart')?.classList.remove('ann-picking');
  document.querySelectorAll('[data-ann-pick]').forEach(b=>b.classList.remove('active'));
  for(const pr of ANN._prims||[])pr.requestUpdate();
}
function annStartChartPick(field){
  if(!ANN.on||!annCoverActive()||!AnnotationForm.ANCHORS[field])return;
  annSaveAssessment();
  if(activeView!=='raw'){
    const range=chart.timeScale().getVisibleRange();setView('raw');
    if(range)chart.timeScale().setVisibleRange(range);
  }
  if(typeof drawingMgr!=='undefined'&&drawingMgr){drawingMgr.setTool(null);paintDrawButtons('none');}
  if(typeof cover67VolumeClose==='function')cover67VolumeClose();
  annCancelChartPick();
  ANN_CHART_PICK.target=field;ANN_CHART_PICK.context=annChartPickContext();
  const banner=document.getElementById('annChartPickBanner');
  banner.querySelector('span').textContent=`请选择「${AnnotationForm.ANCHORS[field]}」：点击图上K线（Esc取消）`;
  banner.hidden=false;document.getElementById('chart').classList.add('ann-picking');
  document.querySelector(`[data-ann-pick="${field}"]`)?.classList.add('active');
}
function annHandleChartPick(param){
  const field=ANN_CHART_PICK.target;if(!field)return;
  if(!ANN.on||!annCoverActive()||activeView!=='raw'||ANN_CHART_PICK.context!==annChartPickContext()){annCancelChartPick(true);return;}
  const cs=candlesFor('raw'),sg=ANN.sigs[ANN.idx];
  const index=cs.findIndex(c=>c.time===param.time);
  const status=document.getElementById('annChartPickBanner').querySelector('span');
  if(index<0){status.textContent='请点击实际K线所在列；Esc取消';return;}
  if(index>sg.i){status.textContent='不能选择当前信号之后的K线，请重新选择；Esc取消';return;}
  const c=cs[index],date=AnnotationForm.stamp(c,activeTF);
  const duplicate=cs.slice(0,sg.i+1).filter(b=>AnnotationForm.stamp(b,activeTF)===date).length>1;
  const input=document.querySelector(`[data-assessment="${field}"]`);
  if(!input){annCancelChartPick(true);return;}
  input.value=AnnotationForm.stamp(c,duplicate?'intraday':activeTF);
  annSaveAssessment();
  ANN_CHART_PICK.selected={time:c.time,high:c.high,low:c.low,label:AnnotationForm.ANCHORS[field]};
  ANN_CHART_PICK.suppressUntil=Date.now()+150;
  annCancelChartPick();
  const saved=document.querySelector('.ann-assessment-status');if(saved)saved.textContent=`已选择${AnnotationForm.ANCHORS[field]}：${input.value}，已保存`;
  input.scrollIntoView({block:'nearest'});
}

function annAssessmentCard(d) {
  if(!annCoverActive())return '';
  const a=d?.assessment||{},cs=candlesFor('raw'),sg=ANN.sigs[ANN.idx];
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const select=key=>{const [label,...options]=AnnotationForm.FIELDS[key];return `<label class="ann-form-field"><span>${label}${key==='preferredEntry'?'（可选）':' *'}</span><select data-assessment="${key}"><option value="">请选择</option>${options.map(v=>`<option${a[key]===v?' selected':''}>${v}</option>`).join('')}</select></label>`;};
  const input=(key,label,placeholder,type='text')=>`<label class="ann-form-field"><span>${label}</span><span class="ann-date-pick-row"><input type="${type}" ${type==='number'?'step="any" min="0"':'list="annReferenceBars"'} data-assessment="${key}" value="${esc(a[key])}" placeholder="${placeholder}">${AnnotationForm.ANCHORS[key]?`<button type="button" class="lab-btn" data-ann-pick="${key}">图上选择</button>`:''}</span></label>`;
  const dateCounts=new Map();for(const c of cs.slice(0,sg.i+1)){const t=AnnotationForm.stamp(c,activeTF);dateCounts.set(t,(dateCounts.get(t)||0)+1);}
  const placeholder=activeTF==='1D'?'例如 2025-06-15':'例如 2025-06-15 08:00';
  return `<section class="ann-card ann-assessment"><div class="ann-sec">量化复盘表单</div>
    <p class="ann-line">会做／不做前填写带 * 项；看不清可选不确定或不适用。自动保存，跳过不要求填全。</p>
    <details><summary>填写示例与说明（虚构案例）</summary><p class="ann-line">假设信号日为2025-06-25：区域成立，本根足够强，行情为趋势回调，把握中；主要依据“调整充分且突破有效”；调整幅度与时间充分、波动明显收窄。参照范围可明确标出：调整开始2025-06-01、结束2025-06-24，前高2025-05-28；如有箱体，上沿2500、下沿2300。最后选“会做”。<br>如果位置合适但阳线弱：区域仍选成立，触发选力度不足，主要依据选“区域成立但触发不足”，最终对当前根选“不做”。<br>日期可点“图上选择”再点K线；也可输入完整年月日，日内周期还需时分，均为UTC。合并视角选取时自动切为原始K线。若日线同一天有多根，也需时分以精确定位。示例不会自动填入真实标注。</p></details>
    ${['region','trigger','regime','confidence','primaryReason'].map(select).join('')}
    <div class="ann-sec">调整与参照范围</div>
    ${['depth','duration','contraction','referenceStatus'].map(select).join('')}
    <p class="ann-line">能明确范围时填调整起止K线；不确定时允许留空。结束可为信号前一根或当前根，以你的判断为准。前高和箱体边界可选。</p>
    <details class="ann-reference-details" ${a.referenceStatus==='可以明确标出'?'open':''}><summary>填写参照K线、箱体或更好买点</summary>
    ${input('adjustStart','调整开始K线（UTC）',placeholder)}${input('adjustEnd','调整结束K线（UTC）',placeholder)}${input('referencePeak','参照高点K线（UTC，可选）',placeholder)}
    ${input('boxHigh','箱体上沿（价格，可选）','例如 2500','number')}${input('boxLow','箱体下沿（价格，可选）','例如 2300','number')}
    ${select('preferredEntry')}${input('earlierEntry','此前更好买点K线（UTC）',placeholder)}
    </details>
    <label class="ann-form-field"><span>一句说明（选“其他”时必填）</span><textarea data-assessment="explanation" rows="2" placeholder="例如：此处回调位置合适，但实体偏小，继续等放量阳线。">${esc(a.explanation)}</textarea></label>
    <datalist id="annReferenceBars">${cs.slice(Math.max(0,sg.i-180),sg.i+1).map(c=>`<option value="${AnnotationForm.stamp(c,dateCounts.get(AnnotationForm.stamp(c,activeTF))>1?'intraday':activeTF)}"></option>`).join('')}</datalist>
    <p class="ann-line ann-assessment-status" role="status"></p>
  </section>`;
}
function annSaveAssessment() {
  if(!annCoverActive())return null;
  const sg=ANN.sigs[ANN.idx];if(!sg)return null;
  const key=annKey(sg.i),prev=ANN.decisions[key]||{d:null,reasons:[]},a={...prev.assessment};
  document.querySelectorAll('[data-assessment]').forEach(el=>{a[el.dataset.assessment]=el.value;});
  const check=AnnotationForm.inspect(a,candlesFor('raw'),sg.i,activeTF);
  ANN.decisions[key]={...prev,assessment:a,assessmentEvidence:check.evidence,ts:Date.now()};annSave();
  const status=document.querySelector('.ann-assessment-status');if(status)status.textContent='表单已保存';
  const refs=document.querySelector('.ann-reference-details');if(refs&&a.referenceStatus==='可以明确标出')refs.open=true;
  return check;
}

const ANN_SOURCES = {
  ma20_simple_v2: { name: 'MA20 简化 v2 · 阳线上穿候选', group: '我的规则', build() { return annCoverBuild(false); } },
  ma20_simple_check_v2: { name: 'MA20 简化 v2 · 全部上穿查漏', group: '我的规则', build() { return annCoverBuild(true); } },
  zzy: {
    name: 'ZZY-MA突破 v2', group: '我的规则',
    build() { return annZzyBuild(); },
  },
  dif: {
    name: '分歧复核 我 vs 规则', group: '我的规则',
    build() {
      const cs = candlesFor('raw');
      const v2 = annZzyBuild().map(x => x.i);
      const v2set = new Set(v2);
      const out = [];
      // ① 你在 ma20 上标了"会做", 但 v2 不出信号 → 规则漏了
      for (const sg of annMaSource(20).build()) {
        const c = cs[sg.i]; if (!c) continue;
        const d = annDecisionOf(c.time, 'ma20');
        if (!d || !d.d) continue;
        if (d.d === 'yes' && !v2set.has(sg.i)) out.push({ i: sg.i, side: 'buy', tag: '漏·你会做', kind: 'miss' });
      }
      // ② v2 出了信号, 但你标过"不做" → 规则错了
      for (const i of v2) {
        const c = cs[i]; if (!c) continue;
        const d = annDecisionOf(c.time, 'ma20');
        if (d && d.d === 'no') out.push({ i, side: 'buy', tag: '错·规则出', kind: 'false' });
        else if (!d || !d.d) out.push({ i, side: 'buy', tag: '新·未复核', kind: 'new' });
      }
      return out.sort((a, b) => a.i - b.i);
    },
  },
  ma20: annMaSource(20),
  ma60: annMaSource(60),
  ma120: annMaSource(120),
  k2060_v1: { name: '阳线同时突破 MA20＋MA60', group: '双均线突破',
    build() { return AnnotationSignals.bodyBreakouts(candlesFor('raw'), [20,60]); } },
  k20120_v1: { name: '阳线同时突破 MA20＋MA120', group: '双均线突破',
    build() { return AnnotationSignals.bodyBreakouts(candlesFor('raw'), [20,120]); } },
  k60120_v1: { name: '阳线同时突破 MA60＋MA120', group: '双均线突破',
    build() { return AnnotationSignals.bodyBreakouts(candlesFor('raw'), [60,120]); } },
  macd_low_dif_v1: { name: '创新低＋DIF底背离候选', group: 'MACD背离',
    build() { return AnnotationSignals.divergence(candlesFor('raw'), 'dif'); } },
  macd_low_hist_v1: { name: '创新低＋MACD负柱底背离候选', group: 'MACD背离',
    build() { return AnnotationSignals.divergence(candlesFor('raw'), 'hist'); } },
  x2060: annCrossSource(20, 60),
  x60120: annCrossSource(60, 120),
  x20120: annCrossSource(20, 120),
  dual: {
    name: '金叉 MA20×MA60', group: '双均线突破', hidden: true,
    build() {
      const cs = candlesFor('raw');
      const C = cs.map(c => c.close);
      const F = mdSma(C, 20), S = mdSma(C, 60);
      const out = [];
      for (let i = 1; i < C.length; i++) {
        if (!F[i] || !S[i] || !F[i - 1] || !S[i - 1]) continue;
        if (F[i - 1] <= S[i - 1] && F[i] > S[i]) out.push({ i, side: 'buy', tag: '金叉' });
      }
      return out;
    },
  },
  trend: {
    name: 'MA50 斜率趋势入场', group: '其它实验',
    build() {
      const cs = candlesFor('raw');
      const C = cs.map(c => c.close);
      const F = mdSma(C, 50);
      const sl = new Array(C.length).fill(null);
      for (let i = 5; i < C.length; i++) if (F[i] != null && F[i - 5]) sl[i] = (F[i] - F[i - 5]) / 5 / F[i - 5] * 100;
      const out = []; let prev = false;
      for (let i = 1; i < C.length; i++) {
        const ok = F[i] != null && sl[i] != null && C[i] > F[i] && sl[i] > -0.15;
        if (ok && !prev) out.push({ i, side: 'buy', tag: '趋势' });
        prev = ok;
      }
      return out;
    },
  },
  slope: {
    name: '突破 MA20 且斜率>0', group: '其它实验',
    build() {
      const cs = candlesFor('raw');
      const C = cs.map(c => c.close);
      const F = mdSma(C, 20);
      const sl = new Array(C.length).fill(null);
      for (let i = 5; i < C.length; i++) if (F[i] != null && F[i - 5]) sl[i] = (F[i] - F[i - 5]) / 5 / F[i - 5] * 100;
      const out = [];
      for (let i = 1; i < C.length; i++) {
        if (F[i] == null || F[i - 1] == null || sl[i] == null) continue;
        if (C[i - 1] <= F[i - 1] && C[i] > F[i] && sl[i] > 0) out.push({ i, side: 'buy', tag: '突破' });
      }
      return out;
    },
  },
  chan: {
    name: '缠论买点·笔中枢', group: '缠论',
    build() {
      const cs = candlesFor('raw');
      const idx = new Map(cs.map((c, i) => [c.time, i]));
      return buysFor('raw', 'stroke')
        .map(b => ({ i: idx.get(b.t), side: 'buy', tag: b.k, note: b.why || '' }))
        .filter(x => x.i != null)
        .sort((a, b) => a.i - b.i);
    },
  },
  chan_seg: {
    name: '缠论买点·线段中枢', group: '缠论',
    build() {
      const cs = candlesFor('raw');
      const idx = new Map(cs.map((c, i) => [c.time, i]));
      return buysFor('raw', 'seg')
        .map(b => ({ i: idx.get(b.t), side: 'buy', tag: b.k, note: b.why || '' }))
        .filter(x => x.i != null)
        .sort((a, b) => a.i - b.i);
    },
  },
  fast: {
    name: '背驰买点·笔中枢', group: '缠论',
    build() {
      const cs = candlesFor('raw');
      const idx = new Map(cs.map((c, i) => [c.time, i]));
      return fastFor('raw', 'stroke')
        .filter(x => x.side === '买')
        .map(x => ({ i: idx.get(x.t), side: 'buy', tag: x.k, note: x.why || '' }))
        .filter(x => x.i != null)
        .sort((a, b) => a.i - b.i);
    },
  },
};

// ---------------------------------------------------------------- 存储 -- //
function annKey(i) {
  const cs = candlesFor('raw');
  const tt = cs[i] ? cs[i].time : 0;
  return [currentSym, activeTF, ANN.source, tt].join('|');
}
function annLoad() {
  try { ANN.decisions = JSON.parse(localStorage.getItem(ANN_STORE_KEY) || '{}') || {}; }
  catch (e) { ANN.decisions = {}; }
  // 旧键 dual 迁移到 x2060
  let moved = 0;
  for (const k of Object.keys(ANN.decisions)) {
    const nk = k.replace('|dual|', '|x2060|');
    if (nk !== k) { ANN.decisions[nk] = ANN.decisions[k]; delete ANN.decisions[k]; moved++; }
  }
  if (moved) annSave();
}
function annSave() {
  try {
    localStorage.setItem(ANN_STORE_KEY, JSON.stringify(ANN.decisions));
    const tip = document.getElementById('annSaveTip');
    if (tip) {
      const d = new Date();
      const hh = String(d.getHours()).padStart(2, '0'), mm = String(d.getMinutes()).padStart(2, '0'), ss = String(d.getSeconds()).padStart(2, '0');
      tip.textContent = `✓ 已保存 ${hh}:${mm}:${ss}`;
      tip.classList.remove('flash');
      void tip.offsetWidth;
      tip.classList.add('flash');
    }
  } catch (e) {
    const tip = document.getElementById('annSaveTip');
    if (tip) { tip.textContent = '⚠ 保存失败(存储已满?)'; tip.classList.add('err'); }
  }
}
function annDecision(i) { return ANN.decisions[annKey(i)] || null; }
// 查"另一个信号源"在同一根K线上的历史判定(用于分歧复核)
function annDecisionOf(time, src) {
  return ANN.decisions[[currentSym, activeTF, src, time].join('|')] || null;
}

// ---------------------------------------------------------------- 指标 -- //
function annFeatures(i) {
  const cs = candlesFor('raw');
  const c = cs[i];
  if (!c) return null;
  const C = cs.map(x => x.close), V = volArrFor('raw');
  const MA = { 20: mdSma(C, 20), 60: mdSma(C, 60), 120: mdSma(C, 120) };
  const r4 = v => (v == null || !isFinite(v)) ? null : Math.round(v * 1e4) / 1e4;
  const slope = (arr, w) => {
    if (i - w < 0 || arr[i] == null || arr[i - w] == null || !arr[i - w]) return null;
    return (arr[i] - arr[i - w]) / w / arr[i - w] * 100;
  };
  // --- 1) 价格 / K 线本身 ---
  const chg = i > 0 ? c.close / C[i - 1] - 1 : 0;
  const body = (c.close - c.open) / c.open;
  const rng = (c.high - c.low) / c.open;
  const closePos = rng > 0 ? (c.close - c.low) / (c.high - c.low) : 0.5;
  const upWick = rng > 0 ? (c.high - Math.max(c.open, c.close)) / c.open : 0;
  const dnWick = rng > 0 ? (Math.min(c.open, c.close) - c.low) / c.open : 0;
  const avgVol = k => {
    const w = V.slice(Math.max(0, i - k), i).filter(x => x > 0);
    return w.length ? w.reduce((a, b) => a + b, 0) / w.length : null;
  };
  const av20 = avgVol(20), av5 = avgVol(5);
  // --- 2) 均线及其斜率 ---
  const maOf = k => {
    const a = MA[k];
    if (a[i] == null) return null;
    return { v: Math.round(a[i] * 100) / 100, dist: r4(c.close / a[i] - 1), slope5: r4(slope(a, 5)), slope10: r4(slope(a, 10)), slope20: r4(slope(a, 20)) };
  };
  // --- 3) K 线与均线的关系 ---
  const m20 = MA[20][i], m60 = MA[60][i], m120 = MA[120][i];
  const arrange = (m20 && m60 && m120)
    ? (m20 > m60 && m60 > m120 ? 'bull' : (m20 < m60 && m60 < m120 ? 'bear' : 'mixed')) : null;
  const above = [20, 60, 120].filter(k => MA[k][i] != null && c.close > MA[k][i]).length;
  let streak60 = 0;
  for (let k = i; k >= 0; k--) { if (MA[60][k] != null && C[k] > MA[60][k]) streak60++; else break; }
  let streak20 = 0;
  for (let k = i; k >= 0; k--) { if (MA[20][k] != null && C[k] > MA[20][k]) streak20++; else break; }
  // --- 4) 波动 / 位置 ---
  let atr = 0; const a0 = Math.max(1, i - 13);
  for (let k = a0; k <= i; k++) {
    const pc = C[k - 1] != null ? C[k - 1] : C[k];
    atr += Math.max(cs[k].high - cs[k].low, Math.abs(cs[k].high - pc), Math.abs(cs[k].low - pc));
  }
  atr = atr / (i - a0 + 1);
  const hi20 = Math.max(...cs.slice(Math.max(0, i - 19), i + 1).map(x => x.high));
  const pullback20 = r4(c.close / hi20 - 1);          // 距最近 20 根最高点回撤
  const volShrink = (av5 && av20) ? r4(av5 / av20) : null;   // <1 = 回调缩量
  const hi60 = Math.max(...C.slice(Math.max(0, i - 59), i + 1));
  const lo60 = Math.min(...C.slice(Math.max(0, i - 59), i + 1));
  // --- 5) 指标 / 缠论上下文 ---
  const macd = macdFor('raw');
  const strokes = strokesFor('raw');
  const last = strokes.filter(x => x[2] <= c.time).slice(-1)[0] || null;
  // 只认"包含当根"的中枢, 避免把很远的历史中枢当成当前位置
  const pv = pivotsFor('raw', 'stroke').filter(x => x[0] <= c.time && x[1] >= c.time)[0] || null;
  const chanBuys = buysFor('raw', pivotMode).filter(b => Math.abs(b.t - c.time) <= 86400 * 3).map(b => b.k);
  // --- 6) 结果(只在展开/导出时用, 不作为判断依据) ---
  const fwd = k => C[Math.min(i + k, C.length - 1)] / c.close - 1;
  const w20 = C.slice(i + 1, Math.min(i + 21, C.length));
  return {
    i, time: c.time,
    price: {
      open: c.open, high: c.high, low: c.low, close: c.close,
      chg: r4(chg), body: r4(body), range: r4(rng), closePos: r4(closePos),
      upWick: r4(upWick), dnWick: r4(dnWick),
      vol: V[i], volRatio20: r4(av20 ? V[i] / av20 : null), volRatio5: r4(av5 ? V[i] / av5 : null),
      volShrink,
    },
    ma: { '20': maOf(20), '60': maOf(60), '120': maOf(120) },
    relation: {
      arrange, above, streak20, streak60,
      gap2060: (m20 && m60) ? r4(m20 / m60 - 1) : null,
      gap60120: (m60 && m120) ? r4(m60 / m120 - 1) : null,
      priceVsGap2060: (m20 && m60) ? r4((c.close - m60) / (m20 - m60)) : null,
    },
    ctx: {
      atr14: r4(atr), atrPct: r4(atr / c.close), pullback20,
      distHigh60: r4(c.close / hi60 - 1), distLow60: r4(c.close / lo60 - 1),
      dif: r4(macd.diff[i]), dea: r4(macd.dea[i]), hist: r4(macd.hist[i]),
      strokeDir: last ? last[4] : 0,
      pivotIn: !!pv, pivotZd: pv ? pv[2] : null, pivotZg: pv ? pv[3] : null,
      chanBuys,
    },
    forward: {
      fwd5: r4(fwd(5)), fwd20: r4(fwd(20)), fwd60: r4(fwd(60)),
      mfe20: w20.length ? r4(Math.max(...w20) / c.close - 1) : null,
      mae20: w20.length ? r4(Math.min(...w20) / c.close - 1) : null,
    },
  };
}

// 面板用的薄封装(保持旧的字段名, 少改模板)
function annSnapshot(i) {
  const f = annFeatures(i);
  if (!f) return null;
  return {
    i, time: f.time, close: f.price.close,
    chg: f.price.chg, body: f.price.body, rng: f.price.range, pos: f.price.closePos,
    vr: f.price.volRatio20, vsh: f.price.volShrink, pub20: f.ctx.pullback20,
    ma20: f.ma['20'] && f.ma['20'].v, ma60: f.ma['60'] && f.ma['60'].v, ma120: f.ma['120'] && f.ma['120'].v,
    d20: f.ma['20'] && f.ma['20'].dist, d60: f.ma['60'] && f.ma['60'].dist, d120: f.ma['120'] && f.ma['120'].dist,
    s20: f.ma['20'] && f.ma['20'].slope5, s60: f.ma['60'] && f.ma['60'].slope5, s120: f.ma['120'] && f.ma['120'].slope5,
    gap: f.relation.gap2060, arrange: f.relation.arrange, above: f.relation.above,
    streak60: f.relation.streak60,
    atrPct: f.ctx.atrPct, distHigh60: f.ctx.distHigh60, distLow60: f.ctx.distLow60,
    dif: f.ctx.dif, dea: f.ctx.dea, hist: f.ctx.hist,
    strokeDir: f.ctx.strokeDir,
    pivot: (f.ctx.pivotZd != null) ? { zd: f.ctx.pivotZd, zg: f.ctx.pivotZg } : null,
    chanBuys: f.ctx.chanBuys,
    fwd5: f.forward.fwd5, fwd20: f.forward.fwd20, fwd60: f.forward.fwd60,
    mfe20: f.forward.mfe20, mae20: f.forward.mae20,
    features: f,
  };
}

// ---------------------------------------------------------------- 绘制 -- //
class AnnRenderer {
  constructor(prim) { this._prim = prim; }
  draw(target) {
    if (!ANN.on || !ANN.sigs.length) return;
    const cs = candlesFor('raw');
    const s = ANN.sigs[ANN.idx];
    if (!s) return;
    const c = cs[s.i];
    if (!c) return;
    const chartOf = this._prim._chart || chart;
    const main = this._prim._mode !== 'shade';
    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context;
      const W = scope.mediaSize.width, H = scope.mediaSize.height;
      const x = chartOf.timeScale().timeToCoordinate(c.time);
      const yH = main ? this._prim._series.priceToCoordinate(c.high) : null;
      const yL = main ? this._prim._series.priceToCoordinate(c.low) : null;
      if (x == null) return;
      // 信号必须在这一屏里才遮: 否则整屏都会被盖住, 看起来像"图坏了"
      const onScreen = x >= 0 && x <= W;
      if (ANN.hideFuture && onScreen) {
        const x0 = Math.max(0, Math.min(W, x + 2));
        const w = W - x0;
        if (w > 1) {
          ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--plane').trim() || '#131722';
          ctx.globalAlpha = 0.985;
          ctx.fillRect(x0, 0, w, H);
          ctx.globalAlpha = 1;
        }
      }
      if (!main) return;
      const picked=ANN_CHART_PICK.context===annChartPickContext()?ANN_CHART_PICK.selected:null;
      if(picked&&activeView==='raw'){
        const px=chartOf.timeScale().timeToCoordinate(picked.time),ph=this._prim._series.priceToCoordinate(picked.high),pl=this._prim._series.priceToCoordinate(picked.low);
        if(px!=null&&ph!=null&&pl!=null){
          ctx.fillStyle='rgba(34,211,238,.15)';ctx.fillRect(px-7,0,14,H);
          ctx.strokeStyle='#22d3ee';ctx.lineWidth=2;ctx.strokeRect(px-7,Math.min(ph,pl)-4,14,Math.abs(pl-ph)+8);
          ctx.fillStyle='#22d3ee';ctx.font='600 11px system-ui';ctx.fillText(picked.label,Math.max(4,Math.min(W-100,px-30)),32);
        }
      }
      if (!onScreen) {   // 信号不在这一屏: 只提示, 不遮
        ctx.fillStyle = 'rgba(245,158,11,.9)';
        ctx.font = '600 12px system-ui, -apple-system, sans-serif';
        ctx.textAlign = 'center';
        const tip = x < 0 ? '← 信号 K 线在左侧视野外，按 ←/→ 回到信号' : '信号 K 线在右侧视野外，按 → 回到信号';
        ctx.fillText(tip, W / 2, Math.min(28, H / 2));
        ctx.textAlign = 'left';
        return;
      }
      ctx.strokeStyle = '#f59e0b';
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
      ctx.setLineDash([]);
      if (yH != null && yL != null) {
        ctx.strokeStyle = '#f59e0b';
        ctx.lineWidth = 2;
        ctx.strokeRect(x - 6, Math.min(yH, yL) - 6, 12, Math.abs(yL - yH) + 12);
      }
      ctx.fillStyle = '#f59e0b';
      ctx.font = '600 11px system-ui, -apple-system, sans-serif';
      ctx.fillText(`${s.tag} · ${annDate(c.time)}`, Math.max(6, x - 40), 14);
    });
  }
}
class AnnView {
  constructor(prim) { this._prim = prim; this._r = new AnnRenderer(prim); }
  zOrder() { return 'top'; }
  update() {}
  renderer() { return this._r; }
}
class AnnPrimitive {
  constructor(series, chartRef, mode) {
    this._series = series; this._chart = chartRef; this._mode = mode || 'main';
    this._view = new AnnView(this); this._req = null;
  }
  attached(p) { this._req = p.requestUpdate; if (p.chart) this._chart = p.chart; }
  detached() { this._req = null; }
  updateAllViews() {}
  paneViews() { return [this._view]; }
  requestUpdate() { if (this._req) this._req(); }
}

// ---------------------------------------------------------------- 工具 -- //
function annDate(t) { return new Date(t * 1000).toISOString().slice(0, 10); }
function annPct(v, d = 1) { return v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(d) + '%'; }
function annNum(v, d = 1) { return v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(d); }

// ---------------------------------------------------------------- 面板 -- //
function annBuildSigs() {
  const src = ANN_SOURCES[ANN.source];
  ANN.sigs = src ? src.build() : [];
  ANN.idx = Math.max(0, Math.min(ANN.idx, ANN.sigs.length - 1));
}

function annGo(k) {
  if (!ANN.sigs.length) return;
  ANN.idx = Math.max(0, Math.min(k, ANN.sigs.length - 1));
  const s = ANN.sigs[ANN.idx];
  const cs = candlesFor('raw');
  const c = cs[s.i];
  if (c) {
    const from = Math.max(0, s.i - ANN.ctx);
    const to = s.i + Math.max(20, Math.round(ANN.ctx / 4));
    const apply = () => { try { chart.timeScale().setVisibleLogicalRange({ from, to }); } catch (e) {} };
    apply();
    // 隐藏/恢复价格轴会触发一次重排并把范围还原, 所以下一帧和 160ms 后再各设一次
    requestAnimationFrame(() => { apply(); annDraw(); });
    setTimeout(() => { apply(); annDraw(); }, 160);
  }
  annRender();
  annDraw();
}

function annDraw() {
  if (typeof cover67IndicatorRefresh === 'function') cover67IndicatorRefresh();
  for (const pr of (ANN._prims || [])) if (pr) pr.requestUpdate();
}

// 遮住未来时右侧价格轴也要藏起来: 轴上的"最新价"本身就是未来价
function annApplyBlind() {
  // 用 DOM 盖住右侧价格轴, 而不是把 scale 设成 visible:false。
  // 设 visible:false 会让 priceScale('right').width() 变 0, alignPanes() 就算不出副图宽度,
  // 副图会卡在旧宽度上; 而且隐藏轴会触发重排, 把刚设好的可视范围还原。
  const cover = document.getElementById('annBlindCover');
  for (const c of [chart, volumeChart, macdChart]) {
    try { c.priceScale('right').applyOptions({ visible: true }); } catch (e) {}
  }
  if (!cover) return;
  const blind = ANN.on && ANN.hideFuture;
  let w = 0;
  for (const c of [chart, volumeChart, macdChart]) {
    try { w = Math.max(w, c.priceScale('right').width() || 0); } catch (e) {}
  }
  if (blind && w > 0) {
    cover.style.width = Math.round(w) + 'px';
    cover.style.display = 'block';
  } else {
    cover.style.display = 'none';
  }
}
// 均线配色: 与图表一致(MA_COLORS 按 #maPeriods 的顺序循环)
function annMaColor(p) {
  const src = (typeof MA_COLORS !== 'undefined' && MA_COLORS.length) ? MA_COLORS : ['#2962ff', '#ff9800', '#9c27b0'];
  const el = document.getElementById('maPeriods');
  const list = el ? String(el.value).split(',').map(x => parseInt(x.trim(), 10)).filter(n => n > 0) : [20, 60, 120];
  let i = list.indexOf(p);
  if (i < 0) i = p === 20 ? 0 : p === 60 ? 1 : 2;
  return src[i % src.length];
}

const ANN_GROUP_COLOR = {
  '我的规则': 'g-zzy', '单均线突破': 'g-single', '双均线突破': 'g-cross', '缠论': 'g-chan', '其它实验': 'g-other', 'MACD背离': 'g-cross',
};

function annSourceList() {
  return Object.entries(ANN_SOURCES).filter(([, v]) => !v.hidden);
}

// 每个实验的信号数(切品种/周期后失效)
function annSourceCount(id) {
  const key = `${currentSym}|${activeTF}|${id}`;
  if (!ANN._counts) ANN._counts = {};
  if (ANN._counts[key] != null) return ANN._counts[key];
  let n = 0;
  try { n = (ANN_SOURCES[id] ? ANN_SOURCES[id].build() : []).length; } catch (e) { n = 0; }
  ANN._counts[key] = n;
  return n;
}

function annSourceMenuRender() {
  const menu = document.getElementById('annSelMenu');
  const label = document.getElementById('annSelLabel');
  const dot = document.getElementById('annSelDot');
  if (!menu) return;
  const groups = new Map();
  for (const [id, v] of annSourceList()) {
    const g = v.group || '其它实验';
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push([id, v]);
  }
  let html = '';
  for (const [g, items] of groups) {
    html += `<div class="ann-sel-group"><i class="ann-dot ${ANN_GROUP_COLOR[g] || 'g-other'}"></i>${g}</div>`;
    for (const [id, v] of items) {
      const cur = id === ANN.source ? ' on' : '';
      html += `<button class="ann-sel-opt${cur}" data-v="${id}"><i class="ann-dot ${ANN_GROUP_COLOR[g] || 'g-other'}"></i>`
            + `<span>${v.name}</span><em>${annSourceCount(id)}</em></button>`;
    }
  }
  menu.innerHTML = html;
  menu.querySelectorAll('.ann-sel-opt').forEach(b => b.addEventListener('click', () => {
    ANN.source = b.dataset.v; ANN.idx = 0;
    menu.classList.remove('open');
    annBuildSigs(); annSourceMenuRender(); annGo(0);
  }));
  const cur = ANN_SOURCES[ANN.source];
  if (label) label.textContent = cur ? cur.name : '—';
  if (dot) dot.className = 'ann-dot ' + (ANN_GROUP_COLOR[(cur && cur.group) || ''] || 'g-other');
  // 兼容老测试/外部脚本: 同步一个隐藏 select
  const sel = document.getElementById('annSource');
  if (sel) sel.value = ANN.source;
}

function annPanelBuild() {
  if (ANN._built) return;
  const layout = document.body;
  const el = document.createElement('aside');
  el.id = 'annDrawer';
  el.className = 'ann-drawer collapsed';
  el.innerHTML = `
    <div class="ann-inner">
      <div class="ann-head">
        <span class="ann-dot g-single"></span>
        <span class="ann-title">标注模式</span>
        <span class="ann-badge" id="annBadge">—</span>
        <button class="ann-collapse" id="annCollapse" title="收回到右侧">»</button>
      </div>
      <div class="ann-scroll">
        <div class="lab-row">
          <div class="ann-sel" id="annSel">
            <button class="ann-sel-btn" id="annSelBtn">
              <i class="ann-dot g-single" id="annSelDot"></i>
              <span id="annSelLabel">—</span>
              <b class="caret">▾</b>
            </button>
            <div class="ann-sel-menu" id="annSelMenu"></div>
          </div>
        </div>
        <select id="annSource" style="display:none"></select>
        <div class="lab-row">
          <label class="ann-chk"><input type="checkbox" id="annHide" checked>遮住之后</label>
          <label class="ann-chk"><input type="checkbox" id="annOnly">只看未标注</label>
          <span style="flex:1"></span>
          <span class="ann-pos" id="annPos">0/0</span>
        </div>
        <div id="annBody" class="ann-body"></div>
      </div>
      <div class="ann-foot">
        <div class="ann-chips" id="annReasons"></div>
        <textarea id="annNote" class="ann-note" rows="2" placeholder="备注（不做时：选理由或写这里）"></textarea>
        <div class="ann-actions5">
          <button class="ann-nav" id="annPrev" title="上一笔（←）">◀</button>
          <button class="ann-btn yes" data-d="yes" title="会做（快捷键 1）">会做<i>1</i></button>
          <button class="ann-btn no" data-d="no" title="不做（快捷键 2）">不做<i>2</i></button>
          <button class="ann-btn skip" data-d="skip" title="跳过（快捷键 3）">跳过<i>3</i></button>
          <button class="ann-nav next" id="annNext" title="下一个待标注（→，自动跳过已标注）">▶</button>
        </div>
        <div class="ann-footbar">
          <span class="lab-hint" id="annSum">—</span>
          <span class="ann-saved" id="annSaveTip"></span>
          <button class="lab-btn" id="annImport" title="导入之前导出的 JSON(换设备/清了缓存后用)">导入</button>
          <button class="lab-btn" id="annExport" title="导出 JSON (备份/换设备用)">导出</button>
          <input type="file" id="annFile" accept=".json,application/json" hidden>
        </div>
      </div>
    </div>`;
  layout.appendChild(el);
  ANN._built = true;

  const inp = q => el.querySelector(q);
  inp('#annCollapse').addEventListener('click', () => annSetOpen(false));
  inp('#annSelBtn').addEventListener('click', e => {
    e.stopPropagation();
    const m = inp('#annSelMenu');
    if (!m.classList.contains('open')) annSourceMenuRender();
    m.classList.toggle('open');
  });
  document.addEventListener('click', e => {
    const m = document.getElementById('annSelMenu');
    if (m && m.classList.contains('open') && !e.target.closest('#annSel')) m.classList.remove('open');
  });
  inp('#annHide').addEventListener('change', e => { ANN.hideFuture = e.target.checked; annApplyBlind(); annDraw(); annRender(); });
  inp('#annOnly').addEventListener('change', e => { ANN.onlyUnmarked = e.target.checked; });
  inp('#annPrev').addEventListener('click', () => annStep(-1));
  inp('#annNext').addEventListener('click', () => annGotoNext());
  el.querySelectorAll('.ann-btn').forEach(b => b.addEventListener('click', () => annMark(b.dataset.d)));
  inp('#annExport').addEventListener('click', annExport);
  let noteTimer = null;
  const saveNote = (val) => {
    const sg = ANN.sigs[ANN.idx]; if (!sg) return;
    const k = annKey(sg.i); const d = ANN.decisions[k] || { d: null, reasons: [] };
    d.note = val; d.ts = Date.now(); ANN.decisions[k] = d; annSave(); annSumRender();
  };
  inp('#annNote').addEventListener('input', e => {
    clearTimeout(noteTimer);
    const v = e.target.value;
    noteTimer = setTimeout(() => saveNote(v), 350);   // 边打边存, 0.35s 防抖
  });
  inp('#annNote').addEventListener('change', e => { clearTimeout(noteTimer); saveNote(e.target.value); });
  window.addEventListener('keydown', e => {
    if (!ANN.on) return;
    if(e.key==='Escape'&&ANN_CHART_PICK.target){e.preventDefault();annCancelChartPick();return;}
    const tag = (document.activeElement && document.activeElement.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (typeof trLocked === 'function' && trLocked()) return;
    if (e.key === 'ArrowLeft') { e.preventDefault(); annStep(-1); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); annGotoNext(); }
    else if (e.key === '1') { e.preventDefault(); annMark('yes'); }
    else if (e.key === '2') { e.preventDefault(); annMark('no'); }
    else if (e.key === '3') { e.preventDefault(); annMark('skip'); }
    else if (e.key === 'Escape') { annSetOpen(false); }
  });
}

// 抽屉是 fixed 定位 + 主区让出 380px, 主图 autoSize 的 ResizeObserver 落地时机不定,
// 只在 rAF 调一次 alignPanes 会算出旧宽度(副图比主图宽)。多次补齐。
function annRealign() {
  const run = () => { try { alignPanes(); } catch (e) {} };
  requestAnimationFrame(run);
  [110, 220, 360, 560, 900].forEach(t => setTimeout(run, t));
}

// 右侧抽屉开关: 独立浮层(不挂在复盘交易侧栏里), 打开时主区让出宽度
function annSetOpen(open) {
  const el = document.getElementById('annDrawer');
  if (!el) return;
  if(!open)annCancelChartPick(true);
  ANN.on = !!open;
  el.classList.toggle('collapsed', !open);
  document.body.classList.toggle('ann-open', ANS_OPEN_CLASS());
  const hdr = document.querySelector('header');
  if (hdr) el.style.top = Math.round(hdr.getBoundingClientRect().height) + 'px';
  const btn = document.getElementById('annBtn');
  if (btn) btn.classList.toggle('active', ANN.on);
  if (open) {
    annBuildSigs();
    annSourceMenuRender();
    annApplyBlind();
    requestAnimationFrame(() => { annApplyBlind(); annGo(Math.min(ANN.idx, Math.max(0, ANN.sigs.length - 1))); });
  } else {
    ANN.hideFuture = document.getElementById('annHide') ? document.getElementById('annHide').checked : true;
    annApplyBlind();
    annDraw();
  }
  annRealign();
}
const ANS_OPEN_CLASS = () => !!ANN.on;

// 下一个还没判定的信号(自动跳过已标注, 到末尾回绕; 全标完就提示)
function annGotoNext() {
  const n = ANN.sigs.length;
  if (!n) return;
  for (let k = 1; k <= n; k++) {
    const j = ((ANN.idx + k) % n + n) % n;
    const sg = ANN.sigs[j];
    const d = ANN.decisions[annKey(sg.i)];
    if (!d || !d.d) { ANN.idx = j; annGo(j); return; }
  }
  const sumEl = document.getElementById('annSum');
  if (sumEl) {
    sumEl.textContent = `这个实验的 ${n} 个信号全部标完了 · 可以导出了`;
    sumEl.classList.add('warn');
    setTimeout(() => { sumEl.classList.remove('warn'); annSumRender(); }, 2600);
  }
}

function annStep(dir) {
  if (!ANN.sigs.length) return;
  let k = ANN.idx + dir;
  if (ANN.onlyUnmarked) {
    while (k >= 0 && k < ANN.sigs.length && annDecision(ANN.sigs[k].i)) k += dir;
    if (k < 0 || k >= ANN.sigs.length) { annRender(); return; }
  }
  annGo(k);
}

function annMark(d) {
  if(ANN_CHART_PICK.target)annCancelChartPick();
  const s = ANN.sigs[ANN.idx];
  if (!s) return;
  const k = annKey(s.i);
  if(annCoverActive()){
    const check=annSaveAssessment();
    if(d!=='skip'&&check?.errors.length){
      const status=document.querySelector('.ann-assessment-status');
      if(status){status.textContent=check.errors.join('；');status.scrollIntoView({block:'nearest'});}
      annTip('请补充复盘表单；不确定也可明确选择',true);return;
    }
  }
  const prev = ANN.decisions[k] || { reasons: [] };
  // 「不做」必须留下理由: 至少一个标签, 或写了备注
  if (d === 'no') {
    const noteEl = document.getElementById('annNote');
    const noteTxt = noteEl ? noteEl.value.trim() : '';
    const hasReason = (prev.reasons && prev.reasons.length > 0) || noteTxt.length > 0 || (annCoverActive() && !!prev.assessment?.primaryReason);
    if (!hasReason) {
      const chipBox = document.getElementById('annReasons');
      if (chipBox) {
        chipBox.classList.remove('need');
        void chipBox.offsetWidth;
        chipBox.classList.add('need');
        setTimeout(() => chipBox.classList.remove('need'), 1600);
      }
      const sumEl = document.getElementById('annSum');
      if (sumEl) {
        sumEl.textContent = '「不做」请先选至少一个理由（或用备注说明）';
        sumEl.classList.add('warn');
        setTimeout(() => { sumEl.classList.remove('warn'); annSumRender(); }, 2200);
      }
      return;
    }
    if (noteTxt && !prev.note) prev.note = noteTxt;
  }
  ANN.decisions[k] = { ...prev, d, ts: Date.now(), source: ANN.source, tag: s.tag, kind: s.kind || '',
                       time: annDate(candlesFor('raw')[s.i].time),
                       features: annFeatures(s.i), ...(s.evidence ? { signalEvidence: s.evidence } : {}), ...(annCoverActive() ? { algorithm: annCoverMeta(s.i) } : {}) };
  annSave();
  annRender();
}

function annToggleReason(r) {
  const s = ANN.sigs[ANN.idx];
  if (!s) return;
  const k = annKey(s.i);
  const cur = ANN.decisions[k] || { d: null, reasons: [] };
  const set = new Set(cur.reasons || []);
  set.has(r) ? set.delete(r) : set.add(r);
  ANN.decisions[k] = { ...cur, reasons: [...set], ts: Date.now() };
  annSave();
  annRender();
}

function annSumRender() {
  const total = ANN.sigs.length;
  let yes = 0, no = 0, skip = 0, done = 0;
  for (const s of ANN.sigs) {
    const d = annDecision(s.i);
    if (!d || !d.d) continue;
    done++;
    if (d.d === 'yes') yes++; else if (d.d === 'no') no++; else skip++;
  }
  const el = document.getElementById('annSum');
  if (el) el.textContent = `已标注 ${done}/${total} · 会做 ${yes} / 不做 ${no} / 跳过 ${skip}`;
  const b = document.getElementById('annBadge');
  if (b) b.textContent = `${ANN_SOURCES[ANN.source].name} · ${done}/${total}`;
}

function annRender() {
  if(ANN_CHART_PICK.context&&(ANN_CHART_PICK.context!==annChartPickContext()||activeView!=='raw'))annCancelChartPick(true);
  const body = document.getElementById('annBody');
  const pos = document.getElementById('annPos');
  if (!body) return;
  if (!ANN.sigs.length) {
    body.innerHTML = '<div class="lab-note">当前品种/周期上，这个实验没有信号。</div>';
    if (pos) pos.textContent = '0/0';
    annSumRender();
    return;
  }
  const s = ANN.sigs[ANN.idx];
  const d = annDecision(s.i);
  const snap = annSnapshot(s.i);
  const thin = ANN.sigs.length < 3 ? `<div class="lab-note">这个实验在当前数据上只有 ${ANN.sigs.length} 个信号，样本太少，建议换实验。</div>` : '';
  if (pos) pos.textContent = `${ANN.idx + 1}/${ANN.sigs.length}`;
  if (!snap) return;
  const strokeTxt = snap.strokeDir > 0 ? '向上笔' : snap.strokeDir < 0 ? '向下笔' : '—';
  const pivotTxt = snap.pivot
    ? `中枢 ${Math.round(snap.pivot.zd)}~${Math.round(snap.pivot.zg)}（价格${snap.close >= snap.pivot.zg ? '在上方' : snap.close <= snap.pivot.zd ? '在下方' : '在内部'}）`
    : '不在任何笔中枢内';
  const arrTxt = snap.arrange === 'bull' ? '多头排列' : snap.arrange === 'bear' ? '空头排列' : snap.arrange === 'mixed' ? '缠绕' : '—';
  // 涨绿 / 跌红 / 平灰
  const sg = v => (v == null || !isFinite(v)) ? 'flat' : (v > 0 ? 'up' : v < 0 ? 'down' : 'flat');
  const V = (v, txt) => `<b class="ann-v ${sg(v)}">${txt}</b>`;
  const P = v => V(v, annPct(v));
  const maRow = (k) => {
    const idx = k === 20 ? 0 : k === 60 ? 1 : 2;
    const v = [snap.ma20, snap.ma60, snap.ma120][idx];
    const dd = [snap.d20, snap.d60, snap.d120][idx];
    const ss = [snap.s20, snap.s60, snap.s120][idx];
    const cc = annMaColor(k);
    return `<span class="k"><i class="ma-dot" style="background:${cc}"></i>MA${k}</span>`
         + `<span class="v" style="color:${cc}">${v ? Math.round(v).toLocaleString() : '—'}</span>`
         + `<span class="d ann-v ${sg(dd)}">${annPct(dd)}</span>`
         + `<span class="s ann-v ${sg(ss)}">${annNum(ss, 2)}</span>`;
  };
  const candleUp = snap.features && snap.features.price ? (snap.features.price.close >= snap.features.price.open) : true;
  const bodyCls = candleUp ? 'up' : 'down';
  const prevGap = ANN.idx > 0 ? (snap.i - ANN.sigs[ANN.idx - 1].i) : null;
  const nextGap = ANN.idx < ANN.sigs.length - 1 ? (ANN.sigs[ANN.idx + 1].i - snap.i) : null;
  const annotedTxt = d && d.d ? (d.d === 'yes' ? '会做' : d.d === 'no' ? '不做' : '跳过') : '';
  const evidence = s.evidence;
  const signalCard = !evidence ? '' : `<section class="ann-card"><div class="ann-sec">信号判定依据</div>
    <p class="ann-line">${evidence.definition}</p>
    ${evidence.type === 'ma_body_combo_v1' ? `<div class="ann-grid">${evidence.periods.map((p,k)=>`<span>SMA${p}</span><b>${fmt(evidence.maValues[k])}</b>`).join('')}<span>开盘 → 收盘</span><b>${fmt(evidence.open)} → ${fmt(evidence.close)}</b></div>`
      : `<div class="ann-grid"><span>参照低点</span><b>${annDate(evidence.referenceTime)} · ${fmt(evidence.referenceLow)}</b><span>当前新低</span><b>${fmt(evidence.currentLow)}</b><span>间隔</span><b>${s.i-evidence.referenceIndex} 根</b><span>${evidence.metric==='dif'?'DIF':'MACD柱'}：前低 → 当前</span><b>${evidence.referenceValue.toPrecision(5)} → ${evidence.currentValue.toPrecision(5)}</b></div><p class="ann-line">候选信号可继续下跌；不使用未来K线确认底部。</p>`}
  </section>`;
  body.innerHTML = thin + annCoverCard(s.i) + annAssessmentCard(d) + signalCard + `
    <div class="ann-date"><b>${annDate(snap.time)}</b><span class="ann-tag">${s.tag}</span>
      <span style="flex:1"></span><span style="color:var(--muted)">收</span><b>${snap.close.toLocaleString()}</b></div>

    <div class="ann-card">
      <div class="ann-sec">K 线</div>
      <div class="ann-grid">
        <span>当日涨跌</span>${P(snap.chg)}
        <span>实体</span><b class="ann-v ${bodyCls}">${(snap.body * 100).toFixed(2)}%</b>
        <span>振幅</span><b>${(snap.rng * 100).toFixed(2)}%</b>
        <span>收盘位置</span><b>${snap.pos.toFixed(2)}</b>
        ${annCoverActive() ? '' : `<span>量比 20</span><b>${snap.vr == null ? '—' : snap.vr.toFixed(2)}</b><span title="前5日均量 ÷ 前20日均量，<1 表示缩量">缩量比</span><b>${snap.vsh == null ? '—' : snap.vsh.toFixed(2)}</b>`}
        <span title="收盘距最近 20 根最高点的回撤">距20日高</span><b>${snap.pub20 == null ? '—' : annPct(snap.pub20)}</b>
        <span title="ATR%(14) = 过去 14 根 K 线的平均真实波幅 ÷ 收盘价">ATR%(14)</span><b>${snap.atrPct == null ? '—' : (snap.atrPct * 100).toFixed(2) + '%'}</b>
      </div>
    </div>

    <div class="ann-card">
      <div class="ann-sec">均线 / 斜率 <span class="lab-hint" style="text-transform:none;letter-spacing:0">距 离 · 斜率 w5</span></div>
      <div class="ann-ma">
        <span class="ann-ma-head"></span><span class="ann-ma-head">均线</span>
        <span class="ann-ma-head">距离</span><span class="ann-ma-head" title="斜率 = 每根 K 线 MA 变化 %">斜率w5</span>
        ${maRow(20)}${maRow(60)}${maRow(120)}
      </div>
      <div class="ann-line" style="margin-top:6px">${arrTxt} · 站上 ${snap.above}/3 · 连站 MA60 ${snap.streak60} 根 · 双线差 ${annPct(snap.gap)}</div>
      <div class="ann-line">距上一个信号 <b>${prevGap == null ? '—' : prevGap + ' 根'}</b> · 距下一个 ${(annCoverActive() || s.evidence) && ANN.hideFuture ? '已遮住' : nextGap == null ? '—' : nextGap + ' 根'}</div>
    </div>

    <div class="ann-card">
      <div class="ann-sec">位置 / MACD</div>
      <div class="ann-grid">
        <span>距 60 日高</span><b>${annPct(snap.distHigh60)}</b>
        <span>距 60 日低</span><b>${annPct(snap.distLow60)}</b>
        <span>DIF / DEA</span><b>${snap.dif == null ? '—' : snap.dif.toFixed(0) + ' / ' + snap.dea.toFixed(0)}</b>
        <span>MACD 柱</span>${V(snap.hist, snap.hist == null ? '—' : snap.hist.toFixed(0))}
        ${annCoverActive() || s.evidence ? '' : `<span>最近笔</span>${V(snap.strokeDir, strokeTxt)}<span>买点</span><b>${snap.chanBuys.length ? snap.chanBuys.join('、') : '—'}</b>`}
      </div>
      <div class="ann-line" style="margin-top:5px">${annCoverActive() || s.evidence ? '本信号仅使用当时价格与指标，不使用事后笔/中枢。' : pivotTxt}</div>
    </div>

    <details class="ann-fwd ann-card">
      <summary>未来 5 / 20 / 60 日（先判断，再展开）</summary>
      <div class="ann-grid" style="margin-top:6px">
        <span>+5 日</span>${P(snap.fwd5)}
        <span>+20 日</span>${P(snap.fwd20)}
        <span>+60 日</span>${P(snap.fwd60)}
        <span>20 日内最大浮盈</span>${P(snap.mfe20)}
        <span>20 日内最大浮亏</span>${P(snap.mae20)}
      </div>
    </details>
    ${annotedTxt ? `<div class="ann-mine">你的判定：<b class="${d.d}">${annotedTxt}</b>${(d.reasons || []).length ? ' · ' + d.reasons.join('、') : ''}</div>` : ''}`;

  const volumeOnChart = document.getElementById('cover67VolumeOnChart');
  if (volumeOnChart) volumeOnChart.addEventListener('click', () => {
    cover67IndicatorEnable(true);
    cover67VolumeOpen(candlesFor('raw')[s.i].time);
  });
  const coverReview = document.getElementById('cover67Review');
  if (coverReview) coverReview.addEventListener('click', () => {
    const i = s.i;
    ANN.source = ANN.source === 'ma20_simple_v2' ? 'ma20_simple_check_v2' : 'ma20_simple_v2';
    annBuildSigs(); annSourceMenuRender();
    const next = ANN.sigs.findIndex(x => x.i >= i);
    if (ANN.sigs.length) annGo(next < 0 ? ANN.sigs.length - 1 : next); else annRender();
  });
  // 已判定 → 对应按钮持续高亮(随时能看到这笔之前怎么判的)
  document.querySelectorAll('.ann-btn').forEach(b => {
    b.classList.toggle('on', !!(d && d.d === b.dataset.d));
  });

  body.querySelectorAll('[data-ann-pick]').forEach(b=>b.addEventListener('click',()=>annStartChartPick(b.dataset.annPick)));
  body.querySelectorAll('[data-assessment]').forEach(el=>{
    el.addEventListener('change',annSaveAssessment);
    if(el.tagName!=='SELECT')el.addEventListener('input',annSaveAssessment);
  });
  const chips = document.getElementById('annReasons');
  if (chips) {
    const sel = new Set((d && d.reasons) || []);
    chips.innerHTML = ANN_REASON_GROUPS.map(g =>
      `<span class="ann-chip-sep">${g.label}</span>` +
      g.items.map(r => `<button class="ann-chip${sel.has(r) ? ' on' : ''}" data-r="${r}">${r}</button>`).join('')
    ).join('');
    chips.querySelectorAll('.ann-chip').forEach(b => b.addEventListener('click', () => annToggleReason(b.dataset.r)));
  }
  const note = document.getElementById('annNote');
  if (note && document.activeElement !== note) note.value = (d && d.note) || '';
  annSumRender();
}

// 导入: 把导出的 items 写回 localStorage(按 品种|周期|源|时间 建键)
function annImportData(txt) {
  let obj = null;
  try { obj = JSON.parse(txt); } catch (e) { annTip('导入失败: 不是合法 JSON', true); return; }
  const items = (obj && obj.items) || [];
  if (!items.length) { annTip('导入失败: 文件里没有 items', true); return; }
  const cs = candlesFor('raw');
  const byTime = new Map();
  for (const c of cs) byTime.set(c.time, c);
  const byDate = new Map();
  for (const c of cs) byDate.set(String(c.time), c);
  let n = 0, miss = 0;
  for (const it of items) {
    const t = (it.features && it.features.time) || null;
    let bar = t != null ? byTime.get(t) : null;
    if (!bar && it.time) {
      for (const c of cs) if (fmtTime(c.time) === it.time) { bar = c; break; }
    }
    if (!bar) { miss++; continue; }
    const k = [currentSym, activeTF, it.source || 'ma20', bar.time].join('|');
    ANN.decisions[k] = {
      d: it.decision || null, reasons: it.reasons || [], note: it.note || '',
      ts: Date.now(), source: it.source || 'ma20', tag: it.tag || '', kind: it.kind || '',
      ...(it.algorithm ? { algorithm: it.algorithm } : {}),
      ...(it.assessment ? { assessment: it.assessment } : {}),
      ...(it.assessmentEvidence ? { assessmentEvidence: it.assessmentEvidence } : {}),
      ...(it.signalEvidence ? { signalEvidence: it.signalEvidence } : {}),
    };
    n++;
  }
  annSave(); annBuildSigs(); annRender(); annSumRender();
  annTip(`✓ 导入 ${n} 条${miss ? ' · ' + miss + ' 条对不上K线' : ''}`, false);
}

function annTip(msg, warn) {
  const el = document.getElementById('annSaveTip');
  if (!el) return;
  el.textContent = msg;
  el.classList.toggle('warn', !!warn);
  clearTimeout(annTip._t);
  annTip._t = setTimeout(() => { el.textContent = ''; el.classList.remove('warn'); }, 3600);
}

function annExportData() {
  const cs = candlesFor('raw');
  const idx = new Map(cs.map((c, i) => [c.time, i]));
  const prefix = `${currentSym}|${activeTF}|`;
  const items = [];
  for (const [key, rec] of Object.entries(ANN.decisions)) {
    if (!key.startsWith(prefix) || !rec || !rec.d) continue;
    const parts = key.split('|');
    const src = parts[2], tt = +parts[3];
    const i = idx.has(tt) ? idx.get(tt) : null;
    items.push({
      source: src,
      sourceName: (ANN_SOURCES[src] || {}).name || src,
      time: annDate(tt),
      decision: rec.d,
      tag: rec.tag || '',
      kind: rec.kind || '',
      ...(rec.algorithm ? { algorithm: rec.algorithm } : {}),
      ...(rec.assessment ? { assessment: rec.assessment } : {}),
      ...(rec.assessmentEvidence ? { assessmentEvidence: rec.assessmentEvidence } : {}),
      ...(rec.signalEvidence ? { signalEvidence: rec.signalEvidence } : {}),
      reasons: rec.reasons || [],
      note: rec.note || '',
      // 判定当时的特征快照(旧数据没有则按当前数据补算)
      features: rec.features || (i != null ? annFeatures(i) : null),
    });
  }
  items.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : a.source < b.source ? -1 : 1));
  return { schema: 'chanlun.ann/2', symbol: currentSym, tf: activeTF,
           exportedAt: new Date().toISOString(), count: items.length, items };
}

function annExport() {
  const out = annExportData();
  if (!out.count) { alert('当前品种/周期还没有任何标注'); return; }
  const blob = new Blob([JSON.stringify(out, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `annotations-${currentSym}-${activeTF}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 3000);
}

// ---------------------------------------------------------------- 启动 -- //
function annRefresh() {
  if (!ANN._built || !ANN.on) return;
  ANN._counts = {};
  annBuildSigs();
  annSourceMenuRender();
  annRender();
  annDraw();
}

function annInit() {
  if (ANN._prim) return;
  ANN._prim = new AnnPrimitive(candleSeries, chart, 'main');
  candleSeries.attachPrimitive(ANN._prim);
  // 副图同样遮住: 量能 / MACD 各自挂一个只画遮罩的 primitive
  const pv = new AnnPrimitive(volumeSeries, volumeChart, 'shade');
  const pm = new AnnPrimitive(macdHistSeries, macdChart, 'shade');
  volumeSeries.attachPrimitive(pv);
  macdHistSeries.attachPrimitive(pm);
  ANN._prims = [ANN._prim, pv, pm];
  const banner=document.createElement('div');banner.id='annChartPickBanner';banner.hidden=true;
  banner.innerHTML='<span></span><button type="button" class="lab-btn">取消</button>';
  document.getElementById('chart').appendChild(banner);banner.querySelector('button').addEventListener('click',()=>annCancelChartPick());
  chart.subscribeClick(annHandleChartPick);
  // 右侧价格轴遮罩(盲判时盖住轴上的最新价)
  const box = document.querySelector('.chart-box');
  if (box && !document.getElementById('annBlindCover')) {
    const cover = document.createElement('div');
    cover.id = 'annBlindCover';
    cover.className = 'ann-blind-cover';
    box.appendChild(cover);
  }
  annLoad();
  annPanelBuild();
  annBuildSigs();
  annRender();
}

(function () {
  const btn = document.getElementById('annBtn');
  if (!btn) return;
  btn.addEventListener('click', () => annSetOpen(!ANN.on));
  annInit();
  // Shareable verification entry: open ETH/BTC with all MA20 crosses visible.
  const verifySymbol = new URLSearchParams(location.search).get('cover67');
  if (['ETH', 'BTC'].includes(verifySymbol)) {
    (async () => {
      if (currentSym !== verifySymbol) await switchSymbol(verifySymbol);
      ANN.source = 'ma20_simple_check_v2'; ANN.idx = 0;
      annSetOpen(true);
    })().catch(e => console.error('MA20 verification entry failed', e));
  }
})();
