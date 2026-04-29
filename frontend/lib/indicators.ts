/**
 * 前端指标计算库（纯 TypeScript，避免动态加载时重复请求后端）
 * 与后端 app/core/indicators.py 逻辑一致。
 */
import type { Candle, Indicators, MAPoint, RSIPoint, MACDPoint, ImpulseColor } from '@/types'

function sma(values: number[], period: number): (number | null)[] {
  const result: (number | null)[] = new Array(values.length).fill(null)
  for (let i = period - 1; i < values.length; i++) {
    let sum = 0
    for (let j = i - period + 1; j <= i; j++) sum += values[j]
    result[i] = sum / period
  }
  return result
}

function ema(values: number[], period: number): (number | null)[] {
  const result: (number | null)[] = new Array(values.length).fill(null)
  if (values.length < period) return result
  const k = 2 / (period + 1)
  result[period - 1] = values.slice(0, period).reduce((a, b) => a + b, 0) / period
  for (let i = period; i < values.length; i++) {
    result[i] = values[i] * k + result[i - 1]! * (1 - k)
  }
  return result
}

function rsi(values: number[], period = 14): (number | null)[] {
  const n = values.length
  const result: (number | null)[] = new Array(n).fill(null)
  if (n < period + 1) return result

  const gains: number[] = []
  const losses: number[] = []
  for (let i = 1; i < n; i++) {
    const d = values[i] - values[i - 1]
    gains.push(Math.max(d, 0))
    losses.push(Math.max(-d, 0))
  }

  let ag = gains.slice(0, period).reduce((a, b) => a + b, 0) / period
  let al = losses.slice(0, period).reduce((a, b) => a + b, 0) / period
  const calc = (g: number, l: number) => (l === 0 ? 100 : 100 - 100 / (1 + g / l))

  result[period] = calc(ag, al)
  for (let i = period + 1; i < n; i++) {
    ag = (ag * (period - 1) + gains[i - 1]) / period
    al = (al * (period - 1) + losses[i - 1]) / period
    result[i] = calc(ag, al)
  }
  return result
}

function macd(
  values: number[],
  fast = 12,
  slow = 26,
  signal = 9
): { macdLine: (number | null)[]; signalLine: (number | null)[]; histogram: (number | null)[] } {
  const n = values.length
  const emaF = ema(values, fast)
  const emaS = ema(values, slow)

  const macdLine: (number | null)[] = new Array(n).fill(null)
  for (let i = 0; i < n; i++) {
    if (emaF[i] !== null && emaS[i] !== null) macdLine[i] = emaF[i]! - emaS[i]!
  }

  const firstValid = macdLine.findIndex(v => v !== null)
  if (firstValid === -1) {
    return {
      macdLine,
      signalLine: new Array(n).fill(null),
      histogram: new Array(n).fill(null),
    }
  }

  const validMacd = macdLine.filter(v => v !== null) as number[]
  const sigValid = ema(validMacd, signal)

  const signalLine: (number | null)[] = new Array(n).fill(null)
  let j = 0
  for (let i = firstValid; i < n; i++) signalLine[i] = sigValid[j++] ?? null

  const histogram: (number | null)[] = new Array(n).fill(null)
  for (let i = 0; i < n; i++) {
    if (macdLine[i] !== null && signalLine[i] !== null)
      histogram[i] = macdLine[i]! - signalLine[i]!
  }

  return { macdLine, signalLine, histogram }
}

function r5(v: number) {
  return Math.round(v * 100000) / 100000
}

export function calculateIndicators(candles: Candle[], periods: number[] = [20, 60]): Indicators {
  if (!candles.length) return { ma_lines: [], rsi: [], macd: [] }

  const closes = candles.map(c => c.close)
  const times  = candles.map(c => c.time)

  const toPoints = (vals: (number | null)[]): MAPoint[] =>
    vals
      .map((v, i) => (v !== null ? { time: times[i], value: r5(v) } : null))
      .filter(Boolean) as MAPoint[]

  const ma_lines = periods.map(period => ({
    period,
    data: toPoints(sma(closes, period)),
  }))

  const rsiVals = rsi(closes, 14)
  const { macdLine, signalLine, histogram } = macd(closes)

  const macdPoints: MACDPoint[] = []
  for (let i = 0; i < candles.length; i++) {
    if (macdLine[i] !== null && signalLine[i] !== null && histogram[i] !== null) {
      macdPoints.push({
        time:      times[i],
        macd:      r5(macdLine[i]!),
        signal:    r5(signalLine[i]!),
        histogram: r5(histogram[i]!),
      })
    }
  }

  return {
    ma_lines,
    rsi:  toPoints(rsiVals) as RSIPoint[],
    macd: macdPoints,
  }
}

export function elderImpulse(candles: Candle[]): ImpulseColor[] {
  const closes = candles.map(c => c.close)
  const n = closes.length
  if (n < 2) return new Array(n).fill('blue')

  const ema13 = ema(closes, 13)
  const { histogram: hist } = macd(closes)

  const colors: ImpulseColor[] = new Array(n).fill('blue')
  for (let i = 1; i < n; i++) {
    if (ema13[i] === null || ema13[i - 1] === null || hist[i] === null || hist[i - 1] === null) continue
    const emaRising = ema13[i]! > ema13[i - 1]!
    const emaFalling = ema13[i]! < ema13[i - 1]!
    const histRising = hist[i]! > hist[i - 1]!
    const histFalling = hist[i]! < hist[i - 1]!

    if (emaRising && histRising) colors[i] = 'green'
    else if (emaFalling && histFalling) colors[i] = 'red'
  }
  return colors
}
