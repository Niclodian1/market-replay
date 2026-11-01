/**
 * 缠论口径契约：Python 引擎生成 golden（scripts/export_chanlun_golden.py），
 * TS 侧用同一批数据算 MACD、overlay 图层与统计，防止两端各写一遍导致漂移。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { chanlunMacdHist, computeChanlunStats } from '../lib/chanlunStats.ts'
import {
  buildBuySellMarkers,
  buildChanlunLayerData,
  buildChanlunMarkers,
} from '../components/chart/chanlunOverlay.ts'

const golden = JSON.parse(
  readFileSync(new URL('./fixtures/chanlun_golden.json', import.meta.url), 'utf8'),
)

const level = {
  symbol: golden.symbol,
  timeframe: golden.timeframe,
  strokes: golden.strokes,
  segments: golden.segments,
  pivots_stroke: golden.pivots_stroke,
  pivots_segment: golden.pivots_segment,
  buy_sell_points: golden.buy_sell_points,
  beichi_points: golden.beichi_points,
  top_fractals: golden.top_fractals,
  bot_fractals: golden.bot_fractals,
  meta: golden.meta,
}

test('MACD 柱与 Python 引擎逐根一致', () => {
  const hist = chanlunMacdHist(golden.closes)
  assert.equal(hist.length, golden.macd_hist.length)
  for (let i = 0; i < hist.length; i += 1) {
    const expected = golden.macd_hist[i]
    // Python 侧 macdHist 以 %.6g 输出（6 位有效数字），允许格式化带来的相对误差
    const tolerance = 1e-5 * Math.max(1, Math.abs(expected))
    assert.ok(
      Math.abs(hist[i] - expected) <= tolerance,
      `bar ${i}: ts=${hist[i]} py=${expected}`,
    )
  }
})

test('overlay 图层与 Python 输出数量一致', () => {
  const layers = buildChanlunLayerData(level, {
    stroke: true,
    segment: true,
    pivot: true,
    point: true,
    beichi: true,
  })
  assert.equal(layers.strokes?.length, golden.strokes.length)
  assert.equal(layers.segments?.length, golden.segments.length)
  assert.equal(layers.pivotsStroke?.length, golden.pivots_stroke.length)
  assert.equal(layers.pivotsSegment?.length, golden.pivots_segment.length)
})

test('markers 覆盖全部买卖点且按时间升序', () => {
  const markers = buildBuySellMarkers(level, false)
  assert.equal(markers.length, golden.buy_sell_points.length)
  for (let i = 1; i < markers.length; i += 1) {
    assert.ok(markers[i].time >= markers[i - 1].time, 'markers 必须按时间升序')
  }
  const all = buildChanlunMarkers(level, false)
  assert.equal(all.length, golden.buy_sell_points.length + golden.beichi_points.length)
})

test('所有点满足因果约束（confirmedT 不早于 t）', () => {
  for (const point of [...golden.buy_sell_points, ...golden.beichi_points]) {
    if (point.confirmedT == null) continue
    assert.ok(point.confirmedT >= point.t, `${point.k} confirmedT ${point.confirmedT} < t ${point.t}`)
  }
})

test('统计函数在 golden 数据上确定性输出', () => {
  const first = computeChanlunStats(golden.candles, golden.buy_sell_points)
  const second = computeChanlunStats(golden.candles, golden.buy_sell_points)
  assert.deepEqual(first, second)
  assert.ok(Number.isFinite(first.avgPnlPct))
  assert.equal(first.total, first.trades.length)
})
