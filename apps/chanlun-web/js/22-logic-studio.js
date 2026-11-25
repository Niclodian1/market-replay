// js/22-logic-studio.js — 主屏每品种「S 级背景红/绿悬浮框」+ 极致收敛 2-Tab「逻辑工坊（Logic Studio）」
//   · 主屏悬浮框 (#sBgFloat): 按品种独立存储看多(绿框)/看空(红框)/观望(灰框)背景与操作纪律, 支持拖拽/收起/原地修改, 盲测自动隐藏
//   · 逻辑工坊 (快捷键 G): 收敛为 2 个核心视图「1. 30秒开仓卡」「2. 复盘本(合大盘与档案)」+ 按需展开的「📖 速查清单」
(function () {
  'use strict';

  const KEY_CARDS = 'chanlun.logic.cards.v1';
  const KEY_PATTERNS = 'chanlun.logic.patterns.v1';
  const KEY_TABOOS = 'chanlun.logic.taboos.v1';
  const KEY_PREFS = 'chanlun.logic.prefs.v1';
  const KEY_SBG = 'chanlun.logic.sbg.v1';
  const KEY_SBG_UI = 'chanlun.logic.sbg.ui.v1';

  const BIAS_META = {
    bull: {
      id: 'bull', label: '看多', short: '<i class="sbg-dot dot-bull" aria-hidden="true"></i>看多', shortText: '看多', tone: 'up',
      defaultPlan: '顺大势绿框：回调关键均线或收敛突破按 1.5% 风险做多，不轻易摸顶做空。',
    },
    bear: {
      id: 'bear', label: '看空', short: '<i class="sbg-dot dot-bear" aria-hidden="true"></i>看空', shortText: '看空', tone: 'down',
      defaultPlan: '红框压制：禁用左侧抄底；反抽仅限 0.5% 风险+技术验证，或等反抽承压顺势做空。',
    },
    neutral: {
      id: 'neutral', label: '观望', short: '<i class="sbg-dot dot-neutral" aria-hidden="true"></i>观望', shortText: '观望', tone: 'muted',
      defaultPlan: '分歧/无主线期：以空仓等待为主，仅在右侧放量突破清晰结构时轻仓参与。',
    },
  };

  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const uid = p => `${p}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
  const blindNow = () => typeof trLocked === 'function' && trLocked();
  const curSymKey = () => (typeof currentSym !== 'undefined' && currentSym) ? currentSym : 'BTC';
  const curSymDisplay = () => (typeof DATA !== 'undefined' && DATA?.meta?.symbol) ? DATA.meta.symbol : curSymKey();

  function loadJson(key, fallback) {
    try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) : fallback; }
    catch { return fallback; }
  }
  function saveJson(key, val) {
    try { localStorage.setItem(key, JSON.stringify(val)); return true; }
    catch { window.showToast?.('⚠ 本地存储空间不足，请导出 JSON 备份', 'warn', 3000); return false; }
  }

  function defaultPrefs() {
    return { strictGate: false, defaultRiskPct: 1.5, seeded: false };
  }

  const LS = {
    open: false,
    tab: 'gate',         // gate | review
    refOpen: false,      // 按需展开的「📖 速查清单」抽屉
    cards: [],
    patterns: [],
    taboos: [],
    prefs: defaultPrefs(),
    sbgMap: {},          // { [sym]: { bias:'bull'|'bear'|'neutral', headline, plan, updatedAt } }
    sbgUi: { visible: true, collapsed: false, x: null, y: null },
    sbgEditing: false,
    sbgEditDraft: null,
    sbgEl: null,
    draft: null,
    priced: {
      upstreamBlunted: false, midstreamDiverged: false, downstreamResilient: false,
      realDeleveraging: false, secondBadNewsTest: false, lowVolRetest: false, rightSideBreakout: false,
    },
    archiveFilter: { status: 'all', quadrant: 'all', q: '' },
    selectedCardId: null,
    dashWindow: 20,
    quickNodeCardId: null,
    quickNodeReact: '顺行',
    inlineSaveKind: null, // 'pattern' | 'taboo' | null
    rootEl: null,
  };

  function hydrate() {
    LS.prefs = { ...defaultPrefs(), ...loadJson(KEY_PREFS, {}) };
    LS.cards = loadJson(KEY_CARDS, []);
    LS.sbgMap = loadJson(KEY_SBG, {});
    LS.sbgUi = { visible: true, collapsed: false, x: null, y: null, ...loadJson(KEY_SBG_UI, {}) };
    const storedPat = loadJson(KEY_PATTERNS, null);
    const storedTab = loadJson(KEY_TABOOS, null);
    if (!LS.prefs.seeded && storedPat === null && storedTab === null) {
      LS.patterns = LogicCore.DEFAULT_PATTERNS.map(x => ({ ...x }));
      LS.taboos = LogicCore.DEFAULT_TABOOS.map(x => ({ ...x }));
      LS.prefs.seeded = true;
      saveJson(KEY_PATTERNS, LS.patterns);
      saveJson(KEY_TABOOS, LS.taboos);
      saveJson(KEY_PREFS, LS.prefs);
    } else {
      LS.patterns = Array.isArray(storedPat) ? storedPat : [];
      LS.taboos = Array.isArray(storedTab) ? storedTab : [];
    }
  }

  const persistCards = () => saveJson(KEY_CARDS, LS.cards);
  const persistPatterns = () => saveJson(KEY_PATTERNS, LS.patterns);
  const persistTaboos = () => saveJson(KEY_TABOOS, LS.taboos);
  const persistPrefs = () => saveJson(KEY_PREFS, LS.prefs);
  const persistSBg = () => saveJson(KEY_SBG, LS.sbgMap);
  const persistSBgUi = () => saveJson(KEY_SBG_UI, LS.sbgUi);

  // =========================================================================
  // A. 主屏幕「每品种独立 S 级背景红/绿悬浮框」(#sBgFloat)
  // =========================================================================
  function getSBg(sym = curSymKey()) {
    const rec = LS.sbgMap[sym];
    if (rec && BIAS_META[rec.bias]) return { sym, ...rec };
    return { sym, bias: 'neutral', headline: '', plan: '', updatedAt: '' };
  }
  window.getCurrentSBg = () => getSBg(curSymKey());

  function isDefaultPlan(txt) {
    if (!txt || !txt.trim()) return true;
    return Object.values(BIAS_META).some(m => m.defaultPlan === txt.trim());
  }

  function applySBgPos() {
    if (!LS.sbgEl) return;
    if (Number.isFinite(LS.sbgUi.x) && Number.isFinite(LS.sbgUi.y)) {
      const box = document.querySelector('.chart-box');
      const maxX = Math.max(8, (box?.clientWidth || 1000) - LS.sbgEl.offsetWidth - 8);
      const maxY = Math.max(8, (box?.clientHeight || 600) - 48);
      const x = Math.max(8, Math.min(maxX, LS.sbgUi.x));
      const y = Math.max(8, Math.min(maxY, LS.sbgUi.y));
      LS.sbgEl.style.left = x + 'px';
      LS.sbgEl.style.top = y + 'px';
      LS.sbgEl.style.right = 'auto';
    } else {
      LS.sbgEl.style.left = '';
      LS.sbgEl.style.top = '';
      LS.sbgEl.style.right = '';
    }
  }

  function renderSBgHud() {
    if (!LS.sbgEl) return;
    const sym = curSymKey();
    const symDisp = curSymDisplay();
    const rec = getSBg(sym);
    const meta = BIAS_META[rec.bias] || BIAS_META.neutral;

    LS.sbgEl.hidden = !LS.sbgUi.visible;
    LS.sbgEl.className = `sbg-float sbg-${rec.bias}${LS.sbgUi.collapsed && !LS.sbgEditing ? ' is-collapsed' : ''}${LS.sbgEditing ? ' is-editing' : ''}`;

    if (LS.sbgEditing) {
      const ed = LS.sbgEditDraft || { ...rec };
      LS.sbgEl.className = `sbg-float sbg-${ed.bias} is-editing`;
      LS.sbgEl.innerHTML = `
        <div class="sbg-head" data-sbg-drag title="拖动调整位置 · 双击复位">
          <span class="sbg-tag">S级背景 · <b>${esc(symDisp)}</b></span>
          <button type="button" class="sbg-icon-btn" data-sbg-act="cancel" title="取消 (Esc)">✕</button>
        </div>
        <div class="sbg-edit-body">
          <div class="sbg-bias-pick">
            ${['bull', 'bear', 'neutral'].map(b => `
              <button type="button" class="sbg-bias-btn bias-${b}${ed.bias === b ? ' on' : ''}" data-sbg-bias="${b}">${BIAS_META[b].short}</button>`).join('')}
          </div>
          <textarea class="sbg-inp" id="sbgHeadlineInp" rows="2" maxlength="200" placeholder="写下影响 ${esc(sym)} 的 S 级大背景（例：宏观利率路径、行业监管杀PE、ETF资金流、重大黑天鹅）…">${esc(ed.headline)}</textarea>
          <input type="text" class="sbg-inp" id="sbgPlanInp" maxlength="160" value="${esc(ed.plan)}" placeholder="一句话操作纪律（切换多空自动填充默认，可改）…">
          <div class="sbg-edit-foot">
            <button type="button" class="btn ts-mini" data-sbg-act="clear" title="清空当前品种背景">清空</button>
            <span class="sbg-edit-right">
              <button type="button" class="btn ts-mini" data-sbg-act="cancel">取消</button>
              <button type="button" class="btn btn-primary ts-mini" data-sbg-act="save">保存背景</button>
            </span>
          </div>
        </div>`;
      applySBgPos();
      return;
    }

    if (LS.sbgUi.collapsed) {
      LS.sbgEl.innerHTML = `
        <div class="sbg-pill-row" data-sbg-drag title="拖动移动 · 双击复位">
          <span class="sbg-bias-badge bias-${rec.bias}">${meta.short}</span>
          <span class="sbg-pill-sym">${esc(sym)}</span>
          <span class="sbg-pill-txt">${esc(rec.headline || '未定调')}</span>
          <button type="button" class="sbg-icon-btn" data-sbg-act="edit" title="编辑 S 级背景">✎</button>
          <button type="button" class="sbg-icon-btn" data-sbg-act="toggle-collapse" title="展开详情">▾</button>
        </div>`;
      applySBgPos();
      return;
    }

    // 未定调且无正文时自动以轻巧单行胶囊呈现，不遮挡 K 线，也不与侧栏重复占位
    if (rec.bias === 'neutral' && !rec.headline) {
      LS.sbgEl.className = 'sbg-float sbg-neutral is-unset';
      LS.sbgEl.innerHTML = `
        <div class="sbg-pill-row sbg-unset-row" data-sbg-drag title="拖动调整位置 · 双击复位">
          <button type="button" class="sbg-unset-btn" data-sbg-act="edit" title="10 秒写下 ${esc(sym)} 的 S 级多空定调与纪律">
            <i class="sbg-dot dot-neutral" aria-hidden="true"></i>
            <span>定调 <b>${esc(sym)}</b> S 级背景</span>
          </button>
        </div>`;
      applySBgPos();
      return;
    }

    const planShow = rec.plan || (rec.headline ? meta.defaultPlan : '');
    LS.sbgEl.innerHTML = `
      <div class="sbg-head" data-sbg-drag title="拖动调整位置 · 双击复位">
        <span class="sbg-tag">S级背景 · <b>${esc(sym)}</b></span>
        <button type="button" class="sbg-bias-badge bias-${rec.bias}" data-sbg-act="cycle-bias" title="点击快速切换 看多 / 看空 / 观望">${meta.short}</button>
        <span class="sbg-head-acts">
          <button type="button" class="sbg-icon-btn" data-sbg-act="edit" title="修改 S 级背景与纪律">编辑</button>
          <button type="button" class="sbg-icon-btn" data-sbg-act="toggle-collapse" title="收起成单行胶囊">▴</button>
        </span>
      </div>
      <div class="sbg-body" data-sbg-act="edit" title="点击修改">
        ${rec.headline
          ? `<div class="sbg-headline">${esc(rec.headline)}</div>
             ${planShow ? `<div class="sbg-plan">${esc(planShow)}</div>` : ''}`
          : `<div class="sbg-empty">已定调 <b>${meta.label}</b> · 点击补充核心驱动与操作纪律</div>`}
      </div>
      ${rec.updatedAt ? `<div class="sbg-foot"><span>${esc(symDisp)} 独立背景</span><span>更新 ${esc(rec.updatedAt)}</span></div>` : ''}`;
    applySBgPos();
  }

  function cycleSBgBias(sym = curSymKey()) {
    const cur = getSBg(sym);
    const order = ['bull', 'bear', 'neutral'];
    const nextB = order[(order.indexOf(cur.bias) + 1) % order.length];
    LS.sbgMap[sym] = {
      bias: nextB,
      headline: cur.headline,
      plan: isDefaultPlan(cur.plan) ? BIAS_META[nextB].defaultPlan : cur.plan,
      updatedAt: new Date().toISOString().slice(5, 10),
    };
    persistSBg();
    renderSBgHud();
    renderLogicSidebar();
    if (typeof qoIsOpen === 'function' && qoIsOpen() && typeof qoRender === 'function') qoRender();
    window.showToast?.(`${sym} S 级定调已切为「${BIAS_META[nextB].shortText}」`, nextB === 'bull' ? 'ok' : nextB === 'bear' ? 'err' : 'info', 1600);
  }

  function startEditSBg() {
    const rec = getSBg(curSymKey());
    LS.sbgUi.visible = true;
    LS.sbgUi.collapsed = false;
    LS.sbgEditing = true;
    LS.sbgEditDraft = { ...rec };
    persistSBgUi();
    renderSBgHud();
    requestAnimationFrame(() => LS.sbgEl?.querySelector('#sbgHeadlineInp')?.focus());
  }
  window.startEditSBg = startEditSBg;

  function syncSBgHud() {
    LS.sbgEditing = false;
    LS.sbgEditDraft = null;
    if (LS.draft && !blindNow() && LS.draft.symbol !== curSymKey()) LS.draft = null;
    renderSBgHud();
    if (LS.open && LS.tab === 'gate') renderStudioBody();
  }
  window.syncSBgHud = syncSBgHud;

  function setupSBgHud() {
    const box = document.querySelector('.chart-box');
    if (!box || LS.sbgEl) return;
    const el = document.createElement('aside');
    el.id = 'sBgFloat';
    el.className = 'sbg-float sbg-neutral';
    el.setAttribute('aria-label', 'S级大背景悬浮框');
    box.appendChild(el);
    LS.sbgEl = el;

    // 拖拽支持（仅在 [data-sbg-drag] 上按下时触发）
    let drag = null;
    el.addEventListener('pointerdown', e => {
      if (e.button !== 0 || e.target.closest('button, input, textarea')) return;
      const handle = e.target.closest('[data-sbg-drag]');
      if (!handle) return;
      e.preventDefault();
      const boxRect = box.getBoundingClientRect();
      const elRect = el.getBoundingClientRect();
      drag = {
        id: e.pointerId,
        offX: e.clientX - elRect.left,
        offY: e.clientY - elRect.top,
        boxRect,
      };
      el.setPointerCapture(e.pointerId);
      el.classList.add('dragging');
    });
    el.addEventListener('pointermove', e => {
      if (!drag || e.pointerId !== drag.id) return;
      const x = e.clientX - drag.boxRect.left - drag.offX;
      const y = e.clientY - drag.boxRect.top - drag.offY;
      LS.sbgUi.x = Math.round(x);
      LS.sbgUi.y = Math.round(y);
      applySBgPos();
    });
    const endDrag = () => {
      if (!drag) return;
      drag = null;
      el.classList.remove('dragging');
      persistSBgUi();
    };
    el.addEventListener('pointerup', endDrag);
    el.addEventListener('pointercancel', endDrag);
    el.addEventListener('dblclick', e => {
      if (!e.target.closest('[data-sbg-drag]') || e.target.closest('button')) return;
      const prevRect = el.getBoundingClientRect();
      LS.sbgUi.x = null;
      LS.sbgUi.y = null;
      persistSBgUi();
      applySBgPos();
      window.uiMotion?.flipFrom?.(el, prevRect, 260);
      window.showToast?.('S 级悬浮框位置已复位', 'info', 1400);
    });

    el.addEventListener('click', e => {
      const biasBtn = e.target.closest('[data-sbg-bias]');
      if (biasBtn && LS.sbgEditing && LS.sbgEditDraft) {
        const nextB = biasBtn.dataset.sbgBias;
        const hInp = el.querySelector('#sbgHeadlineInp');
        const pInp = el.querySelector('#sbgPlanInp');
        if (hInp) LS.sbgEditDraft.headline = hInp.value;
        if (pInp) LS.sbgEditDraft.plan = pInp.value;
        if (isDefaultPlan(LS.sbgEditDraft.plan)) LS.sbgEditDraft.plan = BIAS_META[nextB].defaultPlan;
        LS.sbgEditDraft.bias = nextB;
        renderSBgHud();
        return;
      }
      const act = e.target.closest('[data-sbg-act]')?.dataset.sbgAct;
      if (!act) return;
      e.stopPropagation();
      const sym = curSymKey();
      if (act === 'edit') {
        startEditSBg();
      } else if (act === 'cancel') {
        LS.sbgEditing = false;
        LS.sbgEditDraft = null;
        renderSBgHud();
      } else if (act === 'toggle-collapse') {
        const prevRect = el.getBoundingClientRect();
        LS.sbgUi.collapsed = !LS.sbgUi.collapsed;
        persistSBgUi();
        renderSBgHud();
        window.uiMotion?.flipFrom?.(el, prevRect, 220);
      } else if (act === 'cycle-bias') {
        cycleSBgBias(sym);
      } else if (act === 'clear') {
        const prevRec = LS.sbgMap[sym] ? { ...LS.sbgMap[sym] } : null;
        delete LS.sbgMap[sym];
        LS.sbgEditing = false;
        LS.sbgEditDraft = null;
        persistSBg();
        renderSBgHud();
        renderLogicSidebar();
        if (prevRec) {
          window.showToast?.(`已清空 ${sym} 的 S 级背景`, 'info', {
            ms: 5000,
            action: {
              label: '撤销',
              onClick: () => {
                LS.sbgMap[sym] = prevRec;
                persistSBg(); renderSBgHud(); renderLogicSidebar();
                window.showToast?.(`✓ 已恢复 ${sym} 的 S 级背景`, 'ok', 1600);
              },
            },
          });
        } else {
          window.showToast?.(`已清空 ${sym} 的 S 级背景`, 'info', 1600);
        }
      } else if (act === 'save') {
        const h = (el.querySelector('#sbgHeadlineInp')?.value || '').trim();
        const p = (el.querySelector('#sbgPlanInp')?.value || '').trim();
        const b = LS.sbgEditDraft?.bias || 'neutral';
        LS.sbgMap[sym] = {
          bias: b,
          headline: h,
          plan: p || (h ? BIAS_META[b].defaultPlan : ''),
          updatedAt: new Date().toISOString().slice(5, 10),
        };
        LS.sbgEditing = false;
        LS.sbgEditDraft = null;
        persistSBg();
        renderSBgHud();
        renderLogicSidebar();
        if (typeof qoIsOpen === 'function' && qoIsOpen() && typeof qoRender === 'function') qoRender();
        if (LS.draft && LS.draft.symbol === sym) { LS.draft.sbgBias = b; LS.draft.sbgHeadline = h; }
        if (LS.open && LS.tab === 'gate') renderStudioBody();
        window.showToast?.(`✓ 已保存 ${sym} 的 S 级背景（${BIAS_META[b].label}）`, 'ok', 1800);
      }
    });

    el.addEventListener('keydown', e => {
      if (!LS.sbgEditing) return;
      if (e.key === 'Escape') {
        e.preventDefault(); e.stopPropagation();
        LS.sbgEditing = false; LS.sbgEditDraft = null; renderSBgHud();
      } else if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        el.querySelector('[data-sbg-act="save"]')?.click();
      }
    });

    renderSBgHud();
  }

  // =========================================================================
  // B. 图表上下文与 30 秒开仓卡草稿
  // =========================================================================
  function captureChartContext(side = '多') {
    const bar = typeof curBar === 'function' ? curBar() : null;
    const locked = blindNow();
    const sym = locked ? 'BLIND' : curSymKey();
    const symLabel = locked ? '盲测标的' : curSymDisplay();
    const tf = locked ? '盲测' : (typeof activeTF !== 'undefined' ? activeTF : '1D');
    const bars = typeof prBars === 'function' && bar ? prBars(-Infinity, bar.time) : [];
    const mkt = typeof QuickCore !== 'undefined' && bars.length ? QuickCore.market(bars) : {};
    const stops = typeof QuickCore !== 'undefined' && bars.length ? QuickCore.stops(bars, side, bar?.close) : {};
    const sigs = typeof QuickCore !== 'undefined' && bars.length ? QuickCore.signals(bars, side) : [];
    const fgi = !locked && bar && typeof window.fngInfoForTs === 'function' && ['BTC', 'ETH', 'SOL', 'PEPE', 'HYPE'].includes(sym)
      ? window.fngInfoForTs(bar.time) : null;
    const maSnap = [mkt.ma120dir, mkt.pricepos].filter(Boolean).join(' · ') || '均线数据不足';
    const eq = typeof acctEquity === 'function' ? Math.round(acctEquity()) : 100000;
    let ctxText = '';
    if (bar && typeof window.MarketContext?.snapshotFor === 'function') {
      const rawCs = typeof candlesFor === 'function' ? candlesFor('raw') : [];
      const snap = window.MarketContext.snapshotFor(sym, bar.time, rawCs, rawCs.length - 1);
      const parts = [];
      if (!locked && snap.macro?.us10y != null) parts.push(`10Y ${snap.macro.us10y.toFixed(2)}%`);
      if (!locked && snap.macro?.dxy != null) parts.push(`DXY ${snap.macro.dxy.toFixed(1)}`);
      if (!locked && snap.macro?.vix != null) parts.push(`VIX ${snap.macro.vix.toFixed(1)}`);
      if (!locked && snap.rs) parts.push(`RS/${snap.rs.benchLabel} ${snap.rs.excess20 >= 0 ? '+' : ''}${snap.rs.excess20}%`);
      if (snap.cycle) parts.push(`52W ${snap.cycle.dd52w}%`);
      ctxText = parts.join(' · ');
    }
    return {
      sym, symLabel, tf, bar, bars, stops, sigs, maSnap, fgi, ctxText, equity: eq,
      barLabel: bar ? (typeof fmtTime === 'function' ? fmtTime(bar.time) : String(bar.time)) : '—',
    };
  }

  function sbgToThreeLayer(bias) {
    if (bias === 'bull') return { macro: 'tailwind', crossMarket: 'tailwind', endogenous: 'tailwind' };
    if (bias === 'bear') return { macro: 'headwind', crossMarket: 'headwind', endogenous: 'headwind' };
    return { macro: 'neutral', crossMarket: 'neutral', endogenous: 'neutral' };
  }

  function draftCtxKey() {
    const blind = blindNow();
    const runKey = typeof TR !== 'undefined' && TR.active
      ? (TR.sessionId || `${TR.sym || ''}|${TR.tf || ''}|${TR.startTs || ''}|${TR.revealed ? 1 : 0}`)
      : 'free';
    const bar = curBar();
    return [
      blind ? 'blind' : 'live',
      runKey,
      curSymKey(),
      typeof activeTF !== 'undefined' ? activeTF : '1D',
      bar?.time ?? '',
    ].join('|');
  }

  function isDraftStale(d) {
    if (!d) return true;
    if (!!d._blind !== blindNow()) return true;
    return !d._ctxKey || d._ctxKey !== draftCtxKey();
  }

  function newDraft(prefill = {}) {
    const blind = blindNow();
    const sbg = blind ? { bias: 'neutral', headline: '', plan: '' } : getSBg(curSymKey());
    const side = prefill.side || (sbg.bias === 'bear' ? '空' : '多');
    const ctx = captureChartContext(side);
    const entryPrice = prefill.entryPrice ?? (ctx.bar ? +ctx.bar.close : null);
    const stopPrice = prefill.stopPrice ?? ctx.stops.bar ?? ctx.stops.swing ?? ctx.stops.atr ?? null;
    const counter = (sbg.bias === 'bear' && side === '多') || (sbg.bias === 'bull' && side === '空');
    const audit = LogicCore.computeAuditMetrics(LS.cards, { windowSize: 20 });
    const baseRisk = (audit.complianceStatus === 'penalty' || counter) ? 0.5 : (LS.prefs.defaultRiskPct || 1.5);

    return {
      _ctxKey: draftCtxKey(),
      _blind: blind,
      _barTime: ctx.bar?.time ?? null,
      symbol: ctx.sym,
      symbolLabel: ctx.symLabel,
      tf: ctx.tf,
      barTime: ctx.bar?.time ?? null,
      barLabel: ctx.barLabel,
      barPrice: ctx.bar?.close ?? null,
      maSnap: ctx.maSnap,
      ctxText: ctx.ctxText,
      fngValue: ctx.fgi?.value ?? null,
      fngLabel: ctx.fgi?.label ?? '',
      stops: ctx.stops,
      sbgBias: sbg.bias,
      sbgHeadline: sbg.headline,
      side,
      horizon: LogicCore.HORIZONS.includes(prefill.horizon) ? prefill.horizon : '中线',
      logicType: prefill.logicType || 'resonance',
      eventTier: prefill.eventTier ?? (sbg.headline ? 'S' : 'A'),
      threeLayer: prefill.threeLayer ? { ...prefill.threeLayer } : sbgToThreeLayer(sbg.bias),
      blackSwan: { hasSystemicCrisis: false, crisisDaysElapsed: 0, reclaimedStructure: false, usedNoDropAsBullish: false },
      impulseFlag: false,
      whyNow: prefill.whyNow || '',
      opponentTags: Array.isArray(prefill.opponentTags) ? [...prefill.opponentTags] : [],
      techEntry: prefill.techEntry || (ctx.sigs.length ? ctx.sigs.join('、') : ''),
      equity: ctx.equity,
      riskPct: prefill.riskPct ?? baseRisk,
      entryPrice,
      stopPrice,
      targetPrice: prefill.targetPrice ?? null,
      invalidRule: prefill.invalidRule || (stopPrice != null ? `跌破/触及 ${stopPrice} 或 5 根未脱离成本` : '5 根 K 线未走强主动离场'),
      exitAnchor: prefill.exitAnchor || '',
    };
  }

  function ensureDraft(prefill) {
    if (prefill || isDraftStale(LS.draft)) LS.draft = newDraft(prefill || {});
    return LS.draft;
  }

  function onBlindModeChange(on) {
    LS.draft = null;
    LS.sbgEditing = false;
    LS.quickNodeCardId = null;
    LS.inlineSaveKind = null;
    if (on && LS.open) {
      LS.draft = newDraft({});
      renderStudioBody(false);
    } else if (LS.open) {
      renderStudioBody(false);
    }
    renderLogicSidebar();
    renderSBgHud();
  }
  window.onBlindModeChange = onBlindModeChange;

  function updateLogicBadges() {
    const pending = LS.cards.filter(c => c.status === 'closed' || (c.status === 'locked' && !c.postmortem?.quadrant)).length;
    for (const id of ['logicStudioBadge', 'sideTabLogicBadge']) {
      const el = document.getElementById(id);
      if (!el) continue;
      el.textContent = pending ? String(pending) : '';
      el.classList.toggle('on', pending > 0);
    }
  }

  // ---- 锁定时间胶囊（可选一键开仓）----
  function lockDraftCard(alsoOpenOrder = false) {
    const d = LS.draft;
    if (!d) return;
    if (isDraftStale(d)) {
      LS.draft = newDraft({});
      renderStudioBody(false);
      window.showToast?.('⚠ 行情/模式已变化，已自动刷新为当前点位，请核对后再锁卡', 'warn', 3200);
      return;
    }
    const sbg = blindNow() ? { bias: 'neutral', headline: '' } : getSBg(curSymKey());
    const counter = (sbg.bias === 'bear' && d.side === '多') || (sbg.bias === 'bull' && d.side === '空');
    const sizing = LogicCore.calcRiskSizing({
      equity: d.equity, riskPct: d.riskPct, entry: d.entryPrice,
      stop: d.stopPrice, target: d.targetPrice, side: d.side, maxLeverage: 10,
    });
    const swan = LogicCore.evalBlackSwanGate({ ...d.blackSwan, side: d.side });

    if (d.impulseFlag) {
      window.showToast?.('✗ 已触碰冲动一票否决（试仓/猜底/报复单），禁止开仓！', 'err', 3000);
      return;
    }
    if (!d.whyNow.trim() && !d.techEntry.trim()) {
      window.showToast?.('✗ 禁止「双无入场」：至少写一句逻辑或结构依据', 'err', 2800);
      return;
    }
    if (!sizing.valid) {
      window.showToast?.('✗ ' + sizing.error, 'err', 2800);
      return;
    }
    if (LS.prefs.strictGate && swan.veto) {
      window.showToast?.('🛑 严格门禁拦截：S 级黑天鹅冷却期内禁止做多！', 'err', 3200);
      return;
    }

    const now = Date.now();
    const mode = blindNow() ? 'trainer' : (typeof inReplay === 'function' && inReplay() ? 'replay' : 'live');
    const card = {
      id: uid('lc'),
      createdAt: now,
      lockedAt: now,
      orderFilledAt: null,
      status: 'locked',
      symbol: d.symbol,
      symbolLabel: d.symbolLabel,
      tf: d.tf,
      mode,
      linkedPosId: null,
      compliance: {
        isPreLocked: true,
        violated: !!(swan.veto || sizing.flags.riskOverLimit || (counter && sizing.riskPct > 0.75)),
        vetoTriggered: d.impulseFlag ? ['impulse'] : [],
      },
      capsule: {
        barTime: d.barTime,
        barLabel: d.barLabel,
        barPrice: d.barPrice,
        maSnap: d.maSnap,
        fngValue: d.fngValue,
        fngLabel: d.fngLabel,
        side: d.side,
        horizon: d.horizon,
        logicType: d.logicType,
        eventTier: d.eventTier,
        threeLayer: sbgToThreeLayer(sbg.bias),
        blackSwan: { ...d.blackSwan },
        infoMacro: [sbg.headline ? `[S级${BIAS_META[sbg.bias].label}] ${sbg.headline}` : '', d.ctxText || ''].filter(Boolean).join(' ｜ '),
        whyNow: d.whyNow.trim(),
        opponentTags: d.opponentTags.slice(),
        opponentNote: counter ? `逆S级${BIAS_META[sbg.bias].label}战术单` : '',
        techBig: d.maSnap,
        techEntry: d.techEntry.trim(),
        equity: +d.equity,
        riskPct: sizing.riskPct,
        entryPrice: sizing.entry,
        stopPrice: sizing.stop,
        targetPrice: sizing.target,
        suggestedMargin: sizing.suggestedMargin,
        suggestedLev: sizing.suggestedLev,
        plannedRR: sizing.rr,
        invalidPrice: d.invalidRule.trim(),
        invalidTime: '',
        exitAnchor: d.exitAnchor.trim(),
      },
      evolution: {
        nodes: [],
        checks: {},
        falsified: false,
        falsifyType: '',
        exitTime: null,
        exitPrice: null,
        exitReason: '',
        followedPlan: true,
        metrics: {},
      },
      postmortem: {
        completedAt: null,
        fullMoveRange: '',
        isNoise: false,
        realMainline: '',
        alignment: '',
        blindSpot: '',
        logicScore: null,
        chartScore: null,
        quadrant: '',
      },
      iteration: { keepAction: '', fixRule: '', patternId: null },
    };

    // 先入库再开仓，确保 renderJournal() 渲染持仓卡时立刻匹配到已锁准入卡徽章
    LS.cards.unshift(card);
    persistCards();

    if (alsoOpenOrder) {
      if (!(typeof inReplay === 'function' && inReplay())) {
        window.showToast?.('请先进入「复盘」或「练习」模式再直接开仓（卡片已为你锁定存盘）', 'warn', 3000);
      } else {
        const capInp = document.getElementById('jCapital');
        const levInp = document.getElementById('jLeverage');
        const bal = typeof acctBal === 'function' ? acctBal() : 100000;
        const marginToUse = Math.min(bal, Math.max(1, Math.round(sizing.suggestedMargin)));
        if (capInp) capInp.value = String(marginToUse);
        if (levInp) levInp.value = String(sizing.suggestedLev);

        if (typeof ltSelected === 'function' && !ltSelected('entry').length) {
          const autoSigs = typeof QuickCore !== 'undefined' && typeof prBars === 'function' && typeof curBar === 'function' && curBar()
            ? QuickCore.signals(prBars(-Infinity, curBar().time), d.side) : [];
          const pickVal = autoSigs[0] || '均线突破';
          document.querySelector(`#jLogicTags .logic-tag[data-cat="entry"][data-val="${pickVal}"]`)?.classList.add('active');
          if (typeof updateLtCounts === 'function') updateLtCounts();
        }
        const logicText = `[开仓卡] ${card.capsule.whyNow || card.capsule.techEntry}`;
        const opened = typeof openJournalPosition === 'function'
          ? openJournalPosition({
              side: d.side,
              expect: d.horizon,
              entry: blindNow() ? undefined : sizing.entry,
              stop: sizing.stop,
              logic: logicText,
              logicCardId: card.id,
              onError: msg => window.showToast?.('⚠ ' + msg, 'warn', 3000),
            }) : false;
        if (opened) {
          card.orderFilledAt = Date.now();
          const lastPos = Array.isArray(positions) ? positions[positions.length - 1] : null;
          if (lastPos) card.linkedPosId = lastPos.id;
          persistCards();
        }
      }
    }

    LS.selectedCardId = card.id;
    LS.draft = null;
    LS.tab = 'review';
    updateLogicBadges();
    renderLogicSidebar();
    if (alsoOpenOrder && card.linkedPosId != null) {
      closeLogicStudio();
      window.showToast?.(`✓ 时间胶囊已锁并开仓 #${card.linkedPosId}（风险 ${sizing.riskPct}% · ${sizing.suggestedLev}x）`, 'ok', 2600);
    } else {
      renderStudio();
      window.showToast?.('🔒 时间胶囊已锁定（原文只读封存）', 'ok', 2200);
    }
  }

  function recalcCardMetrics(card, customExitPrice, customExitTs) {
    if (!card) return null;
    const cap = card.capsule || {};
    const ev = card.evolution || (card.evolution = {});
    const exitPrice = customExitPrice ?? ev.exitPrice ?? (typeof curBar === 'function' ? curBar()?.close : null);
    const exitTs = customExitTs ?? ev.exitTime ?? (typeof curBar === 'function' ? curBar()?.time : null);
    if (!(exitPrice > 0)) return null;
    let bars = [];
    const sameSym = typeof currentSym !== 'undefined' && (card.symbol === currentSym || card.symbolLabel === DATA?.meta?.symbol || card.symbol === 'BLIND');
    if (sameSym && typeof candlesFor === 'function') {
      bars = candlesFor('raw').map(c => [c.time, c.open, c.high, c.low, c.close]);
    }
    const m = LogicCore.calcTradeExcursion(bars, {
      side: cap.side || '多', entryTs: cap.barTime, exitTs: exitTs ?? Infinity,
      entryPrice: cap.entryPrice, stopPrice: cap.stopPrice, exitPrice,
    });
    if (m) ev.metrics = m;
    return m;
  }

  // ---- 交易平仓回调 & 严格门禁钩子 ----
  window.onLogicTradeClosed = function (rec, pos) {
    if (!pos?.logicCardId) return;
    const card = LS.cards.find(c => c.id === pos.logicCardId);
    if (!card) return;
    card.evolution.exitPrice = rec.exit_price;
    card.evolution.exitTime = rec.exit_ts;
    if (!card.evolution.exitReason) {
      if (rec.exit_reason?.includes('止损')) card.evolution.exitReason = 'hard_stop';
      else if (rec.pnl_u > 0) card.evolution.exitReason = 'target_realized';
      else card.evolution.exitReason = 'falsified_early';
    }
    recalcCardMetrics(card, rec.exit_price, rec.exit_ts);
    if (card.status === 'locked') card.status = 'closed';
    persistCards(); updateLogicBadges(); renderLogicSidebar();
    const r = card.evolution.metrics?.realizedR;
    window.showToast?.(`🧭 复盘卡已更新（${r != null ? (r >= 0 ? '+' : '') + r + 'R' : '待归因'}），记得补写 2×2 定性`, 'info', 2800);
  };

  window.logicCardForPos = pos => (pos?.logicCardId ? LS.cards.find(c => c.id === pos.logicCardId) || null : null);
  window.logicStrictGateCheck = () => (LS.prefs.strictGate
    ? { ok: false, message: '已开启「🛡️ 严格门禁」：请先填 30 秒开仓卡锁定时间胶囊再一键开仓！' }
    : { ok: true });

  // =========================================================================
  // C. 右侧极简「逻辑」侧栏渲染
  // =========================================================================
  function renderLogicSidebar() {
    const host = document.getElementById('logicSideHost');
    if (!host) return;
    updateLogicBadges();
    const isBlind = blindNow();
    const sym = curSymKey();
    const sbg = getSBg(sym);
    const meta = BIAS_META[sbg.bias] || BIAS_META.neutral;
    const audit = LogicCore.computeAuditMetrics(LS.cards, { windowSize: 20 });
    const compTxt = audit.complianceRate != null ? `${audit.complianceRate}%` : '—';
    const compCls = audit.complianceStatus === 'ok' ? 'up' : audit.complianceStatus === 'penalty' ? 'down' : audit.complianceStatus === 'warn' ? 'warn' : 'muted';
    const activeCards = LS.cards.filter(c => c.status === 'locked' || c.status === 'closed').slice(0, 6);

    const sbgSideHtml = isBlind
      ? `<div class="ls-side-sbg sbg-neutral ls-blind-sbg">
          <div class="ls-side-row">
            <span class="ls-side-label">S 级背景 · 盲测隔离中</span>
            <span class="ls-badge warn">已隐藏</span>
          </div>
          <div class="ls-side-sbg-txt">为防泄露盲测品种与历史背景，揭示答案后自动恢复显示。</div>
        </div>`
      : `<div class="ls-side-sbg sbg-${sbg.bias}">
          <div class="ls-side-row">
            <span class="ls-side-label">S 级背景 · <b>${esc(sym)}</b></span>
            <button type="button" class="sbg-bias-badge bias-${sbg.bias}" data-ls-sbg="cycle-bias" title="点击切换 看多 / 看空 / 观望">${meta.short}</button>
          </div>
          <div class="ls-side-sbg-txt">${esc(sbg.headline || '未填写当前品种 S 级背景，点击编辑快速定调。')}</div>
          <div class="ls-side-sbg-foot">
            <button type="button" class="btn ts-mini" data-ls-sbg="edit">✎ 修改背景</button>
            <label class="ls-inline-chk"><input type="checkbox" id="lsSbgVisChk" ${LS.sbgUi.visible ? 'checked' : ''}><span>主屏悬浮</span></label>
          </div>
        </div>`;

    host.innerHTML = `
      ${sbgSideHtml}

      <div class="ls-side-block ls-audit-strip">
        <div class="ls-audit-top">
          <div>
            <span class="ls-side-label">近 20 笔合规</span>
            <b class="tnum ls-comp-val tone-${compCls}">${compTxt}</b>
            ${audit.poisonCount ? `<span class="ls-pill warn">☠️ ${audit.poisonCount}</span>` : ''}
          </div>
          <label class="ls-strict-toggle" title="开启后必须先锁开仓卡才允许下单">
            <input type="checkbox" id="lsStrictChk" ${LS.prefs.strictGate ? 'checked' : ''}>
            <span>🛡️ 严格门禁</span>
          </label>
        </div>
        ${audit.complianceStatus === 'penalty' ? `<div class="ls-alert-mini down">⚠ 合规率 &lt;70%，建议单笔风险减半至 0.5%！</div>` : ''}
      </div>

      <div class="ls-side-cta">
        <button type="button" class="btn btn-primary ls-cta-main" data-ls-open="gate">＋ 30 秒开仓卡 <kbd>G</kbd></button>
        <div class="ls-cta-sub">
          <button type="button" class="btn btn-outline" data-ls-open="review">📒 复盘本 (${LS.cards.length})</button>
          <button type="button" class="btn btn-outline" data-ls-open="ref">📖 速查清单</button>
        </div>
      </div>

      <div class="ls-side-section">
        <div class="sub-head">
          <span>进行中 &amp; 待复盘</span>
          <span class="sub-head-hint">${activeCards.length} 张</span>
        </div>
        ${activeCards.length ? activeCards.map(c => {
          const cap = c.capsule || {};
          const needPm = c.status === 'closed';
          const r = c.evolution?.metrics?.realizedR;
          const qnOpen = LS.quickNodeCardId === c.id;
          return `<div class="ls-mini-card${needPm ? ' need-pm' : ''}" data-ls-card="${esc(c.id)}">
            <div class="ls-mc-top">
              <span><b>${esc(c.symbolLabel || c.symbol)}</b> <span class="pos-side-tag ${cap.side === '空' ? 'short' : 'long'}">${esc(cap.side || '多')}</span></span>
              <span class="ls-badge ${needPm ? 'warn' : 'accent'}">${needPm ? '待复盘' : '已锁'}</span>
            </div>
            <div class="ls-mc-why">${esc(cap.whyNow || cap.techEntry || '—')}</div>
            <div class="ls-mc-foot tnum">
              <span>${esc(cap.barLabel || '')}${r != null ? ` · <b class="${r >= 0 ? 'tone-up' : 'tone-down'}">${r >= 0 ? '+' : ''}${r}R</b>` : ''}</span>
              <span class="ls-mc-acts">
                ${c.status === 'locked' ? `<button type="button" class="btn ts-mini${qnOpen ? ' active' : ''}" data-ls-quicknode="${esc(c.id)}">＋节点</button>` : ''}
                <button type="button" class="btn ts-mini" data-ls-view="${esc(c.id)}">${needPm ? '去归因' : '查看'}</button>
              </span>
            </div>
            ${qnOpen ? `<div class="ls-qn-form" data-qn-card="${esc(c.id)}">
              <input type="text" class="ls-qn-inp" placeholder="记一条盘中催化或关键 K 线反应（回车保存）…" maxlength="140">
              <div class="ls-qn-bar">
                <div class="ls-qn-reacts">
                  ${['顺行', '滞涨', '反向破位'].map(rx => `<button type="button" class="ls-qn-rbtn${LS.quickNodeReact === rx ? ' on' : ''}" data-qn-react="${rx}">${rx}</button>`).join('')}
                </div>
                <span class="ls-mc-acts">
                  <button type="button" class="btn ts-mini" data-qn-cancel>取消</button>
                  <button type="button" class="btn btn-primary ts-mini" data-qn-save="${esc(c.id)}">保存</button>
                </span>
              </div>
            </div>` : ''}
          </div>`;
        }).join('') : '<div class="ts-empty">暂无进行中卡片。先在主屏定好 S 级红/绿背景，有赔率位再按 <kbd>G</kbd> 锁卡。</div>'}
      </div>`;
  }
  window.renderLogicSidebar = renderLogicSidebar;

  function renderGateSizingSummary(sizing) {
    return sizing.valid ? `
      <div class="ls-sizing-box tnum">
        <div><span>止损距 / 最大可亏</span><b>${sizing.stopDistPct}% · ${sizing.maxLossU}U</b></div>
        <div><span>建议保证金 / 杠杆</span><b>${sizing.suggestedMargin.toLocaleString()}U · ${sizing.suggestedLev}x</b></div>
      </div>
      ${sizing.flags.riskOverLimit ? '<div class="ls-banner tone-down">⚠ 风险超过 2% 红线，请主动降仓！</div>' : ''}
      ${sizing.flags.leverageCapped ? '<div class="ls-banner tone-warn">⚠ 止损极窄触及 10x 上限，建议改用「波段」或「1.5ATR」止损</div>' : ''}
      ${sizing.flags.rrReject ? '<div class="ls-banner tone-down">⚠ 盈亏比 &lt; 2:1，赔率不划算，建议放弃！</div>' : ''}
    ` : `<div class="ls-banner tone-warn">${esc(sizing.error)}</div>`;
  }
  function updateGateSizingLive() {
    if (!LS.rootEl || !LS.draft || LS.tab !== 'gate') return;
    const d = LS.draft;
    const sizing = LogicCore.calcRiskSizing({
      equity: d.equity, riskPct: d.riskPct, entry: d.entryPrice,
      stop: d.stopPrice, target: d.targetPrice, side: d.side, maxLeverage: 10,
    });
    const liveBox = LS.rootEl.querySelector('#lsSizingLive');
    if (liveBox) liveBox.innerHTML = renderGateSizingSummary(sizing);
    const pill = LS.rootEl.querySelector('#lsSizingPill');
    if (pill) {
      pill.className = `ls-pill ${sizing.valid ? (sizing.flags.riskOverLimit || sizing.flags.rrReject ? 'down' : sizing.flags.rrGood ? 'up' : 'accent') : 'muted'}`;
      pill.textContent = sizing.valid ? (sizing.rr != null ? `R:R ${sizing.rr}:1` : `风险 ${sizing.riskPct}%`) : '待填点位';
    }
  }
  function renderGateTab() {
    const d = ensureDraft();
    const sbg = blindNow() ? { bias: 'neutral', headline: '盲测练习中已隐藏背景', plan: '' } : getSBg(curSymKey());
    const meta = BIAS_META[sbg.bias] || BIAS_META.neutral;
    const counter = !blindNow() && ((sbg.bias === 'bear' && d.side === '多') || (sbg.bias === 'bull' && d.side === '空'));
    const swan = LogicCore.evalBlackSwanGate({ ...d.blackSwan, side: d.side });
    const sizing = LogicCore.calcRiskSizing({
      equity: d.equity, riskPct: d.riskPct, entry: d.entryPrice,
      stop: d.stopPrice, target: d.targetPrice, side: d.side, maxLeverage: 10,
    });
    const inRep = typeof inReplay === 'function' && inReplay();

    return `
      <div class="ls-sbg-bar sbg-${sbg.bias}">
        <div class="ls-sbg-bar-main">
          <span class="sbg-bias-badge bias-${sbg.bias}">${meta.short}</span>
          <b>${esc(d.symbolLabel)} S 级背景：</b>
          <span>${esc(sbg.headline || '未设置（默认中性）')}</span>
        </div>
        ${!blindNow() ? `<button type="button" class="btn ts-mini" data-g-act="edit-sbg">✎ 修改背景</button>` : ''}
      </div>

      ${counter ? `
        <div class="ls-banner tone-warn strong ls-counter-bar">
          <span>⚠ 你正在<b>逆 S 级${sbg.bias === 'bear' ? '红框(看空)' : '绿框(看多)'}</b>做${d.side}：须确认已完成最小技术验证（日线反包或 1H/4H 破颈线），且风险减半至 0.5%、只吃反抽不格局！</span>
          ${Math.abs((d.riskPct || 0) - 0.5) > 0.01 ? `<button type="button" class="btn ts-mini" data-g-risk="0.5">一键切 0.5%</button>` : ''}
        </div>` : ''}

      <div class="ls-two-col">
        <div class="ls-col-main">
          <div class="ls-card">
            <div class="ls-card-head">
              <b>核心逻辑（锁定后时间胶囊永久只读）</b>
              <div class="ls-head-right">
                <span class="tnum ls-snap-inline">${esc(d.barLabel)} @ ${d.barPrice ?? '—'} · ${esc(d.maSnap)}${d.ctxText ? ` · ${esc(d.ctxText)}` : ''}${d.fngValue != null ? ` · FGI ${d.fngValue}` : ''}</span>
                <button type="button" class="btn ts-mini" data-g-act="refresh-ctx">↻ 同步</button>
              </div>
            </div>

            <div class="ls-form-grid3">
              <label class="field">方向
                <div class="ls-seg">
                  <button type="button" class="ls-seg-btn${d.side === '多' ? ' active val-tailwind' : ''}" data-g-side="多">做多</button>
                  <button type="button" class="ls-seg-btn${d.side === '空' ? ' active val-headwind' : ''}" data-g-side="空">做空</button>
                </div>
              </label>
              <label class="field">预期周期
                <div class="ls-seg">
                  ${LogicCore.HORIZONS.map(h => `<button type="button" class="ls-seg-btn${d.horizon === h ? ' active' : ''}" data-g-horizon="${esc(h)}">${esc(h)}</button>`).join('')}
                </div>
              </label>
              <label class="field">逻辑类型
                <select data-g-field="logicType">${LogicCore.LOGIC_TYPES.map(lt => `<option value="${lt.id}" ${d.logicType === lt.id ? 'selected' : ''}>${esc(lt.short)}（${lt.id === 'resonance' ? '事件+结构' : lt.id === 'event' ? '事件驱动' : '纯结构'}）</option>`).join('')}</select>
              </label>
            </div>

            <label class="field">一句话驱动（Why Now · 为什么现在资金会重估或走出波段？）
              <input type="text" data-g-field="whyNow" maxlength="200" value="${esc(d.whyNow)}" placeholder="例：放量突破收敛箱体 + 宏观利率回落共振 / 日线恐慌长下影技术反抽…">
            </label>

            <div class="field">
              <span>对手盘是谁（点选 1 个即可）</span>
              <div class="ls-chips-wrap">
                ${LogicCore.OPPONENT_PRESETS.slice(0, 6).map(op => `<button type="button" class="ls-chip${d.opponentTags.includes(op) ? ' on' : ''}" data-g-opp="${esc(op)}">${esc(op)}</button>`).join('')}
              </div>
            </div>

            <label class="field">入场技术结构（必须有客观技术位支撑，不盲接下落刀）
              <input type="text" data-g-field="techEntry" value="${esc(d.techEntry)}" placeholder="例：均线突破 / 大阳线后的顺势短线多 / 大阴线后的顺势短线空 / 震荡区间的反转">
            </label>

            <div class="ls-form-grid2">
              <label class="field">证伪红线（价格 + 时间）
                <input type="text" data-g-field="invalidRule" value="${esc(d.invalidRule)}" placeholder="跌破前低 / 5 根不走强即认错">
              </label>
              <label class="field">退出锚点（什么信号止盈？）
                <input type="text" data-g-field="exitAnchor" value="${esc(d.exitAnchor)}" placeholder="例：到前破位颈线/MA20 压制 / 放量滞涨">
              </label>
            </div>
          </div>
        </div>

        <div class="ls-col-side">
          <div class="ls-card">
            <div class="ls-card-head">
              <b>以损定仓 &amp; 盈亏比</b>
              <span id="lsSizingPill" class="ls-pill ${sizing.valid ? (sizing.flags.riskOverLimit || sizing.flags.rrReject ? 'down' : sizing.flags.rrGood ? 'up' : 'accent') : 'muted'}">
                ${sizing.valid ? (sizing.rr != null ? `R:R ${sizing.rr}:1` : `风险 ${sizing.riskPct}%`) : '待填点位'}
              </span>
            </div>
            <div class="ls-form-grid2">
              <label class="field">账户净值 U<input type="number" step="any" data-g-num="equity" value="${d.equity ?? ''}"></label>
              <label class="field">风险 %（顺势 1.5% / 逆势 0.5%）<input type="number" step="0.1" min="0.1" max="10" data-g-num="riskPct" value="${d.riskPct ?? 1.5}"></label>
            </div>
            <div class="ls-risk-presets">
              ${[0.5, 1, 1.5, 2].map(v => `<button type="button" class="btn ts-mini${Math.abs((d.riskPct || 0) - v) < 0.01 ? ' active' : ''}" data-g-risk="${v}">${v}%${v === 0.5 ? ' 逆势/惩罚' : ''}</button>`).join('')}
            </div>
            <div class="ls-form-grid3">
              <label class="field">入场价<input type="number" step="any" data-g-num="entryPrice" value="${d.entryPrice ?? ''}"></label>
              <label class="field">硬止损<input type="number" step="any" data-g-num="stopPrice" value="${d.stopPrice ?? ''}"></label>
              <label class="field">目标价<input type="number" step="any" data-g-num="targetPrice" value="${d.targetPrice ?? ''}" placeholder="≥2.5R"></label>
            </div>
            <div class="ls-stop-picks">
              <span>取止损：</span>
              ${[['bar', '本根'], ['swing', '波段'], ['atr', '1.5ATR']].map(([k, lbl]) =>
                `<button type="button" class="btn ts-mini" data-g-stop="${k}" ${d.stops?.[k] == null ? 'disabled' : ''}>${lbl}${d.stops?.[k] != null ? ` ${Number(d.stops[k]).toPrecision(5)}` : ''}</button>`).join('')}
            </div>
            <div id="lsSizingLive">${renderGateSizingSummary(sizing)}</div>
          </div>

          <div class="ls-card">
            <label class="ls-chk-row">
              <input type="checkbox" data-g-swan="hasSystemicCrisis" ${d.blackSwan.hasSystemicCrisis ? 'checked' : ''}>
              <span><b>S 级黑天鹅冷却期</b><small>头部暴雷/巨额被盗/脱锚未满 3～5 天 → 常规技术买点一票否决</small></span>
            </label>
            ${d.blackSwan.hasSystemicCrisis && swan.veto ? `<div class="ls-banner tone-down strong">${esc(swan.message)}</div>` : ''}
            <label class="ls-chk-row${d.impulseFlag ? ' veto-hit' : ''}">
              <input type="checkbox" data-g-impulse ${d.impulseFlag ? 'checked' : ''}>
              <span><b>冲动一票否决自检</b><small>我正想「轻仓占坑防踏空 / 跌多了猜底 / 亏了想一把捞回来」</small></span>
            </label>
          </div>

          <div class="ls-lock-actions">
            <button type="button" class="btn btn-primary ls-lock-btn" data-g-act="lock-only" ${d.impulseFlag ? 'disabled' : ''}>🔒 锁定时间胶囊</button>
            <button type="button" class="btn ls-open-btn" data-g-act="lock-and-open" ${(!inRep || d.impulseFlag) ? 'disabled' : ''}>🚀 锁定并一键开仓${inRep ? '' : '（需复盘/练习）'}</button>
          </div>
        </div>
      </div>`;
  }

  function getFilteredReviewCards() {
    const f = LS.archiveFilter;
    return LS.cards.filter(c => {
      if (f.status === 'pending' && !(c.status === 'closed' || (c.status === 'locked' && !c.postmortem?.quadrant))) return false;
      if (f.status === 'reviewed' && c.status !== 'reviewed') return false;
      if (f.quadrant !== 'all' && c.postmortem?.quadrant !== f.quadrant) return false;
      if (f.q) {
        const hay = `${c.symbol} ${c.symbolLabel} ${c.capsule?.whyNow} ${c.postmortem?.realMainline}`.toLowerCase();
        if (!hay.includes(f.q.toLowerCase())) return false;
      }
      return true;
    });
  }

  function renderReviewItemsHtml(filtered) {
    return filtered.length ? filtered.map(c => {
      const qm = LogicCore.QUADRANT_META[c.postmortem?.quadrant];
      const r = c.evolution?.metrics?.realizedR;
      return `<button type="button" class="ls-arc-item${c.id === LS.selectedCardId ? ' active' : ''}" data-a-sel="${esc(c.id)}">
        <div class="ls-arc-top">
          <b>${esc(c.symbolLabel || c.symbol)} <span class="pos-side-tag ${c.capsule?.side === '空' ? 'short' : 'long'}">${esc(c.capsule?.side || '多')}</span></b>
          <span class="tnum ${r == null ? '' : (r >= 0 ? 'tone-up' : 'tone-down')}">${r != null ? `${r >= 0 ? '+' : ''}${r}R` : (c.status === 'closed' ? '待复盘' : '锁定')}</span>
        </div>
        <div class="ls-arc-why">${esc(c.capsule?.whyNow || c.capsule?.techEntry || '—')}</div>
        <div class="ls-arc-meta"><span>${esc(c.capsule?.barLabel || '')}</span>${qm ? `<i class="ls-pill ${qm.tone}">${esc(qm.short)}</i>` : ''}</div>
      </button>`;
    }).join('') : '<div class="ts-empty">暂无匹配复盘卡</div>';
  }

  function patchReviewSearchResults() {
    if (!LS.rootEl || LS.tab !== 'review') return;
    const filtered = getFilteredReviewCards();
    if (!LS.selectedCardId || !filtered.some(c => c.id === LS.selectedCardId)) {
      LS.selectedCardId = filtered[0]?.id || null;
    }
    const card = filtered.find(c => c.id === LS.selectedCardId) || null;
    const listBox = LS.rootEl.querySelector('.ls-card-items');
    const detailBox = LS.rootEl.querySelector('.ls-archive-detail');
    if (listBox) listBox.innerHTML = renderReviewItemsHtml(filtered);
    if (detailBox) detailBox.innerHTML = card ? renderCardDetail(card) : '<div class="ts-empty" style="margin-top:32px;">选择左侧复盘卡查看，或切到「1. 30秒开仓卡」锁定第一笔记录。</div>';
  }

  function renderReviewTab() {
    const a = LogicCore.computeAuditMetrics(LS.cards, { windowSize: LS.dashWindow });
    const compCls = a.complianceStatus === 'ok' ? 'up' : a.complianceStatus === 'penalty' ? 'down' : a.complianceStatus === 'warn' ? 'warn' : 'muted';
    const f = LS.archiveFilter;
    const filtered = getFilteredReviewCards();
    if (!LS.selectedCardId || !filtered.some(c => c.id === LS.selectedCardId)) {
      LS.selectedCardId = filtered[0]?.id || null;
    }
    const card = filtered.find(c => c.id === LS.selectedCardId) || null;

    return `
      <div class="ls-stack">
        <!-- 紧凑顶条：合规率 + 2×2 四象限一键筛选 + 备份 -->
        <div class="ls-review-top">
          <div class="ls-dash-bar">
            <div class="ls-rev-kpis tnum">
              <div class="ls-seg">
                <button type="button" class="ls-seg-btn${LS.dashWindow === 20 ? ' active' : ''}" data-dash-win="20">近 20 笔</button>
                <button type="button" class="ls-seg-btn${LS.dashWindow === 0 ? ' active' : ''}" data-dash-win="0">全部</button>
              </div>
              <span class="ls-pill ${compCls}">合规率 ${a.complianceRate != null ? a.complianceRate + '%' : '—'} (${a.compliantCount}/${a.total})</span>
              <span class="ls-pill ${a.poisonCount ? 'warn' : 'muted'}">☠️ 毒药单 ${a.poisonCount} · 剥离后净 ${a.totalRWithoutPoison >= 0 ? '+' : ''}${a.totalRWithoutPoison}R</span>
              <span class="ls-side-hint">逻辑分 ${a.avgLogicScore ?? '—'} · K线分 ${a.avgChartScore ?? '—'}</span>
            </div>
            <div class="ls-dash-prefs">
              <button type="button" class="btn ts-mini" data-dash-act="export-json">导出 JSON</button>
              <label class="btn ts-mini pr-import">导入<input type="file" accept=".json,application/json" id="lsImportJson"></label>
            </div>
          </div>

          <div class="ls-quad-strip">
            ${['rr', 'rl', 'wl', 'wr'].map(id => {
              const qm = LogicCore.QUADRANT_META[id];
              const q = a.quadrants[id];
              const on = f.quadrant === id;
              return `<button type="button" class="ls-qstrip-cell tone-${qm.tone}${on ? ' active' : ''}" data-a-quad="${id}">
                <span><b>${esc(qm.short)}</b></span>
                <span class="tnum">${q.count}笔 (${q.pct}%) · ${q.totalR >= 0 ? '+' : ''}${q.totalR}R</span>
              </button>`;
            }).join('')}
          </div>
        </div>

        <div class="ls-archive-layout">
          <div class="ls-archive-list">
            <div class="ls-filter-bar">
              <input type="text" class="ls-search" data-a-filter="q" value="${esc(f.q)}" placeholder="搜索品种 / 逻辑…">
              <div class="ls-filter-row">
                <select data-a-filter="status">
                  <option value="all" ${f.status === 'all' ? 'selected' : ''}>全部状态</option>
                  <option value="pending" ${f.status === 'pending' ? 'selected' : ''}>待复盘/进行中</option>
                  <option value="reviewed" ${f.status === 'reviewed' ? 'selected' : ''}>已归档</option>
                </select>
                <button type="button" class="btn ts-mini" data-a-reset>重置筛选</button>
              </div>
            </div>
            <div class="ls-card-items">
              ${renderReviewItemsHtml(filtered)}
            </div>
          </div>

          <div class="ls-archive-detail">
            ${card ? renderCardDetail(card) : '<div class="ts-empty" style="margin-top:32px;">选择左侧复盘卡查看，或切到「1. 30秒开仓卡」锁定第一笔记录。</div>'}
          </div>
        </div>
      </div>`;
  }

  function renderCardDetail(card) {
    const cap = card.capsule || {};
    const ev = card.evolution || {};
    const pm = card.postmortem || {};
    const it = card.iteration || {};
    const m = ev.metrics || {};
    const comp = LogicCore.isCardCompliant(card);
    const lockedStr = card.lockedAt ? new Date(card.lockedAt).toLocaleString('zh-CN', { hour12: false }) : '—';

    return `
      <div class="ls-detail-head">
        <div>
          <h3>${esc(card.symbolLabel || card.symbol)} · 做${esc(cap.side || '多')} · ${esc(cap.horizon || '')}</h3>
          <div class="ls-detail-sub">
            <span>🔒 ${esc(lockedStr)}</span>
            <span class="ls-pill ${comp ? 'up' : 'down'}">${comp ? '✅ 合规' : '⚠ 违纪/超限'}</span>
          </div>
        </div>
        <div class="ls-detail-acts">
          <button type="button" class="btn ts-mini" data-d-act="locate">📍 定位图表</button>
          <button type="button" class="btn ts-mini" data-d-act="copy-md">📋 复制 MD</button>
          <button type="button" class="btn ts-mini" data-d-act="dl-md">📥 .md</button>
          <button type="button" class="btn ts-mini btn-danger" data-d-act="delete">🗑</button>
        </div>
      </div>

      <div class="ls-card ls-capsule-locked">
        <div class="ls-card-head"><b>🔒 模块一：时间胶囊（只读原稿）</b><span class="ls-side-hint">不可篡改</span></div>
        <div class="ls-ro-grid">
          <div><span>当时背景 &amp; 结构</span><b>${esc(cap.infoMacro || '—')} ｜ ${esc(cap.barLabel || '—')} @ ${esc(cap.barPrice ?? '—')}</b></div>
          <div><span>风控边界</span><b class="tnum">入 ${esc(cap.entryPrice ?? '—')} · 损 ${esc(cap.stopPrice ?? '—')} · 标 ${esc(cap.targetPrice ?? '—')} · 风险 ${esc(cap.riskPct ?? '—')}% (${cap.plannedRR != null ? esc(cap.plannedRR) + ':1' : '—'})</b></div>
          <div><span>一句话驱动</span><b>${esc(cap.whyNow || '—')}</b></div>
          <div><span>入场结构 / 红线 / 退出</span><b>${esc(cap.techEntry || '—')} ｜ 红线：${esc(cap.invalidPrice || '—')} ｜ 止盈：${esc(cap.exitAnchor || '—')}</b></div>
        </div>
      </div>

      <div class="ls-card">
        <div class="ls-card-head">
          <b>二、演进节点 &amp; 自动 MAE/MFE</b>
          <button type="button" class="btn ts-mini" data-d-act="recalc-mfe">⚡ 重算 MAE/MFE</button>
        </div>
        ${(ev.nodes || []).length ? `<div class="ls-nodes">${ev.nodes.map((n, idx) => `
          <div class="ls-node-row">
            <span class="tnum ls-node-time">${esc(n.barLabel || '')}${n.price != null ? ` @ ${esc(n.price)}` : ''}</span>
            <span class="ls-node-ev">${esc(n.event)}</span>
            <span class="ls-pill ${n.reaction === '顺行' ? 'up' : n.reaction === '反向破位' ? 'down' : 'warn'}">${esc(n.reaction)}</span>
            <button type="button" class="ls-node-del" data-d-delnode="${idx}">×</button>
          </div>`).join('')}</div>` : ''}
        <div class="ls-add-node">
          <input type="text" id="lsNodeEvent" placeholder="追加盘中节点（关键催化 / 盘面反应）…">
          <select id="lsNodeReaction"><option value="顺行">顺行</option><option value="滞涨">滞涨</option><option value="反向破位">反向破位</option></select>
          <button type="button" class="btn btn-outline" data-d-act="add-node">＋节点</button>
        </div>
        <div class="ls-form-grid3">
          <label class="field">离场价<input type="number" step="any" data-d-evnum="exitPrice" value="${ev.exitPrice ?? ''}"></label>
          <label class="field">离场原因
            <select data-d-ev="exitReason">
              <option value="">待平仓…</option>
              ${LogicCore.EXIT_REASONS.map(r => `<option value="${r.id}" ${ev.exitReason === r.id ? 'selected' : ''}>${esc(r.label)}</option>`).join('')}
            </select>
          </label>
          <label class="field">是否按计划
            <select data-d-ev="followedStr">
              <option value="yes" ${ev.followedPlan !== false ? 'selected' : ''}>严格按计划</option>
              <option value="no" ${ev.followedPlan === false ? 'selected' : ''}>违纪/扛单拖延</option>
            </select>
          </label>
        </div>
        <div class="ls-metrics-strip tnum">
          <div><span>实际盈亏</span><b class="${(m.realizedR ?? 0) >= 0 ? 'tone-up' : 'tone-down'}">${m.realizedR != null ? `${m.realizedR >= 0 ? '+' : ''}${m.realizedR}R` : '—'} (${m.realizedPct != null ? `${m.realizedPct >= 0 ? '+' : ''}${m.realizedPct}%` : '—'})</b></div>
          <div><span>MAE 最大浮亏</span><b class="tone-down">${m.maePct != null ? `${m.maePct}%` : '—'}${m.maeR != null ? ` (${m.maeR}R)` : ''}</b></div>
          <div><span>MFE 最大浮盈</span><b class="tone-up">${m.mfePct != null ? `+${m.mfePct}%` : '—'}${m.mfeR != null ? ` (+${m.mfeR}R)` : ''}</b></div>
          <div><span>利润留存</span><b>${m.retention != null ? `${m.retention}%` : '—'}${m.barsHeld ? ` · ${m.barsHeld}根` : ''}</b></div>
        </div>
      </div>

      <div class="ls-card">
        <div class="ls-card-head">
          <b>三、终局 2×2 定性 &amp; 1 条迭代硬规则</b>
          <label class="ls-inline-chk"><input type="checkbox" data-d-pmbool="isNoise" ${pm.isNoise ? 'checked' : ''}><span>噪音，不归因</span></label>
        </div>
        <div class="ls-form-grid2">
          <label class="field">真实主导主线<input type="text" data-d-pm="realMainline" ${pm.isNoise ? 'disabled' : ''} value="${esc(pm.realMainline || '')}" placeholder="事后看真正驱动行情的主线…"></label>
          <label class="field">下次必须修正的 1 条硬规则<input type="text" data-d-it="fixRule" value="${esc(it.fixRule || '')}" placeholder="具体到过滤条件、仓位或止损规则…"></label>
        </div>
        <div class="ls-scores-row">
          <div><span>逻辑分</span>${[1, 2, 3, 4, 5].map(n => `<button type="button" class="ls-score-btn${pm.logicScore === n ? ' on' : ''}" data-d-score="logicScore" data-val="${n}" title="再点可取消">${n}</button>`).join('')}</div>
          <div><span>K线执行分</span>${[1, 2, 3, 4, 5].map(n => `<button type="button" class="ls-score-btn${pm.chartScore === n ? ' on' : ''}" data-d-score="chartScore" data-val="${n}" title="再点可取消">${n}</button>`).join('')}</div>
          <span class="ls-head-right" style="margin-left:auto;">
            <button type="button" class="btn ts-mini${LS.inlineSaveKind === 'pattern' ? ' active' : ''}" data-d-act="save-pattern">＋存模式</button>
            <button type="button" class="btn ts-mini${LS.inlineSaveKind === 'taboo' ? ' active' : ''}" data-d-act="save-taboo">＋记禁忌</button>
          </span>
        </div>
        ${LS.inlineSaveKind ? `<div class="ls-inline-save">
          <input type="text" id="lsInlineSaveInp" maxlength="60" value="${esc(LS.inlineSaveKind === 'pattern'
            ? (pm.realMainline || cap.whyNow?.slice(0, 20) || '新模式')
            : (pm.quadrant === 'wr' ? '☠️ 毒药单警示' : '避坑硬规则'))}" placeholder="${LS.inlineSaveKind === 'pattern' ? '输入模式名称（回车保存）…' : '输入禁忌标题（回车保存）…'}">
          <button type="button" class="btn btn-primary ts-mini" data-d-act="inline-save-ok">保存</button>
          <button type="button" class="btn ts-mini" data-d-act="inline-save-cancel">取消</button>
        </div>` : ''}
        <div class="ls-quad-pick">
          ${Object.values(LogicCore.QUADRANT_META).map(q => `
            <button type="button" class="ls-quad-btn tone-${q.tone}${pm.quadrant === q.id ? ' on' : ''}" data-d-quad="${q.id}">
              <b>${esc(q.label)}</b><small>${esc(q.action)}</small>
            </button>`).join('')}
        </div>
      </div>`;
  }

  // ---- 按需展开的「📖 速查清单」抽屉（平时 0 占用）----
  function renderRefDrawer() {
    if (!LS.refOpen) return '';
    const p = LS.priced;
    const res = LogicCore.evalFullyPricedIn(p);
    return `
      <div class="ls-ref-drawer">
        <div class="ls-ref-head">
          <b>📖 速查清单（充分定价 · 币本位 OI · 冲突对照 · 模式/禁忌）</b>
          <button type="button" class="btn ts-mini" data-ls-ref="close">收起 ✕</button>
        </div>
        <div class="ls-ref-body">
          <details class="ls-ref-sec" open>
            <summary><b>1. 充分定价 4 项硬验收 &amp; 币本位 OI 监控</b> <span class="ls-pill ${res.tone}">${esc(res.label)}</span></summary>
            <div class="ls-ref-grid2">
              <div class="ls-ref-checks">
                <label class="ls-inline-chk"><input type="checkbox" data-p-chk="upstreamBlunted" ${p.upstreamBlunted ? 'checked' : ''}><span>上游美债/DXY 鹰派钝化</span></label>
                <label class="ls-inline-chk"><input type="checkbox" data-p-chk="midstreamDiverged" ${p.midstreamDiverged ? 'checked' : ''}><span>中游纳指/AI 不破前低 &amp; VIX 不创新高</span></label>
                <label class="ls-inline-chk"><input type="checkbox" data-p-chk="downstreamResilient" ${p.downstreamResilient ? 'checked' : ''}><span>下游标的相对抗跌 / ETF 流出收窄</span></label>
              </div>
              <div class="ls-ref-checks">
                <label class="ls-inline-chk"><input type="checkbox" data-p-chk="realDeleveraging" ${p.realDeleveraging ? 'checked' : ''}><span>① 币本位 OI 缩水 25%~40%+（去 CME 看永续）</span></label>
                <label class="ls-inline-chk"><input type="checkbox" data-p-chk="secondBadNewsTest" ${p.secondBadNewsTest ? 'checked' : ''}><span>② ≥3~5 天后通过二次利空压力测试</span></label>
                <label class="ls-inline-chk"><input type="checkbox" data-p-chk="lowVolRetest" ${p.lowVolRetest ? 'checked' : ''}><span>③ 放量下跌后缩量二次探底不破前低</span></label>
                <label class="ls-inline-chk"><input type="checkbox" data-p-chk="rightSideBreakout" ${p.rightSideBreakout ? 'checked' : ''}><span>④ 右侧放量突破结构（做多扳机）</span></label>
              </div>
            </div>
            <div class="ls-banner tone-${res.tone}" style="margin:7px 0 2px;">${esc(res.guidance)}</div>
            <div class="ls-monitor-row">
              ${LogicCore.MONITOR_LINKS.map(lk => `<a class="btn ts-mini" href="${esc(lk.url)}" target="_blank" rel="noopener noreferrer" title="${esc(lk.tip)}">${esc(lk.name)} ↗</a>`).join('')}
              <span class="ls-side-hint">⚠ 必切「币本位 BTC 数量」且分开看 CME 套利盘 vs 永续盘</span>
            </div>
          </details>

          <details class="ls-ref-sec">
            <summary><b>2. 逻辑冲突快速决策对照表（5 类）</b></summary>
            <table class="ls-table">
              <thead><tr><th>冲突状态</th><th>盘面裁判信号</th><th>正确动作</th></tr></thead>
              <tbody>
                ${LogicCore.CONFLICT_MATRIX.map(m => `<tr><td><b>${esc(m.state)}</b></td><td>${esc(m.signal)}</td><td><b>${esc(m.action)}</b></td></tr>`).join('')}
              </tbody>
            </table>
          </details>

          <details class="ls-ref-sec">
            <summary><b>3. 模式卡片库 (${LS.patterns.length}) &amp; 禁忌铁律 (${LS.taboos.length})</b></summary>
            <div class="ls-ref-grid2">
              <div class="ls-lib-list">
                ${LS.patterns.map(pt => `<div class="ls-lib-item">
                  <div class="ls-lib-top"><b>${esc(pt.name)}</b><span><button type="button" class="btn ts-mini" data-pat-use="${esc(pt.id)}">套用</button> <button type="button" class="btn ts-mini btn-danger" data-pat-del="${esc(pt.id)}">×</button></span></div>
                  <div class="ls-lib-body">${esc(pt.trigger)}</div>
                </div>`).join('') || '<div class="ls-side-hint">暂无模式卡</div>'}
              </div>
              <div class="ls-lib-list">
                ${LS.taboos.map(tb => `<div class="ls-lib-item taboo">
                  <div class="ls-lib-top"><b>${esc(tb.title)}</b><button type="button" class="btn ts-mini btn-danger" data-taboo-del="${esc(tb.id)}">×</button></div>
                  <div class="ls-lib-body">${esc(tb.rule)}</div>
                </div>`).join('') || '<div class="ls-side-hint">暂无禁忌</div>'}
              </div>
            </div>
          </details>
        </div>
      </div>`;
  }

  function renderStudioBody(animateTab = false) {
    if (!LS.rootEl) return;
    const body = LS.rootEl.querySelector('.ls-modal-body');
    if (!body) return;
    LS.rootEl.querySelectorAll('.ls-nav-tab').forEach(b => b.classList.toggle('active', b.dataset.lsTab === LS.tab));
    LS.rootEl.querySelector('[data-ls-ref="toggle"]')?.classList.toggle('active', LS.refOpen);
    body.innerHTML = renderRefDrawer() + (LS.tab === 'gate' ? renderGateTab() : renderReviewTab());
    const nav = LS.rootEl.querySelector('.ls-nav');
    if (nav) requestAnimationFrame(() => window.uiMotion?.syncTabIndicator?.(nav, '.ls-nav-tab.active', !animateTab));
    if (animateTab && !window.uiMotion?.prefersReduced?.()) {
      body.classList.remove('ls-body-swap');
      void body.offsetWidth;
      body.classList.add('ls-body-swap');
    }
  }

  function renderStudio(animateOpen = false) {
    if (!LS.rootEl) {
      LS.rootEl = document.getElementById('logicStudioRoot') || document.body.appendChild(Object.assign(document.createElement('div'), { id: 'logicStudioRoot' }));
      LS.rootEl.className = 'ui-modal-backdrop ls-backdrop';
      LS.rootEl.hidden = true;
      LS.rootEl.innerHTML = `
        <div class="ls-modal ls-modal-slim ui-dialog" role="dialog" aria-modal="true" aria-label="逻辑工坊">
          <div class="ls-modal-head">
            <div class="ls-brand">
              <span class="ls-logo">🧭</span>
              <b>逻辑工坊</b>
            </div>
            <div class="ls-nav" role="tablist">
              <i class="tab-indicator" aria-hidden="true"></i>
              <button type="button" class="ls-nav-tab" data-ls-tab="gate">1. 30秒开仓卡</button>
              <button type="button" class="ls-nav-tab" data-ls-tab="review">2. 复盘本</button>
            </div>
            <div class="ls-head-right" style="margin-left:auto;">
              <button type="button" class="btn btn-outline" data-ls-ref="toggle">📖 速查清单</button>
              <button type="button" class="btn ls-close" data-ls-close aria-label="关闭">✕</button>
            </div>
          </div>
          <div class="ls-modal-body"></div>
        </div>`;
      bindStudioEvents();
    }
    if (LS.open) {
      renderStudioBody(false);
      if (animateOpen && window.uiMotion) window.uiMotion.openOverlay(LS.rootEl, () => {
        const nav = LS.rootEl.querySelector('.ls-nav');
        if (nav) window.uiMotion.syncTabIndicator(nav, '.ls-nav-tab.active', true);
      });
      else {
        LS.rootEl.hidden = false;
        LS.rootEl.classList.remove('is-closing');
        LS.rootEl.classList.add('is-open');
      }
    } else if (window.uiMotion) {
      window.uiMotion.closeOverlay(LS.rootEl, { duration: 150 });
    } else {
      LS.rootEl.hidden = true;
    }
  }

  function openLogicStudio(tab = 'gate', prefill = null) {
    if (tab === 'ref') { LS.refOpen = true; LS.tab = 'gate'; }
    else if (tab === 'archive' || tab === 'dashboard' || tab === 'review') { LS.tab = 'review'; LS.refOpen = false; }
    else { LS.tab = 'gate'; LS.refOpen = false; }
    LS.inlineSaveKind = null;
    if (LS.tab === 'gate' && (prefill || isDraftStale(LS.draft))) {
      LS.draft = newDraft(prefill || {});
    }
    const wasOpen = LS.open;
    LS.open = true;
    renderStudio(!wasOpen);
    document.getElementById('logicStudioBtn')?.classList.add('open');
  }
  function closeLogicStudio() {
    LS.open = false;
    LS.inlineSaveKind = null;
    document.getElementById('logicStudioBtn')?.classList.remove('open');
    if (LS.rootEl) {
      if (window.uiMotion) window.uiMotion.closeOverlay(LS.rootEl, { duration: 150 });
      else LS.rootEl.hidden = true;
    }
  }
  function toggleLogicStudio() {
    if (LS.open) closeLogicStudio(); else openLogicStudio(LS.tab || 'gate');
  }
  window.openLogicStudio = openLogicStudio;
  window.closeLogicStudio = closeLogicStudio;
  window.toggleLogicStudio = toggleLogicStudio;
  window.isLogicStudioOpen = () => LS.open;

  const selectedCard = () => LS.cards.find(c => c.id === LS.selectedCardId) || null;

  function bindStudioEvents() {
    LS.rootEl.addEventListener('pointerdown', e => { if (e.target === LS.rootEl) closeLogicStudio(); });

    LS.rootEl.addEventListener('click', async e => {
      const t = e.target;
      if (t.closest('[data-ls-close]')) { closeLogicStudio(); return; }
      const refAct = t.closest('[data-ls-ref]')?.dataset.lsRef;
      if (refAct) { LS.refOpen = refAct === 'toggle' ? !LS.refOpen : false; renderStudioBody(); return; }
      const tabBtn = t.closest('[data-ls-tab]');
      if (tabBtn) {
        const changed = LS.tab !== tabBtn.dataset.lsTab;
        LS.tab = tabBtn.dataset.lsTab;
        LS.inlineSaveKind = null;
        renderStudioBody(changed);
        return;
      }

      // Tab 1: 30秒开仓卡
      const sideBtn = t.closest('[data-g-side]');
      if (sideBtn && LS.draft) {
        LS.draft.side = sideBtn.dataset.gSide;
        const ctx = captureChartContext(LS.draft.side);
        LS.draft.stops = ctx.stops;
        LS.draft.stopPrice = ctx.stops.bar ?? ctx.stops.swing ?? ctx.stops.atr ?? null;
        renderStudioBody(); return;
      }
      const hBtn = t.closest('[data-g-horizon]');
      if (hBtn && LS.draft) {
        LS.draft.horizon = hBtn.dataset.gHorizon;
        renderStudioBody(); return;
      }
      const oppBtn = t.closest('[data-g-opp]');
      if (oppBtn && LS.draft) {
        const val = oppBtn.dataset.gOpp;
        const i = LS.draft.opponentTags.indexOf(val);
        if (i >= 0) LS.draft.opponentTags.splice(i, 1); else LS.draft.opponentTags.push(val);
        renderStudioBody(); return;
      }
      const riskBtn = t.closest('[data-g-risk]');
      if (riskBtn && LS.draft) { LS.draft.riskPct = +riskBtn.dataset.gRisk; renderStudioBody(); return; }
      const stopPick = t.closest('[data-g-stop]');
      if (stopPick && LS.draft) {
        const px = LS.draft.stops?.[stopPick.dataset.gStop];
        if (px != null) {
          LS.draft.stopPrice = +Number(px).toPrecision(7);
          LS.draft.invalidRule = `触及 ${LS.draft.stopPrice} 或 5 根未脱离成本`;
          renderStudioBody();
        }
        return;
      }
      const gAct = t.closest('[data-g-act]')?.dataset.gAct;
      if (gAct === 'refresh-ctx') { LS.draft = newDraft({ side: LS.draft?.side || '多' }); renderStudioBody(); return; }
      if (gAct === 'edit-sbg') { closeLogicStudio(); startEditSBg(); return; }
      if (gAct === 'lock-only') { lockDraftCard(false); return; }
      if (gAct === 'lock-and-open') { lockDraftCard(true); return; }

      // Tab 2: 复盘本
      const dWin = t.closest('[data-dash-win]');
      if (dWin) { LS.dashWindow = Number(dWin.dataset.dashWin); renderStudioBody(); return; }
      const qCell = t.closest('[data-a-quad]');
      if (qCell) {
        const id = qCell.dataset.aQuad;
        LS.archiveFilter.quadrant = LS.archiveFilter.quadrant === id ? 'all' : id;
        renderStudioBody(); return;
      }
      if (t.closest('[data-a-reset]')) { LS.archiveFilter = { status: 'all', quadrant: 'all', q: '' }; renderStudioBody(); return; }
      const aSel = t.closest('[data-a-sel]');
      if (aSel) { LS.selectedCardId = aSel.dataset.aSel; renderStudioBody(); return; }
      const delNode = t.closest('[data-d-delnode]');
      if (delNode) {
        const c = selectedCard();
        const idx = +delNode.dataset.dDelnode;
        if (c && c.evolution.nodes[idx]) {
          const [removed] = c.evolution.nodes.splice(idx, 1);
          persistCards(); renderStudioBody();
          showToast?.('已删除演进节点', 'info', {
            ms: 4800,
            action: { label: '撤销', onClick: () => { c.evolution.nodes.splice(idx, 0, removed); persistCards(); renderStudioBody(); } },
          });
        }
        return;
      }
      const scBtn = t.closest('[data-d-score]');
      if (scBtn) {
        const c = selectedCard();
        if (c) {
          const v = +scBtn.dataset.val;
          c.postmortem[scBtn.dataset.dScore] = c.postmortem[scBtn.dataset.dScore] === v ? null : v;
          persistCards(); renderStudioBody();
        }
        return;
      }
      const qBtn = t.closest('[data-d-quad]');
      if (qBtn) {
        const c = selectedCard();
        if (c) {
          c.postmortem.quadrant = qBtn.dataset.dQuad;
          c.postmortem.completedAt = Date.now();
          c.status = 'reviewed';
          persistCards(); updateLogicBadges(); renderLogicSidebar(); renderStudioBody();
        }
        return;
      }
      const dAct = t.closest('[data-d-act]')?.dataset.dAct;
      if (dAct) {
        const c = selectedCard();
        if (!c) return;
        if (dAct === 'add-node') {
          const evInp = LS.rootEl.querySelector('#lsNodeEvent');
          const rSel = LS.rootEl.querySelector('#lsNodeReaction');
          const txt = (evInp?.value || '').trim();
          if (!txt) { showToast?.('请填写盘中演进说明', 'warn'); return; }
          const bar = typeof curBar === 'function' ? curBar() : null;
          c.evolution.nodes.push({
            ts: Date.now(), barTime: bar?.time ?? null,
            barLabel: bar && typeof fmtTime === 'function' ? fmtTime(bar.time) : new Date().toISOString().slice(0, 10),
            price: bar?.close ?? null, event: txt, reaction: rSel?.value || '顺行',
          });
          persistCards(); renderStudioBody(); renderLogicSidebar();
        } else if (dAct === 'recalc-mfe') {
          const m = recalcCardMetrics(c);
          persistCards(); renderStudioBody();
          showToast?.(m ? `✓ 实得 ${m.realizedR ?? '—'}R · MAE ${m.maePct}% · MFE +${m.mfePct}%` : '请先填写离场价', m ? 'ok' : 'warn');
        } else if (dAct === 'copy-md') {
          const md = LogicCore.exportCardMarkdown(c);
          try { await navigator.clipboard.writeText(md); showToast?.('✓ 标准 Markdown 已复制', 'ok'); }
          catch { showToast?.('剪贴板不可用，请点 .md 下载', 'warn'); }
        } else if (dAct === 'dl-md') {
          const md = LogicCore.exportCardMarkdown(c);
          const a = document.createElement('a');
          a.href = URL.createObjectURL(new Blob([md], { type: 'text/markdown;charset=utf-8' }));
          a.download = `review_${c.symbol}_${new Date(c.lockedAt || Date.now()).toISOString().slice(0, 10)}.md`;
          a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
        } else if (dAct === 'locate') {
          if (blindNow()) { showToast?.('盲测练习中暂不跳转历史坐标', 'warn'); return; }
          if (c.symbol && c.symbol !== 'BLIND' && typeof switchSymbol === 'function' && c.symbol !== currentSym) await switchSymbol(c.symbol);
          if (c.capsule?.barTime && typeof locateIdx === 'function') {
            const i1 = locateIdx(c.capsule.barTime);
            const i2 = c.evolution?.exitTime ? locateIdx(c.evolution.exitTime) : null;
            if (i1 != null) {
              chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, i1 - 25), to: Math.max(i1 + 45, (i2 ?? i1) + 25) });
              closeLogicStudio();
            }
          }
        } else if (dAct === 'save-pattern') {
          LS.inlineSaveKind = LS.inlineSaveKind === 'pattern' ? null : 'pattern';
          renderStudioBody();
          if (LS.inlineSaveKind) requestAnimationFrame(() => { const i = LS.rootEl.querySelector('#lsInlineSaveInp'); i?.focus(); i?.select(); });
        } else if (dAct === 'save-taboo') {
          LS.inlineSaveKind = LS.inlineSaveKind === 'taboo' ? null : 'taboo';
          renderStudioBody();
          if (LS.inlineSaveKind) requestAnimationFrame(() => { const i = LS.rootEl.querySelector('#lsInlineSaveInp'); i?.focus(); i?.select(); });
        } else if (dAct === 'inline-save-cancel') {
          LS.inlineSaveKind = null; renderStudioBody();
        } else if (dAct === 'inline-save-ok') {
          const val = (LS.rootEl.querySelector('#lsInlineSaveInp')?.value || '').trim();
          if (!val) { window.uiMotion?.shake?.(LS.rootEl.querySelector('#lsInlineSaveInp')); return; }
          if (LS.inlineSaveKind === 'pattern') {
            LS.patterns.unshift({ id: uid('pat'), name: val, tier: 'A', logicType: c.capsule?.logicType || 'resonance', trigger: c.capsule?.whyNow || '—', entryAnchor: c.capsule?.techEntry || '—', invalidLine: c.capsule?.invalidPrice || '—', exitRule: c.capsule?.exitAnchor || '—', trap: c.iteration?.fixRule || '' });
            persistPatterns(); showToast?.(`✓ 已存入模式库「${val}」`, 'ok');
          } else if (LS.inlineSaveKind === 'taboo') {
            LS.taboos.unshift({ id: uid('taboo'), title: val, rule: c.iteration?.fixRule || c.capsule?.whyNow || '严禁重复', source: c.symbolLabel, createdAt: Date.now() });
            persistTaboos(); showToast?.(`✓ 已写入禁忌清单「${val}」`, 'ok');
          }
          LS.inlineSaveKind = null;
          renderStudioBody();
        } else if (dAct === 'delete') {
          const idx = LS.cards.findIndex(x => x.id === c.id);
          if (idx < 0) return;
          const [gone] = LS.cards.splice(idx, 1);
          LS.selectedCardId = LS.cards[0]?.id || null;
          persistCards(); updateLogicBadges(); renderLogicSidebar(); renderStudioBody();
          showToast?.(`已删除 ${gone.symbolLabel || gone.symbol} 复盘卡`, 'info', {
            ms: 5500,
            action: {
              label: '撤销',
              onClick: () => {
                LS.cards.splice(Math.min(idx, LS.cards.length), 0, gone);
                LS.selectedCardId = gone.id;
                persistCards(); updateLogicBadges(); renderLogicSidebar(); renderStudioBody();
                showToast?.('✓ 已恢复复盘卡', 'ok', 1600);
              },
            },
          });
        }
        return;
      }

      // 速查清单：模式卡 / 禁忌 / 导出备份
      const patUse = t.closest('[data-pat-use]');
      if (patUse) {
        const p = LS.patterns.find(x => x.id === patUse.dataset.patUse);
        if (p) {
          LS.draft = newDraft({ whyNow: p.trigger, techEntry: p.entryAnchor, invalidRule: p.invalidLine, exitAnchor: p.exitRule });
          LS.refOpen = false; LS.tab = 'gate'; renderStudioBody(true);
          showToast?.(`✓ 已套用「${p.name}」`, 'ok');
        }
        return;
      }
      const patDel = t.closest('[data-pat-del]');
      if (patDel) {
        const idx = LS.patterns.findIndex(x => x.id === patDel.dataset.patDel);
        if (idx >= 0) {
          const [gone] = LS.patterns.splice(idx, 1);
          persistPatterns(); renderStudioBody();
          showToast?.(`已删除模式「${gone.name}」`, 'info', {
            ms: 5000,
            action: { label: '撤销', onClick: () => { LS.patterns.splice(Math.min(idx, LS.patterns.length), 0, gone); persistPatterns(); renderStudioBody(); } },
          });
        }
        return;
      }
      const tbDel = t.closest('[data-taboo-del]');
      if (tbDel) {
        const idx = LS.taboos.findIndex(x => x.id === tbDel.dataset.tabooDel);
        if (idx >= 0) {
          const [gone] = LS.taboos.splice(idx, 1);
          persistTaboos(); renderStudioBody();
          showToast?.(`已删除禁忌「${gone.title}」`, 'info', {
            ms: 5000,
            action: { label: '撤销', onClick: () => { LS.taboos.splice(Math.min(idx, LS.taboos.length), 0, gone); persistTaboos(); renderStudioBody(); } },
          });
        }
        return;
      }
      if (t.closest('[data-dash-act]')?.dataset.dashAct === 'export-json') {
        const payload = { version: LogicCore.VERSION, exportedAt: new Date().toISOString(), sbgMap: LS.sbgMap, cards: LS.cards, patterns: LS.patterns, taboos: LS.taboos, prefs: LS.prefs };
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }));
        a.download = `logic_studio_backup_${new Date().toISOString().slice(0, 10)}.json`;
        a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
        showToast?.('✓ 全部逻辑卡与 S 级背景已导出 JSON', 'ok');
      }
    });

    LS.rootEl.addEventListener('input', e => {
      const t = e.target;
      if (t.dataset.gField && LS.draft) { LS.draft[t.dataset.gField] = t.value; return; }
      if (t.dataset.gNum && LS.draft) {
        LS.draft[t.dataset.gNum] = t.value.trim() === '' ? null : Number(t.value);
        updateGateSizingLive();
        return;
      }
      if (t.dataset.aFilter === 'q') {
        LS.archiveFilter.q = t.value;
        patchReviewSearchResults();
        return;
      }
      if (t.dataset.aFilter) { LS.archiveFilter[t.dataset.aFilter] = t.value; renderStudioBody(); return; }
      const c = selectedCard();
      if (!c) return;
      if (t.dataset.dPm) { c.postmortem[t.dataset.dPm] = t.value; persistCards(); }
      else if (t.dataset.dIt) { c.iteration[t.dataset.dIt] = t.value; persistCards(); }
      else if (t.dataset.dEvnum) { c.evolution[t.dataset.dEvnum] = t.value.trim() === '' ? null : Number(t.value); persistCards(); }
    });

    LS.rootEl.addEventListener('keydown', e => {
      if (e.target.id === 'lsNodeEvent' && e.key === 'Enter') {
        e.preventDefault();
        LS.rootEl.querySelector('[data-d-act="add-node"]')?.click();
      } else if (e.target.id === 'lsInlineSaveInp') {
        if (e.key === 'Enter') { e.preventDefault(); LS.rootEl.querySelector('[data-d-act="inline-save-ok"]')?.click(); }
        else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); LS.inlineSaveKind = null; renderStudioBody(); }
      }
    });

    LS.rootEl.addEventListener('change', async e => {
      const t = e.target;
      if (t.dataset.gNum && LS.draft) { renderStudioBody(); return; }
      if (t.dataset.gField && t.tagName === 'SELECT' && LS.draft) { LS.draft[t.dataset.gField] = t.value; renderStudioBody(); return; }
      if (t.dataset.gSwan && LS.draft) { LS.draft.blackSwan[t.dataset.gSwan] = t.checked; renderStudioBody(); return; }
      if (t.hasAttribute('data-g-impulse') && LS.draft) { LS.draft.impulseFlag = t.checked; renderStudioBody(); return; }
      if (t.dataset.pChk) { LS.priced[t.dataset.pChk] = t.checked; renderStudioBody(); return; }
      if (t.dataset.aFilter) { LS.archiveFilter[t.dataset.aFilter] = t.value; renderStudioBody(); return; }
      if (t.id === 'lsImportJson') {
        const file = t.files?.[0]; t.value = '';
        if (!file) return;
        const asStr = (v, max = 400) => typeof v === 'string' ? v.slice(0, max) : (typeof v === 'number' && Number.isFinite(v) ? String(v) : '');
        const asNum = v => { const n = Number(v); return (v === '' || v == null || !Number.isFinite(n)) ? null : n; };
        const sanitizeCard = raw => {
          if (!raw || typeof raw !== 'object' || typeof raw.id !== 'string' || !raw.id.trim()) return null;
          const cap = (raw.capsule && typeof raw.capsule === 'object') ? raw.capsule : {};
          const ev = (raw.evolution && typeof raw.evolution === 'object') ? raw.evolution : {};
          const pm = (raw.postmortem && typeof raw.postmortem === 'object') ? raw.postmortem : {};
          const it = (raw.iteration && typeof raw.iteration === 'object') ? raw.iteration : {};
          const met = (ev.metrics && typeof ev.metrics === 'object') ? ev.metrics : {};
          const status = ['locked', 'closed', 'reviewed'].includes(raw.status) ? raw.status : 'locked';
          const side = cap.side === '空' ? '空' : '多';
          const quad = ['rr', 'rl', 'wl', 'wr', ''].includes(pm.quadrant) ? pm.quadrant : '';
          const score = v => { const n = asNum(v); return n != null && n >= 1 && n <= 5 ? Math.round(n) : null; };
          return {
            id: asStr(raw.id, 80),
            createdAt: asNum(raw.createdAt) || Date.now(),
            lockedAt: asNum(raw.lockedAt) || Date.now(),
            orderFilledAt: asNum(raw.orderFilledAt),
            status,
            symbol: asStr(raw.symbol, 32) || 'BTC',
            symbolLabel: asStr(raw.symbolLabel || raw.symbol, 48) || 'BTC',
            tf: asStr(raw.tf, 12) || '1D',
            mode: ['live', 'replay', 'trainer'].includes(raw.mode) ? raw.mode : 'live',
            linkedPosId: asNum(raw.linkedPosId),
            compliance: {
              isPreLocked: raw.compliance?.isPreLocked !== false,
              violated: !!raw.compliance?.violated,
              vetoTriggered: Array.isArray(raw.compliance?.vetoTriggered) ? raw.compliance.vetoTriggered.map(x => asStr(x, 32)).slice(0, 8) : [],
            },
            capsule: {
              barTime: asNum(cap.barTime),
              barLabel: asStr(cap.barLabel, 40),
              barPrice: asNum(cap.barPrice),
              maSnap: asStr(cap.maSnap, 160),
              fngValue: asNum(cap.fngValue),
              fngLabel: asStr(cap.fngLabel, 24),
              side,
              horizon: asStr(cap.horizon, 16) || '中线',
              logicType: asStr(cap.logicType, 32) || 'event_catalyst',
              eventTier: ['S', 'A', 'B', 'C'].includes(cap.eventTier) ? cap.eventTier : 'A',
              threeLayer: cap.threeLayer && typeof cap.threeLayer === 'object' ? {
                macro: asStr(cap.threeLayer.macro, 16), industry: asStr(cap.threeLayer.industry, 16), individual: asStr(cap.threeLayer.individual, 16),
              } : { macro: 'neutral', industry: 'neutral', individual: 'neutral' },
              blackSwan: {
                hasSystemicCrisis: !!cap.blackSwan?.hasSystemicCrisis,
                crisisDaysElapsed: asNum(cap.blackSwan?.crisisDaysElapsed) || 0,
                reclaimedStructure: !!cap.blackSwan?.reclaimedStructure,
                usedNoDropAsBullish: !!cap.blackSwan?.usedNoDropAsBullish,
              },
              infoMacro: asStr(cap.infoMacro, 300),
              whyNow: asStr(cap.whyNow, 400),
              opponentTags: Array.isArray(cap.opponentTags) ? cap.opponentTags.map(x => asStr(x, 32)).slice(0, 12) : [],
              opponentNote: asStr(cap.opponentNote, 200),
              techBig: asStr(cap.techBig, 160),
              techEntry: asStr(cap.techEntry, 240),
              equity: asNum(cap.equity) || 100000,
              riskPct: asNum(cap.riskPct),
              entryPrice: asNum(cap.entryPrice),
              stopPrice: asNum(cap.stopPrice),
              targetPrice: asNum(cap.targetPrice),
              suggestedMargin: asNum(cap.suggestedMargin),
              suggestedLev: asNum(cap.suggestedLev),
              plannedRR: asNum(cap.plannedRR),
              invalidPrice: asStr(cap.invalidPrice, 200),
              invalidTime: asStr(cap.invalidTime, 80),
              exitAnchor: asStr(cap.exitAnchor, 200),
            },
            evolution: {
              nodes: Array.isArray(ev.nodes) ? ev.nodes.filter(n => n && typeof n === 'object').slice(0, 100).map(n => ({
                barTime: asNum(n.barTime),
                barLabel: asStr(n.barLabel, 40),
                price: asNum(n.price),
                event: asStr(n.event, 200),
                reaction: ['顺行', '滞涨', '反向破位'].includes(n.reaction) ? n.reaction : '顺行',
              })) : [],
              checks: {},
              falsified: !!ev.falsified,
              falsifyType: asStr(ev.falsifyType, 40),
              exitTime: asNum(ev.exitTime),
              exitPrice: asNum(ev.exitPrice),
              exitReason: asStr(ev.exitReason, 40),
              followedPlan: ev.followedPlan !== false,
              metrics: {
                realizedR: asNum(met.realizedR),
                realizedPct: asNum(met.realizedPct),
                maePct: asNum(met.maePct),
                maeR: asNum(met.maeR),
                mfePct: asNum(met.mfePct),
                mfeR: asNum(met.mfeR),
                retention: asNum(met.retention),
                barsHeld: asNum(met.barsHeld),
              },
            },
            postmortem: {
              completedAt: asNum(pm.completedAt),
              fullMoveRange: asStr(pm.fullMoveRange, 120),
              isNoise: !!pm.isNoise,
              realMainline: asStr(pm.realMainline, 240),
              alignment: asStr(pm.alignment, 40),
              blindSpot: asStr(pm.blindSpot, 240),
              logicScore: score(pm.logicScore),
              chartScore: score(pm.chartScore),
              quadrant: quad,
            },
            iteration: {
              keepAction: asStr(it.keepAction, 240),
              fixRule: asStr(it.fixRule, 240),
              patternId: it.patternId ? asStr(it.patternId, 80) : null,
            },
          };
        };
        try {
          const doc = JSON.parse(await file.text());
          const seen = new Set(LS.cards.map(x => x.id));
          let added = 0;
          for (const rawItem of Array.isArray(doc?.cards) ? doc.cards : []) {
            const clean = sanitizeCard(rawItem);
            if (clean && !seen.has(clean.id)) { LS.cards.push(clean); seen.add(clean.id); added++; }
          }
          if (doc?.sbgMap && typeof doc.sbgMap === 'object') {
            for (const [k, v] of Object.entries(doc.sbgMap)) {
              if (!v || typeof v !== 'object' || LS.sbgMap[k]) continue;
              const bias = ['bull', 'bear', 'neutral'].includes(v.bias) ? v.bias : 'neutral';
              LS.sbgMap[asStr(k, 32)] = {
                bias,
                headline: asStr(v.headline, 200),
                plan: asStr(v.plan, 160),
                updatedAt: asStr(v.updatedAt, 20),
              };
            }
            persistSBg(); renderSBgHud();
          }
          persistCards(); updateLogicBadges(); renderLogicSidebar(); renderStudioBody();
          showToast?.(`✓ 已导入 ${added} 张复盘卡`, 'ok');
        } catch { showToast?.('导入失败：JSON 无效', 'err'); }
        return;
      }
      const c = selectedCard();
      if (!c) return;
      if (t.dataset.dEv === 'followedStr') { c.evolution.followedPlan = t.value !== 'no'; persistCards(); renderStudioBody(); }
      else if (t.dataset.dEv) { c.evolution[t.dataset.dEv] = t.value; persistCards(); renderStudioBody(); }
      else if (t.dataset.dEvnum) { recalcCardMetrics(c); persistCards(); renderStudioBody(); renderLogicSidebar(); }
      else if (t.dataset.dPmbool) { c.postmortem[t.dataset.dPmbool] = t.checked; persistCards(); renderStudioBody(); }
    });
  }

  let _studioReady = false;
  function setupLogicStudio() {
    if (_studioReady) return;
    _studioReady = true;
    hydrate();
    setupSBgHud();
    renderLogicSidebar();
    document.getElementById('logicStudioBtn')?.addEventListener('click', () => toggleLogicStudio());

    const sideHost = document.getElementById('logicSideHost');
    if (sideHost) {
      sideHost.addEventListener('change', e => {
        if (e.target.id === 'lsStrictChk') {
          LS.prefs.strictGate = e.target.checked;
          persistPrefs();
          window.showToast?.(LS.prefs.strictGate ? '🛡️ 严格门禁已开启' : '已切回软提示模式', 'info', 1600);
        } else if (e.target.id === 'lsSbgVisChk') {
          LS.sbgUi.visible = e.target.checked;
          persistSBgUi();
          renderSBgHud();
        }
      });
      sideHost.addEventListener('click', e => {
        const sbgAct = e.target.closest('[data-ls-sbg]')?.dataset.lsSbg;
        if (sbgAct === 'edit') { startEditSBg(); return; }
        if (sbgAct === 'cycle-bias') { cycleSBgBias(); return; }
        const openBtn = e.target.closest('[data-ls-open]');
        if (openBtn) { openLogicStudio(openBtn.dataset.lsOpen); return; }
        const viewBtn = e.target.closest('[data-ls-view]');
        if (viewBtn) { LS.selectedCardId = viewBtn.dataset.lsView; openLogicStudio('review'); return; }
        const qnBtn = e.target.closest('[data-ls-quicknode]');
        if (qnBtn) {
          const cid = qnBtn.dataset.lsQuicknode;
          LS.quickNodeCardId = LS.quickNodeCardId === cid ? null : cid;
          LS.quickNodeReact = '顺行';
          renderLogicSidebar();
          if (LS.quickNodeCardId) requestAnimationFrame(() => sideHost.querySelector('.ls-qn-inp')?.focus());
          return;
        }
        const reactBtn = e.target.closest('[data-qn-react]');
        if (reactBtn) {
          LS.quickNodeReact = reactBtn.dataset.qnReact;
          sideHost.querySelectorAll('[data-qn-react]').forEach(b => b.classList.toggle('on', b === reactBtn));
          return;
        }
        if (e.target.closest('[data-qn-cancel]')) {
          LS.quickNodeCardId = null;
          renderLogicSidebar();
          return;
        }
        const saveBtn = e.target.closest('[data-qn-save]');
        if (saveBtn) {
          const c = LS.cards.find(x => x.id === saveBtn.dataset.qnSave);
          const inp = sideHost.querySelector('.ls-qn-inp');
          const txt = (inp?.value || '').trim();
          if (!c || !txt) { window.uiMotion?.shake?.(inp); inp?.focus(); return; }
          const bar = typeof curBar === 'function' ? curBar() : null;
          c.evolution.nodes.push({
            ts: Date.now(), barTime: bar?.time ?? null,
            barLabel: bar && typeof fmtTime === 'function' ? fmtTime(bar.time) : new Date().toISOString().slice(0, 10),
            price: bar?.close ?? null, event: txt, reaction: LS.quickNodeReact || '顺行',
          });
          LS.quickNodeCardId = null;
          persistCards(); renderLogicSidebar();
          window.showToast?.('✓ 已追加演进节点', 'ok', 1800);
        }
      });
      sideHost.addEventListener('keydown', e => {
        if (!e.target.classList?.contains('ls-qn-inp')) return;
        if (e.key === 'Enter') {
          e.preventDefault();
          sideHost.querySelector('[data-qn-save]')?.click();
        } else if (e.key === 'Escape') {
          e.preventDefault(); e.stopPropagation();
          LS.quickNodeCardId = null;
          renderLogicSidebar();
        }
      });
    }

    window.addEventListener('keydown', e => {
      if (e.key === 'Escape' && LS.open) { e.preventDefault(); closeLogicStudio(); }
    });
  }
  window.setupLogicStudio = setupLogicStudio;
  if (document.readyState === 'loading') window.addEventListener('DOMContentLoaded', setupLogicStudio, { once: true });
  else setupLogicStudio();
})();
