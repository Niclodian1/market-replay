/**
 * 静态数据出库的读取端：优先吃 `public/data/manifest.json`，
 * 缺失时返回 null，调用方回退到实时 API。
 */
export type StaticWatchData = {
  symbol: string
  timeframe: string
  generated_at: string
  candles: { time: number; open: number; high: number; low: number; close: number; volume?: number }[]
  series: { key: string; label: string; panel: string; kind: string; values: (number | null)[] }[]
  events: {
    uid: string
    indicator_id: string
    direction: string
    bar_time: number
    confirmed_at: number
    price: number
    strength?: number
    title?: string
  }[]
}

const BASE = '/data'

const STATIC_SYMBOL_ALIASES: Record<string, string[]> = {
  '^NDX': ['^NDX', 'NDX', '^IXIC'],
  'NDX': ['NDX', '^NDX', '^IXIC'],
  '^IXIC': ['^IXIC', 'NDX', '^NDX'],
  '纳指': ['NDX', '^NDX', '^IXIC'],
  '纳斯达克': ['NDX', '^NDX', '^IXIC'],
  '纳斯达克100': ['NDX', '^NDX'],
  '^GSPC': ['^GSPC', 'SPX'],
  'SPX': ['SPX', '^GSPC'],
  '标普500': ['SPX', '^GSPC'],
  '^N225': ['^N225', 'N225'],
  'N225': ['N225', '^N225'],
}

export async function loadStaticWatch(
  symbol: string,
  timeframe: string,
): Promise<StaticWatchData | null> {
  try {
    const manifestResponse = await fetch(`${BASE}/manifest.json`, { cache: 'no-store' })
    if (!manifestResponse.ok) return null
    const manifest = await manifestResponse.json()
    const rawSym = symbol.trim()
    const upSym = rawSym.toUpperCase()
    const candidates = new Set<string>([
      upSym,
      upSym.replace(/^\^/, ''),
      ...(STATIC_SYMBOL_ALIASES[rawSym] ?? []),
      ...(STATIC_SYMBOL_ALIASES[upSym] ?? []),
    ])
    const entry = (manifest?.symbols ?? []).find(
      (item: { symbol: string }) => candidates.has(String(item.symbol || '').toUpperCase()),
    )
    const info = entry?.timeframes?.[timeframe]
    if (!info?.path) return null
    const fileResponse = await fetch(`${BASE}/${info.path}`, { cache: 'no-store' })
    if (!fileResponse.ok) return null
    return (await fileResponse.json()) as StaticWatchData
  } catch {
    return null
  }
}
