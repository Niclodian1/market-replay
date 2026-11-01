/**
 * chart-kit · 统一序列 → 图表图层。
 *
 * 所有图（看盘 / 图表 / 练习）都从 `POST /api/series` 的同一个 payload 取数，
 * 只在这里做一次「序列 → 线/柱/标记」的映射，页面不再各写一遍。
 */
import type { ChartLine, ChartMarker } from '@/components/chart/SimpleChart'

export type SeriesRow = {
  key: string
  label: string
  panel: string
  values: (number | null)[]
  kind?: string
}

export type CandleLike = { time: number }

export type EventLike = {
  uid?: string
  indicator_id: string
  direction: string
  confirmed_at: number
  bar_time?: number
  price?: number
  title?: string
}

export const DEFAULT_LINE_COLORS = ['#60a5fa', '#f97316', '#34d399', '#a78bfa', '#f472b6', '#facc15']

/** 序列值按索引对齐 K 线时间，去掉空值与无效值。 */
export function alignValues(candles: CandleLike[], values: (number | null)[]): { time: number; value: number }[] {
  const out: { time: number; value: number }[] = []
  for (let i = 0; i < values.length; i += 1) {
    const value = values[i]
    const time = candles[i]?.time
    if (time === undefined || value === null || value === undefined) continue
    if (!Number.isFinite(value)) continue
    out.push({ time, value })
  }
  return out
}

/** 取某个 panel 的序列（默认 price），可选按 key 白名单过滤。 */
export function pickSeries(rows: SeriesRow[], panel = 'price', keys?: string[]): SeriesRow[] {
  const wanted = keys && keys.length ? new Set(keys) : null
  return rows.filter((row) => row.panel === panel && (!wanted || wanted.has(row.key)))
}

/** 序列 → lightweight-charts 折线；颜色按顺序轮转，保证同一 key 稳定同色。 */
export function seriesToLines(
  rows: SeriesRow[],
  candles: CandleLike[],
  options: { panel?: string; keys?: string[]; colors?: string[] } = {},
): ChartLine[] {
  const colors = options.colors ?? DEFAULT_LINE_COLORS
  const picked = pickSeries(rows, options.panel ?? 'price', options.keys)
  const used = new Map<string, string>()
  rows.forEach((row, index) => used.set(row.key, colors[index % colors.length]))
  return picked.map((row, index) => ({
    id: row.key,
    label: row.label || row.key,
    color: used.get(row.key) ?? colors[index % colors.length],
    data: alignValues(candles, row.values),
  }))
}

/** 序列 → 直方图（MACD hist 等），零值直接丢掉避免图上留空档。 */
export function seriesToHistogram(
  rows: SeriesRow[],
  candles: CandleLike[],
  key = 'macd_hist',
): { time: number; value: number }[] {
  const row = rows.find((item) => item.key === key)
  if (!row) return []
  return alignValues(candles, row.values).filter((point) => point.value !== 0)
}

/** 事件 → 图上买卖标记（同一时间多个事件合并文本，避免重叠成一团）。 */
export function eventsToMarkers(
  events: EventLike[],
  options: { show?: boolean; maxText?: number } = {},
): ChartMarker[] {
  if (options.show === false) return []
  const byKey = new Map<string, ChartMarker>()
  for (const event of events) {
    const kind: ChartMarker['kind'] = event.direction === 'long' ? 'buy' : 'sell'
    const label = (event.indicator_id.split('.').pop() || event.indicator_id).slice(0, options.maxText ?? 12)
    const time = event.confirmed_at
    const mapKey = `${time}-${kind}`
    const existing = byKey.get(mapKey)
    if (existing) {
      if (existing.text && !existing.text.includes(label)) existing.text = `${existing.text}/${label}`
      continue
    }
    byKey.set(mapKey, { time, kind, text: label, price: event.price })
  }
  return Array.from(byKey.values()).sort((a, b) => a.time - b.time)
}

/**
 * 统一序列 → 图表页 `Indicators` 结构（MA / RSI / MACD）。
 *
 * 图表页原本自己用 `calculateIndicators` 算 MA/MACD/RSI；改为优先用
 * `POST /api/series` 的同一份序列，页面不再有第二套指标实现。
 */
export function seriesToIndicators(
  rows: SeriesRow[],
  candles: CandleLike[],
  options: { periods?: number[] } = {},
): { ma_lines: { period: number; data: { time: number; value: number }[] }[]; rsi: { time: number; value: number }[]; macd: { time: number; macd: number; signal: number; histogram: number }[] } {
  const byKey = new Map(rows.map((row) => [row.key, row]))
  const periods = options.periods && options.periods.length ? options.periods : [20, 60]

  const ma_lines = periods
    .map((period) => {
      const row = byKey.get(`ma_sma_${period}`)
      if (!row) return null
      return { period, data: alignValues(candles, row.values) }
    })
    .filter((item): item is { period: number; data: { time: number; value: number }[] } => item !== null)

  const rsiRow = byKey.get('rsi')
  const rsi = rsiRow ? alignValues(candles, rsiRow.values) : []

  const dif = byKey.get('macd_dif')
  const dea = byKey.get('macd_dea')
  const hist = byKey.get('macd_hist')
  const macd: { time: number; macd: number; signal: number; histogram: number }[] = []
  if (dif && dea && hist) {
    const difMap = new Map(alignValues(candles, dif.values).map((p) => [p.time, p.value]))
    const deaMap = new Map(alignValues(candles, dea.values).map((p) => [p.time, p.value]))
    for (const point of alignValues(candles, hist.values)) {
      const macdValue = difMap.get(point.time)
      const signal = deaMap.get(point.time)
      if (macdValue === undefined || signal === undefined) continue
      macd.push({ time: point.time, macd: macdValue, signal, histogram: point.value })
    }
  }

  return { ma_lines, rsi, macd }
}
