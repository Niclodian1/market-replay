// js/08-draw.js — 手绘线: TradingView 风格画线组件 (lightweight-charts-drawing v0.2.5, MIT)
// 工具: 选择(编辑) / 趋势线 trend-line / 水平线 horizontal-line / 斐波那契回撤 fib-retracement / 折线 polyline
// 交互: 点击落点(TV 规则), 选中后可整体拖动 / 拖锚点 / Delete 删除 / Esc 或右键取消; 双击结束折线。
// 持久化: localStorage chan_drawings_v2 存 DrawingManager.exportJSON();
//         首次运行自动迁移 v1 旧数据(趋势线 trend / 支撑阻力 sandr), 迁移后 v1 保留可回滚。
// 兼容: 组件按 LW v5 编写, v4.2 缺 series.getPane() — 用垫片补齐; 未来升 v5 后自动跳过。

const DRAW_KEY_V2 = 'chan_drawings_v2';
const DRAW_KEY_V1 = 'chan_drawings_v1';
// 工具条 data-tool ↔ 组件 kind 双向映射
const DRAW_TOOL_KIND = { trend: 'trend-line', sandr: 'horizontal-line', fib: 'fib-retracement', poly: 'polyline' };
const DRAW_KIND_TOOL = { 'trend-line': 'trend', 'horizontal-line': 'sandr', 'fib-retracement': 'fib', 'polyline': 'poly' };
const DRAW_TOOL_LABEL = { none: '选择', trend: '趋势线', sandr: '水平线', fib: '斐波那契', poly: '折线' };

let drawingMgr = null;
let drawSaveTimer = null;

function paintDrawButtons(tool){
  document.querySelectorAll('#drawToggle button').forEach(b =>
    b.classList.toggle('active', (b.dataset.tool || 'none') === tool));
}
function saveDrawingsNow(){
  if (!drawingMgr) return;
  try { localStorage.setItem(DRAW_KEY_V2, drawingMgr.exportJSON()); } catch (e) { /* 隐私模式/超配额: 仅本次会话保留 */ }
}
function scheduleSaveDrawings(){
  clearTimeout(drawSaveTimer);
  drawSaveTimer = setTimeout(saveDrawingsNow, 250);
}
// v1 旧画线 → 组件绘画模型
function migrateDrawingsV1(){
  let old = [];
  try { old = JSON.parse(localStorage.getItem(DRAW_KEY_V1)) || []; } catch { old = []; }
  const cs = candlesFor(activeView);
  const t0 = cs.length ? cs[Math.max(0, cs.length - 1)].time : Math.floor(Date.now() / 1000);
  let n = 0;
  for (const r of old){
    if (r.type === 'trend' && r.t1 != null && r.t2 != null){
      drawingMgr.add({
        kind: 'trend-line',
        points: [{ time: r.t1, price: r.p1 }, { time: r.t2, price: r.p2 }],
        style: { color: r.color || '#2962ff' },
      });
      n++;
    } else if (r.type === 'sandr' && r.p != null){
      drawingMgr.add({
        kind: 'horizontal-line',
        points: [{ time: t0, price: r.p }],
        style: { color: r.color || '#ff9800' },
      });
      n++;
    }
  }
  saveDrawingsNow();   // 写入 v2 空/迁移结果, 保证只迁移一次
  return n;
}
function restoreDrawings(){
  const raw = localStorage.getItem(DRAW_KEY_V2);
  if (raw != null){
    try { drawingMgr.importJSON(raw); return 0; }
    catch (e){
      console.warn('[画线] v2 数据损坏, 尝试旧数据迁移', e);
      try { drawingMgr.clear(); } catch {}
    }
  }
  return migrateDrawingsV1();
}
// LW v4.2 兼容垫片: 组件用 v5 的 series.getPane().paneIndex()/getHTMLElement() 计算画布尺寸与命中区
function shimDrawingPane(series, el){
  if (typeof series.getPane === 'function') return;   // v5+ 原生支持
  const pane = { paneIndex: () => 0, getHTMLElement: () => el };
  series.getPane = () => pane;
}
function setupDrawings(){
  if (typeof LightweightChartsDrawing === 'undefined'){
    console.error('[画线] lightweight-charts-drawing 未加载, 画线功能不可用');
    return;
  }
  const chartEl = document.getElementById('chart');
  shimDrawingPane(candleSeries, chartEl);
  drawingMgr = new LightweightChartsDrawing.DrawingManager(chart, candleSeries, {
    magnet: 'off',           // 关闭磁吸: 落点/拖拽完全跟手, 不吸附 OHLC
    fontFamily: FONT,
  });
  // 数据变化即持久化(拖动过程防抖; 手势结束/页面隐藏立即落盘)
  drawingMgr.on('change', scheduleSaveDrawings);
  drawingMgr.on('gestureEnd', saveDrawingsNow);
  window.addEventListener('pagehide', saveDrawingsNow);
  // 落点完成后组件自动解除工具回到选择态 → 工具条同步高亮
  drawingMgr.on('tool', kind => paintDrawButtons(kind ? (DRAW_KIND_TOOL[kind] || 'none') : 'none'));
  const nMigrated = restoreDrawings();
  // 工具条
  document.querySelectorAll('#drawToggle button').forEach(b =>
    b.addEventListener('click', () => {
      const tool = b.dataset.tool || 'none';
      drawingMgr.setTool(DRAW_TOOL_KIND[tool] || null);
      paintDrawButtons(tool);
      flashBtn('snapshotBtn', tool === 'none'
        ? '画线: 选择/编辑模式'
        : `画线: ${DRAW_TOOL_LABEL[tool] || tool}`);
    }));
  document.getElementById('drawClear').addEventListener('click', () => {
    drawingMgr.clear();
    saveDrawingsNow();
    paintDrawButtons('none');
    flashBtn('snapshotBtn', '线条已清空');
  });
  // 右键取消画线(组件本身只处理 Esc): 与 Esc 同语义 —
  // 工具进行中 → 清掉未完成落点并退出工具回到选择态; 已有选中 → 取消选中; 空闲 → 保留原生右键菜单。
  chartEl.addEventListener('contextmenu', e => {
    const tool = drawingMgr.tool();
    const hasSel = drawingMgr.selection().length > 0;
    if (!tool && !hasSel) return;
    e.preventDefault();
    if (tool){
      drawingMgr.setTool(null);   // 清 pending/cursor + 退出工具(内部自动重绘, tool 事件同步按钮)
      paintDrawButtons('none');
      flashBtn('snapshotBtn', '已取消画线');
    } else {
      drawingMgr.select([]);
      flashBtn('snapshotBtn', '已取消选中');
    }
  });
  if (nMigrated > 0) flashBtn('snapshotBtn', `旧画线已迁移 ${nMigrated} 条`);
}
try { setupDrawings(); } catch (e) {
  console.error('[画线] 初始化失败', e);
  if (typeof flashBtn === 'function') flashBtn('snapshotBtn', '⚠ 画线组件初始化失败');
}
