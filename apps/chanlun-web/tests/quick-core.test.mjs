import test from 'node:test';
import assert from 'node:assert/strict';
import Q from '../js/06-quick-core.js';

// 生成平稳上涨序列: 收盘 = base + k*step, 振幅 ±1
const ramp = (n, base = 100, step = 0.5) =>
  Array.from({ length: n }, (_, k) => { const c = base + k * step; return [k, c - 0.2, c + 1, c - 1, c, 1000]; });

test('市场环境: 稳步上涨 → MA120向上 + 价格在MA120以上; 数据不足返回 null', () => {
  const m = Q.market(ramp(130));
  assert.equal(m.ma120dir, 'MA120向上');
  assert.equal(m.pricepos, '价格在MA120以上');
  const short = Q.market(ramp(60));
  assert.equal(short.ma120dir, null);
  assert.equal(short.pricepos, null);
});

test('市场环境: 横盘 → MA120走平; 下跌 → MA120向下 + 价格在下', () => {
  const flat = Array.from({ length: 130 }, (_, k) => [k, 100, 101, 99, 100 + (k % 2 ? 0.1 : -0.1), 1]);
  assert.equal(Q.market(flat).ma120dir, 'MA120走平');
  const down = ramp(130, 300, -1);
  const m = Q.market(down);
  assert.equal(m.ma120dir, 'MA120向下');
  assert.equal(m.pricepos, '价格在MA120以下');
});

test('信号: 上根收在 MA20 下、本根收在上 → 突破MA20', () => {
  const cs = Array.from({ length: 30 }, (_, k) => [k, 100, 101, 99, 100, 1]);
  cs[28] = [28, 100, 100.5, 97, 97.5, 1];   // 收在 MA20 下
  cs[29] = [29, 97.5, 104, 97.4, 103.8, 1];  // 收回 MA20 上
  const s = Q.signals(cs, '多');
  assert.ok(s.includes('突破MA20'), s.join(','));
  assert.ok(!s.includes('回踩MA20'));
});

test('信号: 长下影 Pinbar 与看涨吞没; 做空镜像只看上影/看跌吞没, 不给均线类', () => {
  const base = Array.from({ length: 30 }, (_, k) => [k, 100, 101, 99, 100, 1]);
  const pin = base.slice(); pin[29] = [29, 100, 100.6, 94, 100.4, 1];
  assert.ok(Q.signals(pin, '多').includes('Pinbar'));
  assert.ok(!Q.signals(pin, '空').includes('Pinbar'));
  const eng = base.slice(); eng[28] = [28, 101, 101.2, 99.4, 99.6, 1]; eng[29] = [29, 99.5, 102, 99.3, 101.5, 1];
  assert.ok(Q.signals(eng, '多').includes('吞没'));
  const bear = base.slice(); bear[28] = [28, 99.6, 101.2, 99.4, 101, 1]; bear[29] = [29, 101.1, 101.3, 99, 99.4, 1];
  const ss = Q.signals(bear, '空');
  assert.ok(ss.includes('吞没'));
  assert.ok(!ss.some(x => x.startsWith('突破') || x.startsWith('回踩')));
});

test('信号: 额外传入 MACD 背离时加入建议', () => {
  assert.ok(Q.signals(ramp(40), '多', { macdDivergence: true }).includes('MACD背离'));
});

test('止损: 本根极值 / ATR / 波段, 且必须在不利方向', () => {
  const cs = ramp(40);
  const last = cs.at(-1);
  assert.equal(Q.stop(cs, '多', 'bar'), last[3]);
  assert.equal(Q.stop(cs, '空', 'bar'), last[2]);
  const a = Q.stop(cs, '多', 'atr');
  assert.ok(a < last[4]);
  const sw = Q.stop(cs, '多', 'swing');
  assert.ok(sw != null && sw < last[4]);
  // 极值不在不利方向时返回 null
  const bad = [[0, 10, 10, 10, 10, 1]];
  assert.equal(Q.stop(bad, '多', 'bar'), null);
});

test('波段止损: 取最近一个左右各2根确认的低点', () => {
  const cs = [
    [0, 10, 11, 9, 10], [1, 10, 11, 8.5, 10], [2, 10, 11, 7, 10],
    [3, 10, 11, 8, 10], [4, 10, 11, 8.6, 10], [5, 10, 11, 9, 10.5],
  ];
  assert.equal(Q.stop(cs, '多', 'swing'), 7);
});

test('stops: 批量返回三档止损, 自定义开仓价同时作用于 ATR 与方向校验', () => {
  const cs = ramp(40);
  const last = cs.at(-1);
  const sDefault = Q.stops(cs, '多');
  assert.equal(sDefault.bar, last[3]);
  assert.equal(sDefault.swing, Q.stop(cs, '多', 'swing'));
  assert.equal(sDefault.atr, Q.stop(cs, '多', 'atr'));
  // 限价低于本根最低价时: bar/swing 均不再在不利方向, 返回 null; ATR 基于自定义入场价计算
  const limit = last[3] - 2;
  const sLimit = Q.stops(cs, '多', limit);
  assert.equal(sLimit.bar, null);
  assert.ok(sLimit.atr < limit && sLimit.atr < sDefault.atr);
});

test('止损亏损估算: 保证金×杠杆×价差', () => {
  assert.equal(Q.riskAt(100, 98, '多', 1000, 10), -200);
  assert.equal(Q.riskAt(100, 102, '空', 1000, 10), -200);
});
