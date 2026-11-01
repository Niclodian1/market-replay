'use client'

import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { computeSeries, getCandles, getEvents } from '@/lib/api'
import SimpleChart from '@/components/chart/SimpleChart'
import { eventsToMarkers, seriesToLines } from '@/lib/chart-kit'
import {
  addLeg,
  buildView,
  clampCursor,
  closePosition,
  createPosition,
  defaultStart,
  filterEvents,
  nextReviewTime,
  positionStats,
  randomStart,
  revealedStats,
  type ClosedPosition,
  type PracticeEvent,
  type PracticePosition,
} from '@/lib/practiceCore'
import { loadPracticeData, savePracticeData } from '@/lib/practiceCache'

const TIMEFRAMES = ['15min', '1h', '4h', '1day']
const INDICATORS = ['chanlun.buy3', 'chanlun.sell3', 'ma.breakout', 'macd.divergence']

export default function PracticePage() {
  const [symbol, setSymbol] = useState('BTC-USD')
  const [timeframe, setTimeframe] = useState('4h')
  const [cursor, setCursor] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [revealed, setRevealed] = useState<Record<string, boolean>>({})
  const [activeIndicators, setActiveIndicators] = useState<string[]>(INDICATORS)
  const [showMa, setShowMa] = useState(true)
  const [showMacd, setShowMacd] = useState(false)
  // 加仓：一笔持仓由多条腿组成，平仓时按加权 R 结算
  const [position, setPosition] = useState<PracticePosition | null>(null)
  const [closedPositions, setClosedPositions] = useState<ClosedPosition[]>([])

  const query = useQuery({
    queryKey: ['practice', symbol, timeframe],
    queryFn: async () => {
      const cacheKey = `${symbol}:${timeframe}`
      try {
        const payload = await getCandles(symbol, timeframe, 800)
      const candles = payload.candles
      const data = await computeSeries({
        symbol,
        timeframe,
        candles,
        series: [{ id: 'ma', params: { periods: [20, 60] } }, { id: 'macd' }],
        events: true,
        indicators: INDICATORS,
      })
      // 只取当前练习窗口内的事件：/api/events 按 confirmed_at 升序并截断 limit，
      // 不带窗口时库里事件超过 limit 会把最新的（也就是正在练的）全截掉 → 揭晓永远 0/N。
      const windowStart = candles[0]?.time
      const windowEnd = candles[candles.length - 1]?.time
      const outcomes = await getEvents({
        symbol,
        timeframe,
        with_outcomes: true,
        start: windowStart,
        end: windowEnd,
        limit: 5000,
      })
      const outcomeMap: Record<string, { won: boolean; r_multiple: number; exit_reason: string; censored?: boolean }> = {}
        for (const event of outcomes.events ?? []) {
          if (event.outcome) outcomeMap[event.uid] = event.outcome
        }
        const result = { ...data, outcomeMap, cached: false }
        savePracticeData(cacheKey, result)
        return result
      } catch (error) {
        const cached = loadPracticeData<{ candles: unknown[]; cached: boolean }>(cacheKey)
        if (cached) return { ...cached, cached: true }
        throw error
      }
    },
    retry: false,
  })

  const bars = query.data?.candles ?? []
  const events = useMemo(() => query.data?.events ?? [], [query.data?.events])
  const outcomes = query.data?.outcomeMap ?? {}
  const filteredEvents = useMemo(() => filterEvents(events, activeIndicators), [events, activeIndicators])

  useEffect(() => {
    if (bars.length) setCursor(defaultStart(bars.length))
  }, [query.data?.candles?.length]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!playing) return
    const timer = setInterval(() => {
      setCursor((value) => {
        if (value >= bars.length - 1) {
          setPlaying(false)
          return value
        }
        return value + 1
      })
    }, 220)
    return () => clearInterval(timer)
  }, [playing, bars.length])

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'ArrowRight') setCursor((value) => clampCursor(value + 1, bars.length))
      if (event.key === 'ArrowLeft') setCursor((value) => clampCursor(value - 1, bars.length))
      if (event.key === ' ') {
        event.preventDefault()
        setPlaying((value) => !value)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [bars.length])

  const view = useMemo(() => buildView(bars, filteredEvents, cursor), [bars, filteredEvents, cursor])
  const visibleCandles = view.candles
  const visibleTime = view.visibleTime
  const visibleEvents = view.events
  const stats = useMemo(() => revealedStats(visibleEvents, outcomes, revealed), [visibleEvents, outcomes, revealed])
  const reviewTarget = useMemo(
    () => nextReviewTime(events, outcomes, revealed, visibleTime),
    [events, outcomes, revealed, visibleTime],
  )
  const jumpToReview = () => {
    if (reviewTarget == null) return
    const index = bars.findIndex((bar: { time: number }) => bar.time >= reviewTarget)
    setPlaying(false)
    setCursor(clampCursor(index === -1 ? bars.length - 1 : index, bars.length))
  }

  const lines = useMemo(() => {
    const rows = query.data?.series ?? []
    if (!rows.length) return []
    const price = showMa ? seriesToLines(rows, visibleCandles, { panel: 'price' }) : []
    const macd = showMacd
      ? seriesToLines(rows, visibleCandles, {
          panel: 'sub',
          keys: ['macd_dif', 'macd_dea'],
          colors: ['#38bdf8', '#f472b6'],
        })
      : []
    return [...price, ...macd]
  }, [query.data, visibleCandles, showMa, showMacd])

  const markers = useMemo(
    () => eventsToMarkers(visibleEvents as PracticeEvent[], { show: true }),
    [visibleEvents],
  )

  const openPosition = (event: PracticeEvent) => {
    setPosition((current) => current ?? createPosition(event))
  }

  const addToPosition = (event: PracticeEvent) => {
    setPosition((current) => (current ? addLeg(current, event) ?? current : createPosition(event)))
  }

  const closeCurrent = () => {
    if (!position) return
    const closed = closePosition(position, outcomes, visibleTime)
    setPosition(null)
    if (closed) setClosedPositions((list) => [...list, closed])
  }

  const posStats = useMemo(() => positionStats(closedPositions), [closedPositions])

  return (
    <div className="p-4 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold mr-2">练习</h1>
        {query.data?.cached && <span className="chip info">离线缓存</span>}
        <input className="input mono w-32" value={symbol} onChange={(event) => setSymbol(event.target.value.toUpperCase())} />
        <select className="input" value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
          {TIMEFRAMES.map((item) => (
            <option key={item}>{item}</option>
          ))}
        </select>
        <button className="btn small" onClick={() => setCursor((value) => clampCursor(value - 10, bars.length))}>
          « 10
        </button>
        <button className="btn small" onClick={() => setCursor((value) => clampCursor(value - 1, bars.length))}>
          « 1
        </button>
        <button className="btn small accent" onClick={() => setPlaying((value) => !value)}>
          {playing ? '暂停' : '播放'}
        </button>
        <button className="btn small" onClick={() => setCursor((value) => clampCursor(value + 1, bars.length))}>
          1 »
        </button>
        <button className="btn small" onClick={() => setCursor((value) => clampCursor(value + 10, bars.length))}>
          10 »
        </button>
        <button className="btn small" onClick={() => setCursor(defaultStart(bars.length))}>
          重置
        </button>
        <button className="btn small" onClick={() => setCursor(randomStart(bars.length))}>
          随机起点
        </button>
        <button className="btn small" onClick={jumpToReview} disabled={reviewTarget == null}>
          错题重练
        </button>
        <button className={showMa ? 'btn small accent' : 'btn small'} onClick={() => setShowMa((value) => !value)}>
          MA 数值
        </button>
        <button className={showMacd ? 'btn small accent' : 'btn small'} onClick={() => setShowMacd((value) => !value)}>
          MACD 数值
        </button>
        <span className="text-xs opacity-60 mono">
          {cursor + 1}/{bars.length} · {visibleTime ? new Date(visibleTime * 1000).toLocaleString('zh-CN', { hour12: false }) : '—'}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-1">
        <span className="text-xs opacity-60 mr-1">指标图层</span>
        {INDICATORS.map((item) => {
          const on = activeIndicators.includes(item)
          return (
            <button
              key={item}
              className={on ? 'btn small accent' : 'btn small'}
              onClick={() =>
                setActiveIndicators((value) =>
                  value.includes(item) ? value.filter((id) => id !== item) : [...value, item],
                )
              }
            >
              {item}
            </button>
          )
        })}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_300px] gap-3">
        <div className="panel p-2">
          {query.isLoading && <div className="p-6 text-sm opacity-60">加载中…</div>}
          {query.error && (
            <div className="p-6 text-sm" style={{ color: 'var(--down)' }}>
              {String((query.error as Error).message)}
            </div>
          )}
          {!!visibleCandles.length && (
            // lockRightEdge：盲测时视口锁在最后一根已收盘 K 线，拖不到未来
            <SimpleChart candles={visibleCandles} lines={lines} markers={markers} height={460} lockRightEdge />
          )}
        </div>

        <div className="panel p-3 overflow-auto" style={{ maxHeight: 620 }}>
          <div className="text-sm font-semibold mb-2">持仓（加仓）</div>
          {position ? (
            <div className="text-xs space-y-1 mb-3">
              <div className="mono">
                {position.direction === 'long' ? '多' : '空'} · {position.legs.length} 条腿 · 均价{' '}
                {position.avgPrice.toFixed(4)} · 权重 {position.totalWeight}
              </div>
              <div className="text-[11px] mono opacity-70">
                {position.legs
                  .map((leg) => `${leg.uid.slice(0, 6)}@${leg.price}×${leg.weight}`)
                  .join(' / ')}
              </div>
              <button className="btn small danger" onClick={closeCurrent}>
                平仓结算
              </button>
            </div>
          ) : (
            <div className="text-xs opacity-60 mb-3">
              在事件卡片点「进场」开仓，同向事件可「加仓」（最多 3 条腿），反向事件点「平仓」结算。
            </div>
          )}
          {posStats.n > 0 && (
            <div className="text-[11px] mono opacity-80 mb-3">
              已平仓 {posStats.n} 笔 · 胜 {posStats.wins} 负 {posStats.losses} · 命中率{' '}
              {(posStats.hitRate * 100).toFixed(1)}% · 加权均 {posStats.avgR.toFixed(2)}R
            </div>
          )}

          <div className="text-sm font-semibold mb-2">练习记录（{visibleEvents.length}）</div>
          <div className="text-xs mb-2 mono opacity-80">
            已揭晓 {stats.revealed}/{stats.total} · 胜 {stats.wins} 负 {stats.losses}
            {stats.censored > 0 ? ` · 截尾 ${stats.censored}` : ''} · 命中率{' '}
            {(stats.hitRate * 100).toFixed(1)}% · 平均 {stats.avgR.toFixed(2)}R
          </div>
          {stats.byIndicator.length > 0 && (
            <div className="text-[11px] mono opacity-70 mb-2 space-y-0.5">
              {stats.byIndicator.map((item) => (
                <div key={item.indicator_id}>
                  {item.indicator_id}: n={item.n} 胜率 {(item.hitRate * 100).toFixed(1)}% 均 {item.avgR.toFixed(2)}R
                </div>
              ))}
            </div>
          )}
          <div className="space-y-1">
            {visibleEvents
              .slice()
              .reverse()
              .slice(0, 120)
              .map((event: {
                uid: string
                indicator_id: string
                direction: string
                confirmed_at: number
                price: number
              }) => {
                const outcome = query.data?.outcomeMap?.[event.uid]
                const shown = revealed[event.uid]
                return (
                  <div key={event.uid} className="p-2 rounded border border-[var(--line-1)] text-xs space-y-1">
                    <div className="flex items-center justify-between">
                      <span className={event.direction === 'long' ? 'chip up' : 'chip down'}>{event.indicator_id}</span>
                      <span className="mono opacity-60">
                        {new Date(event.confirmed_at * 1000).toLocaleDateString('zh-CN')}
                      </span>
                    </div>
                    <div className="mono opacity-80">
                      {event.direction === 'long' ? '买' : '卖'} @ {event.price}
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {outcome ? (
                        shown ? (
                          <span
                            className="mono"
                            style={{
                              color: outcome.censored
                                ? 'var(--fg-3)'
                                : outcome.r_multiple > 0
                                  ? 'var(--up)'
                                  : 'var(--down)',
                            }}
                          >
                            结果：{outcome.censored ? '截尾(未走完)' : outcome.exit_reason} /{' '}
                            {outcome.r_multiple >= 0 ? '+' : ''}
                            {outcome.r_multiple.toFixed(2)}R
                          </span>
                        ) : (
                          <button
                            className="btn small"
                            onClick={() => setRevealed((value) => ({ ...value, [event.uid]: true }))}
                          >
                            揭晓结果
                          </button>
                        )
                      ) : (
                        <span className="opacity-50">（该事件还没有结果标注）</span>
                      )}
                      {!position && (
                        <button className="btn small" onClick={() => openPosition(event as PracticeEvent)}>
                          进场
                        </button>
                      )}
                      {position && position.direction === event.direction && position.legs.length < 3 && (
                        <button className="btn small accent" onClick={() => addToPosition(event as PracticeEvent)}>
                          加仓
                        </button>
                      )}
                      {position && position.direction !== event.direction && (
                        <button className="btn small danger" onClick={closeCurrent}>
                          平仓
                        </button>
                      )}
                    </div>
                  </div>
                )
              })}
            {!visibleEvents.length && <div className="text-xs opacity-60">当前时点之前没有指标触发。</div>}
          </div>
        </div>
      </div>
    </div>
  )
}
