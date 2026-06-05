'use client'

import { useEffect, useState, useCallback, useMemo, useRef } from 'react'
import Link from 'next/link'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getCandles, getQuote, addWatchlist, getWatchlist } from '@/lib/api'
import { useOpportunity } from '@/lib/api/opportunities'
import { calculateIndicators, elderImpulse } from '@/lib/indicators'
import { CandlestickChart } from '@/components/chart/CandlestickChart'
import { TradePlanOverlay } from '@/components/chart/TradePlanOverlay'
import { useAppStore } from '@/store/useAppStore'
import type { Timeframe, Candle, Indicators, WatchlistItem, ImpulseColor } from '@/types'

const TIMEFRAMES: { label: string; value: Timeframe }[] = [
  { label: '15m', value: '15min' },
  { label: '1H',  value: '1h'   },
  { label: '4H',  value: '4h'   },
  { label: '1D',  value: '1day' },
  { label: '1W',  value: '1week'},
]

const EMPTY_IND: Indicators = { ma_lines: [], rsi: [], macd: [] }

function fmtPrice(v: number): string {
  if (v >= 10000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 })
  if (v >= 100) return v.toFixed(2)
  if (v >= 1) return v.toFixed(4)
  return v.toFixed(6)
}

export default function ChartPage({ params, searchParams }: { params: { symbol: string }; searchParams?: { planId?: string } }) {
  const symbol = decodeURIComponent(params.symbol)
  const planId = Number(searchParams?.planId)

  const [timeframe, setTimeframe]         = useState<Timeframe>('1day')
  const [logScale, setLogScale]           = useState(false)
  const [allCandles, setAllCandles]       = useState<Candle[]>([])
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [hasMore, setHasMore]             = useState(true)
  const [addedMsg, setAddedMsg]           = useState('')
  const [isFullscreen, setIsFullscreen]   = useState(false)

  const loadingMoreRef = useRef(false)
  const queryClient = useQueryClient()

  const setCurrentSymbol = useAppStore(s => s.setCurrentSymbol)
  const maPeriods    = useAppStore(s => s.maPeriods)
  const setMaPeriods = useAppStore(s => s.setMaPeriods)
  const impulseEnabled  = useAppStore(s => s.elderImpulse)
  const setImpulseEnabled = useAppStore(s => s.setElderImpulse)

  useEffect(() => {
    setCurrentSymbol(symbol)
    return () => setCurrentSymbol(null)
  }, [symbol, setCurrentSymbol])

  useEffect(() => {
    setAllCandles([])
    setHasMore(true)
    loadingMoreRef.current = false
  }, [symbol, timeframe])

  const { data: initialData, isLoading: candlesLoading, isError: candlesError, error: candlesErr } = useQuery({
    queryKey: ['candles', symbol, timeframe],
    queryFn: () => getCandles(symbol, timeframe, 500),
    retry: 1,
  })

  useEffect(() => {
    if (initialData?.candles?.length) {
      setAllCandles(initialData.candles)
      setHasMore(initialData.candles.length >= 490)
    }
  }, [initialData])

  const { data: quote } = useQuery({
    queryKey: ['quote', symbol],
    queryFn: () => getQuote(symbol),
    refetchInterval: 30_000,
    retry: 1,
  })

  const { data: watchlistData } = useQuery<{ items: WatchlistItem[] }>({
    queryKey: ['watchlist'],
    queryFn: () => getWatchlist(),
    staleTime: 60_000,
  })
  const isInWatchlist = watchlistData?.items.some(w => w.symbol === symbol) ?? false

  const { data: opportunityData } = useOpportunity(Number.isFinite(planId) ? planId : NaN)
  const tradePlan = opportunityData?.symbol === symbol ? opportunityData.trade_plan : null

  const addMutation = useMutation({
    mutationFn: () => addWatchlist({ symbol, name: quote?.name || symbol, asset_type: 'stock' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['watchlist'] })
      setAddedMsg('已加入自选')
      setTimeout(() => setAddedMsg(''), 2000)
    },
    onError: () => {
      setAddedMsg('操作失败')
      setTimeout(() => setAddedMsg(''), 2000)
    },
  })

  const indicators: Indicators = useMemo(
    () => (allCandles.length ? calculateIndicators(allCandles, maPeriods) : EMPTY_IND),
    [allCandles, maPeriods]
  )

  const impulseColors: ImpulseColor[] | undefined = useMemo(
    () => (impulseEnabled && allCandles.length ? elderImpulse(allCandles) : undefined),
    [allCandles, impulseEnabled]
  )

  const loadMoreCandles = useCallback(async () => {
    if (loadingMoreRef.current || !hasMore || !allCandles.length) return
    loadingMoreRef.current = true
    setIsLoadingMore(true)
    const before = allCandles[0].time
    try {
      const data = await getCandles(symbol, timeframe, 500, before)
      const newCandles: Candle[] = data?.candles ?? []
      if (!newCandles.length) { setHasMore(false); return }
      setAllCandles(prev => {
        const oldestExisting = prev[0]?.time ?? Infinity
        const fresh = newCandles.filter(c => c.time < oldestExisting)
        if (!fresh.length) { setHasMore(false); return prev }
        return [...fresh, ...prev]
      })
      if (newCandles.length < 490) setHasMore(false)
    } catch (e) {
      console.error('loadMore failed', e)
    } finally {
      loadingMoreRef.current = false
      setIsLoadingMore(false)
    }
  }, [allCandles, hasMore, symbol, timeframe])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isFullscreen) setIsFullscreen(false)
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [isFullscreen])

  const errMsg = candlesError
    ? ((candlesErr as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? '数据加载失败')
    : null

  const last = allCandles[allCandles.length - 1]
  const prev = allCandles[allCandles.length - 2]
  const up = (quote?.change_percent ?? 0) >= 0

  const pageContent = (
    <div className="page" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
      <div style={{ padding: '12px 16px 10px', borderBottom: '1px solid var(--line-1)', background: 'var(--bg-1)', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 16, flexWrap: 'wrap' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
              <span className="mono" style={{ fontSize: 22, fontWeight: 700, color: 'var(--fg-0)', letterSpacing: '-0.01em' }}>{symbol}</span>
              {quote?.name && <span style={{ color: 'var(--fg-2)', fontSize: 12 }}>{quote.name}</span>}
            </div>
            {quote && (
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginTop: 4 }}>
                <span className="mono" style={{ fontSize: 26, fontWeight: 600, color: 'var(--fg-0)', letterSpacing: '-0.02em' }}>
                  {fmtPrice(quote.price)}
                </span>
                <span className={`mono ${up ? 'up' : 'down'}`} style={{ fontSize: 13, fontWeight: 600 }}>
                  {up ? '▲' : '▼'} {up ? '+' : ''}{quote.change.toFixed(2)}
                </span>
                <span className={`chip ${up ? 'up' : 'down'}`}>
                  {up ? '+' : ''}{quote.change_percent.toFixed(2)}%
                </span>
              </div>
            )}
          </div>

          {last && prev && (
            <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
              {[
                { l: '开盘', v: last.open.toFixed(2) },
                { l: '最高', v: last.high.toFixed(2) },
                { l: '最低', v: last.low.toFixed(2) },
                { l: '前收', v: prev.close.toFixed(2) },
                { l: '成交量', v: last.volume >= 1e6 ? (last.volume / 1e6).toFixed(2) + 'M' : (last.volume / 1e3).toFixed(0) + 'K' },
              ].map((x, i) => (
                <div key={i} className="stat">
                  <div className="stat-label">{x.l}</div>
                  <div className="mono" style={{ fontSize: 13, color: 'var(--fg-0)' }}>{x.v}</div>
                </div>
              ))}
            </div>
          )}

          <div style={{ flex: 1 }} />

          <div style={{ display: 'flex', gap: 6 }}>
            <button
              className="btn"
              onClick={() => addMutation.mutate()}
              disabled={isInWatchlist || addMutation.isPending}
              style={{ opacity: isInWatchlist ? 0.6 : 1 }}
            >
              <svg className="ico-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 3l2.6 5.6 6 .8-4.4 4.3 1 6L12 16.9 6.8 19.7l1-6L3.4 9.4l6-.8L12 3z" />
              </svg>
              {addedMsg || (isInWatchlist ? '已自选' : '加入自选')}
            </button>
            <Link href={`/replay/${encodeURIComponent(symbol)}`} className="btn primary">
              <svg className="ico-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="9" /><path d="M10 8l6 4-6 4V8z" fill="currentColor" stroke="none" />
              </svg>
              进入复盘
            </Link>
          </div>
        </div>
      </div>

      {/* Toolbar */}
      <div style={{ padding: '8px 16px', display: 'flex', alignItems: 'center', gap: 10, borderBottom: '1px solid var(--line-1)', background: 'var(--bg-1)', flexShrink: 0 }}>
        <div className="tweak-segment">
          {TIMEFRAMES.map(tf => (
            <button key={tf.value} className={timeframe === tf.value ? 'on' : ''} onClick={() => setTimeframe(tf.value)}
              style={{ minWidth: 44, fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '.04em' }}>
              {tf.label}
            </button>
          ))}
        </div>
        <div className="mr-topbar-divider" />
        <button className={`btn small${logScale ? '' : ' ghost'}`} onClick={() => setLogScale(v => !v)}>
          对数刻度
        </button>
        <button className={`btn small${impulseEnabled ? '' : ' ghost'}`} onClick={() => setImpulseEnabled(!impulseEnabled)}>
          <span style={{ display: 'inline-flex', gap: 3, alignItems: 'center' }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: impulseEnabled ? '#22c55e' : 'var(--fg-3)' }} />
            Impulse
          </span>
        </button>
        <button className="btn small ghost" onClick={() => setIsFullscreen(v => !v)} title={isFullscreen ? '退出全屏 (Esc)' : '全屏'}>
          {isFullscreen ? (
            <svg style={{ width: 13, height: 13 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M8 3v3a2 2 0 0 1-2 2H3M21 8h-3a2 2 0 0 1-2-2V3M3 16h3a2 2 0 0 1 2 2v3M16 21v-3a2 2 0 0 1 2-2h3" />
            </svg>
          ) : (
            <svg style={{ width: 13, height: 13 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3" />
            </svg>
          )}
        </button>
        <div style={{ flex: 1 }} />
        {isLoadingMore && (
          <span className="mono" style={{ fontSize: 10.5, color: 'var(--fg-2)' }}>加载历史…</span>
        )}
        <span className="mono" style={{ fontSize: 10.5, color: 'var(--fg-2)' }}>
          {allCandles.length} 根K线
        </span>
      </div>

      {/* Chart */}
      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        {candlesLoading && (
          <div style={{ height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, color: 'var(--fg-2)' }}>
            <div style={{ width: 28, height: 28, border: '2.5px solid var(--accent)', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.7s linear infinite' }} />
            <span className="mono" style={{ fontSize: 11.5 }}>加载 {symbol} · {timeframe}…</span>
          </div>
        )}
        {errMsg && (
          <div style={{ height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10, color: 'var(--down)' }}>
            <span style={{ fontSize: 13 }}>{errMsg}</span>
          </div>
        )}
        {!candlesLoading && !errMsg && allCandles.length === 0 && (
          <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fg-3)', fontSize: 13 }}>
            暂无数据
          </div>
        )}
        {allCandles.length > 0 && (
          <>
            <CandlestickChart
              candles={allCandles}
              indicators={indicators}
              logScale={logScale}
              maPeriods={maPeriods}
              onMaPeriodsChange={setMaPeriods}
              isLoadingMore={isLoadingMore}
              onScrollToLeft={loadMoreCandles}
              impulseColors={impulseColors}
              tradePlan={tradePlan}
            />
            <TradePlanOverlay tradePlan={tradePlan} />
          </>
        )}
      </div>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )

  return isFullscreen ? (
    <div style={{ position: 'fixed', inset: 0, zIndex: 50, background: 'var(--bg-0)', display: 'flex', flexDirection: 'column' }}>
      {pageContent}
    </div>
  ) : pageContent
}
