import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const E = require('../js/09-lab-engine.js');

// ---- 构造工具 ---- //
const bar = (i, h, l, c) => ({ time: 1000 + i * 86400, open: (h + l) / 2, high: h, low: l, close: c == null ? (h + l) / 2 : c });
const fromHL = pairs => pairs.map(([h, l], i) => bar(i, h, l));
function rng(seed) {
  let a = seed >>> 0;
  return () => { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
function walk(n, seed, vol = 0.012, drift = 0.0002) {
  const r = rng(seed); const out = []; let p = 100;
  for (let i = 0; i < n; i++) {
    const o = p;
    p = p * (1 + drift + (r() - 0.5) * vol * 2);
    const h = Math.max(o, p) * (1 + r() * vol * 0.5);
    const l = Math.min(o, p) * (1 - r() * vol * 0.5);
    out.push({ time: 1000 + i * 86400, open: o, high: h, low: l, close: p });
  }
  return out;
}
const P = {
  macdFast: 12, macdSlow: 26, macdSignal: 9, divMode: 'area', divRatio: 0.9,
  trigger: 'shrink', priceMode: 'close', trigWindow: 20, minGap: 4, confirmWin: 0,
  pivotRatio: 0.3, pivotLevel: 'stroke', thirdMaxDist: 0.12,
};

// ---- 包含处理 ---- //
test('merge: 包含关系按方向合并, 输出不再有包含', () => {
  const cs = fromHL([[10, 5], [12, 4], [8, 3], [9, 2]]);   // b1 包含 b0(向上), b3 包含 b2(向下)
  const m = E.merge(cs);
  assert.equal(m.length, 2);
  assert.deepEqual([m[0].high, m[0].low], [12, 5], '向上包含: 取高高/低高');
  assert.deepEqual([m[1].high, m[1].low], [8, 2], '向下包含: 取低低/高低');
  for (let i = 0; i + 1 < m.length; i++) {
    const a = m[i], b = m[i + 1];
    const contained = (b.high <= a.high && b.low >= a.low) || (b.high >= a.high && b.low <= a.low);
    assert.equal(contained, false, `第${i}根与第${i + 1}根仍存在包含`);
  }
});

test('merge: 未包含时原样保留', () => {
  const cs = fromHL([[1, 0.5], [2, 1], [3, 1.5], [2.5, 1], [1.5, 0.5]]);
  assert.equal(E.merge(cs).length, 5);
});

// ---- 分型 ---- //
test('fractals: 识别顶分型并给出确认K', () => {
  const cs = fromHL([[1, 0.5], [2, 1], [3, 1.5], [2.5, 1], [1.5, 0.5]]);
  const m = E.merge(cs);
  const fr = E.fractals(m, cs, 0);
  assert.equal(fr.length, 1);
  assert.equal(fr[0].kind, 'T');
  assert.equal(fr[0].i, 2);
  assert.equal(fr[0].cIdx, 2, 'confirmWin=0 时确认K=分型中K末根');
});

test('fractals: 收盘确认窗口内没有反向突破 → 分型不成立', () => {
  // 直接构造合并K: 顶分型中K=i1(high 3 / low 2), 后续两根收盘始终站在 2 上方
  const merged = [
    { i0: 0, i1: 0, high: 1, low: 0.5 },
    { i0: 1, i1: 1, high: 3, low: 2 },
    { i0: 2, i1: 2, high: 2, low: 1.5 },
    { i0: 3, i1: 3, high: 2.2, low: 1.9 },
  ];
  const cs = [
    bar(0, 1, 0.5, 0.7), bar(1, 3, 2, 2.5), bar(2, 2.4, 2.0, 2.05), bar(3, 2.3, 2.05, 2.1),
  ];
  const tops = fr => fr.filter(f => f.kind === 'T');
  assert.equal(tops(E.fractals(merged, cs, 0)).length, 1, '纯几何口径成立');
  assert.equal(tops(E.fractals(merged, cs, 2)).length, 0, '窗口内无收盘跌破 2 → 不确认');
});

// ---- 交替 / 笔 ---- //
test('alternate: 连续同类分型保留更极端者', () => {
  const m = [{ i0: 1, i1: 1, high: 3, low: 1 }, { i0: 3, i1: 3, high: 4, low: 2 }];
  const seq = E.alternate(m, [{ i: 0, kind: 'T', cIdx: 0 }, { i: 1, kind: 'T', cIdx: 1 }]);
  assert.equal(seq.length, 1);
  assert.equal(seq[0].i, 1);
});

test('strokes: 首尾相接、方向交替、间隔达标', () => {
  const cs = walk(600, 7);
  const m = E.merge(cs), fr = E.fractals(m, cs, 0), seq = E.alternate(m, fr);
  const st = E.strokes(m, cs, seq, 4);
  assert.ok(st.length > 10, `笔数应足够, 实际 ${st.length}`);
  for (let k = 0; k < st.length; k++) {
    if (k) {
      assert.equal(st[k].sIdx, st[k - 1].eIdx, `第${k}笔未与上一笔相接`);
      assert.equal(st[k].sRaw, st[k - 1].eRaw, `第${k}笔原始K未相接`);
      assert.notEqual(st[k].dir, st[k - 1].dir, `第${k}笔方向未交替`);
    }
    assert.ok(st[k].eIdx - st[k].sIdx >= 4, `第${k}笔间隔不足`);
    assert.ok(st[k].tS < st[k].tE && st[k].sRaw < st[k].eRaw);
    // 向上笔终点价 > 起点价, 向下笔反之
    assert.equal(st[k].dir, st[k].pE > st[k].pS ? 1 : -1);
  }
});

// ---- 中枢 ---- //
test('pivots: ZG/ZD 取三段重叠区, 且满足重叠比', () => {
  const legs = [
    { pS: 100, pE: 110, tS: 1, tE: 2, sRaw: 0, eRaw: 1 },
    { pS: 110, pE: 105, tS: 2, tE: 3, sRaw: 1, eRaw: 2 },
    { pS: 105, pE: 112, tS: 3, tE: 4, sRaw: 2, eRaw: 3 },
  ];
  const pv = E.pivots(legs, 0.3);
  assert.equal(pv.length, 1);
  assert.equal(pv[0].zg, 110);
  assert.equal(pv[0].zd, 105);
  assert.ok(pv[0].ratio >= 0.3);
});

test('pivots: 三段不重叠 → 无中枢', () => {
  const legs = [
    { pS: 100, pE: 110, tS: 1, tE: 2, sRaw: 0, eRaw: 1 },
    { pS: 110, pE: 120, tS: 2, tE: 3, sRaw: 1, eRaw: 2 },
    { pS: 120, pE: 130, tS: 3, tE: 4, sRaw: 2, eRaw: 3 },
  ];
  assert.equal(E.pivots(legs, 0.1).length, 0);
});

// ---- 背驰 ---- //
test('divergence: 价格新低且柱面积收缩 → 底背驰', () => {
  const cs = Array.from({ length: 20 }, (_, i) => bar(i, 11, 9, 10));
  const hist = [...Array(10).fill(-1), ...Array(10).fill(-0.1)];
  const legs = [
    { dir: -1, pS: 10, pE: 8, tS: cs[0].time, tE: cs[9].time, sRaw: 0, eRaw: 9 },
    { dir: 1, pS: 8, pE: 9, tS: cs[9].time, tE: cs[10].time, sRaw: 9, eRaw: 10 },
    { dir: -1, pS: 9, pE: 7, tS: cs[10].time, tE: cs[19].time, sRaw: 10, eRaw: 19 },
  ];
  const d = E.divergence(legs, cs, hist, 0.9, 'area');
  assert.equal(d.length, 1);
  assert.equal(d[0].kind, 'bottom');
  assert.ok(d[0].ratio < 0.9);
  assert.equal(d[0].barIdx, 19);
});

test('divergence: 价格没创新低 → 不算背驰', () => {
  const cs = Array.from({ length: 20 }, (_, i) => bar(i, 11, 9, 10));
  const hist = [...Array(10).fill(-1), ...Array(10).fill(-0.1)];
  const legs = [
    { dir: -1, pS: 10, pE: 8, tS: cs[0].time, tE: cs[9].time, sRaw: 0, eRaw: 9 },
    { dir: 1, pS: 8, pE: 9, tS: cs[9].time, tE: cs[10].time, sRaw: 9, eRaw: 10 },
    { dir: -1, pS: 9, pE: 8.5, tS: cs[10].time, tE: cs[19].time, sRaw: 10, eRaw: 19 },
  ];
  assert.equal(E.divergence(legs, cs, hist, 0.9, 'area').length, 0);
});

// ---- 触发柱 ---- //
test('trigger: 绿柱首次缩短 = 该根收盘买点(默认口径)', () => {
  const cs = Array.from({ length: 8 }, (_, i) => bar(i, 11, 9, 10 + i));
  const hist = [-1, -2, -3, -2.5, -1, 0.5, 1, 2];
  const i = E.trigger(cs, hist, 2, { ...P, trigger: 'shrink', trigWindow: 10 });
  assert.equal(i, 3, '第3根绿柱比第2根短, 即首次缩短');
  assert.equal(hist[i] < 0 && hist[i] > hist[i - 1], true);
});

test('trigger: 口径 stop = 缩短后首次不再缩短', () => {
  const cs = Array.from({ length: 8 }, (_, i) => bar(i, 11, 9, 10 + i));
  const hist = [-1, -2, -3, -2, -4, -1, 0, 1];
  const i = E.trigger(cs, hist, 2, { ...P, trigger: 'stop', trigWindow: 10 });
  assert.equal(i, 4);
  assert.ok(Math.abs(hist[i]) >= Math.abs(hist[i - 1]));
});

test('trigger: 口径 flip = 柱体首次翻红', () => {
  const cs = Array.from({ length: 8 }, (_, i) => bar(i, 11, 9, 10 + i));
  const hist = [-1, -2, -1.5, 0.5, 2, 3, 4, 5];
  assert.equal(E.trigger(cs, hist, 1, { ...P, trigger: 'flip', trigWindow: 10 }), 3);
});

test('trigger: 窗口内没有触发 → -1', () => {
  const cs = Array.from({ length: 8 }, (_, i) => bar(i, 11, 9, 10 + i));
  const hist = [-1, -2, -3, -4, -5, -6, -7, -8];
  assert.equal(E.trigger(cs, hist, 2, { ...P, trigger: 'shrink', trigWindow: 3 }), -1);
});

// ---- 买卖点 ---- //
test('buyPoints: 一买取触发柱收盘价, 二买回调不破前低', () => {
  const cs = Array.from({ length: 30 }, (_, i) => bar(i, 11, 9, 100 + i));
  const hist = Array(30).fill(-1); hist[7] = -0.5; hist[6] = -1;
  const div = { kind: 'bottom', barIdx: 5, p: 100, t: cs[5].time, prevT: cs[1].time, prevP: 105, ratio: 0.4 };
  const st = [
    { dir: 1, pS: 100, pE: 120, tS: cs[6].time, tE: cs[10].time, sRaw: 6, eRaw: 10, sIdx: 0, eIdx: 1 },
    { dir: -1, pS: 120, pE: 110, tS: cs[10].time, tE: cs[14].time, sRaw: 10, eRaw: 14, sIdx: 1, eIdx: 2 },
  ];
  const buys = E.buyPoints(st, [], cs, hist, [div], P);
  const b1 = buys.find(b => b.kind === '一买');
  const b2 = buys.find(b => b.kind === '二买');
  assert.ok(b1, '应有一买');
  assert.equal(b1.barIdx, 7, '触发柱 = 首次绿柱缩短');
  assert.equal(b1.price, cs[7].close, '点位=触发柱收盘价');
  assert.equal(b1.time, cs[7].time);
  assert.ok(b2, '应有二买');
  assert.equal(b2.price, 110);
  assert.deepEqual(b2.zone, [100, 110], '区间 = [背驰结构低点, 回抽低点]');
});

test('buyPoints: 三买要求回抽不回中枢且不过度远离 ZG', () => {
  const cs = Array.from({ length: 30 }, (_, i) => bar(i, 11, 9, 100 + i));
  const hist = Array(30).fill(-1);
  const pv = { sIdx: 0, eIdx: 1, zd: 90, zg: 100, tS: cs[0].time, tE: cs[5].time };
  const filler = (i) => ({ dir: i % 2 ? 1 : -1, pS: 100, pE: 100, tS: cs[i].time, tE: cs[i + 1].time, sRaw: i, eRaw: i + 1, sIdx: i, eIdx: i + 1 });
  const mk = (pE, sRaw, eRaw) => ({ dir: -1, pS: 120, pE, tS: cs[sRaw].time, tE: cs[eRaw].time, sRaw, eRaw, sIdx: 3, eIdx: 4 });
  // 离开段(向上笔收在 ZG 上方, 位于中枢之后) + 回抽低点 104 (在 12% 内) → 三买
  const leave = { dir: 1, pS: 95, pE: 120, tS: cs[6].time, tE: cs[10].time, sRaw: 6, eRaw: 10, sIdx: 2, eIdx: 3 };
  const ok = E.buyPoints([filler(0), filler(1), leave, mk(104, 10, 14)], [pv], cs, hist, [], P);
  assert.equal(ok.filter(b => b.kind === '三买').length, 1);
  // 回抽低点 130 → 距 ZG 30% 太远 → 不算
  const far = E.buyPoints([filler(0), filler(1), leave, mk(130, 10, 14)], [pv], cs, hist, [], P);
  assert.equal(far.filter(b => b.kind === '三买').length, 0);
  // 回抽回到中枢内(95) → 不算
  const back = E.buyPoints([filler(0), filler(1), leave, mk(95, 10, 14)], [pv], cs, hist, [], P);
  assert.equal(back.filter(b => b.kind === '三买').length, 0);
});

// ---- 端到端不变量(合成行情) ---- //
test('compute: 结构不变量与因果性', () => {
  const cs = walk(1200, 42);
  const R = E.compute(cs, { ...P });
  assert.equal(R.error, null);
  assert.ok(R.strokes.length > 50, `笔数 ${R.strokes.length}`);
  assert.ok(R.pivots.length > 0, '应有中枢');
  // 笔: 相接 + 交替
  for (let k = 1; k < R.strokes.length; k++) {
    assert.equal(R.strokes[k].sRaw, R.strokes[k - 1].eRaw);
    assert.notEqual(R.strokes[k].dir, R.strokes[k - 1].dir);
  }
  // 中枢: ZD < ZG 且重叠比达标
  for (const pv of R.pivots) {
    assert.ok(pv.zd < pv.zg);
    assert.ok(pv.ratio >= P.pivotRatio - 1e-9);
    assert.ok(pv.gg >= pv.zg && pv.dd <= pv.zd);
  }
  // 背驰: 只在价格创新极值时出现
  for (const d of R.divs) {
    const prev = R.legs[d.prevIdx], cur = R.legs[d.legIdx];
    assert.equal(cur.dir, prev.dir);
    assert.ok(cur.dir === -1 ? cur.pE < prev.pE : cur.pE > prev.pE);
    assert.ok(d.ratio < P.divRatio);
  }
  // 买点: 按时间升序; 一买必须在背驰低点之后(因果)
  const times = R.buys.map(b => b.time);
  assert.deepEqual(times, [...times].sort((a, b) => a - b));
  const divBars = new Set(R.divs.map(d => d.barIdx));
  for (const b of R.buys.filter(x => x.kind === '一买')) {
    assert.ok(b.barIdx > 0);
    const div = R.divs.find(d => d.kind === 'bottom' && d.t <= b.time && b.barIdx - d.barIdx <= P.trigWindow);
    assert.ok(div, '一买必须能追溯到窗口内的底背驰');
    assert.ok(divBars.has(div.barIdx));
    assert.ok(b.price >= div.p * 0.9, '价格未过度偏离背驰低点');
  }
});

test('compute: 数据不足 → 报错而不是抛异常', () => {
  const R = E.compute(walk(30, 1), { ...P });
  assert.equal(R.error, '数据不足');
});

test('compute: 参数改变会改变结构 (中枢/买点随参数变化)', () => {
  const cs = walk(1500, 9);
  const base = E.compute(cs, { ...P });
  const strict = E.compute(cs, { ...P, minGap: 7, pivotRatio: 0.5, divRatio: 0.6 });
  assert.notEqual(base.strokes.length, strict.strokes.length);
  assert.ok(strict.strokes.length <= base.strokes.length, '成笔更严 → 笔更少');
  assert.notDeepEqual(base.buys.map(b => b.time), strict.buys.map(b => b.time));
});
