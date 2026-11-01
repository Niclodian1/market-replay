/**
 * chart-kit 契约：序列映射、收盘对齐、增量合并。
 * 这些函数同时被看盘/图表/练习使用，必须与后端 scan_window.py 口径一致。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  alignValues,
  eventsToMarkers,
  seriesToHistogram,
  seriesToIndicators,
  seriesToLines,
} from '../lib/chart-kit/series.ts'
import { lastClosedBarMs, msUntilBarClose, nextRefreshDelayMs } from '../lib/chart-kit/barClose.ts'
import { mergeStructure, tailCandles } from '../lib/chart-kit/structure.ts'

const candles = [
  { time: 1000 },
  { time: 2000 },
  { time: 3000 },
]

test('alignValues 按索引对齐 K 线并丢掉空值', () => {
  const aligned = alignValues(candles, [1, null, 3])
  assert.deepEqual(aligned, [
    { time: 1000, value: 1 },
    { time: 3000, value: 3 },
  ])
  assert.deepEqual(alignValues(candles, [1, 2]), [
    { time: 1000, value: 1 },
    { time: 2000, value: 2 },
  ])
})

test('seriesToLines 只取 price panel，颜色按序列顺序稳定', () => {
  const rows = [
    { key: 'ma20', label: 'MA20', panel: 'price', values: [1, 2, 3] },
    { key: 'ma60', label: 'MA60', panel: 'price', values: [4, 5, 6] },
    { key: 'macd', label: 'MACD', panel: 'macd', values: [7, 8, 9] },
  ]
  const lines = seriesToLines(rows, candles)
  assert.deepEqual(lines.map((line) => line.id), ['ma20', 'ma60'])
  assert.equal(lines[0].color, lines[0].color)
  assert.notEqual(lines[0].color, lines[1].color)
  assert.deepEqual(lines[0].data, [
    { time: 1000, value: 1 },
    { time: 2000, value: 2 },
    { time: 3000, value: 3 },
  ])
  assert.deepEqual(seriesToLines(rows, candles, { keys: ['ma60'] }).map((l) => l.id), ['ma60'])
})

test('seriesToHistogram 丢掉零值', () => {
  const rows = [{ key: 'macd_hist', label: 'hist', panel: 'macd', values: [0, 2, -1] }]
  assert.deepEqual(seriesToHistogram(rows, candles), [
    { time: 2000, value: 2 },
    { time: 3000, value: -1 },
  ])
  assert.deepEqual(seriesToHistogram(rows, candles, 'nope'), [])
})

test('eventsToMarkers 合并同时间同方向事件并排序', () => {
  const markers = eventsToMarkers([
    { indicator_id: 'chanlun.buy3', direction: 'long', confirmed_at: 2000, price: 10 },
    { indicator_id: 'macd.divergence', direction: 'long', confirmed_at: 2000, price: 10 },
    { indicator_id: 'chanlun.sell3', direction: 'short', confirmed_at: 1000, price: 11 },
  ])
  assert.deepEqual(markers.map((m) => [m.time, m.kind]), [
    [1000, 'sell'],
    [2000, 'buy'],
  ])
  assert.equal(markers[1].text, 'buy3/divergence')
  assert.deepEqual(eventsToMarkers([{ indicator_id: 'x', direction: 'long', confirmed_at: 1 }], { show: false }), [])
})

test('收盘对齐与后端 scan_window 同口径（UTC 边界）', () => {
  const fourH = 4 * 60 * 60 * 1000
  const base = 1_700_000_000_000 - (1_700_000_000_000 % fourH) + 60_000
  assert.equal(lastClosedBarMs('4h', base), base - 60_000)
  assert.equal(msUntilBarClose('4h', base), fourH - 60_000)
  assert.equal(nextRefreshDelayMs('4h', base), fourH - 60_000 + 5_000)
  assert.equal(lastClosedBarMs('1day', base)! % (24 * 60 * 60 * 1000), 0)
  assert.equal(msUntilBarClose('weird', base), null)
  assert.equal(nextRefreshDelayMs('weird', base), null)
})

test('tailCandles 只保留最近 N 根', () => {
  const bars = Array.from({ length: 1000 }, (_, i) => i)
  assert.equal(tailCandles(bars, 600).length, 600)
  assert.equal(tailCandles(bars, 600)[0], 400)
  assert.equal(tailCandles(bars, 5000).length, 1000)
})

test('mergeStructure 同时间以新结果为准且保留更早的点', () => {
  const previous = {
    buy_sell_points: [
      { t: 100, k: '一买', p: 1 },
      { t: 200, k: '二买', p: 2 },
    ],
    top_fractals: [{ time: 50, price: 9 }],
    strokes: [[100, 1, 200, 2, 1]],
  }
  const fresh = {
    buy_sell_points: [
      { t: 200, k: '三买', p: 20 },
      { t: 300, k: '一卖', p: 3 },
    ],
    top_fractals: [{ time: 250, price: 10 }],
    strokes: [[200, 2, 300, 3, 1]],
  }
  const merged = mergeStructure(previous, fresh)!
  assert.deepEqual(
    (merged.buy_sell_points as { t: number; k: string }[]).map((p) => [p.t, p.k]),
    [
      [100, '一买'],
      [200, '三买'],
      [300, '一卖'],
    ],
  )
  assert.deepEqual((merged.top_fractals as { time: number }[]).map((f) => f.time), [50, 250])
  assert.equal((merged.strokes as unknown[][]).length, 2)
  assert.equal(mergeStructure(null, fresh), fresh)
  assert.equal(mergeStructure(previous, null), previous)
})

test('seriesToIndicators 把统一序列映射成图表页结构', () => {
  const rows = [
    { key: 'ma_sma_20', label: 'MA20', panel: 'price', values: [1, 2, 3] },
    { key: 'ma_sma_60', label: 'MA60', panel: 'price', values: [4, 5, 6] },
    { key: 'macd_dif', label: 'DIF', panel: 'sub', values: [0.1, 0.2, 0.3] },
    { key: 'macd_dea', label: 'DEA', panel: 'sub', values: [0.05, 0.1, 0.15] },
    { key: 'macd_hist', label: 'MACD', panel: 'sub', values: [0.05, 0.1, 0.15] },
    { key: 'rsi', label: 'RSI', panel: 'sub', values: [50, null, 70] },
  ]
  const indicators = seriesToIndicators(rows, candles, { periods: [20, 60] })
  assert.deepEqual(indicators.ma_lines.map((line) => line.period), [20, 60])
  assert.deepEqual(indicators.ma_lines[1].data, [
    { time: 1000, value: 4 },
    { time: 2000, value: 5 },
    { time: 3000, value: 6 },
  ])
  assert.deepEqual(indicators.rsi, [
    { time: 1000, value: 50 },
    { time: 3000, value: 70 },
  ])
  assert.deepEqual(indicators.macd[2], { time: 3000, macd: 0.3, signal: 0.15, histogram: 0.15 })
  // 缺序列时返回空，不抛异常（旧数据/降级路径）
  assert.deepEqual(seriesToIndicators([], candles), { ma_lines: [], rsi: [], macd: [] })
})
