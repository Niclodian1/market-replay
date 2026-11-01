/**
 * chart-kit · K 线收盘对齐。
 *
 * 与后端 `app/core/scan_window.py` 同一套口径：周期秒数固定、UTC 边界对齐。
 * 前端只做「什么时候该刷新」，不在这里算指标。
 */
export const TIMEFRAME_MS: Record<string, number> = {
  '15min': 15 * 60 * 1000,
  '30min': 30 * 60 * 1000,
  '1h': 60 * 60 * 1000,
  '2h': 2 * 60 * 60 * 1000,
  '4h': 4 * 60 * 60 * 1000,
  '6h': 6 * 60 * 60 * 1000,
  '12h': 12 * 60 * 60 * 1000,
  '1day': 24 * 60 * 60 * 1000,
  '1week': 7 * 24 * 60 * 60 * 1000,
}

export function timeframeMs(timeframe: string): number | null {
  return TIMEFRAME_MS[timeframe.toLowerCase()] ?? null
}

/** 最近一根已收盘 K 线的收盘时间（毫秒）。 */
export function lastClosedBarMs(timeframe: string, nowMs: number): number | null {
  const size = timeframeMs(timeframe)
  if (!size) return null
  return Math.floor(nowMs / size) * size
}

/** 距下一根 K 线收盘还有多少毫秒；未知周期返回 null。 */
export function msUntilBarClose(timeframe: string, nowMs: number): number | null {
  const close = lastClosedBarMs(timeframe, nowMs)
  const size = timeframeMs(timeframe)
  if (close === null || !size) return null
  return close + size - nowMs
}

/** 收盘后多等一点再拉数据，避免交易所落库比收盘晚几秒。 */
export const REFRESH_GRACE_MS = 5_000

/** 下一次刷新的延迟（含 grace）；未知周期返回 null。 */
export function nextRefreshDelayMs(timeframe: string, nowMs: number): number | null {
  const remaining = msUntilBarClose(timeframe, nowMs)
  if (remaining === null) return null
  return remaining + REFRESH_GRACE_MS
}

/** 收盘对齐的定时器延迟：封顶 1 分钟轮询，保证长时间挂机也不会漂移。 */
export const MAX_TIMER_MS = 60_000
