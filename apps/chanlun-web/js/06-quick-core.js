// js/06-quick-core.js — 快速下单的纯计算: 市场环境自动判定 + 入场信号自动建议 + 默认止损。
// Bar tuple 与 PracticeCore 一致: [UTC秒, open, high, low, close, volume|null]。
// 只使用传入数组(调用方保证截止当前K线), 不访问任何未来数据。
(function(root){
'use strict';
const VERSION = 'quick-order/1';
// 阈值集中在这里, 需要时直接调
const CFG = {
  ma120FlatPct: 0.3,      // MA120 近5根变化率绝对值 ≤ 此值视为走平(%)
  ma120Lookback: 5,
  bigBarAtrMult: 1.2,     // 大阳/大阴线: 实体 ≥ 1.2×ATR14 (或涨跌幅 ≥ 3%)
  bigBarPct: 0.03,
  bigBarBodyRatio: 0.55,  // 大阳/大阴线: 实体占比 ≥ 55%
  rangeLookback: 20,      // 震荡区间统计窗口(根)
  rangeMaxEff: 0.45,      // 区间效率上限(低效率=震荡箱体)
  rangeEdgeRatio: 0.25,   // 触及区间上下 25% 边界视为反转测试位
  swingLookback: 20,      // 前一个波段低/高点: 向前最多找 20 根
  atrStopMult: 1.5,
};
const sma = (cs, p, end = cs.length) => {
  if (end < p) return null;
  let s = 0; for (let k = end - p; k < end; k++) s += cs[k][4];
  return s / p;
};
function atr14(cs){
  if (cs.length < 15) return null;
  let s = 0;
  for (let k = cs.length - 14; k < cs.length; k++){
    const x = cs[k], pc = cs[k-1][4];
    s += Math.max(x[2]-x[3], Math.abs(x[2]-pc), Math.abs(x[3]-pc));
  }
  return s / 14;
}
// 市场环境: { ma120dir:'MA120向上'|'MA120向下'|'MA120走平'|null, pricepos:'价格在MA120以上'|'价格在MA120以下'|null }
function market(cs){
  const out = { ma120dir:null, pricepos:null, ma120:null, change5Pct:null };
  if (!Array.isArray(cs) || !cs.length) return out;
  const n = cs.length, b = cs[n-1];
  const now = sma(cs, 120), prior = sma(cs, 120, n - CFG.ma120Lookback);
  out.ma120 = now;
  if (now != null) out.pricepos = b[4] >= now ? '价格在MA120以上' : '价格在MA120以下';
  if (now != null && prior != null && prior > 0){
    const ch = 100 * (now / prior - 1);
    out.change5Pct = ch;
    out.ma120dir = ch > CFG.ma120FlatPct ? 'MA120向上' : ch < -CFG.ma120FlatPct ? 'MA120向下' : 'MA120走平';
  }
  return out;
}
// 入场信号建议: 与右侧 4 项入场信号严格对齐
//   1) 均线突破  2) 大阳线后的顺势短线多  3) 大阴线后的顺势短线空  4) 震荡区间的反转
function isStrongBar(bar, dir, atr){
  const [, o, h, l, c] = bar;
  const rng = h - l;
  const body = (c - o) * dir;
  if (!(rng > 0) || !(body > 0)) return false;
  if (body / rng < CFG.bigBarBodyRatio) return false;
  const byAtr = atr != null && atr > 0 && body >= CFG.bigBarAtrMult * atr;
  const byPct = o > 0 && body / o >= CFG.bigBarPct;
  return byAtr || byPct;
}
function signals(cs, side = '多', extra = {}){
  const out = [];
  if (!Array.isArray(cs) || cs.length < 2) return out;
  const n = cs.length, b = cs[n-1], pb = cs[n-2];
  const [, o, h, l, c] = b;
  const range = h - l, body = Math.abs(c - o);
  const atr = atr14(cs);
  const long = side !== '空';

  // 1. 均线突破(多: 下穿转上穿 MA20/60/120; 空: 上穿转下破 MA20/60/120)
  for (const p of [20, 60, 120]){
    const m = sma(cs, p), mp = sma(cs, p, n - 1);
    if (m == null || mp == null) continue;
    const hit = long ? (pb[4] < mp && c > m) : (pb[4] > mp && c < m);
    if (hit){ out.push('均线突破'); break; }
  }

  // 2 & 3. 大阳线后的顺势短线多 / 大阴线后的顺势短线空
  if (long){
    const curBig = isStrongBar(b, 1, atr);
    const prevBig = isStrongBar(pb, 1, atr) && c >= (pb[1] + pb[4]) / 2 && c >= pb[1];
    if (curBig || prevBig) out.push('大阳线后的顺势短线多');
  } else {
    const curBig = isStrongBar(b, -1, atr);
    const prevBig = isStrongBar(pb, -1, atr) && c <= (pb[1] + pb[4]) / 2 && c <= pb[1];
    if (curBig || prevBig) out.push('大阴线后的顺势短线空');
  }

  // 4. 震荡区间的反转(前窗箱体震荡 + 触及边界反向拒绝/吞没, 或外部传入背离信号)
  let rangeRev = !!extra.macdDivergence;
  if (!rangeRev && n >= 12){
    const winStart = Math.max(0, n - 1 - CFG.rangeLookback);
    const pre = cs.slice(winStart, n - 1);
    if (pre.length >= 8){
      let hi = -Infinity, lo = Infinity, path = 0;
      for (let k = 0; k < pre.length; k++){
        const x = pre[k];
        if (x[2] > hi) hi = x[2];
        if (x[3] < lo) lo = x[3];
        if (k > 0) path += Math.abs(x[4] - pre[k - 1][4]);
      }
      const span = hi - lo;
      const net = Math.abs(pre[pre.length - 1][4] - pre[0][4]);
      const isBox = span > 0 && (path > 0 ? net / path <= CFG.rangeMaxEff : true);
      if (isBox && range > 0){
        const lowerWick = Math.min(o, c) - l;
        const upperWick = h - Math.max(o, c);
        const pBody = Math.abs(pb[4] - pb[1]);
        if (long){
          const nearBottom = l <= lo + span * CFG.rangeEdgeRatio;
          const pin = lowerWick / range >= 0.45 && c >= o;
          const bounce = c > o && (c - l) / range >= 0.65 && body / range >= 0.35;
          const engulf = c > o && pb[4] < pb[1] && c >= pb[1] && body > pBody;
          if (nearBottom && (pin || bounce || engulf)) rangeRev = true;
        } else {
          const nearTop = h >= hi - span * CFG.rangeEdgeRatio;
          const pin = upperWick / range >= 0.45 && c <= o;
          const drop = c < o && (h - c) / range >= 0.65 && body / range >= 0.35;
          const engulf = c < o && pb[4] > pb[1] && c <= pb[1] && body > pBody;
          if (nearTop && (pin || drop || engulf)) rangeRev = true;
        }
      }
    }
  }
  if (rangeRev) out.push('震荡区间的反转');
  return out;
}
// 默认止损: 'bar' 本根极值 / 'swing' 前一个波段极值 / 'atr' 入场 ∓ 1.5ATR。无效(不在不利方向)返回 null。
function stop(cs, side = '多', method = 'bar', entryPx = null){
  if (!Array.isArray(cs) || !cs.length) return null;
  const n = cs.length, b = cs[n-1], long = side !== '空';
  const entry = Number.isFinite(entryPx) && entryPx > 0 ? entryPx : b[4];
  let v = null;
  if (method === 'bar') v = long ? b[3] : b[2];
  else if (method === 'atr'){ const a = atr14(cs); v = a ? (long ? entry - CFG.atrStopMult * a : entry + CFG.atrStopMult * a) : null; }
  else if (method === 'swing'){
    // 最近一个确认的分型极值(左右各 2 根), 不含当前根; 找不到时退回区间极值
    const from = Math.max(2, n - 1 - CFG.swingLookback);
    for (let k = n - 3; k >= from; k--){
      const x = cs[k], L = [cs[k-1], cs[k-2]], R = [cs[k+1], cs[k+2]];
      if (!R[1]) continue;
      const ok = long ? [...L, ...R].every(y => y[3] > x[3]) : [...L, ...R].every(y => y[2] < x[2]);
      const px = long ? x[3] : x[2];
      if (ok && (long ? px < entry : px > entry)){ v = px; break; }
    }
    if (v == null){
      const part = cs.slice(Math.max(0, n - 1 - CFG.swingLookback), n);
      v = long ? Math.min(...part.map(x => x[3])) : Math.max(...part.map(x => x[2]));
    }
  }
  if (!Number.isFinite(v) || v <= 0) return null;
  return (long ? v < entry : v > entry) ? v : null;
}
function stops(cs, side = '多', entryPx = null){
  return {
    bar: stop(cs, side, 'bar', entryPx),
    swing: stop(cs, side, 'swing', entryPx),
    atr: stop(cs, side, 'atr', entryPx),
  };
}
// 估算止损时亏损: 保证金×杠杆×价差%
function riskAt(entry, stopPx, side, margin, leverage){
  if (!(entry > 0) || !(stopPx > 0)) return null;
  const dir = side === '空' ? -1 : 1;
  return (stopPx - entry) / entry * dir * leverage * margin;
}
const api = { VERSION, CFG, market, signals, stop, stops, riskAt, atr14 };
root.QuickCore = api;
if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
