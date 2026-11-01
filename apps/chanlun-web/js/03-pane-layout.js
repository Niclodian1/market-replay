// 副图布局：隐藏时保留非零画布，恢复/拖动/窗口变化时统一同步尺寸与时间范围。
const PANE_DEFAULTS = { volume: 140, macd: 180 };
const PANE_MIN_HEIGHT = 64;
const PANE_HEIGHT_KEY = 'chanlun.pane.heights.v1';
const paneHeights = { ...PANE_DEFAULTS };
const subPanes = () => [['volume', volumeChart], ['macd', macdChart]];
let paneLayoutFrame = 0;

function schedulePaneLayout(){
  if (paneLayoutFrame) return;
  paneLayoutFrame = requestAnimationFrame(() => {
    paneLayoutFrame = 0;
    fitPaneHeights();
    alignPanes();
  });
}

function fitPaneHeights(){
  const box = document.querySelector('.chart-box');
  const visible = subPanes().filter(([id]) => !document.getElementById(id).classList.contains('pane-off'));
  if (!visible.length || !box.clientHeight) return;
  const handlesHeight = visible.reduce((sum, [id]) => sum + document.querySelector(`.pane-resizer[data-pane="${id}"]`).offsetHeight, 0);
  const budget = Math.max(visible.length * PANE_MIN_HEIGHT, box.clientHeight - 160 - handlesHeight);
  const requested = visible.reduce((sum, [id]) => sum + paneHeights[id], 0);
  const spare = Math.max(0, budget - visible.length * PANE_MIN_HEIGHT);
  const extra = requested - visible.length * PANE_MIN_HEIGHT;
  for (const [id] of visible){
    const height = requested > budget && extra > 0
      ? PANE_MIN_HEIGHT + Math.floor((paneHeights[id] - PANE_MIN_HEIGHT) * spare / extra)
      : paneHeights[id];
    const el = document.getElementById(id);
    el.style.height = height + 'px';
    const handle = document.querySelector(`.pane-resizer[data-pane="${id}"]`);
    const other = visible.reduce((sum, [otherId]) => sum + (otherId === id ? 0 : document.getElementById(otherId).offsetHeight), 0);
    handle.setAttribute('aria-valuemin', String(PANE_MIN_HEIGHT));
    handle.setAttribute('aria-valuemax', String(Math.max(height, budget - other)));
    handle.setAttribute('aria-valuenow', String(height));
    handle.setAttribute('aria-valuetext', `${height} 像素`);
  }
}

function alignPanes(){
  const width = chart.timeScale().width();
  if (!Number.isFinite(width) || width < 100) return;
  const range = chart.timeScale().getVisibleLogicalRange();
  const wasSyncing = _syncingRange;
  _syncingRange = true; // resize 的中间范围不能反写到主图。
  try {
    for (const [id, sub] of subPanes()){
      if (!paneReady(id)) continue;
      const el = document.getElementById(id);
      const target = Math.max(100, Math.round(width + sub.priceScale('right').width()));
      el.style.width = target + 'px';
      const height = el.clientHeight;
      const options = sub.options();
      if (options.autoSize || options.width !== target || options.height !== height){
        sub.applyOptions({ autoSize: false, width: target, height });
      }
      if (range && Number.isFinite(range.from) && Number.isFinite(range.to) && range.to > range.from){
        const current = sub.timeScale().getVisibleLogicalRange();
        if (!current || Math.abs(current.from - range.from) > 1e-6 || Math.abs(current.to - range.to) > 1e-6){
          sub.timeScale().setVisibleLogicalRange(range);
        }
      }
    }
  } finally { _syncingRange = wasSyncing; }
}

function setSubPaneVisible(id, visible){
  const el = document.getElementById(id);
  const handle = document.querySelector(`.pane-resizer[data-pane="${id}"]`);
  // 先固定当前宽度，离屏时也保留有效尺寸；不让图表进入零宽度状态。
  if (!visible) el.style.width = Math.max(100, el.clientWidth) + 'px';
  el.classList.toggle('pane-off', !visible);
  el.setAttribute('aria-hidden', String(!visible));
  handle.hidden = !visible;
  const button = document.querySelector(`#overlayToggle button[data-ov="${id === 'volume' ? 'vol' : 'macd'}"]`);
  if (button) button.setAttribute('aria-pressed', String(visible));
  fitPaneHeights();
  alignPanes();
  schedulePaneLayout(); // 等主图的 autoSize 处理 flex 布局后，再对齐一次。
}

function savePaneHeights(){
  try { localStorage.setItem(PANE_HEIGHT_KEY, JSON.stringify(paneHeights)); } catch { /* 隐私模式 */ }
}

function resizeSubPane(id, height){
  const box = document.querySelector('.chart-box');
  const others = subPanes().filter(([otherId]) => otherId !== id && paneReady(otherId));
  const handlesHeight = [...box.querySelectorAll('.pane-resizer')]
    .filter(handle => !handle.hidden).reduce((sum, handle) => sum + handle.offsetHeight, 0);
  const otherHeight = others.reduce((sum, [otherId]) => sum + document.getElementById(otherId).offsetHeight, 0);
  const maxHeight = Math.max(PANE_MIN_HEIGHT, box.clientHeight - 160 - handlesHeight - otherHeight);
  paneHeights[id] = Math.round(Math.max(PANE_MIN_HEIGHT, Math.min(maxHeight, height)));
  fitPaneHeights();
  schedulePaneLayout();
}

function setupPaneLayout(){
  try {
    const stored = JSON.parse(localStorage.getItem(PANE_HEIGHT_KEY) || '{}');
    for (const id of Object.keys(PANE_DEFAULTS)){
      if (Number.isFinite(stored?.[id]) && stored[id] >= PANE_MIN_HEIGHT){
        paneHeights[id] = Math.min(2000, stored[id]);
      }
    }
  } catch { /* 无效存档或隐私模式：使用默认高度 */ }
  for (const handle of document.querySelectorAll('.pane-resizer')){
    const id = handle.dataset.pane;
    let drag = null;
    const finish = () => {
      if (!drag) return;
      drag = null;
      handle.classList.remove('dragging');
      document.body.classList.remove('resizing-pane');
      savePaneHeights();
      schedulePaneLayout();
    };
    handle.addEventListener('pointerdown', e => {
      if (e.button !== 0) return;
      e.preventDefault();
      handle.focus();
      drag = { pointerId: e.pointerId, y: e.clientY, height: document.getElementById(id).offsetHeight };
      handle.setPointerCapture(e.pointerId);
      handle.classList.add('dragging');
      document.body.classList.add('resizing-pane');
    });
    handle.addEventListener('pointermove', e => {
      if (!drag || drag.pointerId !== e.pointerId) return;
      resizeSubPane(id, drag.height + drag.y - e.clientY);
    });
    handle.addEventListener('pointerup', finish);
    handle.addEventListener('pointercancel', finish);
    handle.addEventListener('lostpointercapture', finish);
    window.addEventListener('blur', finish);
    handle.addEventListener('dblclick', () => {
      resizeSubPane(id, PANE_DEFAULTS[id]);
      savePaneHeights();
    });
    handle.addEventListener('keydown', e => {
      if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
      e.preventDefault();
      e.stopPropagation();
      resizeSubPane(id, document.getElementById(id).offsetHeight + (e.key === 'ArrowUp' ? 10 : -10));
      savePaneHeights();
    });
  }
  const observer = new ResizeObserver(schedulePaneLayout);
  for (const selector of ['.chart-box', '#chart', '#volume', '#macd']) observer.observe(document.querySelector(selector));
  fitPaneHeights();
  schedulePaneLayout();
}
