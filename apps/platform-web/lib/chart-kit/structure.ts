/**
 * chart-kit · 缠论 overlay 的增量合并。
 *
 * 收盘刷新时只重算最近 N 根 K 线（后端一次跑全量很贵），因此需要把新结果
 * 合并进已有图层：同一时间点以新结果为准，更早的点保留。
 */
export type StructureItem = unknown

export type ChanlunStructure = {
  strokes?: StructureItem[]
  segments?: StructureItem[]
  pivots_stroke?: StructureItem[]
  pivots_segment?: StructureItem[]
  buy_sell_points?: StructureItem[]
  beichi_points?: StructureItem[]
  top_fractals?: StructureItem[]
  bot_fractals?: StructureItem[]
  meta?: Record<string, unknown>
}

/** 列表元素的时间键：数组型（[t, p, ...]）取首元素，对象型取 t/time/t1。 */
export function itemTimeKey(item: StructureItem): number | null {
  if (Array.isArray(item)) {
    return typeof item[0] === 'number' ? item[0] : null
  }
  if (item && typeof item === 'object') {
    const record = item as Record<string, unknown>
    for (const field of ['t', 'time', 't1']) {
      const value = record[field]
      if (typeof value === 'number') return value
    }
  }
  return null
}

export function tailCandles<T>(candles: T[], limit = 600): T[] {
  if (!Number.isFinite(limit) || limit <= 0 || candles.length <= limit) return candles
  return candles.slice(-limit)
}

/**
 * 合并两版 overlay：逐列表按时间键去重，fresh 覆盖同键，按时间排序，
 * 超过 maxItems 只保留最近的（默认 2000，避免长时间挂机内存无上限增长）。
 */
export function mergeStructure<T extends ChanlunStructure>(
  previous: T | null | undefined,
  fresh: T | null | undefined,
  options: { maxItems?: number } = {},
): T | null {
  if (!fresh) return previous ?? null
  if (!previous) return fresh
  const maxItems = options.maxItems ?? 2000
  const out: Record<string, unknown> = { ...(fresh as Record<string, unknown>) }
  const prevRecord = previous as Record<string, unknown>
  for (const [key, freshList] of Object.entries(fresh)) {
    if (!Array.isArray(freshList)) continue
    const prevList = prevRecord[key]
    if (!Array.isArray(prevList)) continue
    const merged = new Map<number | string, StructureItem>()
    for (const item of prevList) {
      const time = itemTimeKey(item)
      merged.set(time === null ? `p${merged.size}` : time, item)
    }
    for (const item of freshList) {
      const time = itemTimeKey(item)
      merged.set(time === null ? `f${merged.size}` : time, item)
    }
    const list = Array.from(merged.entries())
      .sort((a, b) =>
        typeof a[0] === 'number' && typeof b[0] === 'number'
          ? a[0] - b[0]
          : String(a[0]).localeCompare(String(b[0])),
      )
      .map(([, value]) => value)
    out[key] = list.slice(-maxItems)
  }
  return out as T
}
