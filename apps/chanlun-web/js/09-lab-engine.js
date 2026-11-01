// js/09-lab-engine.js — 参数实验室计算内核(纯函数, 无 DOM / 无全局依赖)
//
// 口径说明见 js/09-params-lab.js 头部。这里只做计算, 便于 Node 端单测:
//   merge / fractals / alternate / strokes / segments / pivots / divergence / trigger / buyPoints / compute
//
// 因果性: compute() 只使用传进来的 K 线数组(前端调用方传入 candlesFor('raw'),
// 复盘态该数组已按 replayT 截断), 所有信号点的时间 = 触发那根 K 线的时间。
(function (root) {
  'use strict';

  function ema(vals, period) {
    const k = 2 / (period + 1);
    let e = null;
    const out = new Array(vals.length);
    for (let i = 0; i < vals.length; i++) { e = e == null ? vals[i] : vals[i] * k + e * (1 - k); out[i] = e; }
    return out;
  }

  function macd(closes, fast, slow, signal) {
    const ef = ema(closes, fast), es = ema(closes, slow);
    const diff = closes.map((_, i) => ef[i] - es[i]);
    const dea = ema(diff, signal);
    return { diff, dea, hist: diff.map((d, i) => d - dea[i]) };
  }

  // 包含处理: 向上取高高、向下取低低; 输出 bars 只保留 {i0,i1,high,low}
  function merge(cs) {
    const out = [];
    let dir = 1;
    for (let i = 0; i < cs.length; i++) {
      const b = cs[i];
      if (!out.length) { out.push({ i0: i, i1: i, high: b.high, low: b.low }); continue; }
      const last = out[out.length - 1];
      const inside = (b.high <= last.high && b.low >= last.low) || (b.high >= last.high && b.low <= last.low);
      if (inside) {
        if (dir === 1) { last.high = Math.max(last.high, b.high); last.low = Math.max(last.low, b.low); }
        else { last.high = Math.min(last.high, b.high); last.low = Math.min(last.low, b.low); }
        last.i1 = i;
      } else {
        dir = b.high > last.high ? 1 : -1;
        out.push({ i0: i, i1: i, high: b.high, low: b.low });
      }
    }
    return out;
  }

  // 分型: 三根合并K严格比较; confirmWin>0 时要求窗口内有收盘价反向突破中K极值
  function fractals(merged, cs, confirmWin) {
    const out = [];
    const confirm = (m, kind) => {
      if (confirmWin <= 0) return m.i1;
      for (let j = m.i1 + 1; j < Math.min(m.i1 + 1 + confirmWin, cs.length); j++) {
        if (kind === 'T' && cs[j].close < m.low) return j;
        if (kind === 'B' && cs[j].close > m.high) return j;
      }
      return -1;
    };
    for (let i = 1; i < merged.length - 1; i++) {
      const a = merged[i - 1], m = merged[i], c = merged[i + 1];
      const isT = m.high > a.high && m.high > c.high && m.low > a.low && m.low > c.low;
      const isB = m.low < a.low && m.low < c.low && m.high < a.high && m.high < c.high;
      if (!isT && !isB) continue;
      const kind = isT ? 'T' : 'B';
      const cIdx = confirm(m, kind);
      if (cIdx >= 0) out.push({ i, kind, cIdx });
    }
    return out;
  }

  function alternate(merged, fr) {
    const seq = [];
    for (const f of fr) {
      const prev = seq[seq.length - 1];
      if (prev && prev.kind === f.kind) {
        const better = f.kind === 'T'
          ? merged[f.i].high > merged[prev.i].high
          : merged[f.i].low < merged[prev.i].low;
        if (better) seq[seq.length - 1] = f;
      } else seq.push(f);
    }
    return seq;
  }

  function extreme(merged, m, cs, kind) {
    let bi = m.i0, bv = kind === 'T' ? cs[m.i0].high : cs[m.i0].low;
    for (let j = m.i0; j <= m.i1; j++) {
      const v = kind === 'T' ? cs[j].high : cs[j].low;
      if (kind === 'T' ? v > bv : v < bv) { bv = v; bi = j; }
    }
    return { idx: bi, price: bv, time: cs[bi].time };
  }

  // 笔: 贪心连笔, 间隔不足的分型跳过; 输出首尾相接、方向严格交替
  function strokes(merged, cs, seq, minGap, opts) {
    const priceCheck = !(opts && opts.priceCheck === false);
    const st = [];
    let anchor = null;
    for (const f of seq) {
      if (!anchor) { anchor = f; continue; }
      // 同类分型(行情越过链末端继续创新极值): 笔随极值延伸, 再把链末端前移
      if (f.kind === anchor.kind) {
        const ea = extreme(merged, merged[anchor.i], cs, anchor.kind);
        const eb = extreme(merged, merged[f.i], cs, f.kind);
        const better = f.kind === 'T' ? eb.price > ea.price : eb.price < ea.price;
        if (!better) continue;
        const last = st[st.length - 1];
        if (last && last.eIdx === anchor.i) {
          last.eIdx = f.i; last.eRaw = eb.idx; last.pE = eb.price; last.tE = eb.time; last.eC = f.cIdx;
        }
        anchor = f;
        continue;
      }
      if (f.i - anchor.i < minGap) continue;
      const ea = extreme(merged, merged[anchor.i], cs, anchor.kind);
      const eb = extreme(merged, merged[f.i], cs, f.kind);
      // 笔必须真的创出极值(与 Python 引擎同口径): 向上笔要求顶的 high 高过「底分型所在
      // 合并K的 high」, 向下笔要求底的 low 低过「顶分型所在合并K的 low」。
      // 注意参照物是合并K的 high/low 而不是分型极值本身, 否则宽幅合并K会被过度切笔。
      if (priceCheck && (f.kind === 'T' ? eb.price <= merged[anchor.i].high : eb.price >= merged[anchor.i].low)) continue;
      st.push({
        sIdx: anchor.i, eIdx: f.i, dir: f.kind === 'T' ? 1 : -1,
        pS: ea.price, pE: eb.price, tS: ea.time, tE: eb.time,
        sRaw: ea.idx, eRaw: eb.idx, sC: anchor.cIdx, eC: f.cIdx,
      });
      anchor = f;
    }
    return st;
  }

  // 线段(近似): ≥3笔; 同向笔创新极值延伸; 反向笔破段起点则结束
  function segments(st) {
    const segs = [];
    let i = 0;
    while (i + 2 < st.length) {
      const dir = st[i].dir;
      let end = i + 2, ext = st[i + 2].pE, j = i + 3;
      while (j < st.length) {
        const s = st[j];
        if (s.dir === dir) {
          const beyond = dir === 1 ? s.pE > ext : s.pE < ext;
          if (!beyond) break;
          ext = s.pE; end = j; j++;
        } else {
          const brk = dir === 1 ? s.pE < st[i].pS : s.pE > st[i].pS;
          if (brk) break;
          j++;
        }
      }
      segs.push({
        sIdx: i, eIdx: end, dir, pS: st[i].pS, pE: st[end].pE,
        tS: st[i].tS, tE: st[end].tE, sRaw: st[i].sRaw, eRaw: st[end].eRaw,
      });
      i = end + 1;
    }
    return segs;
  }

  // 中枢: 连续三段重叠 [ZD,ZG], 重叠比 ≥ minRatio; 后续段仍重叠则延伸
  function pivots(legs, minRatio) {
    const out = [];
    let i = 0;
    while (i + 2 < legs.length) {
      const hi = [], lo = [];
      for (let k = 0; k < 3; k++) {
        const L = legs[i + k];
        hi.push(Math.max(L.pS, L.pE)); lo.push(Math.min(L.pS, L.pE));
      }
      const zg = Math.min(...hi), zd = Math.max(...lo);
      const hh = Math.max(...hi), ll = Math.min(...lo);
      const ratio = hh > ll ? (zg - zd) / (hh - ll) : 0;
      if (zg > zd && ratio >= minRatio) {
        let e = i + 2;
        while (e + 1 < legs.length) {
          const L = legs[e + 1];
          const nh = Math.max(L.pS, L.pE), nl = Math.min(L.pS, L.pE);
          if (Math.min(nh, zg) > Math.max(nl, zd)) e++; else break;
        }
        let gg = -Infinity, dd = Infinity;
        for (let k = i; k <= e; k++) { gg = Math.max(gg, legs[k].pS, legs[k].pE); dd = Math.min(dd, legs[k].pS, legs[k].pE); }
        out.push({ sIdx: i, eIdx: e, zd, zg, gg, dd, tS: legs[i].tS, tE: legs[e].tE, ratio });
        i = e + 1;
      } else i++;
    }
    return out;
  }

  // 背驰: 同向两段(隔一段反向)比较柱面积/峰值; 价格创新低(新高)且比值 < ratio
  function divergence(legs, cs, hist, ratio, mode) {
    const out = [];
    const stat = (a, b) => {
      let area = 0, peak = 0;
      for (let i = Math.max(0, a); i <= Math.min(cs.length - 1, b); i++) {
        const h = Math.abs(hist[i] || 0);
        area += h; if (h > peak) peak = h;
      }
      return { area, peak };
    };
    for (let k = 2; k < legs.length; k++) {
      if (legs[k].dir !== legs[k - 2].dir) continue;
      const prev = legs[k - 2], cur = legs[k];
      const newExt = cur.dir === -1 ? cur.pE < prev.pE : cur.pE > prev.pE;
      if (!newExt) continue;
      const A = stat(prev.sRaw, prev.eRaw), B = stat(cur.sRaw, cur.eRaw);
      const r = mode === 'peak'
        ? (A.peak > 0 ? B.peak / A.peak : 1)
        : (A.area > 0 ? B.area / A.area : 1);
      if (r < ratio) {
        out.push({
          legIdx: k, prevIdx: k - 2, kind: cur.dir === -1 ? 'bottom' : 'top', ratio: r,
          t: cur.tE, p: cur.pE, prevT: prev.tE, prevP: prev.pE, barIdx: cur.eRaw,
        });
      }
    }
    return out;
  }

  // 触发柱: shrink=绿柱首次缩短 | stop=缩短后首次不再缩短 | flip=柱体首次翻红
  function trigger(cs, hist, fromIdx, p) {
    const win = Math.max(1, p.trigWindow | 0);
    const end = Math.min(cs.length - 1, fromIdx + win);
    for (let i = fromIdx + 1; i <= end; i++) {
      const h = hist[i], hp = hist[i - 1], hpp = hist[i - 2];
      if (p.trigger === 'flip') {
        if (h > 0 && hp <= 0) return i;
      } else if (p.trigger === 'shrink') {
        if (h < 0 && hp < 0 && h > hp) return i;
      } else {
        if (h < 0 && hp < 0 && Math.abs(h) >= Math.abs(hp) && hpp < 0 && Math.abs(hp) < Math.abs(hpp)) return i;
      }
    }
    return -1;
  }

  function buyPoints(st, pivs, cs, hist, divs, p) {
    const priceAt = (i) => (p.priceMode === 'low' ? cs[i].low : cs[i].close);
    const buys = [];
    for (const d of divs) {
      if (d.kind !== 'bottom') continue;
      const bi = trigger(cs, hist, d.barIdx, p);
      if (bi < 0) continue;
      const price = priceAt(bi);
      if (price < d.p * 0.9) continue;
      buys.push({
        kind: '一买', price, time: cs[bi].time, barIdx: bi,
        anchor: d.p, anchorTime: d.t, ratio: d.ratio, refT: d.prevT, refP: d.prevP, zone: null,
      });
    }
    for (const b of buys) {
      if (b.kind !== '一买') continue;
      const upIdx = st.findIndex(s => s.dir === 1 && s.eRaw >= b.barIdx && s.eRaw > s.sRaw);
      if (upIdx < 0) continue;
      const down = st[upIdx + 1];
      if (!down || down.dir !== -1) continue;
      // 护栏用【背驰结构低点】而不是触发柱收盘: 二买只要求回调不破前低,
      // 用收盘价当护栏会把绝大多数二买误杀。
      const floor = b.anchor;
      if (down.pE <= floor) continue;
      if (down.eRaw <= b.barIdx) continue;
      buys.push({
        kind: '二买', price: down.pE, time: down.tE, barIdx: down.eRaw, anchor: floor, anchorTime: b.time,
        zone: [Math.min(floor, down.pE), Math.max(floor, down.pE)],
      });
    }
    for (const pv of pivs) {
      const leave = st.findIndex((s, i) => i > pv.eIdx && s.dir === 1 && s.pE > pv.zg);
      if (leave < 0) continue;
      const back = st[leave + 1];
      if (!back || back.dir !== -1) continue;
      if (back.pE <= pv.zg) continue;
      if (back.sRaw < st[leave].eRaw) continue;
      if (back.pE > pv.zg * (1 + p.thirdMaxDist)) continue;
      buys.push({
        kind: '三买', price: back.pE, time: back.tE, barIdx: back.eRaw, anchor: pv.zg, anchorTime: pv.tE,
        zone: [pv.zg, back.pE],
      });
    }
    buys.sort((a, b) => a.time - b.time);
    return buys;
  }

  function compute(cs, p) {
    if (!cs || cs.length < 60) return { bars: cs || [], error: '数据不足' };
    const closes = cs.map(c => c.close);
    const hist = macd(closes, p.macdFast | 0, p.macdSlow | 0, p.macdSignal | 0).hist;
    const merged = merge(cs);
    const fr = fractals(merged, cs, p.confirmWin | 0);
    const seq = alternate(merged, fr);
    const st = strokes(merged, cs, seq, Math.max(2, p.minGap | 0), { priceCheck: p.priceCheck !== false });
    const segs = segments(st);
    const legs = p.pivotLevel === 'segment' ? segs : st;
    const pivs = pivots(legs, p.pivotRatio);
    const divs = divergence(legs, cs, hist, p.divRatio, p.divMode);
    // 三买固定用笔中枢判突破(线段级中枢只影响显示), 避免笔/段下标混用
    const strokePivots = p.pivotLevel === 'segment' ? pivots(st, p.pivotRatio) : pivs;
    const buys = buyPoints(st, strokePivots, cs, hist, divs, p);
    return { bars: cs, merged, fr, seq, strokes: st, segments: segs, legs, pivots: pivs, divs, buys, macd: hist, error: null };
  }

  const API = { ema, macd, merge, fractals, alternate, extreme, strokes, segments, pivots, divergence, trigger, buyPoints, compute };
  if (typeof module !== 'undefined' && module.exports) module.exports = API;
  root.LabEngine = API;
})(typeof window !== 'undefined' ? window : globalThis);
