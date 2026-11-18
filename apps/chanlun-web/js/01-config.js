// js/01-config.js — 主题配色 / 页面状态 / 时间格式化 / 练习器状态

// 专业量化终端调色板: 图表 canvas(经 t()) 与 CSS 变量(见 css/main.css)严格同源。
const THEMES = {
  dark:  { surface:'#111620', plane:'#0b0e14', panel:'#1c2331', ink:'#e6edf7', ink2:'#a9b4c7', muted:'#6b7689', grid:'#141923', border:'#1f2736', up:'#0d9f86', down:'#ef4452', top:'#f59e0b', bottom:'#3b82f6', segUp:'#26a69a', segDown:'#ef5350' },
  light: { surface:'#ffffff', plane:'#f8fafc', panel:'#e8eef6', ink:'#0f172a', ink2:'#334155', muted:'#64748b', grid:'#edf1f7', border:'#e2e8f0', up:'#089981', down:'#e11d48', top:'#d97706', bottom:'#2563eb', segUp:'#0d9488', segDown:'#e11d48' },
};
// 买卖点色板: 保持原有语义(一买粉/二买绿/三买橙, 一卖青/二卖红/三卖紫)与像素校验兼容色。
const BUY_COLORS = {
  dark:  { b1:'#e91e63', b2:'#0d9f86', b3:'#f59e0b' },
  light: { b1:'#db2777', b2:'#089981', b3:'#d97706' },
};
const SELL_COLORS = {
  dark:  { s1:'#06b6d4', s2:'#ef4452', s3:'#9c27b0' },
  light: { s1:'#0891b2', s2:'#e11d48', s3:'#9333ea' },
};
const FAST_COLORS = {
  dark:  { alert:'#fbbf24', buy:'#26a69a', sell:'#fb7185' },
  light: { alert:'#d97706', buy:'#0d9488', sell:'#e11d48' },
};
const PANBEI_COLORS = {
  dark:  { buy:'#8b5cf6', sell:'#f472b6' },
  light: { buy:'#7c3aed', sell:'#db2777' },
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

// ---- 乖离档位(MA20/MA60 乖离, 止盈参考) ----
// 绝对档按 BTC 日线回测校准(2026-10); 分位档取该品种自身滚动窗口分位 → 适配全品种/全周期。
// 一档=减1/3(MA60≥b60Confirm 可减半); 二档=再减1/3; 回补=减仓现金分批买回; 尾仓=收盘破MA20离场。
const BIAS_CFG = {
  l1: 20, l2: 30, rb: -20,        // 绝对档: 一档/二档/恐慌回补 (%)
  p1: 0.97, p2: 0.99, pr: 0.02,   // 分位档: 一档/二档/回补 (滚动窗口内分位)
  b60Confirm: 30,                 // MA60乖离≥该值时, 一档建议减半
  lookback: { '1D': 365, '4H': 365 * 6, '1H': 365 * 24, '15F': 365 * 96 },  // 约1年根数
};
