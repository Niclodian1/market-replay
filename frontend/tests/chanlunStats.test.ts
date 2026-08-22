import { test } from 'node:test'
import assert from 'node:assert/strict'
import { computeChanlunStats, chanlunMacdHist, pairTrades } from '../lib/chanlunStats.ts'

// 基础行情：T0..T5 共 6 根 1h bar
const T0 = 1700000000
const H = 3600
function bar(t: number, close: number, low = close - 200): { time: number; open: number; high: number; low: number; close: number; volume: number } {
  return { time: t, open: close - 100, high: close + 300, low, close, volume: 1000 }
}
const candles = [
  bar(T0, 10000),
  bar(T0 + H, 10200),
  bar(T0 + 2 * H, 10300, 9800), // 买点 bar：止损取 9800
  bar(T0 + 3 * H, 10800),
  bar(T0 + 4 * H, 10700),
  bar(T0 + 5 * H, 11200),
]

function buy(tt: number, ct: number): any {
  return { t: tt, ct, p: 0, k: '一买', mode: 'stroke', confirmedT: ct }
}
function sell(tt: number, ct: number): any {
  return { t: tt, ct, p: 0, k: '一卖', mode: 'stroke', confirmedT: ct }
}

test('买点入场(确认bar收盘) → 卖点出场，胜率/盈亏比正确', () => {
  const stats = computeChanlunStats(candles, [
    buy(T0 + 2 * H, T0 + 3 * H), // 入场 10800
    sell(T0 + 4 * H, T0 + 5 * H), // 出场 11200
  ])
  assert.equal(stats.total, 1)
  assert.equal(stats.wins, 1)
  assert.equal(stats.losses, 0)
  assert.equal(stats.trades[0].entryPrice, 10800)
  assert.equal(stats.trades[0].exitPrice, 11200)
  assert.equal(stats.trades[0].stop, 9800)
  assert.equal(stats.trades[0].exitReason, 'sell')
  assert.ok(Math.abs(stats.trades[0].pnlPct - (400 / 10800) * 100) < 0.001)
})

test('未平仓(持仓中)不纳入已平仓统计', () => {
  const stats = computeChanlunStats(candles, [
    buy(T0 + 2 * H, T0 + 3 * H),
  ])
  assert.equal(stats.total, 0)
  assert.equal(stats.openTrades.length, 1)
  assert.equal(stats.openTrades[0].entryPrice, 10800)
})

test('持仓中收盘跌破止损 → 止损离场(exitReason=stop)', () => {
  // 买点 bar = T0+2H(low 9800)；确认 T0+3H(close 10800)；T0+4H close 10700 未破止损；
  // 构造 T0+5H close 跌到 9700(≤9800) → 该 bar 止损平仓(价=9800)
  const candles2 = [
    bar(T0, 10000),
    bar(T0 + H, 10200),
    bar(T0 + 2 * H, 10300, 9800),
    bar(T0 + 3 * H, 10800),
    bar(T0 + 4 * H, 10700),
    bar(T0 + 5 * H, 9700, 9600),
  ]
  const stats = computeChanlunStats(candles2, [
    buy(T0 + 2 * H, T0 + 3 * H),
    sell(T0 + 4 * H, T0 + 5 * H), // 卖点与止损触发同 bar：止损优先
  ])
  assert.equal(stats.total, 1)
  assert.equal(stats.trades[0].exitReason, 'stop')
  assert.equal(stats.trades[0].exitPrice, 9800)
  assert.equal(stats.losses, 1)
})

test('盈亏比 = 平均盈利/平均亏损绝对值', () => {
  const c4 = [
    bar(T0, 10000, 9900),
    bar(T0 + H, 10200),
    bar(T0 + 2 * H, 10300, 9800),
    bar(T0 + 3 * H, 10800),
    bar(T0 + 4 * H, 10700, 9600), // pair2 买点 bar（止损 9600）
    bar(T0 + 5 * H, 10600, 10000), // pair2 确认入场 close 10600
    bar(T0 + 6 * H, 9500, 9000), // 跌破 9600 → 止损离场
    bar(T0 + 7 * H, 9800), // pair2 卖点确认 bar
  ]
  const stats = computeChanlunStats(c4, [
    // pair1 盈利：入场 10200 → 卖点 10800 (+5.88%)
    buy(T0, T0 + H),
    sell(T0 + 2 * H, T0 + 3 * H),
    // pair2 亏损：买入 10600 → T0+6H 跌破止损 9600 → 止损离场 (-9.43%)
    buy(T0 + 4 * H, T0 + 5 * H),
    sell(T0 + 6 * H, T0 + 7 * H),
  ])
  assert.equal(stats.total, 2)
  assert.equal(stats.wins, 1)
  assert.equal(stats.losses, 1)
  assert.equal(stats.trades[0].exitReason, 'sell')
  assert.equal(stats.trades[1].exitReason, 'stop')
  assert.ok(Math.abs(stats.avgWinPct - (600 / 10200) * 100) < 0.001) // +5.88%
  assert.ok(Math.abs(stats.avgLossPct - ((9600 - 10600) / 10600) * 100) < 0.001) // -9.43%
  assert.ok(Math.abs(stats.profitFactor - (stats.avgWinPct / Math.abs(stats.avgLossPct))) < 0.001)
  assert.ok(stats.profitFactor > 0)
})

test('背驰买后同向 MACD 柱放大 → exitReason=macd', () => {
  const closes = [120, 118, 114, 108, 100, 90, 78, 80, 95, 110]
  const cs = closes.map((c, i) => bar(T0 + i * H, c, c - 3))
  const hist = chanlunMacdHist(closes)
  const entryI = 3
  let expandI = -1
  for (let i = entryI + 1; i < hist.length; i++) {
    if (hist[i] < 0 && hist[i - 1] < 0 && Math.abs(hist[i]) > Math.abs(hist[i - 1])) {
      expandI = i
      break
    }
  }
  assert.ok(expandI > entryI, `应能找到绿柱放大 bar, hist=${hist.map(h => h.toFixed(1)).join(',')}`)
  const { trades } = pairTrades(
    cs,
    [
      { t: cs[entryI].time, p: 0, k: '背驰买', mode: 'fast', state: 'confirmed', confirmedT: cs[entryI].time },
      { t: cs[cs.length - 1].time, p: 0, k: '背驰卖', mode: 'fast', state: 'confirmed', confirmedT: cs[cs.length - 1].time },
    ],
    p => p.k === '背驰买',
    p => p.k === '背驰卖',
  )
  assert.equal(trades.length, 1)
  assert.equal(trades[0].exitReason, 'macd')
  assert.equal(trades[0].exitT, cs[expandI].time)
})

test('forming / 无 confirmedT 的点不进入回测（禁止按极值 t 成交）', () => {
  const stats = computeChanlunStats(candles, [
    { t: T0 + 2 * H, ct: T0 + 2 * H, p: 0, k: '一买', mode: 'stroke', state: 'forming', confirmedT: null },
    { t: T0 + 5 * H, ct: T0 + 5 * H, p: 0, k: '一卖', mode: 'stroke', state: 'confirmed', confirmedT: T0 + 5 * H },
  ])
  assert.equal(stats.total, 0)
  assert.equal(stats.openTrades.length, 0)
})

test('只有买卖点无K线/无数据 → 空统计', () => {
  assert.equal(computeChanlunStats([], []).total, 0)
  assert.equal(computeChanlunStats(candles, []).total, 0)
})
