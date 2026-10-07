// js/01-config.js — 主题配色 / 页面状态 / 时间格式化 / 练习器状态

// TradingView 官方暗色/亮色调色板: 图表 canvas(经 t()) 与 CSS 变量(见 main.css)同源。
const THEMES = {
  dark:  { surface:'#1e222d', plane:'#131722', panel:'#2a2e39', ink:'#d1d4dc', ink2:'#b2b5be', muted:'#787b86', grid:'#1e222d', border:'#2a2e39', up:'#089981', down:'#f23645', top:'#ff9800', bottom:'#2962ff', segUp:'#26a69a', segDown:'#ef5350' },
  light: { surface:'#ffffff', plane:'#f0f3fa', panel:'#e0e3eb', ink:'#131722', ink2:'#434651', muted:'#787b86', grid:'#f0f3fa', border:'#e0e3eb', up:'#089981', down:'#f23645', top:'#ff9800', bottom:'#2962ff', segUp:'#26a69a', segDown:'#ef5350' },
};
// 买卖点色板: 保持原有语义(一买粉/二买绿/三买橙, 一卖青/二卖红/三卖紫), 换成 TradingView 色系
const BUY_COLORS = {
  dark:  { b1:'#e91e63', b2:'#089981', b3:'#ff9800' },
  light: { b1:'#e91e63', b2:'#089981', b3:'#ff9800' },
};
const SELL_COLORS = {
  dark:  { s1:'#00bcd4', s2:'#f23645', s3:'#9c27b0' },
  light: { s1:'#00bcd4', s2:'#f23645', s3:'#9c27b0' },
};
const FAST_COLORS = {
  dark:  { alert:'#ffb74d', buy:'#26a69a', sell:'#ff5722' },
  light: { alert:'#f57c00', buy:'#26a69a', sell:'#ff5722' },
};
const PANBEI_COLORS = {
  dark:  { buy:'#7e57c2', sell:'#f06292' },
  light: { buy:'#7e57c2', sell:'#ec407a' },
};
const FONT = '-apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, Ubuntu, "Segoe UI", system-ui, "PingFang SC", sans-serif';
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
let idxMap = {}, markerMap = {}, detailMap = {};   // 预置空对象: 首次 setData 触发的 crosshair 事件早于 buildMaps, 不预置会在事件里炸

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
  drill: '',          // 本局专项(空=随机): 突破失败/震荡反复止损/趋势持有/加仓后回撤
  capital0: 100000,   // 本局初始本金(开始练习时可配置, 默认10万U)
  marks: [],          // 本局信号K/观察标记 [{t, kind:'signal'|'watch', step}]
  desc: '',           // 题目描述(揭示时按本局走势生成): 强趋势/筑底/高位震荡...
  transition: '',     // 趋势转换(揭示时生成): 下跌 → 上涨 / 上涨 → 下跌 / ''
  durSec: 0,          // 本局用时(秒, 揭示/放弃时结算)
  curve: [],          // 本局净值曲线 [{step, eq, cf}] — cf=不加仓对照(保持原出场时点)
  addPnlRealized: 0,  // 已实现加仓贡献 = 实际盈亏 − 仅首笔同出场时点的盈亏
  hadAdds: false,     // 本局是否发生过加仓
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
