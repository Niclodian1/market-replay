export type ChartMeasurementStats = {
  priceChange: number
  percentChange: number
  barCount: number
  durationSeconds: number
  isUp: boolean
}

export type CandleMovementStats = {
  bodyChange: number
  bodyPercent: number
  rangeChange: number
  rangePercent: number
  isUp: boolean
}

export function calculateCandleMovement(
  open: number,
  high: number,
  low: number,
  close: number,
): CandleMovementStats {
  const bodyChange = close - open
  const rangeChange = Math.max(0, high - low)
  return {
    bodyChange,
    bodyPercent: open === 0 ? 0 : bodyChange / open * 100,
    rangeChange,
    rangePercent: open === 0 ? 0 : rangeChange / open * 100,
    isUp: bodyChange >= 0,
  }
}

export function calculateChartMeasurement(
  startPrice: number,
  endPrice: number,
  startTime: number,
  endTime: number,
  startIndex: number,
  endIndex: number,
): ChartMeasurementStats {
  const priceChange = endPrice - startPrice
  return {
    priceChange,
    percentChange: startPrice === 0 ? 0 : priceChange / startPrice * 100,
    barCount: Math.abs(endIndex - startIndex),
    durationSeconds: Math.abs(endTime - startTime),
    isUp: priceChange >= 0,
  }
}

export function formatMeasureDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  if (total < 60) return `${total}秒`
  if (total < 3600) return `${Math.floor(total / 60)}分钟`
  if (total < 86400) {
    const hours = Math.floor(total / 3600)
    const minutes = Math.floor((total % 3600) / 60)
    return minutes ? `${hours}小时${minutes}分钟` : `${hours}小时`
  }
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  return hours ? `${days}天${hours}小时` : `${days}天`
}
