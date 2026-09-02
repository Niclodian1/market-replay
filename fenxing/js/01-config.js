// js/01-config.js — 主题配色 / 页面状态 / 时间格式化 / 练习器状态

const THEMES = {
  dark:  { surface:'#1a1a19', plane:'#0d0d0d', ink:'#ffffff', ink2:'#c3c2b7', muted:'#898781', grid:'#2c2c2a', border:'#383835', up:'#0ca30c', down:'#e66767', top:'#9085e9', bottom:'#3987e5', segUp:'#199e70', segDown:'#d95926' },
  light: { surface:'#fcfcfb', plane:'#f9f9f7', ink:'#0b0b0b', ink2:'#52514e', muted:'#898781', grid:'#e1e0d9', border:'#c3c2b7', up:'#008300', down:'#e34948', top:'#4a3aa7', bottom:'#2a78d6', segUp:'#1baf7a', segDown:'#eb6834' },
};
const BUY_COLORS = {
  dark:  { b1:'#d55181', b2:'#008300', b3:'#c98500' },
  light: { b1:'#e87ba4', b2:'#008300', b3:'#eda100' },
};
const SELL_COLORS = {
  dark:  { s1:'#0094a6', s2:'#e5484d', s3:'#9c5fff' },
  light: { s1:'#007a9e', s2:'#c03028', s3:'#7a3fe0' },
};
const FAST_COLORS = {
  dark:  { alert:'#ad7a00', buy:'#0f9d5c', sell:'#e85f2a' },
  light: { alert:'#b07a00', buy:'#00845f', sell:'#d95400' },
};
const PANBEI_COLORS = {
  dark:  { buy:'#7a5cd6', sell:'#d6685c' },
  light: { buy:'#6a4cc6', sell:'#c05a50' },
};
const FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, "PingFang SC", sans-serif';
const t = () => THEMES[document.documentElement.dataset.theme];
const themeKey = () => document.documentElement.dataset.theme;

// ---- 状态 ----
let activeTF = '1D';
let activeView = 'raw';
let pivotMode = 'seg';            // 'seg' 线段中枢 | 'stroke' 笔中枢
let strokeStd = 'new';            // 'new' 新笔 | 'old' 老笔 — 笔口径, 线段/中枢/买卖点整链随动
const layers = { fractals:false, strokes:false, segments:false, pivots:false, buys:false, sells:false, trades:false, fast:false, panbei:false };
let tradeMode = 'stroke';   // 回测信号级别: stroke 笔 | segment 线段
let tradeCfg  = 'enter';    // 回测口径(已定稿: A段=进入段, 中枢延伸=α)
let selectedTrade = null;   // 当前选中的交易(图表高亮 + 列表选中态)
let idxMap, markerMap, detailMap;

// ---- 时间格式化(UTC, 与数据一致) ----
const p2 = n => String(n).padStart(2,'0');
function fmtTimeReal(ts){
  if (ts == null) return '—';
  const d = new Date(ts * 1000);
  if (activeTF === '1D') return `${d.getUTCFullYear()}-${p2(d.getUTCMonth()+1)}-${p2(d.getUTCDate())}`;
  return `${d.getUTCFullYear()}-${p2(d.getUTCMonth()+1)}-${p2(d.getUTCDate())} ${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}`;
}
// ---- 盘感练习器状态(盲测态所有时间显示走 trLabel 相对根序号, 不泄露绝对日期) ----
const TR = {
  active: false,      // 练习模式总开关
  revealed: false,    // 是否已揭示答案
  sym: null, tf: null,
  startIdx: 0,        // 随机起点在该周期完整K线序列中的下标
  startTs: null,      // 起点K线时间戳(揭示后"定位起点"按钮用)
  startWallTs: null,  // 本局开始墙上时间(历史存档用)
  endCapital: 0,      // 本局终值(揭示/放弃时结算)
  endReason: '',      // 结束原因: 步数走满 / 手动揭示 / 账户爆仓
  step: 0,            // 已向后走的根数
  maxStep: 200,       // 步数上限, 走满强制揭示
  LOOKBACK: 200,      // 起点前展示的历史根数(视口锁定宽度)
  session: [],        // 本局已平仓交易记录(journal rec 引用), 揭示时入账
};
const trLocked = () => TR.active && !TR.revealed;
function trLabel(ts){
  if (chunkedActive() && ChunkStore.win){
    const w = ChunkStore.win;
    const pos = lowerBoundTime(w.times, ts);
    const off = (w.baseIdx + pos) - TR.startIdx;
    return off >= 0 ? `T+${off}` : `T-${-off}`;
  }
  const times = L().candles.map(c => c[0]);
  let lo = 0, hi = times.length - 1, pos = 0;
  while (lo <= hi){
    const mid = (lo + hi) >> 1;
    if (times[mid] <= ts){ pos = mid; lo = mid + 1; } else hi = mid - 1;
  }
  const off = pos - TR.startIdx;   // 负=窗口内过去 / 0=随机起点 / 正=已走步数
  return off >= 0 ? `T+${off}` : `T-${-off}`;
}
function fmtTime(ts){
  if (ts == null) return '—';
  if (trLocked()) return trLabel(ts);
  return fmtTimeReal(ts);
}
