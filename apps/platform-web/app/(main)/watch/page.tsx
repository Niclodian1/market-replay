'use client'

import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { computeSeries, getCandles, getStatsDetail } from '@/lib/api'
import SimpleChart from '@/components/chart/SimpleChart'
import { eventsToMarkers, seriesToHistogram, seriesToLines, useBarCloseRefresh } from '@/lib/chart-kit'
import { loadStaticWatch } from '@/lib/staticData'

const TIMEFRAMES = ['15min', '1h', '4h', '1day']
const EVENT_DEFAULTS = ['chanlun.buy3', 'chanlun.sell3', 'ma.breakout', 'macd.divergence']
const COLORS = ['#60a5fa', '#f97316', '#34d399', '#a78bfa']

function fmtTime(ts: number) {
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

export default function WatchPage() {
  const [symbol, setSymbol] = useState('BTC-USD')
  const [timeframe, setTimeframe] = useState('4h')
  const [showMa, setShowMa] = useState(true)
  const [showMacd, setShowMacd] = useState(false)
  const [showEvents, setShowEvents] = useState(true)
  const [focusTime, setFocusTime] = useState<number | undefined>()
  const [selected, setSelected] = useState<string | undefined>()

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('symbol')) setSymbol(params.get('symbol')!.toUpperCase())
    if (params.get('timeframe')) setTimeframe(params.get('timeframe')!)
    const t = params.get('t')
    if (t && Number.isFinite(Number(t))) setFocusTime(Number(t))
  }, [])

  const query = useQuery({
    queryKey: ['watch', symbol, timeframe],
    queryFn: async () => {
      const staticData = await loadStaticWatch(symbol, timeframe)
      if (staticData) return { ...staticData, source: 'static' as const }
      const payload = await getCandles(symbol, timeframe, 800)
      const candles = payload.candles
      const data = await computeSeries({
        symbol,
        timeframe,
        candles,
        series: [
          { id: 'ma', params: { periods: [20, 60] } },
          { id: 'macd' },
        ],
        events: true,
        indicators: EVENT_DEFAULTS,
      })
      return { ...(data as object), source: 'api' as const } as {
        source: 'static' | 'api'
        candles: { time: number; open: number; high: number; low: number; close: number }[]
        series: { key: string; label: string; panel: string; values: (number | null)[]; kind: string }[]
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
    },
    retry: false,
  })

  const lines = useMemo(
    () =>
      query.data
        ? seriesToLines(query.data.series, query.data.candles, { panel: 'price', colors: COLORS }).filter(
            () => showMa,
          )
        : [],
    [query.data, showMa],
  )

  const macdHistogram = useMemo(
    () => (query.data && showMacd ? seriesToHistogram(query.data.series, query.data.candles) : []),
    [query.data, showMacd],
  )

  const markers = useMemo(
    () => (query.data ? eventsToMarkers(query.data.events, { show: showEvents }) : []),
    [query.data, showEvents],
  )

  // 收盘对齐：K 线收盘后自动刷新一次，避免长时间盯盘看到旧数据
  useBarCloseRefresh(timeframe, () => {
    query.refetch()
  })

  const detailQ = useQuery({
    queryKey: ['watch', 'stats', selected],
    queryFn: () => getStatsDetail(selected!, { symbol, timeframe }),
    enabled: !!selected,
    retry: false,
  })

  const events = query.data?.events ?? []

  return (
    <div className="p-4 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold mr-2">盯盘</h1>
        <input
          className="input mono w-32"
          value={symbol}
          onChange={(event) => setSymbol(event.target.value.toUpperCase())}
          placeholder="BTC-USD"
        />
        <select className="input" value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
          {TIMEFRAMES.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1 text-xs">
          <input type="checkbox" checked={showMa} onChange={(event) => setShowMa(event.target.checked)} />
          均线
        </label>
        <label className="flex items-center gap-1 text-xs">
          <input type="checkbox" checked={showMacd} onChange={(event) => setShowMacd(event.target.checked)} />
          MACD
        </label>
        <label className="flex items-center gap-1 text-xs">
          <input type="checkbox" checked={showEvents} onChange={(event) => setShowEvents(event.target.checked)} />
          指标买卖点
        </label>
        <button className="btn small" onClick={() => query.refetch()}>
          刷新
        </button>
        <a className="btn small accent" href={`/stats?symbol=${symbol}&timeframe=${timeframe}`}>
          看统计
        </a>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_320px] gap-3">
        <div className="panel p-2">
          {query.isLoading && <div className="p-6 text-sm opacity-60">加载中…（首次计算缠论可能需要几秒）</div>}
          {query.error && (
            <div className="p-6 text-sm" style={{ color: 'var(--down)' }}>
              加载失败：{String((query.error as Error).message)}
            </div>
          )}
          {query.data && (
            <>
              <SimpleChart
                candles={query.data.candles}
                lines={lines}
                markers={markers}
                height={440}
                focusTime={focusTime}
              />
              {showMacd && (
                <SimpleChart
                  candles={query.data.candles}
                  showCandles={false}
                  histogram={macdHistogram}
                  height={120}
                />
              )}
            </>
          )}
        </div>

        <div className="panel p-3 overflow-auto" style={{ maxHeight: 620 }}>
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm font-semibold">触发事件</div>
            <span className="chip">{events.length}</span>
            {query.data?.source === 'static' && <span className="chip info">静态出库</span>}
          </div>
          <div className="space-y-1">
            {events
              .slice()
              .reverse()
              .slice(0, 200)
              .map((event) => (
                <button
                  key={event.uid}
                  className="w-full text-left p-2 rounded hover:bg-[var(--bg-2)]"
                  onClick={() => {
                    setSelected(event.indicator_id)
                    setFocusTime(event.confirmed_at)
                  }}
                >
                  <div className="flex items-center justify-between text-xs">
                    <span className={event.direction === 'long' ? 'chip up' : 'chip down'}>
                      {event.indicator_id}
                    </span>
                    <span className="mono opacity-70">{fmtTime(event.confirmed_at)}</span>
                  </div>
                  <div className="mono text-xs mt-1 opacity-80">
                    {event.direction === 'long' ? '买' : '卖'} @ {event.price}
                  </div>
                </button>
              ))}
            {!events.length && !query.isLoading && (
              <div className="text-xs opacity-60 p-2">
                暂无事件。先到「统计」页点一次「回填事件」，或换品种/周期。
              </div>
            )}
          </div>
        </div>
      </div>

      {selected && detailQ.data && (
        <div className="panel p-3 text-sm">
          <div className="font-semibold mb-1">{selected} 历史统计（{symbol} / {timeframe}）</div>
          <div className="flex flex-wrap gap-4 text-xs">
            <span>n = {detailQ.data.summary.n}</span>
            <span>胜率 = {(detailQ.data.summary.win_rate * 100).toFixed(1)}%</span>
            <span>期望 = {detailQ.data.summary.expectancy_r.toFixed(3)}R</span>
            <span>Wilson 下界 = {(detailQ.data.summary.wilson_low * 100).toFixed(1)}%</span>
          </div>
        </div>
      )}
    </div>
  )
}
