// js/20-ui-polish.js — 全站前端交互精修与统一动效层:
//   1) 统一 Apple 级状态变化动效原语 (window.uiMotion): 可打断弹层/浮层、滑动指示胶囊、FLIP、轻抖动、数值轻闪
//   2) 独立浮动 Toast 通知 (showToast): 支持 tone 与可点击「撤销 (Undo)」操作按钮
//   3) Cmd/Ctrl + K 品种分组模糊搜索切换器 (保持与 #symSelect 严格同步，自动区分 Mac/Win 快捷键)
//   4) 右侧边栏拖拽调宽分隔条 (#sideResizer, 拖拽 0ms 跟手，双击恢复默认)
//   5) 工具栏极简浮层管理 ([data-pop] 智能 fixed 定位防裁切) + 实时 --toolbar-h
//   6) 图表 Legend 加密恐惧贪婪指数 (FGI) 胶囊 + 市场上下文胶囊点击解读气泡
//   7) ? 快捷键速查弹窗 (可切换开关)
(function () {
  'use strict';

  const SIDE_W_KEY = 'chanlun.side.width.v1';
  const CRYPTO_SYMS = new Set(['BTC', 'ETH', 'SOL', 'PEPE', 'HYPE']);
  const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent || '');
  const MOD_KBD = IS_MAC ? '⌘K' : 'Ctrl K';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // ---- 0. 统一图标集（统一 1.35px-1.4px 线宽矢量图标，替代风格分裂的 emoji） ----
  const UI_ICONS = {
    draw: '<svg class="ui-ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M11.6 2.4a1.4 1.4 0 0 1 2 2L5.5 12.5 2.5 13.5l1-3 8.1-8.1Z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>',
    themeDark: '<svg class="ui-ico ui-ico-spin" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="3" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M8 1.8v1.6M8 12.6v1.6M1.8 8h1.6M12.6 8h1.6M3.6 3.6l1.1 1.1M11.3 11.3l1.1 1.1M12.4 3.6l-1.1 1.1M4.7 11.3l-1.1 1.1" stroke="currentColor" stroke-width="1.35" stroke-linecap="round"/></svg>',
    themeLight: '<svg class="ui-ico ui-ico-spin" viewBox="0 0 16 16" aria-hidden="true"><path d="M12.8 9.8A5.2 5.2 0 1 1 6.2 3.2a4.3 4.3 0 0 0 6.6 6.6Z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>',
    fsEnter: '<svg class="ui-ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 2.5H2.5V6m7.5-3.5h3.5V6m0 4v3.5H10M6 13.5H2.5V10M2.8 2.8 6 6m7.2-3.2L10 6m3.2 7.2L10 10m-7.2 3.2L6 10" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    fsExit: '<svg class="ui-ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 6H6V2.5M13.5 6H10V2.5M10 13.5V10h3.5M6 13.5V10H2.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    camera: '<svg class="ui-ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 5.2A1.2 1.2 0 0 1 3.7 4h1.6l.9-1.5h3.6l.9 1.5h1.6a1.2 1.2 0 0 1 1.2 1.2v6.6a1.2 1.2 0 0 1-1.2 1.2H3.7a1.2 1.2 0 0 1-1.2-1.2V5.2Z" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linejoin="round"/><circle cx="8" cy="8.4" r="2.2" fill="none" stroke="currentColor" stroke-width="1.35"/></svg>',
    sideOpen: '<svg class="ui-ico" viewBox="0 0 16 16" aria-hidden="true"><rect x="2" y="2.5" width="12" height="11" rx="1.8" fill="none" stroke="currentColor" stroke-width="1.35"/><path d="M10 2.5v11" stroke="currentColor" stroke-width="1.35"/></svg>',
    sideClosed: '<svg class="ui-ico" viewBox="0 0 16 16" aria-hidden="true"><rect x="2" y="2.5" width="12" height="11" rx="1.8" fill="none" stroke="currentColor" stroke-width="1.35"/><path d="M10 2.5v11" stroke="currentColor" stroke-width="1.35" stroke-dasharray="2 1.8"/></svg>',
  };
  window.UI_ICONS = UI_ICONS;

  // ---- 0b. 统一动效原语 (window.uiMotion) ----
  const overlayTimers = new WeakMap();
  const overlayRafs = new WeakMap();
  const overlayReturnFocus = new WeakMap();
  const prefersReduced = () => {
    try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; }
    catch { return false; }
  };

  function clearOverlayTimer(el) {
    const tid = overlayTimers.get(el);
    if (tid) { clearTimeout(tid); overlayTimers.delete(el); }
    const raf = overlayRafs.get(el);
    if (raf) { cancelAnimationFrame(raf); overlayRafs.delete(el); }
  }

  function openOverlay(el, onOpened) {
    if (!el) return;
    clearOverlayTimer(el);
    if (el.hidden && document.activeElement && !el.contains(document.activeElement)) {
      overlayReturnFocus.set(el, document.activeElement);
    }
    el.classList.remove('is-closing');
    el.hidden = false;
    if (prefersReduced()) {
      el.classList.add('is-open');
      onOpened?.();
      return;
    }
    el.classList.remove('is-open');
    // 强制下一帧触发过渡，并记录 rAF ID 保证中途关闭可立即取消
    const raf = requestAnimationFrame(() => {
      overlayRafs.delete(el);
      if (el.hidden || el.classList.contains('is-closing')) return;
      el.classList.add('is-open');
      onOpened?.();
    });
    overlayRafs.set(el, raf);
  }

  function closeOverlay(el, opts = {}) {
    if (!el) return;
    clearOverlayTimer(el);
    const restoreFocus = () => {
      if (opts.restoreFocus === false) return;
      const prev = overlayReturnFocus.get(el);
      overlayReturnFocus.delete(el);
      if (prev && prev.isConnected && typeof prev.focus === 'function' && (!document.activeElement || document.activeElement === document.body || el.contains(document.activeElement))) {
        try { prev.focus({ preventScroll: true }); } catch {}
      }
    };
    const dur = opts.duration ?? 150;
    if (el.hidden || prefersReduced() || opts.immediate) {
      el.classList.remove('is-open', 'is-closing');
      el.hidden = true;
      restoreFocus();
      opts.onClosed?.();
      return;
    }
    el.classList.remove('is-open');
    el.classList.add('is-closing');
    const done = () => {
      clearOverlayTimer(el);
      el.classList.remove('is-closing', 'is-open');
      el.hidden = true;
      restoreFocus();
      opts.onClosed?.();
    };
    overlayTimers.set(el, setTimeout(done, dur));
  }

  function syncTabIndicator(container, activeSelector = '.active', instant = false) {
    if (!container) return;
    const ind = container.querySelector(':scope > .tab-indicator');
    const act = container.querySelector(activeSelector);
    if (!ind || !act) return;
    const x = act.offsetLeft;
    const w = act.offsetWidth;
    if (!w) return;
    if (instant || prefersReduced()) {
      ind.style.transition = 'none';
      ind.style.width = w + 'px';
      ind.style.transform = `translate3d(${x}px, 0, 0)`;
      ind.style.opacity = '1';
      void ind.offsetWidth;
      ind.style.transition = '';
    } else {
      ind.style.width = w + 'px';
      ind.style.transform = `translate3d(${x}px, 0, 0)`;
      ind.style.opacity = '1';
    }
  }

  function shake(el) {
    if (!el || prefersReduced()) return;
    el.classList.remove('ui-shake');
    void el.offsetWidth;
    el.classList.add('ui-shake');
    el.addEventListener('animationend', () => el.classList.remove('ui-shake'), { once: true });
  }

  function pulseNum(el, dir) {
    if (!el || prefersReduced()) return;
    const cls = dir === 'up' ? 'num-tick-up' : dir === 'down' ? 'num-tick-down' : 'num-tick';
    el.classList.remove('num-tick-up', 'num-tick-down', 'num-tick');
    void el.offsetWidth;
    el.classList.add(cls);
  }

  function popBadge(el) {
    if (!el || prefersReduced()) return;
    el.classList.remove('badge-pop');
    void el.offsetWidth;
    el.classList.add('badge-pop');
  }

  function flipFrom(el, prevRect, dur = 240) {
    if (!el || !prevRect || prefersReduced()) return;
    const nextRect = el.getBoundingClientRect();
    const dx = prevRect.left - nextRect.left;
    const dy = prevRect.top - nextRect.top;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
    el.style.transition = 'none';
    el.style.transform = `translate3d(${dx.toFixed(1)}px, ${dy.toFixed(1)}px, 0)`;
    void el.offsetWidth;
    el.style.transition = `transform ${dur}ms var(--ease-spring)`;
    el.style.transform = 'translate3d(0, 0, 0)';
    const cleanup = () => {
      el.style.transition = '';
      el.style.transform = '';
    };
    el.addEventListener('transitionend', cleanup, { once: true });
    setTimeout(cleanup, dur + 60);
  }

  function positionPopover(trigger, panel) {
    if (!trigger || !panel) return;
    const tr = trigger.getBoundingClientRect();
    const pad = 8;
    const panelW = Math.max(panel.offsetWidth || 180, 174);
    const panelH = Math.max(panel.offsetHeight || 60, 48);
    let left = tr.left;
    if (left + panelW > window.innerWidth - pad) {
      left = Math.max(pad, window.innerWidth - panelW - pad);
    }
    let top = tr.bottom + 6;
    if (top + panelH > window.innerHeight - pad) {
      const above = tr.top - panelH - 6;
      top = above >= pad ? above : Math.max(pad, window.innerHeight - panelH - pad);
    }
    panel.style.left = Math.round(left) + 'px';
    panel.style.top = Math.round(top) + 'px';
  }

  function animateIconSwap(btn) {
    if (!btn || prefersReduced()) return;
    const ico = btn.querySelector('.ui-ico');
    if (!ico) return;
    ico.classList.remove('ico-swap');
    void ico.offsetWidth;
    ico.classList.add('ico-swap');
  }

  window.uiMotion = {
    IS_MAC, MOD_KBD, prefersReduced,
    openOverlay, closeOverlay,
    syncTabIndicator, shake, pulseNum, popBadge,
    flipFrom, positionPopover, animateIconSwap,
  };

  // ---- 1. 独立浮动 Toast 通知（支持 Undo 操作按钮 + 悬停暂停计时） ----
  function showToast(msg, tone = 'info', msOrOpts = 2200) {
    const host = document.getElementById('toastHost');
    if (!host || !msg) return;
    const opts = (typeof msOrOpts === 'object' && msOrOpts !== null) ? msOrOpts : { ms: msOrOpts };
    const ms = Number.isFinite(opts.ms) ? opts.ms : (opts.action ? 5000 : 2200);
    let t = tone;
    if (t === 'info') {
      if (msg.startsWith('✓')) t = 'ok';
      else if (msg.startsWith('⚠')) t = 'warn';
      else if (msg.startsWith('✗') || msg.startsWith('💥') || msg.startsWith('🛑')) t = 'err';
    }
    const item = document.createElement('div');
    item.className = `toast-item toast-${t}${opts.action ? ' has-action' : ''}`;
    const txtSpan = document.createElement('span');
    txtSpan.className = 'toast-msg';
    txtSpan.textContent = msg;
    item.appendChild(txtSpan);

    let dismissed = false;
    let timer = null;
    const dismiss = () => {
      if (dismissed) return;
      dismissed = true;
      clearTimeout(timer);
      item.classList.remove('show');
      item.classList.add('leaving');
      setTimeout(() => item.remove(), prefersReduced() ? 10 : 180);
    };

    if (opts.action && typeof opts.action.onClick === 'function') {
      const actBtn = document.createElement('button');
      actBtn.type = 'button';
      actBtn.className = 'toast-action';
      actBtn.textContent = opts.action.label || '撤销';
      actBtn.addEventListener('click', e => {
        e.stopPropagation();
        dismiss();
        opts.action.onClick();
      });
      item.appendChild(actBtn);
    }

    host.appendChild(item);
    while (host.children.length > 3) host.firstElementChild?.remove();
    requestAnimationFrame(() => item.classList.add('show'));

    const startTimer = () => { clearTimeout(timer); timer = setTimeout(dismiss, ms); };
    const pauseTimer = () => clearTimeout(timer);
    startTimer();
    item.addEventListener('pointerenter', pauseTimer);
    item.addEventListener('pointerleave', () => { if (!item.contains(document.activeElement)) startTimer(); });
    item.addEventListener('focusin', pauseTimer);
    item.addEventListener('focusout', e => { if (!item.contains(e.relatedTarget)) startTimer(); });
    return dismiss;
  }
  window.showToast = showToast;

  // ---- 2. Legend 加密 FGI 情绪胶囊 ----
  function fngInfoForTs(ts) {
    if (ts == null || typeof window.CRYPTO_FNG !== 'object' || !window.CRYPTO_FNG) return null;
    const d = new Date(ts * 1000);
    const ymd = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
    const v = window.CRYPTO_FNG[ymd];
    if (v == null || !Number.isFinite(v)) return null;
    let label = '中性', cls = 'fgi-mid';
    if (v <= 24) { label = '极度恐慌'; cls = 'fgi-efear'; }
    else if (v <= 44) { label = '恐慌'; cls = 'fgi-fear'; }
    else if (v >= 75) { label = '极度贪婪'; cls = 'fgi-egreed'; }
    else if (v >= 56) { label = '贪婪'; cls = 'fgi-greed'; }
    return { value: v, label, cls, ymd };
  }
  window.fngInfoForTs = fngInfoForTs;

  function updateLegendFgi(ts) {
    const el = document.getElementById('legendFgi');
    if (!el) return;
    const locked = typeof trLocked === 'function' && trLocked();
    const sym = typeof currentSym !== 'undefined' ? currentSym : 'BTC';
    if (locked || !CRYPTO_SYMS.has(sym) || ts == null) {
      el.textContent = '';
      el.className = 'legend-fgi';
      el.hidden = true;
      return;
    }
    const info = fngInfoForTs(ts);
    if (!info) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    el.className = `legend-fgi ${info.cls}`;
    el.textContent = `FGI ${info.value} · ${info.label}`;
    el.title = `加密恐惧贪婪指数 (${info.ymd})：${info.value}/100`;
  }
  window.updateLegendFgi = updateLegendFgi;

  // ---- 3. 品种分组模糊搜索切换器 (Cmd/Ctrl + K · 支持核心主池/全部过滤) ----
  let paletteEl = null;
  let paletteOpen = false;
  let paletteItems = [];
  let paletteIdx = 0;
  let paletteScope = 'core';   // 'core' (S+A 30个) | 'all' (46个)

  function readSymbolCatalog() {
    const sel = document.getElementById('symSelect');
    if (!sel) return [];
    const list = [];
    for (const child of sel.children) {
      if (child.tagName === 'OPTGROUP') {
        const group = child.label || '其他';
        for (const opt of child.children) {
          if (opt.tagName === 'OPTION') {
            const tier = window.MarketContext?.tierOf(opt.value) || 'B';
            const isCore = window.MarketContext ? window.MarketContext.isCore(opt.value) : true;
            list.push({ value: opt.value, text: opt.textContent.trim(), group, tier, isCore });
          }
        }
      } else if (child.tagName === 'OPTION') {
        const tier = window.MarketContext?.tierOf(child.value) || 'B';
        const isCore = window.MarketContext ? window.MarketContext.isCore(child.value) : true;
        list.push({ value: child.value, text: child.textContent.trim(), group: '全部', tier, isCore });
      }
    }
    return list;
  }

  function syncSymTrigger() {
    const btn = document.getElementById('symTrigger');
    const sel = document.getElementById('symSelect');
    if (!btn || !sel) return;
    const opt = sel.options[sel.selectedIndex];
    const group = opt?.parentElement?.tagName === 'OPTGROUP' ? opt.parentElement.label : '';
    const txt = opt ? opt.textContent.trim() : (typeof currentSym !== 'undefined' ? currentSym : 'BTC');
    const parts = txt.split('·').map(s => s.trim());
    const code = parts[0] || txt;
    const name = parts[1] || '';
    btn.title = `搜索并切换品种 (${MOD_KBD})`;
    btn.innerHTML =
      (group ? `<span class="sym-mkt">${esc(group)}</span>` : '') +
      `<b class="sym-code">${esc(code)}</b>` +
      (name ? `<span class="sym-name">${esc(name)}</span>` : '') +
      `<kbd class="sym-kbd">${esc(MOD_KBD)}</kbd>`;
  }
  window.syncSymTrigger = syncSymTrigger;

  function renderPaletteList(query) {
    if (!paletteEl) return;
    const q = (query || '').trim().toLowerCase();
    const all = readSymbolCatalog();
    const coreCount = all.filter(it => it.isCore).length;
    paletteEl.querySelectorAll('[data-sp-scope]').forEach(b => {
      b.classList.toggle('active', b.dataset.spScope === paletteScope);
      if (b.dataset.spScope === 'core') b.textContent = `核心主池 (${coreCount})`;
      else b.textContent = `全部 (${all.length})`;
    });
    // 有输入关键字时全库搜索，不挡任何品种；空关键字时按「核心主池 / 全部」过滤
    const base = q ? all : (paletteScope === 'core' ? all.filter(it => it.isCore) : all);
    paletteItems = q
      ? base.filter(it =>
          it.value.toLowerCase().includes(q) ||
          it.text.toLowerCase().includes(q) ||
          it.group.toLowerCase().includes(q))
      : base;
    if (paletteIdx >= paletteItems.length) paletteIdx = 0;

    const listEl = paletteEl.querySelector('.sp-list');
    if (!paletteItems.length) {
      listEl.innerHTML = '<div class="sp-empty">未找到匹配品种，试试输入 BTC、NVDA、黄金、茅台</div>';
      return;
    }
    let html = '';
    let lastGroup = null;
    const cur = typeof currentSym !== 'undefined' ? currentSym : '';
    paletteItems.forEach((it, idx) => {
      if (it.group !== lastGroup) {
        lastGroup = it.group;
        html += `<div class="sp-group">${esc(it.group)}</div>`;
      }
      const isCur = it.value === cur;
      const isActive = idx === paletteIdx;
      const [code, name] = it.text.split('·').map(s => s.trim());
      html += `<button type="button" class="sp-item${isActive ? ' active' : ''}${isCur ? ' current' : ''}" data-sp-idx="${idx}" data-sp-val="${esc(it.value)}">
        <span class="sp-item-main"><span class="sp-tier-pill tier-${it.tier}">${it.tier}</span><b class="tnum">${esc(code || it.value)}</b><span class="sp-item-name">${esc(name || '')}</span></span>
        <span class="sp-item-tag">${isCur ? '当前' : esc(it.group)}</span>
      </button>`;
    });
    listEl.innerHTML = html;
    listEl.querySelector('.sp-item.active')?.scrollIntoView({ block: 'nearest' });
  }

  function closeSymPalette() {
    paletteOpen = false;
    if (paletteEl) closeOverlay(paletteEl, { duration: 140 });
  }

  function openSymPalette() {
    if (typeof trLocked === 'function' && trLocked()) {
      showToast('盲测练习中已锁定品种，揭示答案后可切换', 'warn');
      return;
    }
    if (!paletteEl) {
      paletteEl = document.createElement('div');
      paletteEl.id = 'symPalette';
      paletteEl.className = 'ui-modal-backdrop';
      paletteEl.hidden = true;
      paletteEl.innerHTML = `
        <div class="sym-palette ui-dialog" role="dialog" aria-modal="true" aria-label="快速切换品种">
          <div class="sp-search-row">
            <span class="sp-icon" aria-hidden="true">⌕</span>
            <input type="text" class="sp-input" placeholder="输入品种代码、中文名或市场（如 NVDA / 黄金 / 茅台 / 加密）…" autocomplete="off" spellcheck="false">
            <kbd>Esc</kbd>
          </div>
          <div class="sp-tier-bar">
            <button type="button" class="sp-scope-btn active" data-sp-scope="core">核心主池 (30)</button>
            <button type="button" class="sp-scope-btn" data-sp-scope="all">全部 (46)</button>
            <span class="sp-tier-hint">S/A 高流动主池 · 输入关键词全库搜索</span>
          </div>
          <div class="sp-list"></div>
          <div class="sp-foot">
            <span><kbd>↑</kbd><kbd>↓</kbd> 选择</span>
            <span><kbd>Enter</kbd> 切换</span>
            <span><kbd>Esc</kbd> 关闭</span>
          </div>
        </div>`;
      document.body.appendChild(paletteEl);
      paletteEl.addEventListener('pointerdown', e => {
        if (e.target === paletteEl) closeSymPalette();
      });
      const inp = paletteEl.querySelector('.sp-input');
      inp.addEventListener('input', () => {
        paletteIdx = 0;
        renderPaletteList(inp.value);
      });
      inp.addEventListener('keydown', e => {
        if (e.key === 'Escape') { e.preventDefault(); closeSymPalette(); return; }
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          if (paletteItems.length) { paletteIdx = (paletteIdx + 1) % paletteItems.length; renderPaletteList(inp.value); }
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          if (paletteItems.length) { paletteIdx = (paletteIdx - 1 + paletteItems.length) % paletteItems.length; renderPaletteList(inp.value); }
        } else if (e.key === 'Enter') {
          e.preventDefault();
          const chosen = paletteItems[paletteIdx];
          if (chosen) selectSymFromPalette(chosen.value);
        }
      });
      paletteEl.querySelector('.sp-tier-bar')?.addEventListener('click', e => {
        const b = e.target.closest('[data-sp-scope]');
        if (!b) return;
        paletteScope = b.dataset.spScope;
        paletteIdx = 0;
        renderPaletteList(inp.value);
        inp.focus();
      });
      paletteEl.querySelector('.sp-list').addEventListener('click', e => {
        const btn = e.target.closest('[data-sp-val]');
        if (btn) selectSymFromPalette(btn.dataset.spVal);
      });
    }
    paletteIdx = 0;
    const inp = paletteEl.querySelector('.sp-input');
    inp.value = '';
    paletteOpen = true;
    renderPaletteList('');
    openOverlay(paletteEl, () => inp.focus());
  }
  window.openSymPalette = openSymPalette;
  window.closeSymPalette = closeSymPalette;
  window.toggleSymPalette = () => (paletteOpen ? closeSymPalette() : openSymPalette());

  function selectSymFromPalette(sym) {
    closeSymPalette();
    const sel = document.getElementById('symSelect');
    if (sel) sel.value = sym;
    if (typeof switchSymbol === 'function') {
      Promise.resolve(switchSymbol(sym)).finally(() => syncSymTrigger());
    }
  }

  // ---- 4. 右侧边栏水平拖拽调宽 (#sideResizer) ----
  function setupSideResizer() {
    const handle = document.getElementById('sideResizer');
    const side = document.querySelector('.side');
    if (!handle || !side) return;
    const DEFAULT_W = 368;
    const MIN_W = 300;
    const MAX_W = 540;

    const applyWidth = w => {
      const clamped = Math.round(Math.max(MIN_W, Math.min(MAX_W, w)));
      document.documentElement.style.setProperty('--side-w', clamped + 'px');
      handle.setAttribute('aria-valuenow', String(clamped));
      if (typeof schedulePaneLayout === 'function') schedulePaneLayout();
      else if (typeof alignPanes === 'function') requestAnimationFrame(alignPanes);
      const sideTabs = document.getElementById('sideTabs');
      if (sideTabs) syncTabIndicator(sideTabs, '.side-tab.active', true);
      return clamped;
    };

    try {
      const saved = Number(localStorage.getItem(SIDE_W_KEY));
      if (Number.isFinite(saved) && saved >= MIN_W && saved <= MAX_W) applyWidth(saved);
    } catch {}

    let drag = null;
    const finish = () => {
      if (!drag) return;
      const curW = side.getBoundingClientRect().width;
      drag = null;
      handle.classList.remove('dragging');
      document.body.classList.remove('resizing-side');
      try { localStorage.setItem(SIDE_W_KEY, String(Math.round(curW))); } catch {}
      if (typeof alignPanes === 'function') {
        requestAnimationFrame(alignPanes);
        setTimeout(alignPanes, 90);
      }
    };

    handle.addEventListener('pointerdown', e => {
      if (e.button !== 0 || document.body.classList.contains('side-collapsed')) return;
      e.preventDefault();
      drag = { id: e.pointerId, startX: e.clientX, startW: side.getBoundingClientRect().width };
      handle.setPointerCapture(e.pointerId);
      handle.classList.add('dragging');
      document.body.classList.add('resizing-side');
    });
    handle.addEventListener('pointermove', e => {
      if (!drag || e.pointerId !== drag.id) return;
      const delta = drag.startX - e.clientX;
      applyWidth(drag.startW + delta);
    });
    handle.addEventListener('pointerup', finish);
    handle.addEventListener('pointercancel', finish);
    handle.addEventListener('lostpointercapture', finish);
    window.addEventListener('blur', finish);
    handle.addEventListener('dblclick', () => {
      const w = applyWidth(DEFAULT_W);
      try { localStorage.setItem(SIDE_W_KEY, String(w)); } catch {}
      showToast('右侧面板宽度已重置', 'info', 1400);
    });
  }

  // ---- 5. 工具栏极简浮层 & 画线工具激活态同步 (fixed 定位防滚动裁切) ----
  function setupToolbarPopovers() {
    const pops = [...document.querySelectorAll('[data-pop]')];
    const closePop = pop => {
      const panel = pop.querySelector('[data-pop-panel]');
      const btn = pop.querySelector('[data-pop-toggle]');
      if (panel && !panel.hidden) closeOverlay(panel, { duration: 120 });
      if (btn) { btn.setAttribute('aria-expanded', 'false'); btn.classList.remove('open'); }
    };
    const closeAll = except => pops.forEach(p => { if (p !== except) closePop(p); });
    window.closeToolbarPopovers = () => closeAll(null);

    pops.forEach(pop => {
      const btn = pop.querySelector('[data-pop-toggle]');
      const panel = pop.querySelector('[data-pop-panel]');
      if (!btn || !panel) return;
      btn.addEventListener('click', e => {
        e.stopPropagation();
        const willOpen = panel.hidden || panel.classList.contains('is-closing');
        closeAll(pop);
        if (willOpen) {
          openOverlay(panel);
          positionPopover(btn, panel);
        } else {
          closePop(pop);
        }
        btn.setAttribute('aria-expanded', String(willOpen));
        btn.classList.toggle('open', willOpen);
      });
      // 画线浮层里选中工具后自动收起浮层，清爽不挡图
      if (pop.id === 'drawPop') {
        panel.addEventListener('click', e => {
          if (e.target.closest('#drawToggle button, #drawClear')) {
            setTimeout(() => closePop(pop), 40);
          }
        });
      }
    });

    document.addEventListener('pointerdown', e => {
      if (e.target.closest('.tb-pop')) return;
      closeAll(null);
    });
    window.addEventListener('resize', () => closeAll(null));

    // 工具栏高度写入 --toolbar-h
    const bar = document.querySelector('.toolbar');
    if (bar) {
      const setH = () => document.documentElement.style.setProperty('--toolbar-h', Math.ceil(bar.getBoundingClientRect().height) + 'px');
      setH();
      new ResizeObserver(setH).observe(bar);
    }
  }

  function syncDrawPopLabel(tool) {
    const btn = document.getElementById('drawPopBtn');
    if (!btn) return;
    const names = { none: '画线', trend: '趋势线', sandr: '水平线', fib: '斐波那契', poly: '折线' };
    const isDrawing = tool && tool !== 'none';
    btn.classList.toggle('active', !!isDrawing);
    const lbl = btn.querySelector('.tb-lbl');
    if (lbl) lbl.textContent = names[tool] || '画线';
    else btn.innerHTML = `${UI_ICONS.draw}<span class="tb-lbl">${names[tool] || '画线'}</span><span class="pop-count" id="drawCountBadge"></span><i class="caret">▾</i>`;
  }
  window.syncDrawPopLabel = syncDrawPopLabel;

  // ---- 5b. 市场上下文胶囊点击中文解读气泡 (#ctxStrip .ctx-pill) ----
  let ctxTipEl = null;
  let ctxTipAnchor = null;
  function closeCtxTip() {
    ctxTipAnchor = null;
    if (ctxTipEl) closeOverlay(ctxTipEl, { duration: 120, restoreFocus: false });
  }
  window.closeCtxTip = closeCtxTip;
  function setupCtxStripPopover() {
    const strip = document.getElementById('ctxStrip');
    if (!strip) return;
    strip.addEventListener('click', e => {
      const pill = e.target.closest('.ctx-pill');
      if (!pill) return;
      e.stopPropagation();
      if (ctxTipAnchor === pill && ctxTipEl && !ctxTipEl.hidden && !ctxTipEl.classList.contains('is-closing')) {
        closeCtxTip();
        return;
      }
      if (!ctxTipEl) {
        ctxTipEl = document.createElement('div');
        ctxTipEl.className = 'ctx-explain-pop';
        ctxTipEl.setAttribute('role', 'tooltip');
        ctxTipEl.hidden = true;
        document.body.appendChild(ctxTipEl);
      }
      ctxTipAnchor = pill;
      const head = pill.dataset.ctxHead || pill.textContent.trim();
      const desc = pill.dataset.ctxDesc || pill.title || '';
      if (!desc) return;
      ctxTipEl.innerHTML = `<div class="cep-head">${esc(head)}</div><div class="cep-body">${esc(desc)}</div>`;
      openOverlay(ctxTipEl);
      positionPopover(pill, ctxTipEl);
    });
    document.addEventListener('pointerdown', e => {
      if (ctxTipEl && !ctxTipEl.hidden && !e.target.closest('.ctx-explain-pop, #ctxStrip')) closeCtxTip();
    });
    window.addEventListener('resize', () => { if (ctxTipEl && !ctxTipEl.hidden) closeCtxTip(); });
    window.addEventListener('scroll', () => { if (ctxTipEl && !ctxTipEl.hidden) closeCtxTip(); }, true);
  }

  // ---- 6. ? 键盘快捷键速查弹窗（可再按收回） ----
  let kbdModalEl = null;
  let kbdOpen = false;
  function closeKbdModal() {
    kbdOpen = false;
    if (kbdModalEl) closeOverlay(kbdModalEl, { duration: 140 });
  }
  function openKbdModal() {
    if (!kbdModalEl) {
      kbdModalEl = document.createElement('div');
      kbdModalEl.id = 'kbdModal';
      kbdModalEl.className = 'ui-modal-backdrop';
      kbdModalEl.hidden = true;
      const modKeyHtml = IS_MAC ? '<kbd>⌘</kbd> + <kbd>K</kbd>' : '<kbd>Ctrl</kbd> + <kbd>K</kbd>';
      kbdModalEl.innerHTML = `
        <div class="kbd-modal ui-dialog" role="dialog" aria-modal="true" aria-label="快捷键速查">
          <div class="km-head">
            <b>键盘快捷键速查</b>
            <button type="button" class="btn km-close" aria-label="关闭">✕</button>
          </div>
          <div class="km-grid">
            <div class="km-sec">
              <h4>导航与工作台</h4>
              <div class="km-row"><span>全局搜索切换品种</span><span>${modKeyHtml}</span></div>
              <div class="km-row"><span>打开 / 关闭逻辑复盘台</span><span><kbd>G</kbd></span></div>
              <div class="km-row"><span>查看 / 关闭快捷键帮助</span><span><kbd>?</kbd></span></div>
              <div class="km-row"><span>逐层关闭弹层 / 取消画线</span><span><kbd>Esc</kbd></span></div>
            </div>
            <div class="km-sec">
              <h4>快速下单（复盘 / 练习）</h4>
              <div class="km-row"><span>快速做多 / 做空（再按收回）</span><span><kbd>B</kbd> / <kbd>S</kbd></span></div>
              <div class="km-row"><span>切换止损：本根 / 波段 / ATR</span><span><kbd>L</kbd> / <kbd>P</kbd> / <kbd>A</kbd></span></div>
              <div class="km-row"><span>确认开仓</span><span><kbd>Enter</kbd></span></div>
              <div class="km-row"><span>收起成一行不挡 K 线</span><span><kbd>H</kbd></span></div>
              <div class="km-row"><span>练习记录：等待 / 放弃</span><span><kbd>W</kbd> / <kbd>X</kbd></span></div>
              <div class="km-row"><span>套用下单信号模板</span><span><kbd>1</kbd> – <kbd>9</kbd></span></div>
            </div>
            <div class="km-sec">
              <h4>复盘 / 练习步进与测距</h4>
              <div class="km-row"><span>复盘步进上 / 下一根</span><span><kbd>←</kbd> / <kbd>→</kbd></span></div>
              <div class="km-row"><span>复盘播放/暂停 · 盲测下一根</span><span><kbd>Space</kbd> / <kbd>→</kbd></span></div>
              <div class="km-row"><span>两点测距（幅度 / 根数）</span><span><kbd>Shift</kbd> + 单击起终点</span></div>
              <div class="km-row"><span>删除选中画线 / 取消绘制</span><span><kbd>Delete</kbd> / 右键</span></div>
              <div class="km-row"><span>重置副图或侧栏尺寸</span><span>双击分隔条</span></div>
            </div>
          </div>
        </div>`;
      document.body.appendChild(kbdModalEl);
      kbdModalEl.addEventListener('pointerdown', e => {
        if (e.target === kbdModalEl || e.target.closest('.km-close')) closeKbdModal();
      });
    }
    kbdOpen = true;
    openOverlay(kbdModalEl);
  }
  window.openKbdModal = openKbdModal;
  window.closeKbdModal = closeKbdModal;
  window.toggleKbdModal = () => (kbdOpen ? closeKbdModal() : openKbdModal());
  window.isSymPaletteOpen = () => paletteOpen;
  window.isKbdModalOpen = () => kbdOpen;

  let _polishReady = false;
  function setupUiPolish() {
    if (_polishReady) return;
    _polishReady = true;
    setupToolbarPopovers();
    setupSideResizer();
    setupCtxStripPopover();
    syncSymTrigger();
    window.syncThemeButton?.(document.documentElement.dataset.theme || 'dark');
    {
      const sideBtn = document.getElementById('sideToggleBtn');
      const collapsed = document.body.classList.contains('side-collapsed');
      if (sideBtn) {
        sideBtn.innerHTML = `${collapsed ? UI_ICONS.sideClosed : UI_ICONS.sideOpen}<span class="tb-side-lbl">${collapsed ? '展开' : '侧栏'}</span>`;
      }
    }

    document.getElementById('symTrigger')?.addEventListener('click', () => window.toggleSymPalette());
    document.getElementById('symSelect')?.addEventListener('change', () => setTimeout(syncSymTrigger, 20));
    document.getElementById('kbdHelpBtn')?.addEventListener('click', () => window.toggleKbdModal());

    // 工具栏「已练 N 局」可直达战绩 Tab，收起侧栏时自动展开
    document.getElementById('practiceCount')?.addEventListener('click', () => {
      if (typeof isSideCollapsed === 'function' && isSideCollapsed() && typeof setSideCollapsed === 'function') {
        setSideCollapsed(false);
      }
      if (typeof setSideTab === 'function') setSideTab('practice');
    });

    // 侧栏 Tab 滑动胶囊首帧与窗口变化对齐
    const sideTabs = document.getElementById('sideTabs');
    if (sideTabs) {
      requestAnimationFrame(() => syncTabIndicator(sideTabs, '.side-tab.active', true));
      new ResizeObserver(() => syncTabIndicator(sideTabs, '.side-tab.active', true)).observe(sideTabs);
    }

    // 全局热键: Cmd/Ctrl+K 搜品种 · G 开关逻辑台 · ? 快捷键帮助 · 逐层 Esc
    window.addEventListener('keydown', e => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        window.toggleSymPalette();
        return;
      }
      if (e.key === 'Escape') {
        if (paletteOpen) { e.preventDefault(); e.stopImmediatePropagation(); closeSymPalette(); return; }
        if (kbdOpen) { e.preventDefault(); e.stopImmediatePropagation(); closeKbdModal(); return; }
        if (typeof window.isLogicStudioOpen === 'function' && window.isLogicStudioOpen()) {
          e.preventDefault(); e.stopImmediatePropagation(); window.closeLogicStudio?.(); return;
        }
        if (typeof window.isTrainerConfigOpen === 'function' && window.isTrainerConfigOpen()) {
          e.preventDefault(); e.stopImmediatePropagation(); window.closeTrainerConfig?.(); return;
        }
        if (ctxTipEl && !ctxTipEl.hidden) { e.preventDefault(); closeCtxTip(); return; }
        const openPop = document.querySelector('[data-pop-panel]:not([hidden]):not(.is-closing)');
        if (openPop) { e.preventDefault(); window.closeToolbarPopovers?.(); return; }
        if (typeof qoIsOpen === 'function' && qoIsOpen()) {
          e.preventDefault(); qoClose?.(); return;
        }
        if (typeof closePosPopup === 'function' && typeof posPopup !== 'undefined' && posPopup) {
          e.preventDefault(); closePosPopup(); return;
        }
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const tag = document.activeElement?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || document.activeElement?.isContentEditable) return;
      if (e.key === '?') {
        e.preventDefault();
        window.toggleKbdModal();
      } else if (e.key.toLowerCase() === 'g') {
        if (typeof window.toggleLogicStudio === 'function') {
          e.preventDefault();
          window.toggleLogicStudio();
        }
      } else if (typeof inReplay === 'function' && inReplay() && !(typeof trLocked === 'function' && trLocked())) {
        // 普通复盘模式键盘控制：← / → 前后步进，Space 播放/暂停（无弹层打开时）
        if (paletteOpen || kbdOpen || (typeof window.isLogicStudioOpen === 'function' && window.isLogicStudioOpen())) return;
        if (typeof qoIsOpen === 'function' && qoIsOpen()) return;
        if (e.key === 'ArrowRight') { e.preventDefault(); stopPlay?.(); stepReplay?.(1); }
        else if (e.key === 'ArrowLeft') { e.preventDefault(); stopPlay?.(); stepReplay?.(-1); }
        else if (e.key === ' ') { e.preventDefault(); togglePlay?.(); }
      }
    });
  }
  window.setupUiPolish = setupUiPolish;
  if (document.readyState === 'loading') window.addEventListener('DOMContentLoaded', setupUiPolish, { once: true });
  else setupUiPolish();
})();
