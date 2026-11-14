// js/19-toolbar-ui.js — 工具栏收纳与布局辅助(纯 UI, 不改任何业务状态)
//  1) 下拉面板([data-pop]): 指标 / 研究工具 / 报告 —— 点击开合, 点外部或 Esc 关闭;
//  2) 「⚙ 缠论设置」(#chanDetails)改为浮层, 点外部关闭(开合仍由 07-main.js 负责);
//  3) 参数实验室按钮(09-params-lab.js 动态插入)移入「研究工具」;
//  4) 下拉按钮角标: 已开启的指标/工具数量;
//  5) 把工具栏实际高度写入 CSS 变量 --toolbar-h, 供标注抽屉等 fixed 浮层对齐。
(function setupToolbarUI(){
  const pops = [...document.querySelectorAll('[data-pop]')];
  const chanBtn = document.getElementById('chanDetailsBtn');
  const chanPanel = document.getElementById('chanDetails');

  function closePop(pop){
    const panel = pop.querySelector('[data-pop-panel]');
    const btn = pop.querySelector('[data-pop-toggle]');
    if (panel) panel.hidden = true;
    if (btn){ btn.setAttribute('aria-expanded', 'false'); btn.classList.remove('open'); }
  }
  function closeAll(except){
    pops.forEach(p => { if (p !== except) closePop(p); });
    if (except !== chanPanel && chanPanel && chanPanel.style.display !== 'none'){
      chanPanel.style.display = 'none';
      chanBtn && chanBtn.classList.remove('open');
    }
  }
  pops.forEach(pop => {
    const btn = pop.querySelector('[data-pop-toggle]');
    const panel = pop.querySelector('[data-pop-panel]');
    if (!btn || !panel) return;
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const open = panel.hidden;
      closeAll(pop);
      panel.hidden = !open;
      btn.setAttribute('aria-expanded', String(open));
      btn.classList.toggle('open', open);
    });
    // 报告类链接点完即收起; 指标/工具允许连续切换多个
    panel.addEventListener('click', e => { if (e.target.closest('a')) closePop(pop); });
  });
  if (chanBtn && chanPanel){
    // 07-main.js 已绑定 click 切换 display; 这里只同步按钮高亮并关掉其它浮层
    chanBtn.addEventListener('click', () => {
      requestAnimationFrame(() => {
        const open = chanPanel.style.display !== 'none';
        chanBtn.classList.toggle('open', open);
        if (open) pops.forEach(closePop);
      });
    });
  }
  document.addEventListener('pointerdown', e => {
    if (e.target.closest('.tb-pop')) return;
    closeAll(null);
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeAll(null); });

  // 参数实验室按钮 → 研究工具
  const toolsMenu = document.getElementById('toolsMenu');
  const moveLab = () => {
    const lab = document.getElementById('labBtn');
    if (lab && toolsMenu && lab.parentNode !== toolsMenu){ toolsMenu.appendChild(lab); return true; }
    return !!lab;
  };
  if (!moveLab()){
    let tries = 0;
    const t = setInterval(() => { if (moveLab() || ++tries > 40) clearInterval(t); }, 250);
  }

  // 角标: 已开启数量
  const IND_TOGGLES = ['cover67IndicatorToggle', 'ma20StructureToggle', 'aiMa20AlgoToggle', 'macd3segToggle', 'ma20AIToggle'];
  const indBox = document.getElementById('indicatorToggle');
  const indCount = document.getElementById('indicatorCount');
  const toolsCount = document.getElementById('toolsCount');
  const setCount = (el, n) => { if (!el) return; el.textContent = n ? String(n) : ''; el.classList.toggle('on', n > 0); };
  function refreshCounts(){
    setCount(indCount, IND_TOGGLES.filter(id => document.getElementById(id)?.classList.contains('active')).length);
    setCount(toolsCount, toolsMenu ? toolsMenu.querySelectorAll('.active').length : 0);
  }
  const mo = new MutationObserver(refreshCounts);
  [indBox, toolsMenu].forEach(el => el && mo.observe(el, { subtree: true, childList: true, attributes: true, attributeFilter: ['class', 'aria-pressed'] }));
  refreshCounts();

  // 工具栏高度 → CSS 变量
  const bar = document.querySelector('.toolbar');
  if (bar){
    const setH = () => document.documentElement.style.setProperty('--toolbar-h', Math.ceil(bar.getBoundingClientRect().height) + 'px');
    setH();
    new ResizeObserver(setH).observe(bar);
  }
})();
