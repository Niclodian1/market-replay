// Chart indicator independent of annotation mode. Uses simple-v2 candidates without hard strength filters.
"use strict";
const COVER67_INDICATOR = { on: false, open: false, time: null, market: '', rows: [], popupTime: null, view: null };
try { COVER67_INDICATOR.on = localStorage.getItem('chanlun.cover67.indicator.v1') === 'on'; } catch (_) {}
const cover67GradeLabel = g => ({ high:'高量', normal:'普通量', low:'低量', unknown:'量未知' }[g]);
const cover67GradeColor = g => ({ high:'#2196f3', normal:'#f59e0b', low:'#a78bfa', unknown:'#94a3b8', mixed:'#94a3b8' }[g] || '#94a3b8');
function cover67GradeBadge(g) {
  return `<span class="c67-grade" style="--c67-grade:${cover67GradeColor(g)}">${cover67GradeLabel(g)}</span>`;
}

function cover67VolumeClose() {
  COVER67_INDICATOR.popupTime = null;
  const el = document.getElementById('cover67VolumePopup'); if (el) el.hidden = true;
}

function cover67VolumeOpen(time, point) {
  const r = cover67IndicatorRows().find(row => row.time === time); if (!r) return;
  COVER67_INDICATOR.popupTime = time;
  COVER67_INDICATOR.time = time;
  const popup = document.getElementById('cover67VolumePopup'), box = document.getElementById('chart');
  const cutoff = ANN.on && ANN.hideFuture ? ANN.sigs[ANN.idx]?.i ?? r.i : Infinity;
  const strength = Cover67.stats(annCoverContext().trades, cutoff, r.strengthGrade, 'strengthGrade');
  const st = Cover67.stats(annCoverContext().trades, cutoff, r.grade);
  const pct = v => v == null ? '—' : (v * 100).toFixed(1) + '%';
  const pnl = v => v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(2) + '%';
  const num = v => Number.isFinite(v) ? v.toLocaleString(undefined, {maximumFractionDigits:2}) : '—';
  const volumes = volArrFor('raw'), cs = candlesFor('raw');
  const avg = r.i >= 20 && volumes.slice(r.i - 20, r.i).every(Number.isFinite)
    ? volumes.slice(r.i - 20, r.i).reduce((a,b) => a+b, 0) / 20 : NaN;
  const viewCs = candlesFor(activeView), bar = cover67IndicatorBar(time, viewCs);
  const mapped = viewCs[bar]?.time;
  const group = cover67IndicatorRows().filter(row => {
    const j = cover67IndicatorBar(row.time, viewCs);
    return viewCs[j]?.time === mapped;
  });
  popup.innerHTML = `<div class="c67-panel-head"><b>MA突破 · ${fmtTime(time)}</b><button class="btn" id="cover67VolumeClose" aria-label="关闭量能卡片">×</button></div>
    <p class="note">实体确认（≥0.5 ATR）：${r.bodyConfirmed===null?'未知':r.bodyConfirmed?'有':'无'} · 量能确认（≥1.2）：${r.volumeConfirmed===null?'未知':r.volumeConfirmed?'有':'无'}<br>共 ${r.confirmationCount===null?'未知':r.confirmationCount} 项确认；不决定是否显示候选。</p>
    <div class="c67-volume-grade">${cover67GradeBadge(r.grade)}<strong>量比 ${Number.isFinite(r.volratio) ? r.volratio.toFixed(2) : '—'}</strong></div>
    ${group.length > 1 ? `<div class="c67-volume-group">同一合并K线内的买点：${group.map(row => `<button class="btn" data-volume-time="${row.time}">${fmtTime(row.time)}</button>`).join('')}</div>` : ''}
    <p class="note">${currentSym} ${activeTF} · 信号收盘 ${fmt(cs[r.i].close)} · 止损 ${fmt(cs[r.i].low)}</p>
    <div class="ann-grid"><span>信号本根成交量</span><b>${num(volumes[r.i])}</b><span>前20根平均成交量</span><b>${num(avg)}</b></div>
    <div class="c67-formula">量比 = 本根成交量 ÷ 前20根平均成交量（不含本根）。<br>例如量比 1.20，表示比前20根均量多 20%。按当前周期计算，收盘后确认。</div>
    <div class="c67-volume-legend">${cover67GradeBadge('high')} ≥1.2 ${cover67GradeBadge('normal')} 0.9～&lt;1.2 ${cover67GradeBadge('low')} &lt;0.9</div>
    <details><summary>相同确认项数量的参考表现</summary><p class="note">${ANN.on&&ANN.hideFuture?'仅当前标注之前已结束的交易':'当前品种全样本'} · ${strength.n} 笔 · 胜率 ${pct(strength.winRate)} · 平均净收益 ${pnl(strength.mean)}。确认项多不保证盈利。</p></details>
    <details><summary>同档位历史表现 · ${ANN.on && ANN.hideFuture ? '截至当前标注之前' : '当前品种全样本'}</summary>
      <div class="ann-grid"><span>完成 / 未完成</span><b>${st.n} / ${st.pending}</b><span>净胜率</span><b>${pct(st.winRate)}</b><span>平均净收益</span><b>${pnl(st.mean)}</b><span>盈利单平均</span><b>${pnl(st.avgWin)}</b><span>亏损单平均</span><b>${pnl(st.avgLoss)}</b><span>95%区间</span><b>${st.n ? pct(st.low)+'～'+pct(st.high) : '暂无样本'}</b></div>
      <p class="note">次根开盘入场，信号低点止损，跌破MA20次根开盘退出；单边成本0.10%。${st.n < 30 ? '小样本。' : ''}量能档位不代表胜率排序。</p>
    </details>`;
  popup.hidden = false;
  const rect = box.getBoundingClientRect();
  const x = point?.x ?? chart.timeScale().timeToCoordinate(mapped) ?? rect.width / 2;
  const y = point?.y ?? candleSeries.priceToCoordinate(cs[r.i].low) ?? rect.height / 2;
  popup.style.left = Math.max(8, Math.min(Math.min(innerWidth, rect.right) - popup.offsetWidth - 8, rect.left + x + 16)) + 'px';
  popup.style.top = Math.max(8, Math.min(innerHeight - popup.offsetHeight - 8, rect.top + y + 16)) + 'px';
  popup.querySelectorAll('details').forEach(el=>el.addEventListener('toggle', () => {
    popup.style.top = Math.max(8, Math.min(parseFloat(popup.style.top), innerHeight - popup.offsetHeight - 8)) + 'px';
  }));
  document.getElementById('cover67VolumeClose').addEventListener('click', cover67VolumeClose);
  popup.querySelectorAll('[data-volume-time]').forEach(b => b.addEventListener('click', () => cover67VolumeOpen(Number(b.dataset.volumeTime), point)));
}

function cover67HandleChartClick(param) {
  if(typeof annChartPickBusy==='function'&&annChartPickBusy())return;
  if (!COVER67_INDICATOR.on) return;
  const id = param.hoveredObjectId;
  const row = typeof id === 'string' && id.startsWith('cover67:')
    ? cover67IndicatorRows().find(r => r.time === Number(id.slice(8)))
    : cover67IndicatorRows().find(r => {
      const cs = candlesFor(activeView), i = cover67IndicatorBar(r.time, cs);
      return cs[i]?.time === param.time;
    });
  if (row) {
    cover67VolumeClose(); COVER67_INDICATOR.time = row.time;
    cover67IndicatorRefresh(); cover67VolumeOpen(row.time, param.point);
  } else cover67VolumeClose();
}

function cover67IndicatorRows() {
  const rows = annCoverContext().signals;
  const cutoff = ANN.on && ANN.hideFuture ? ANN.sigs[ANN.idx]?.i : null;
  return cutoff == null ? rows : rows.filter(r => r.i <= cutoff);
}

// Raw signals map to their containing merged candle when that view is selected.
function cover67IndicatorBar(time, cs) {
  let lo = 0, hi = cs.length - 1, found = -1;
  while (lo <= hi) { const mid = (lo + hi) >> 1;
    if (cs[mid].time <= time) { found = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return found;
}

function cover67IndicatorMarkers(view) {
  if (!COVER67_INDICATOR.on) return [];
  const cs = candlesFor(view), grouped = new Map();
  for (const r of cover67IndicatorRows()) {
    const j = cover67IndicatorBar(r.time, cs); if (j < 0) continue;
    const time = cs[j].time, group = grouped.get(time) || [];
    group.push(r); grouped.set(time, group);
  }
  return [...grouped].map(([time, rows]) => {
    const selected = rows.some(r => r.time === COVER67_INDICATOR.time);
    const grade = rows.every(r => r.grade === rows[0].grade) ? rows[0].grade : 'mixed';
    return { time, id: 'cover67:' + rows[0].time, position: 'belowBar', shape: 'arrowUp',
      color: cover67GradeColor(grade), size: selected ? 1.5 : 1,
      text: rows.length > 1 ? `MA突破 ×${rows.length}` : `MA突破·${cover67GradeLabel(rows[0].grade)}` };
  });
}

function cover67IndicatorEnable(on) {
  COVER67_INDICATOR.on = !!on;
  if (!on) { COVER67_INDICATOR.open = false; cover67VolumeClose(); }
  try { localStorage.setItem('chanlun.cover67.indicator.v1', on ? 'on' : 'off'); } catch (_) {}
  cover67IndicatorRefresh();
}

function cover67IndicatorSelect(index) {
  const row = COVER67_INDICATOR.rows[index]; if (!row) return;
  cover67VolumeClose();
  COVER67_INDICATOR.time = row.time;
  cover67IndicatorRefresh();
  const position = () => {
    if (COVER67_INDICATOR.time !== row.time) return;
    const cs = candlesFor(activeView), j = cover67IndicatorBar(row.time, cs);
    if (j >= 0) chart.timeScale().setVisibleLogicalRange({ from: j - 65, to: j + 25 });
    if (COVER67_INDICATOR.popupTime === row.time) cover67VolumeOpen(row.time);
  };
  position(); requestAnimationFrame(position); setTimeout(position, 160);
  document.querySelector('.c67-entry[aria-current="true"]')?.scrollIntoView({ block: 'nearest' });
  cover67VolumeOpen(row.time);
}

function cover67IndicatorRefresh() {
  const state = COVER67_INDICATOR, panel = document.getElementById('cover67IndicatorPanel');
  if (!panel) return;
  const market = `${currentSym}|${activeTF}`;
  if (market !== state.market || state.view !== activeView) {
    state.market = market; state.view = activeView; state.time = null; cover67VolumeClose();
  }
  state.rows = state.on ? cover67IndicatorRows() : [];
  if (!state.rows.some(r => r.time === state.time)) state.time = null;
  if (state.popupTime != null && !state.rows.some(r => r.time === state.popupTime)) cover67VolumeClose();
  const toggle = document.getElementById('cover67IndicatorToggle'), list = document.getElementById('cover67IndicatorList');
  toggle.classList.toggle('active', state.on); toggle.setAttribute('aria-pressed', String(state.on));
  toggle.textContent = state.on ? `MA突破 ${state.rows.length}` : 'MA突破';
  list.classList.toggle('active', state.open); list.setAttribute('aria-expanded', String(state.open));
  panel.hidden = !state.open;
  panel.style.top = Math.ceil(document.querySelector('header').getBoundingClientRect().bottom + 8) + 'px';
  if (state.open) {
    document.getElementById('cover67IndicatorTitle').textContent = `MA突破买点 · ${currentSym} ${activeTF} · ${state.rows.length} 条`;
    const index = state.rows.findIndex(r => r.time === state.time);
    document.getElementById('cover67IndicatorPosition').textContent = index < 0 ? `共 ${state.rows.length} 条，点击条目定位` : `${index + 1} / ${state.rows.length}`;
    document.getElementById('cover67IndicatorPrev').disabled = !state.rows.length || index === 0;
    document.getElementById('cover67IndicatorNext').disabled = !state.rows.length || index === state.rows.length - 1;
    document.getElementById('cover67IndicatorReview').disabled = index < 0;
    const cs = candlesFor('raw');
    document.getElementById('cover67IndicatorEntries').innerHTML = state.rows.length ? state.rows.map((r, k) =>
      `<button class="c67-entry" data-index="${k}" aria-current="${r.time === state.time}">
        <span>${k + 1}. ${fmtTime(r.time)}</span><b>${cs[r.i].close.toLocaleString()}</b>
        <small>${r.confirmationCount===null?'确认未知':r.confirmationCount+'项确认'} · ${cover67GradeBadge(r.grade)} · 量比 ${Number.isFinite(r.volratio) ? r.volratio.toFixed(2) : '—'}${r.complete ? '' : ' · 预热不足'}</small>
      </button>`).join('') : '<p class="note">当前可见行情没有算法买点。</p>';
    document.getElementById('cover67IndicatorBlind').textContent = ANN.on && ANN.hideFuture
      ? '标注遮罩已开启，仅列出当前标注日期及以前的买点。' : '简化v2按当前品种与周期计算，所有阳线上穿均保留，点击条目或用上一条 / 下一条定位。';
  }
  candleSeries.setMarkers(buildMarkers(activeView));
  if (state.popupTime != null) cover67VolumeOpen(state.popupTime);
}

(function () {
  const popup = document.createElement('section'); popup.id = 'cover67VolumePopup';
  popup.className = 'c67-volume-popup'; popup.hidden = true;
  popup.setAttribute('role', 'dialog'); popup.setAttribute('aria-label', '买点量能详情');
  document.body.appendChild(popup);
  const panel = document.createElement('section'); panel.id = 'cover67IndicatorPanel';
  panel.className = 'c67-indicator-panel'; panel.hidden = true;
  panel.setAttribute('aria-label', 'MA突破买点列表');
  panel.innerHTML = `<div class="c67-panel-head"><b id="cover67IndicatorTitle"></b><button class="btn" id="cover67IndicatorClose" aria-label="关闭买点列表">×</button></div>
    <p class="note" id="cover67IndicatorBlind"></p>
    <div class="c67-panel-nav"><button class="btn" id="cover67IndicatorPrev">上一条</button><span id="cover67IndicatorPosition"></span><button class="btn" id="cover67IndicatorNext">下一条</button></div>
    <div class="c67-entries" id="cover67IndicatorEntries"></div>
    <button class="btn" id="cover67IndicatorReview">标注校核此买点</button>`;
  document.body.appendChild(panel);
  document.getElementById('cover67IndicatorToggle').addEventListener('click', () => cover67IndicatorEnable(!COVER67_INDICATOR.on));
  document.getElementById('cover67IndicatorList').addEventListener('click', () => {
    if (!COVER67_INDICATOR.on) cover67IndicatorEnable(true);
    COVER67_INDICATOR.open = !COVER67_INDICATOR.open; cover67IndicatorRefresh();
  });
  document.getElementById('cover67IndicatorClose').addEventListener('click', () => { COVER67_INDICATOR.open = false; cover67IndicatorRefresh(); });
  document.getElementById('cover67IndicatorEntries').addEventListener('click', e => {
    const row = e.target.closest('[data-index]'); if (row) cover67IndicatorSelect(Number(row.dataset.index));
  });
  for (const [id, step] of [['cover67IndicatorPrev', -1], ['cover67IndicatorNext', 1]]) {
    document.getElementById(id).addEventListener('click', () => {
      const i = COVER67_INDICATOR.rows.findIndex(r => r.time === COVER67_INDICATOR.time);
      cover67IndicatorSelect(i < 0 ? (step > 0 ? 0 : COVER67_INDICATOR.rows.length - 1) : i + step);
    });
  }
  document.getElementById('cover67IndicatorReview').addEventListener('click', () => {
    cover67VolumeClose();
    const time = COVER67_INDICATOR.time; COVER67_INDICATOR.open = false;
    ANN.source = 'ma20_simple_v2'; annSetOpen(true);
    const cs = candlesFor('raw'), i = ANN.sigs.findIndex(s => cs[s.i]?.time === time);
    if (i >= 0) annGo(i);
    cover67IndicatorRefresh();
  });
  chart.subscribeClick(cover67HandleChartClick);
  document.addEventListener('pointerdown', e => {
    if (!e.target.closest('#cover67VolumePopup') && !e.target.closest('#cover67VolumeOnChart')) cover67VolumeClose();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') cover67VolumeClose(); });
  window.addEventListener('resize', () => { cover67VolumeClose(); if (COVER67_INDICATOR.open) cover67IndicatorRefresh(); });
  cover67IndicatorRefresh();
})();
