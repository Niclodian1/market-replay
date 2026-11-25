// js/06-quick-order.js — 「快速下单条」(复盘与练习共用, 取代右侧旧开仓表单)
//  B/S 打开(做多/做空) → 自动判定市场环境、预选入场信号、默认本根极值止损 → Enter 开仓。
//  · 保证金/杠杆沿用右侧表单(双向同步并记住上次值), 随时可改;
//  · 至少 1 个入场信号才能开仓; 理由是可选补充;
//  · W 记为「等待」(需写等待条件), X 记为「放弃」;
//  · 数字 1-9 套用下单模板; L/P/A 切换止损(本根/波段/ATR), 或直接在图上点价位;
//  · 每一单记录 quick 元数据: 标签来源(自动/确认/推翻/手动)、模板、止损方式、用时、操作次数。
// 原右侧开仓表单与「记录判断」弹窗全部保留, 快速下单条只是一个更快的入口。
const QO_TPL_KEY = 'chanlun.quick.templates.v1';
const QO_SIZE_KEY = 'chanlun.journal.sizing.v1';
const QO_MIN_KEY = 'chanlun.quick.min.v1';
const QO_STOP_LABEL = { bar:'本根极值', swing:'前波段', atr:'1.5×ATR', chart:'图上点选', manual:'手动' };
const QO_STOP_SPEC = {
  bar:   { key:'L', label:'本根极值', short:'本根', color:'#f59e0b' }, // 琥珀橙
  swing: { key:'P', label:'前波段',   short:'波段', color:'#0ea5e9' }, // 天青蓝
  atr:   { key:'A', label:'1.5×ATR',  short:'ATR',  color:'#a855f7' }, // 紫罗兰
};
const QO_STOP_ORDER = ['bar', 'swing', 'atr'];
const QO_CUSTOM_STOP_COLOR = '#f23645';
const QO_EXPECTS = ['短线', '中线', '长线'];
const QO_EXP_CLS = { '短线': 'exp-short', '中线': 'exp-mid', '长线': 'exp-long' };
const QO = {
  open:false, side:'多', expect:'中线', barTime:null, stop:null, stopMethod:'bar', stopPicked:false,
  stops:{ bar:null, swing:null, atr:null }, presetId:null,
  auto:{ market:{}, entry:new Set(), pattern:new Set() },
  sel:{ market:{}, entry:new Set(), pattern:new Set() },
  touchedMarket:new Set(), keys:0, barSeenAt:Date.now(), lastBar:null, lines:[], el:null,
  min:false, entry:null,   // min=收起成一行(不挡K线); entry=复盘模式自定开仓价(null=收盘)
};
const qoIsOpen = () => QO.open;
// 快速下单可用: 复盘中, 或练习进行中(揭示答案后不可再下单)
const qoAvailable = () => typeof inReplay === 'function' && inReplay() && !(TR.active && TR.revealed);
const qoPractice = () => trLocked() && !!TR.research;
const qoEsc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
const qoTemplates = () => { try { return JSON.parse(localStorage.getItem(QO_TPL_KEY)) || []; } catch { return []; } };
const qoSaveTemplates = arr => { try { localStorage.setItem(QO_TPL_KEY, JSON.stringify(arr.slice(0, 9))); } catch {} };
// 右侧表单里的全部标签定义(唯一来源, 两边永远一致)
function qoTagDefs(cat){
  return [...document.querySelectorAll(`#jLogicTags .logic-tag[data-cat="${cat}"]`)]
    .map(b => ({ val:b.dataset.val, group:b.dataset.group || '' }));
}
function qoBars(){
  const bar = curBar();
  if (!bar || typeof prBars !== 'function') return [];
  return prBars(-Infinity, bar.time);
}
function qoMacdDivergence(side, t){
  try {
    if (typeof MACD3SEG === 'undefined' || !MACD3SEG.on || typeof macd3segSeries !== 'function') return false;
    const s = macd3segSeries();
    const trades = (s && s.trades) || [];
    return trades.some(tr => tr.entry_time === t && ((tr.side === 'long') === (side !== '空')));
  } catch { return false; }
}
// ---- 自动判定 ----
function qoComputeAuto(){
  const cs = qoBars();
  const bar = curBar();
  const m = QuickCore.market(cs);
  QO.auto.market = { ma120dir:m.ma120dir, pricepos:m.pricepos };
  QO.auto.entry = new Set(QuickCore.signals(cs, QO.side, { macdDivergence: bar && qoMacdDivergence(QO.side, bar.time) }));
  QO.auto.pattern = new Set();   // 形态(三角形中继/趋势线/蓄势)暂不自动识别
  return cs;
}
function qoResetStop(cs){
  const bar = curBar();
  const entry = bar ? qoEntry(bar) : null;
  const research = trLocked() && TR.research?.mode === 'research';
  QO.stops = QuickCore.stops(cs || qoBars(), QO.side, entry);
  if (research){
    QO.stopMethod = 'bar';
    QO.stopPicked = true;
    QO.stop = QO.stops.bar;
    return;
  }
  if (QO.stopMethod === 'chart' || QO.stopMethod === 'manual'){
    const ok = QO.stop != null && entry > 0 && (QO.side === '空' ? QO.stop > entry : QO.stop < entry);
    if (ok) return;
    QO.stopMethod = 'bar';
    QO.stopPicked = false;
  }
  if (QO.stops[QO.stopMethod] != null){
    QO.stop = QO.stops[QO.stopMethod];
    return;
  }
  const fallback = QO_STOP_ORDER.find(m => QO.stops[m] != null) || 'bar';
  QO.stopMethod = fallback;
  QO.stop = QO.stops[fallback] ?? null;
  QO.stopPicked = false;
}
// 打开 / 刷新(换K线或换方向时重算自动部分, 保留你手动加的标签)
function qoOpen(side){
  if (!qoAvailable()){ flashBtn('snapshotBtn', '先进入复盘或开始练习，再按 B / S 下单'); return; }
  if (typeof prDraft !== 'undefined' && prDraft) return;   // 「记录判断」弹窗打开时不抢焦点
  const bar = curBar(); if (!bar) return;
  // 按下去有回应，再按能收回：已在当前同方向打开时再次按 B/S 直接收起
  if (QO.open && side && side === QO.side && QO.barTime === bar.time){
    qoClose();
    return;
  }
  // 同一根K线上关掉再打开, 保留刚才的选择(Esc 不会丢掉已选信号)
  const reopen = QO.barTime === bar.time && QO._manual != null;
  if (!reopen){
    QO.entry = null; QO.note = '';
    QO.keys = 0; QO.presetId = null; QO.touchedMarket = new Set();
    QO.sel = { market:{}, entry:new Set(), pattern:new Set() };
    QO.stopMethod = 'bar';
    QO.stopPicked = false;
  } else if (side && side !== QO.side){
    if (QO.stopMethod === 'chart' || QO.stopMethod === 'manual'){
      QO.stopMethod = 'bar';
      QO.stopPicked = false;
    }
  }
  const wasClosed = !QO.open;
  QO.side = side || QO.side;
  QO.barTime = bar.time;
  QO.open = true;
  qoRecalc(!reopen);
  QO.keys++;
  qoRender(wasClosed);
}
function qoRecalc(fresh){
  const prevAuto = { entry:new Set(QO.auto.entry), pattern:new Set(QO.auto.pattern) };
  const cs = qoComputeAuto();
  // 市场环境: 没被你改过的组跟随自动值
  for (const g of ['ma120dir', 'pricepos'])
    if (!QO.touchedMarket.has(g)) QO.sel.market[g] = QO.auto.market[g] || null;
  // 信号: 去掉上一次自动预选但你没动过的, 换成新的自动预选
  for (const cat of ['entry', 'pattern']){
    if (fresh) QO.sel[cat] = new Set(QO.auto[cat]);
    else {
      for (const v of prevAuto[cat]) if (!QO.auto[cat].has(v) && !QO._manual?.[cat]?.has(v)) QO.sel[cat].delete(v);
      for (const v of QO.auto[cat]) if (!QO._rejected?.[cat]?.has(v)) QO.sel[cat].add(v);
    }
  }
  if (fresh){ QO._manual = { entry:new Set(), pattern:new Set() }; QO._rejected = { entry:new Set(), pattern:new Set() }; }
  qoResetStop(cs);
}
function qoClose(immediate = false){
  QO.open = false;
  qoClearLines();
  if (QO.el){
    if (window.uiMotion && !immediate) window.uiMotion.closeOverlay(QO.el, { duration: 140 });
    else { QO.el.classList.remove('is-open', 'is-closing'); QO.el.hidden = true; }
  }
}
function qoClearLines(){
  for (const it of QO.lines){
    try { candleSeries.removePriceLine(it.line); } catch {}
  }
  QO.lines = [];
  document.getElementById('chart')?.classList.remove('qo-line-hover');
}
function qoDrawLines(){
  qoClearLines();
  if (!QO.open) return;
  const SOLID = LightweightCharts.LineStyle.Solid;
  const DASHED = LightweightCharts.LineStyle.Dashed;
  const research = trLocked() && TR.research?.mode === 'research';
  const addLine = (key, price, color, lineStyle, lineWidth, title) => {
    try {
      const line = candleSeries.createPriceLine({
        price, color, lineWidth, lineStyle, axisLabelVisible: true, title,
      });
      QO.lines.push({ key, price, color, lineStyle, lineWidth, line });
    } catch {}
  };
  if (research){
    if (QO.stops.bar != null) addLine('bar', QO.stops.bar, QO_STOP_SPEC.bar.color, SOLID, 2, '✓ 止损·本根');
    return;
  }
  // 先画所有未选中的候选虚线, 再画选中的实线(两档重合时实线压在最上层)
  for (const m of QO_STOP_ORDER){
    const price = QO.stops[m];
    if (price == null) continue;
    if (QO.stopPicked && QO.stopMethod === m) continue;
    const spec = QO_STOP_SPEC[m];
    addLine(m, price, spec.color, DASHED, 1, `${spec.short}(${spec.key})`);
  }
  if (QO.stopPicked && QO_STOP_SPEC[QO.stopMethod] && QO.stops[QO.stopMethod] != null){
    const spec = QO_STOP_SPEC[QO.stopMethod];
    addLine(QO.stopMethod, QO.stops[QO.stopMethod], spec.color, SOLID, 2, `✓ 止损·${spec.short}`);
  } else if (QO.stopPicked && (QO.stopMethod === 'chart' || QO.stopMethod === 'manual') && QO.stop != null){
    addLine(QO.stopMethod, QO.stop, QO_CUSTOM_STOP_COLOR, SOLID, 2, `✓ 止损·${QO.stopMethod === 'chart' ? '点选' : '手动'}`);
  }
}
function qoHitStopLine(y){
  if (!Number.isFinite(y)) return null;
  let best = null, bestD = 9.0001;
  for (const m of QO_STOP_ORDER){
    const px = QO.stops[m];
    if (px == null) continue;
    const py = candleSeries.priceToCoordinate(px);
    if (py == null) continue;
    const d = Math.abs(py - y);
    if (d <= 9 && d < bestD){ bestD = d; best = m; }
  }
  return best;
}
function qoSelectStopMethod(m){
  const spec = QO_STOP_SPEC[m];
  if (!spec) return false;
  if (QO.stops[m] == null){
    qoError(`${spec.label}止损在当前 K 线不可用`);
    return false;
  }
  QO.stopMethod = m;
  QO.stopPicked = true;
  QO.keys++;
  qoResetStop();
  qoRender();
  return true;
}
function qoStopBtns(compact){
  return `<div class="seg qo-stopm${compact ? ' qo-stopm-min' : ''}">` + QO_STOP_ORDER.map(m => {
    const spec = QO_STOP_SPEC[m];
    const avail = QO.stops[m] != null;
    const on = QO.stopPicked && QO.stopMethod === m;
    const title = avail ? `${spec.label} (${prNumber(QO.stops[m], 4)}) · 快捷键 ${spec.key}` : `${spec.label}在当前 K 线不可用`;
    const lbl = compact ? `${m === 'atr' ? 'ATR' : spec.short[0]}<kbd>${spec.key}</kbd>` : `${spec.short} <kbd>${spec.key}</kbd>`;
    return `<button type="button" data-qo-stop="${m}" style="--stop-col:${spec.color}" class="${on ? 'active' : ''}" ${avail ? '' : 'disabled'} title="${title}"><i class="qo-sdot"></i>${lbl}</button>`;
  }).join('') + '</div>';
}
// ---- 渲染 ----
function qoChip(cat, val, group){
  const on = cat === 'market' ? QO.sel.market[group] === val : QO.sel[cat].has(val);
  const auto = cat === 'market' ? QO.auto.market[group] === val : QO.auto[cat].has(val);
  return `<button type="button" class="qo-chip${on ? ' on' : ''}${auto ? ' auto' : ''}" data-qo-cat="${cat}" data-qo-group="${group}" data-qo-val="${qoEsc(val)}"
    title="${auto ? '系统自动判定/预选' : ''}">${qoEsc(val)}</button>`;
}
function qoRender(animateEnter = false){
  if (!QO.el) return;
  const bar = curBar();
  if (!QO.open || !bar){ QO.el.hidden = true; return; }
  const research = trLocked() && TR.research?.mode === 'research';
  const practice = qoPractice();
  const entry = qoEntry(bar);
  const margin = +document.getElementById('jCapital').value || 0;
  const lev = Math.min(125, Math.max(1, +document.getElementById('jLeverage').value || 1));
  const risk = QuickCore.riskAt(entry, QO.stop, QO.side, margin, lev);
  const eq = typeof acctEquity === 'function' ? acctEquity() : null;
  const riskTxt = risk == null ? '<span class="qo-warn">无止损</span>'
    : `亏 <b class="qo-neg">${risk.toFixed(0)}U</b>${eq > 0 ? ` (${(Math.abs(risk) / eq * 100).toFixed(1)}%)` : ''}`;
  const stopPct = QO.stop != null ? ((QO.stop / entry - 1) * 100).toFixed(2) + '%' : '—';
  const stopDesc = QO.stopPicked ? (QO_STOP_LABEL[QO.stopMethod] || '') : '默认本根';
  const expBtns = `<div class="seg qo-exp">${QO_EXPECTS.map(x => `<button type="button" data-qo-expect="${x}" class="${QO.expect === x ? 'active ' + QO_EXP_CLS[x] : ''}">${x}</button>`).join('')}</div>`;
  QO.el.classList.toggle('qo-min', QO.min);
  QO.el.classList.toggle('qo-is-long', QO.side !== '空');
  QO.el.classList.toggle('qo-is-short', QO.side === '空');
  if (QO.min){
    const sig = [...QO.sel.entry, ...QO.sel.pattern];
    QO.el.innerHTML = `
      <div class="qo-row">
        <b class="qo-badge ${QO.side === '空' ? 'short' : 'long'}">${QO.side === '空' ? '做空' : '做多'}</b>
        <span class="pos-exp-tag ${QO_EXP_CLS[QO.expect] || 'exp-mid'}">${QO.expect}</span>
        <span class="qo-dim">@</span><b class="qo-num">${prNumber(entry, 4)}</b>
        <span class="qo-k">损</span><b class="qo-num">${QO.stop != null ? prNumber(QO.stop, 4) : '—'}</b><span class="qo-dim">${stopPct}</span>
        ${research ? '' : qoStopBtns(true)}
        <span class="qo-risk">${riskTxt}</span>
        <span class="qo-sig${sig.length ? '' : ' qo-warn'}">${sig.length ? qoEsc(sig.join('、')) : '未选信号'}</span>
        <button type="button" class="btn qo-btn" data-qo-act="toggle-min" title="展开 (H)">▴ <kbd>H</kbd></button>
        <button type="button" class="btn qo-btn qo-go ${QO.side === '空' ? 'short' : 'long'}" data-qo-act="buy">${QO.side === '空' ? '开空' : '开多'} <kbd>Enter</kbd></button>
        <button type="button" class="qo-x" data-qo-act="close" title="关闭 (Esc)">✕</button>
      </div>
      <div class="qo-err" role="status"></div>`;
    if (animateEnter && window.uiMotion) window.uiMotion.openOverlay(QO.el);
    else { QO.el.hidden = false; QO.el.classList.remove('is-closing'); QO.el.classList.add('is-open'); }
    qoDrawLines();
    return;
  }
  const marketDefs = qoTagDefs('market');
  const groupRow = g => marketDefs.filter(d => d.group === g).map(d => qoChip('market', d.val, g)).join('');
  const tpls = qoTemplates();
  const sbg = !trLocked() && typeof window.getCurrentSBg === 'function' ? window.getCurrentSBg() : null;
  const counterSbg = sbg && ((sbg.bias === 'bear' && QO.side === '多') || (sbg.bias === 'bull' && QO.side === '空'));
  const riskPctAuto = counterSbg ? 0.5 : 1.5;
  QO.el.innerHTML = `
    ${counterSbg ? `<div class="qo-sbg-warn">⚠ 逆 S 级${sbg.bias === 'bear' ? '红框(看空)' : '绿框(看多)'}开${QO.side}：建议技术验证并减半至 <b>0.5%</b>（${qoEsc(sbg.headline || '大背景相反')}）</div>` : ''}
    <div class="qo-row qo-top">
      <div class="seg qo-side">
        <button type="button" data-qo-side="多" class="${QO.side === '多' ? 'active long' : ''}">多 <kbd>B</kbd></button>
        <button type="button" data-qo-side="空" class="${QO.side === '空' ? 'active short' : ''}">空 <kbd>S</kbd></button>
      </div>
      ${expBtns}
      <span class="qo-sep"></span>
      <span class="qo-k">价</span>${practice
        ? `<b class="qo-num">${prNumber(bar.close, 4)}</b>`
        : `<input class="qo-inp qo-entry" type="number" step="any" value="${QO.entry ?? ''}" placeholder="${prNumber(bar.close, 4)}" title="留空 = 当前收盘价">`}
      <span class="qo-k">损</span>
      <input class="qo-inp qo-stop" type="number" step="any" value="${QO.stop ?? ''}" ${research ? 'disabled title="规则研究固定本根极值止损"' : ''}>
      <span class="qo-dim">${stopPct}</span>
      ${research ? '' : qoStopBtns(false)}
      <span class="qo-sep"></span>
      <label class="qo-k">本金<input class="qo-inp qo-margin" type="number" min="1" value="${margin}"></label>
      <label class="qo-k">杠杆<input class="qo-inp qo-lev" type="number" min="1" max="125" value="${lev}"></label>
      <button type="button" class="btn qo-risk-btn${counterSbg ? ' warn' : ''}" data-qo-act="size-by-risk" data-qo-rp="${riskPctAuto}" title="按账户净值 ${riskPctAuto}% 风险算仓">🎯 ${riskPctAuto}%</button>
      <button type="button" class="btn qo-risk-btn" data-qo-act="to-logic-gate" title="带入 30 秒开仓卡">🧭 开仓卡</button>
      <span class="qo-risk">${riskTxt}</span>
      <button type="button" class="qo-x qo-minbtn" data-qo-act="toggle-min" title="收起 (H)">▾ <kbd>H</kbd></button>
      <button type="button" class="qo-x" data-qo-act="close" title="关闭 (Esc)">✕</button>
    </div>
    <div class="qo-row"><span class="qo-k qo-lbl">环境</span>${groupRow('ma120dir')}<span class="qo-gap"></span>${groupRow('pricepos')}
      <span class="qo-dim qo-hint">${QO.auto.market.ma120dir ? '虚线框=系统预选' : '数据不足，请手选'}</span></div>
    <div class="qo-row"><span class="qo-k qo-lbl">信号<b class="qo-req">≥1</b></span>${qoTagDefs('entry').map(d => qoChip('entry', d.val, d.group)).join('')}</div>
    <div class="qo-row"><span class="qo-k qo-lbl">形态</span>${qoTagDefs('pattern').map(d => qoChip('pattern', d.val, d.group)).join('')}</div>
    <div class="qo-row"><span class="qo-k qo-lbl">模板</span>
      ${tpls.length ? tpls.map((t, i) => `<span class="qo-tpl${QO.presetId === t.id ? ' on' : ''}"><button type="button" data-qo-tpl="${i}" title="${qoEsc([...t.entry, ...t.pattern].join(' + '))}"><kbd>${i + 1}</kbd> ${qoEsc(t.name)}</button><button type="button" class="qo-tpl-del" data-qo-tpl-del="${i}" title="删除模板">×</button></span>`).join('')
        : '<span class="qo-dim">暂无模板</span>'}
      ${tpls.length < 9 ? '<button type="button" class="qo-addtpl" data-qo-act="save-tpl">＋ 存模板</button>' : ''}
    </div>
    <div class="qo-row qo-bottom">
      <input class="qo-inp qo-note" maxlength="300" value="${qoEsc(QO.note || '')}" placeholder="${practice ? '备注 / 等待条件（按 W 等待时必填）' : '备注（可选）'}">
      ${practice ? `<button type="button" class="btn qo-btn" data-qo-act="skip" title="记为放弃">放弃 <kbd>X</kbd></button>
      <button type="button" class="btn qo-btn" data-qo-act="wait" title="记为等待">等待 <kbd>W</kbd></button>` : ''}
      <button type="button" class="btn qo-btn qo-go ${QO.side === '空' ? 'short' : 'long'}" data-qo-act="buy">${QO.side === '空' ? '开空' : '开多'} · ${QO.expect} <kbd>Enter</kbd></button>
    </div>
    <div class="qo-err" role="status"></div>`;
  if (animateEnter && window.uiMotion) window.uiMotion.openOverlay(QO.el);
  else { QO.el.hidden = false; QO.el.classList.remove('is-closing'); QO.el.classList.add('is-open'); }
  qoDrawLines();
}
function qoEntry(bar){
  if (qoPractice() || QO.entry == null) return bar.close;   // 练习一律按收盘
  return QO.entry;
}
function qoToggleMin(){
  const prevRect = QO.el && !QO.el.hidden ? QO.el.getBoundingClientRect() : null;
  QO.min = !QO.min; QO.keys++;
  try { localStorage.setItem(QO_MIN_KEY, QO.min ? '1' : '0'); } catch {}
  qoRender(false);
  if (prevRect && QO.el) window.uiMotion?.flipFrom?.(QO.el, prevRect, 200);
}
function qoError(msg){
  const e = QO.el?.querySelector('.qo-err');
  if (e) e.textContent = msg || '';
  if (msg && QO.el) window.uiMotion?.shake?.(QO.el);
}
// ---- 采集元数据 ----
function qoMeta(){
  const tagSource = {};
  for (const g of ['ma120dir', 'pricepos']){
    const v = QO.sel.market[g]; if (!v) continue;
    tagSource[v] = QO.touchedMarket.has(g) ? (QO.auto.market[g] === v ? 'auto-confirmed' : 'manual') : 'auto';
  }
  for (const cat of ['entry', 'pattern']){
    for (const v of QO.sel[cat]) tagSource[v] = QO.auto[cat].has(v) ? 'auto-confirmed' : 'manual';
    for (const v of QO.auto[cat]) if (!QO.sel[cat].has(v)) tagSource[v] = 'auto-rejected';
  }
  return {
    version: QuickCore.VERSION, via: 'quick', presetId: QO.presetId, expect: QO.expect,
    tagSource, autoMarket: { ...QO.auto.market },
    stopMethod: QO.stopMethod,
    stopPicked: QO.stopPicked,
    sizing: { mode: 'margin-leverage', margin: +document.getElementById('jCapital').value || null,
      leverage: +document.getElementById('jLeverage').value || null },
    decisionMs: Math.max(0, Date.now() - QO.barSeenAt), keystrokes: QO.keys,
  };
}
// 把快速条的选择同步到右侧表单: 开仓逻辑、ltSelected(练习存档 tags)都从表单读取
function qoSyncForm(){
  document.getElementById('jSide').value = QO.side;
  const expEl = document.getElementById('jExpect');
  if (expEl) expEl.value = QO.expect;
  document.querySelectorAll('#jLogicTags .logic-tag').forEach(b => {
    const { cat, group, val } = b.dataset;
    const on = cat === 'market' ? QO.sel.market[group] === val : QO.sel[cat]?.has(val);
    b.classList.toggle('active', !!on);
  });
  if (typeof updateLtCounts === 'function') updateLtCounts();
}
function qoSignalSummary(){
  return [...QO.sel.entry, ...QO.sel.pattern].join('、');
}
function qoSubmit(choice){
  if (!QO.open) return;
  QO.keys++;
  const bar = curBar();
  if (!bar || bar.time !== QO.barTime){ qoOpen(); return; }
  const note = (QO.el.querySelector('.qo-note')?.value ?? QO.note ?? '').trim();
  const meta = qoMeta();
  const entry = qoEntry(bar);
  if (choice === 'buy'){
    if (!QO.sel.entry.size) return qoError('至少选 1 个入场信号');
    const miss = ['ma120dir', 'pricepos'].filter(g => !QO.sel.market[g]);
    if (miss.length) return qoError('市场环境无法自动判定, 请手选 MA120 方向和价格位置');
    if (!(entry > 0)) return qoError('开仓价须大于 0');
    if (QO.stop == null || (QO.side === '空' ? QO.stop <= entry : QO.stop >= entry))
      return qoError('没有有效止损: 多单止损须低于开仓价, 空单须高于开仓价');
    qoSyncForm();
    const logic = combineEntryLogic(selectedLogicTags(), note);
    const reason = note || qoSignalSummary();
    const ok = openJournalPosition({
      side: QO.side, expect: QO.expect, entry, stop: QO.stop, logic, quick: meta, onError: qoError,
      details: { reason, reasonTag: null, note: note || null, basis: 'quick_order', quick: meta, expect: QO.expect,
        invalid: '价格触及止损 ' + prNumber(QO.stop, 4) },
    });
    if (!ok) return;
    flashBtn('snapshotBtn', `✓ ${QO.side === '空' ? '开空' : '开多'} · ${QO.expect}`);
  } else {
    if (!qoPractice()) return qoError('等待/放弃只在练习中记录');
    if (choice === 'wait' && !note) return qoError('等待请在备注里写等待条件（例如: 收盘站上箱体上沿）');
    qoSyncForm();
    const reason = choice === 'wait' ? (qoSignalSummary() ? qoSignalSummary() + ' · ' : '') + '等待: ' + note
      : (note || qoSignalSummary() || '放弃');
    prAddDecision(choice, { reason, note: note || null, condition: choice === 'wait' ? note : null, quick: meta },
      bar, QO.side, bar.close, QO.stop);
    prCheckpoint();
    flashBtn('snapshotBtn', choice === 'wait' ? '✓ 已记为等待' : '✓ 已记为放弃');
  }
  QO.note = ''; QO._manual = null;   // 已提交: 下次打开重新开始
  qoClose();
  if (typeof updateTrainerUI === 'function') updateTrainerUI();
}
function qoApplyTemplate(i){
  const t = qoTemplates()[i]; if (!t) return;
  const validEntry = new Set(qoTagDefs('entry').map(d => d.val));
  const validPat = new Set(qoTagDefs('pattern').map(d => d.val));
  const entry = (t.entry || []).filter(v => validEntry.has(v));
  const pattern = (t.pattern || []).filter(v => validPat.has(v));
  QO.sel.entry = new Set(entry); QO.sel.pattern = new Set(pattern);
  QO._manual = { entry:new Set(entry.filter(v => !QO.auto.entry.has(v))), pattern:new Set(pattern.filter(v => !QO.auto.pattern.has(v))) };
  QO._rejected = { entry:new Set([...QO.auto.entry].filter(v => !QO.sel.entry.has(v))), pattern:new Set() };
  QO.presetId = t.id; QO.keys++;
  qoRender();
}
function qoSaveTemplate(){
  if (!QO.sel.entry.size){ qoError('先选至少 1 个入场信号再存模板'); return; }
  const def = qoSignalSummary();
  const name = prompt('模板名称', def.slice(0, 16));
  if (!name) return;
  const arr = qoTemplates();
  arr.push({ id: 'tpl-' + Date.now().toString(36), name: name.slice(0, 20), entry: [...QO.sel.entry], pattern: [...QO.sel.pattern] });
  qoSaveTemplates(arr);
  QO.presetId = arr.at(-1).id;
  qoRender();
}
// ---- 事件 ----
function qoOnClick(e){
  const t = e.target;
  const side = t.closest('[data-qo-side]');
  if (side){
    const nextSide = side.dataset.qoSide;
    if (nextSide !== QO.side && (QO.stopMethod === 'chart' || QO.stopMethod === 'manual')){
      QO.stopMethod = 'bar';
      QO.stopPicked = false;
    }
    QO.side = nextSide; QO.keys++; qoRecalc(false); qoRender(); return;
  }
  const expBtn = t.closest('[data-qo-expect]');
  if (expBtn){
    QO.expect = expBtn.dataset.qoExpect;
    QO.keys++;
    try {
      const s = JSON.parse(localStorage.getItem(QO_SIZE_KEY) || '{}');
      localStorage.setItem(QO_SIZE_KEY, JSON.stringify({ ...s, expect: QO.expect }));
    } catch {}
    qoRender(); return;
  }
  const stopB = t.closest('[data-qo-stop]');
  if (stopB){ qoSelectStopMethod(stopB.dataset.qoStop); return; }
  const chip = t.closest('[data-qo-cat]');
  if (chip){
    const { qoCat:cat, qoGroup:group, qoVal:val } = chip.dataset;
    QO.keys++;
    if (cat === 'market'){
      QO.touchedMarket.add(group);
      QO.sel.market[group] = QO.sel.market[group] === val ? null : val;
    } else {
      const s = QO.sel[cat];
      if (s.has(val)){ s.delete(val); QO._manual[cat].delete(val); if (QO.auto[cat].has(val)) QO._rejected[cat].add(val); }
      else { s.add(val); QO._rejected[cat].delete(val); if (!QO.auto[cat].has(val)) QO._manual[cat].add(val); }
      QO.presetId = null;
    }
    qoRender(); return;
  }
  const tpl = t.closest('[data-qo-tpl]');
  if (tpl){ qoApplyTemplate(+tpl.dataset.qoTpl); return; }
  const del = t.closest('[data-qo-tpl-del]');
  if (del){
    const idx = +del.dataset.qoTplDel;
    const arr = qoTemplates(); const [gone] = arr.splice(idx, 1);
    if (gone){
      const prevPreset = QO.presetId;
      qoSaveTemplates(arr);
      if (QO.presetId === gone.id) QO.presetId = null;
      qoRender();
      window.showToast?.(`已删除模板「${gone.name}」`, 'info', {
        ms: 5000,
        action: {
          label: '撤销',
          onClick: () => {
            const cur = qoTemplates();
            cur.splice(Math.min(idx, cur.length), 0, gone);
            qoSaveTemplates(cur);
            if (prevPreset === gone.id) QO.presetId = gone.id;
            if (QO.open) qoRender();
            window.showToast?.(`✓ 已恢复模板「${gone.name}」`, 'ok', 1600);
          },
        },
      });
    }
    return;
  }
  const actBtn = t.closest('[data-qo-act]');
  const act = actBtn?.dataset.qoAct;
  if (act === 'close') qoClose();
  else if (act === 'toggle-min') qoToggleMin();
  else if (act === 'save-tpl') qoSaveTemplate();
  else if (act === 'size-by-risk'){
    const bar = curBar();
    const entry = bar ? qoEntry(bar) : null;
    const eq = typeof acctEquity === 'function' ? acctEquity() : 100000;
    const rp = Number(actBtn?.dataset.qoRp) || 1.5;
    if (typeof LogicCore === 'undefined' || !bar || !(QO.stop > 0)){ qoError('请先选择有效止损位再按风险算仓'); return; }
    const sz = LogicCore.calcRiskSizing({ equity: eq, riskPct: rp, entry, stop: QO.stop, side: QO.side, maxLeverage: 10 });
    if (!sz.valid){ qoError(sz.error); return; }
    const capEl = document.getElementById('jCapital');
    const levEl = document.getElementById('jLeverage');
    const bal = typeof acctBal === 'function' ? acctBal() : eq;
    const useM = Math.min(bal, Math.max(1, Math.round(sz.suggestedMargin)));
    if (capEl){ capEl.value = String(useM); capEl.dispatchEvent(new Event('change')); }
    if (levEl){ levEl.value = String(sz.suggestedLev); levEl.dispatchEvent(new Event('change')); }
    qoRender();
    window.showToast?.(`✓ 以损定仓 (${rp}% ≈ ${sz.maxLossU}U): ${useM.toLocaleString()}U · ${sz.suggestedLev}x`, 'ok');
  } else if (act === 'to-logic-gate'){
    const bar = curBar();
    const entry = bar ? qoEntry(bar) : null;
    if (typeof window.openLogicStudio === 'function'){
      window.openLogicStudio('gate', {
        side: QO.side,
        horizon: QO.expect,
        entryPrice: entry,
        stopPrice: QO.stop,
        techEntry: qoSignalSummary(),
      });
    }
  } else if (act === 'buy' || act === 'wait' || act === 'skip') qoSubmit(act);
}
function qoOnInput(e){
  const t = e.target;
  if (t.classList.contains('qo-note')){ QO.note = t.value; return; }
  if (t.classList.contains('qo-entry')){
    const v = +t.value;
    QO.entry = t.value.trim() && Number.isFinite(v) && v > 0 ? v : null;
    const bar = curBar();
    if (bar){
      QO.stops = QuickCore.stops(qoBars(), QO.side, qoEntry(bar));
      if (QO_STOP_SPEC[QO.stopMethod] && QO.stops[QO.stopMethod] != null) QO.stop = QO.stops[QO.stopMethod];
    }
    qoDrawLines();
    return;
  }
  if (t.classList.contains('qo-stop')){
    const v = +t.value;
    QO.stop = t.value.trim() && Number.isFinite(v) && v > 0 ? v : null;
    QO.stopMethod = 'manual';
    QO.stopPicked = QO.stop != null;
    qoDrawLines();
  } else if (t.classList.contains('qo-margin') || t.classList.contains('qo-lev')){
    const id = t.classList.contains('qo-margin') ? 'jCapital' : 'jLeverage';
    const el = document.getElementById(id);
    el.value = t.value; el.dispatchEvent(new Event('change'));
  }
}
function qoOnChange(e){
  // 输入完成(失焦/回车)后再重算回退并重绘, 避免打字中途误切止损档位
  if (e.target.classList.contains('qo-entry')){ qoResetStop(); qoRender(); return; }
  if (e.target.matches('.qo-stop, .qo-margin, .qo-lev')) qoRender();
}
function qoTopModalOpen(){
  return !!(
    window.isSymPaletteOpen?.() ||
    window.isKbdModalOpen?.() ||
    window.isLogicStudioOpen?.() ||
    window.isTrainerConfigOpen?.()
  );
}
function qoOnKey(e){
  if (!qoAvailable()) return;
  if (qoTopModalOpen()) return;
  if (typeof prDraft !== 'undefined' && prDraft) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = document.activeElement?.tagName;
  const inQo = QO.el && QO.el.contains(document.activeElement);
  const typing = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
  if (typing && !inQo) return;
  if (inQo && typing){
    if (e.key === 'Enter'){ e.preventDefault(); document.activeElement.blur(); qoSubmit('buy'); }
    else if (e.key === 'Escape'){ e.preventDefault(); document.activeElement.blur(); qoClose(); }
    return;
  }
  const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  if (k === 'b' || k === 's'){ e.preventDefault(); qoOpen(k === 'b' ? '多' : '空'); return; }
  if (!QO.open) return;
  // 下单条打开时独占这些按键(避免和标注模式的 1/2/3 等快捷键同时触发)
  const own = ['Enter', 'Escape', 'h', 'w', 'x', 'l', 'p', 'a'].includes(k) || /^[1-9]$/.test(k);
  if (own) e.stopImmediatePropagation();
  if (k === 'h'){ e.preventDefault(); qoToggleMin(); return; }
  if (k === 'Enter'){ e.preventDefault(); qoSubmit('buy'); }
  else if (k === 'Escape'){ qoClose(); }
  else if (k === 'w' && qoPractice()){ e.preventDefault(); if (QO.min && !(QO.note || '').trim()){ QO.min = false; qoRender(); } const n = QO.el.querySelector('.qo-note'); if (n && !n.value.trim()){ n.focus(); qoError('写下等待条件后再按 W 或点「等待」'); n.dataset.pending = 'wait'; } else qoSubmit('wait'); }
  else if (k === 'x' && qoPractice()){ e.preventDefault(); qoSubmit('skip'); }
  else if (k === 'l' || k === 'p' || k === 'a'){
    if (TR.research?.mode === 'research') return;
    e.preventDefault(); qoSelectStopMethod({ l:'bar', p:'swing', a:'atr' }[k]);
  } else if (/^[1-9]$/.test(k)){ e.preventDefault(); qoApplyTemplate(+k - 1); }
}
// 市场环境: 右侧表单开仓时若没选, 自动补上(记录哪些是自动补的)
function qoAutoFillMarket(){
  const filled = [];
  if (typeof QuickCore === 'undefined') return filled;
  const m = QuickCore.market(qoBars());
  for (const g of ['ma120dir', 'pricepos']){
    if (ltSelected('market', g).length || !m[g]) continue;
    const b = document.querySelector(`#jLogicTags .logic-tag[data-group="${g}"][data-val="${m[g]}"]`);
    if (b){ b.classList.add('active'); filled.push(m[g]); }
  }
  if (filled.length && typeof updateLtCounts === 'function') updateLtCounts();
  return filled;
}
function setupQuickOrder(){
  // 保证金/杠杆/预期记住上次值
  try {
    const s = JSON.parse(localStorage.getItem(QO_SIZE_KEY) || '{}');
    if (s.capital > 0) document.getElementById('jCapital').value = s.capital;
    if (s.leverage >= 1) document.getElementById('jLeverage').value = s.leverage;
    if (QO_EXPECTS.includes(s.expect)){ QO.expect = s.expect; const e = document.getElementById('jExpect'); if (e) e.value = s.expect; }
  } catch {}
  const saveSize = () => { try { localStorage.setItem(QO_SIZE_KEY, JSON.stringify({ capital:+document.getElementById('jCapital').value || null, leverage:+document.getElementById('jLeverage').value || null, expect: QO.expect })); } catch {} };
  ['jCapital', 'jLeverage'].forEach(id => document.getElementById(id)?.addEventListener('change', saveSize));

  const box = document.querySelector('.chart-box');
  QO.el = document.createElement('section');
  QO.el.id = 'quickOrder'; QO.el.className = 'qo'; QO.el.hidden = true;
  QO.el.setAttribute('aria-label', '快速下单');
  box.appendChild(QO.el);
  QO.el.addEventListener('click', qoOnClick);
  QO.el.addEventListener('input', qoOnInput);
  QO.el.addEventListener('change', qoOnChange);
  QO.el.addEventListener('keydown', e => {
    if (e.target.classList?.contains('qo-note') && e.key === 'Enter' && e.target.dataset.pending === 'wait'){
      e.preventDefault(); e.stopPropagation(); delete e.target.dataset.pending; e.target.blur(); qoSubmit('wait');
    }
  }, true);
  try { QO.min = localStorage.getItem(QO_MIN_KEY) === '1'; } catch {}
  window.addEventListener('keydown', qoOnKey);
  document.getElementById('trainerQuick')?.addEventListener('click', () => QO.open ? qoClose() : qoOpen());
  document.getElementById('quickOpenLong')?.addEventListener('click', () => qoOpen('多'));
  document.getElementById('quickOpenShort')?.addEventListener('click', () => qoOpen('空'));
  // 旧版开仓表单: 默认隐藏(与快速下单条重复), 需要时可临时展开
  const LEGACY_KEY = 'chanlun.journal.legacyForm.v1';
  const setLegacy = on => {
    document.body.classList.toggle('legacy-form', on);
    const b = document.getElementById('jLegacyToggle');
    if (b) b.textContent = on ? '收起旧版表单' : '旧版开仓表单';
    try { localStorage.setItem(LEGACY_KEY, on ? '1' : '0'); } catch {}
  };
  let legacyOn = false; try { legacyOn = localStorage.getItem(LEGACY_KEY) === '1'; } catch {}
  setLegacy(legacyOn);
  document.getElementById('jLegacyToggle')?.addEventListener('click', () => setLegacy(!document.body.classList.contains('legacy-form')));
  // 图上点价位设止损(优先吸附三条候选虚线; 画线工具未启用时; 兼容 500ms 内快速连点被路由到 dblclick)
  const onChartClick = param => {
    if (!QO.open || !param.point) return;
    if (TR.research?.mode === 'research') return;
    const tool = document.querySelector('#drawToggle button.active')?.dataset.tool;
    if (tool && tool !== 'none') return;
    const hit = qoHitStopLine(param.point.y);
    if (hit){
      qoSelectStopMethod(hit);
      return;
    }
    const price = candleSeries.coordinateToPrice(param.point.y);
    const bar = curBar();
    if (!Number.isFinite(price) || price <= 0 || !bar) return;
    const entry = qoEntry(bar);
    if (QO.side === '空' ? price <= entry : price >= entry){
      qoError(`止损须在开仓价不利方向: 多单点在 ${prNumber(entry, 4)} 下方, 空单点在上方`);
      return;
    }
    QO.stop = +price.toPrecision(8); QO.stopMethod = 'chart'; QO.stopPicked = true; QO.keys++;
    qoRender();
  };
  chart.subscribeClick(onChartClick);
  chart.subscribeDblClick(onChartClick);
  chart.subscribeCrosshairMove(param => {
    const chartEl = document.getElementById('chart');
    if (!chartEl) return;
    if (!QO.open || !param.point || TR.research?.mode === 'research'){
      chartEl.classList.remove('qo-line-hover');
      return;
    }
    const tool = document.querySelector('#drawToggle button.active')?.dataset.tool;
    if (tool && tool !== 'none'){
      chartEl.classList.remove('qo-line-hover');
      return;
    }
    chartEl.classList.toggle('qo-line-hover', qoHitStopLine(param.point.y) != null);
  });
  setInterval(qoSyncBar, 200);
}
function qoSyncBar(){
  const avail = qoAvailable();
  for (const id of ['quickOpenLong', 'quickOpenShort']){
    const b = document.getElementById(id);
    if (b && b.disabled === avail) b.disabled = !avail;
  }
  if (!avail){ if (QO.open) qoClose(); QO.lastBar = null; return; }
  const bar = curBar(); const t = bar?.time ?? null;
  if (t !== QO.lastBar){
    QO.lastBar = t; QO.barSeenAt = Date.now();
    if (QO.open){
      QO.barTime = t; QO.entry = null; qoRecalc(false); qoRender();
    }
  }
}
