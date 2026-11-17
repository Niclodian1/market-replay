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
  pullbackNearATR: 0.3,   // 回踩: 最低价不高于均线 + 0.3×ATR
  pullbackDeepATR: 0.5,   // 回踩: 最低价不低于均线 − 0.5×ATR(太深算跌破)
  pinWick: 0.6,           // Pinbar: 影线 ≥ 全长 60%
  pinBody: 0.3,           // Pinbar: 实体 ≤ 全长 30%
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
// 入场信号建议: 返回 data-val 数组(与右侧标签同名)。均线类只对做多给建议; K线形态多空镜像。
function signals(cs, side = '多', extra = {}){
  const out = [];
  if (!Array.isArray(cs) || cs.length < 2) return out;
  const n = cs.length, b = cs[n-1], pb = cs[n-2];
  const [, o, h, l, c] = b;
  const range = h - l, body = Math.abs(c - o);
  const atr = atr14(cs);
  const long = side !== '空';
  if (long){
    const crossed = {};
    for (const p of [20, 60, 120]){
      const m = sma(cs, p), mp = sma(cs, p, n - 1);
      if (m == null || mp == null) continue;
      crossed[p] = pb[4] < mp && c > m;
      if (crossed[p]) out.push('突破MA' + p);
      else if (atr && pb[4] >= mp && c > m && l <= m + CFG.pullbackNearATR * atr && l >= m - CFG.pullbackDeepATR * atr)
        out.push('回踩MA' + p);
    }
    if (crossed[20] && crossed[60]) out.push('MA20与MA60双均线突破');
  }
  if (range > 0){
    const upper = h - Math.max(o, c), lower = Math.min(o, c) - l;
    const wick = long ? lower : upper;
    if (wick / range >= CFG.pinWick && body / range <= CFG.pinBody) out.push('Pinbar');
  }
  const pBody = Math.abs(pb[4] - pb[1]);
  const engulf = long
    ? (pb[4] < pb[1] && c > o && o <= pb[4] && c >= pb[1] && body > pBody)
    : (pb[4] > pb[1] && c < o && o >= pb[4] && c <= pb[1] && body > pBody);
  if (engulf) out.push('吞没');
  if (extra.macdDivergence) out.push('MACD背离');
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
