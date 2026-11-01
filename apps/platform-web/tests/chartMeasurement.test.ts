import assert from 'node:assert/strict'
import test from 'node:test'
// Node 25 executes TypeScript directly and requires the runtime extension;
// the application build itself imports this module through the @/ alias.
import { calculateCandleMovement, calculateChartMeasurement, formatMeasureDuration } from '../lib/chartMeasurement.ts'

test('calculates an upward chart measurement', () => {
  const result = calculateChartMeasurement(100, 112.5, 1_000, 260_200, 10, 13)

  assert.equal(result.priceChange, 12.5)
  assert.equal(result.percentChange, 12.5)
  assert.equal(result.barCount, 3)
  assert.equal(result.durationSeconds, 259_200)
  assert.equal(result.isUp, true)
})

test('calculates a downward chart measurement from the selected start point', () => {
  const result = calculateChartMeasurement(200, 150, 20_000, 10_000, 20, 5)

  assert.equal(result.priceChange, -50)
  assert.equal(result.percentChange, -25)
  assert.equal(result.barCount, 15)
  assert.equal(result.durationSeconds, 10_000)
  assert.equal(result.isUp, false)
})

test('handles a zero start price without producing infinity', () => {
  const result = calculateChartMeasurement(0, 10, 0, 0, 0, 0)
  assert.equal(result.percentChange, 0)
})

test('formats measurement durations without unit overflow', () => {
  assert.equal(formatMeasureDuration(45), '45秒')
  assert.equal(formatMeasureDuration(90), '1分钟')
  assert.equal(formatMeasureDuration(7_260), '2小时1分钟')
  assert.equal(formatMeasureDuration(90_000), '1天1小时')
  assert.equal(formatMeasureDuration(86_399), '23小时59分钟')
})

test('calculates cursor candle body and maximum movement from the open', () => {
  const result = calculateCandleMovement(100, 108, 96, 105)

  assert.equal(result.bodyChange, 5)
  assert.equal(result.bodyPercent, 5)
  assert.equal(result.rangeChange, 12)
  assert.equal(result.rangePercent, 12)
  assert.equal(result.isUp, true)
})

test('keeps bearish body movement signed and the full range positive', () => {
  const result = calculateCandleMovement(200, 204, 180, 190)

  assert.equal(result.bodyChange, -10)
  assert.equal(result.bodyPercent, -5)
  assert.equal(result.rangeChange, 24)
  assert.equal(result.rangePercent, 12)
  assert.equal(result.isUp, false)
})

test('handles a zero candle open without non-finite percentages', () => {
  const result = calculateCandleMovement(0, 10, 0, 5)
  assert.equal(result.bodyPercent, 0)
  assert.equal(result.rangePercent, 0)
})
