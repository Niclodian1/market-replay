import { test } from 'node:test'
import assert from 'node:assert/strict'
import { scanChanlunStrategies } from '../lib/chanlunStrategy.ts'

const T0 = 1700000000
const H = 3600
function bar(t: number, close: number, low = close - 200): any {
  return { time: t, open: close - 100, high: close + 300, low, close, volume: 1000 }
}
function sig(t: number, ct: number, k: string): any {
  return { t, ct, p: 0, k, mode: 'stroke', confirmedT: ct }
}

// 序列：二买(入场10300)→一卖(10400)；再 二买(10400)→背驰卖(10700)
const candles = [
  bar(T0, 10000, 9900), // 二买1 bar（stop 9900）
  bar(T0 + H, 10300), // 二买1 确认：入场 10300
  bar(T0 + 2 * H, 10500, 10000), // 二买2 bar（stop 10000）
  bar(T0 + 3 * H, 10400), // 一卖确认：10400
  bar(T0 + 4 * H, 10800, 10200), // 背驰卖 bar
  bar(T0 + 5 * H, 10700), // 背驰卖确认：10700
]
const points = [
  sig(T0, T0 + H, '二买'),
  sig(T0 + 2 * H, T0 + 3 * H, '一卖'),
  sig(T0 + 2 * H, T0 + 3 * H, '二买'),
  sig(T0 + 4 * H, T0 + 5 * H, '背驰卖'),
]

test('二买→一卖 与 二买→背驰卖 各自独立配对', () => {
  const rows = scanChanlunStrategies(candles, points, 1)
  const byLabel = Object.fromEntries(rows.map(r => [r.label, r]))
  const one = byLabel['二买 → 一卖']
  const bi = byLabel['二买 → 背驰卖']
  assert.ok(one && bi)
  assert.equal(one.trades, 1)
  assert.equal(one.recentTrades[0].exitReason, 'sell')
  assert.equal(one.recentTrades[0].exitKind, '一卖')
  assert.equal(bi.trades, 1)
  assert.equal(bi.recentTrades[0].exitKind, '背驰卖')
  assert.ok(bi.recentTrades[0].pnlPct > 0)
})

test('扩展组合 二买→(一卖·背驰卖) 任一离场共用两笔', () => {
  const rows = scanChanlunStrategies(candles, points, 1)
  const combo = rows.find(r => r.label === '二买 → 一卖+背驰卖')
  assert.ok(combo)
  // 二买1 先遇 一卖 离场；同一 bar 又出 二买2（先平后买）→ 遇 背驰卖 离场
  assert.equal(combo.trades, 2)
  assert.equal(combo.wins, 2)
  // recent 按入场时间倒序：背驰卖那笔在前
  assert.deepEqual(combo.recentTrades.map(t => t.exitKind), ['背驰卖', '一卖'])
})

test('按平均收益降序排列 + 收益统计字段完整', () => {
  const rows = scanChanlunStrategies(candles, points, 1)
  for (let i = 1; i < rows.length; i++) {
    assert.ok(rows[i - 1].avgPnlPct >= rows[i].avgPnlPct)
  }
  for (const r of rows) {
    assert.deepEqual(
      Object.keys(r).sort(),
      ['avgPnlPct', 'bestPnl', 'buyKinds', 'id', 'label', 'losses', 'profitFactor',
        'recentTrades', 'sellKinds', 'stability', 'totalPnlPct', 'trades', 'wins', 'winRate', 'worstPnl'].sort(),
    )
  }
})

test('默认 minTrades=3 会过滤样本过少的组合；空数据返回空', () => {
  // 上述数据每个组合最多 2 笔 → 默认(3)全部过滤
  assert.equal(scanChanlunStrategies(candles, points).length, 0)
  assert.deepEqual(scanChanlunStrategies([], []), [])
})
