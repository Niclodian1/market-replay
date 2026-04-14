'use client'

import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import Link from 'next/link'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { clsx } from 'clsx'
import { getCandles, getQuote, addWatchlist, getWatchlist } from '@/lib/api'
import { calculateIndicators } from '@/lib/indicators'
import { CandlestickChart } from '@/components/chart/CandlestickChart'
import { PriceSummary } from '@/components/PriceSummary'
import { useAppStore } from '@/store/useAppStore'
import type { Timeframe, Candle, Indicators, WatchlistItem } from '@/types'

const TIMEFRAMES: { label: string; value: Timeframe }[] = [
  { label: '15m', value: '15min' },
  { label: '1H',  value: '1h'   },
  { label: '4H',  value: '4h'   },
  { label: '1D',  value: '1day' },
  { label: '1W',  value: '1week'},
]

const EMPTY_IND: Indicators = { ma20: [], ma60: [], rsi: [], macd: [] }

export default function ChartPage({ params }: { params: { symbol: string } }) {
  const symbol = decodeURIComponent(params.symbol)

  const [timeframe, setTimeframe]         = useState<Timeframe>('1day')
  const [logScale, setLogScale]           = useState(false)
  const [allCandles, setAllCandles]       = useState<Candle[]>([])
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [hasMore, setHasMore]             = useState(true)
  const [addedMsg, setAddedMsg]           = useState('')

  const loadingMoreRef = useRef(false)
  const queryClient = useQueryClient()

  const setCurrentSymbol = useAppStore(s => s.setCurrentSymbol)
  const maPeriods    = useAppStore(s => s.maPeriods)
  const setMaPeriods = useAppStore(s => s.setMaPeriods)

  useEffect(() => {
    setCurrentSymbol(symbol)
    return () => setCurrentSymbol(null)
  }, [symbol, setCurrentSymbol])

  useEffect(() => {
    setAllCandles([])
    setHasMore(true)
    loadingMoreRef.current = false
  }, [symbol, timeframe])

  const {
    data: initialData,
    isLoading: candlesLoading,
    isError: candlesError,
    error: candlesErr,
  } = useQuery({
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

  const addMutation = useMutation({
    mutationFn: () => addWatchlist({
      symbol,
      name: quote?.name || symbol,
      asset_type: 'stock',
    }),
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
    () => (allCandles.length ? calculateIndicators(allCandles, maPeriods[0], maPeriods[1]) : EMPTY_IND),
    [allCandles, maPeriods]
  )

  const loadMoreCandles = useCallback(async () => {
    if (loadingMoreRef.current || !hasMore || !allCandles.length) return
    loadingMoreRef.current = true
    setIsLoadingMore(true)

    const before = allCandles[0].time

    try {
      const data = await getCandles(symbol, timeframe, 500, before)
      const newCandles: Candle[] = data?.candles ?? []

      if (!newCandles.length) {
        setHasMore(false)
        return
      }

      setAllCandles(prev => {
        const oldestExisting = prev[0]?.time ?? Infinity
        const fresh = newCandles.filter(c => c.time < oldestExisting)
        if (!fresh.length) {
          setHasMore(false)
          return prev
        }
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

  const errMsg = candlesError
    ? ((candlesErr as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? '数据加载失败')
    : null

  return (
    <div className="flex flex-col h-full gap-3">
      {/* 顶部信息栏 */}
      <div className="flex items-center flex-wrap gap-x-3 gap-y-2 shrink-0">
        <div className="min-w-0">
          <h1 className="text-base font-bold font-mono text-gray-100 leading-none tracking-wide">{symbol}</h1>
          {quote?.name && <p className="text-xs text-gray-600 mt-0.5 truncate">{quote.name}</p>}
        </div>

        {quote && <PriceSummary quote={quote} />}

        <div className="flex-1" />

        {/* 周期切换 */}
        <div className="flex items-center gap-0.5 bg-gray-900/80 border border-gray-800/80 rounded-lg p-0.5">
          {TIMEFRAMES.map(tf => (
            <button
              key={tf.value}
              onClick={() => setTimeframe(tf.value)}
              className={clsx(
                'px-2.5 py-1 text-xs rounded-md transition-all',
                timeframe === tf.value
                  ? 'bg-blue-600 text-white shadow-sm'
                  : 'text-gray-600 hover:text-gray-200 hover:bg-gray-800'
              )}
            >
              {tf.label}
            </button>
          ))}
        </div>

        {/* Log 切换 */}
        <button
          onClick={() => setLogScale(v => !v)}
          title="切换对数坐标"
          className={clsx(
            'text-xs px-2 py-1 rounded-md border transition-all',
            logScale
              ? 'border-blue-500/50 text-blue-400 bg-blue-500/8'
              : 'border-gray-700/60 text-gray-600 hover:text-gray-300 hover:border-gray-600'
          )}
        >
          Log
        </button>

        <div className="w-px h-4 bg-gray-800" />

        {/* 加自选 */}
        <button
          onClick={() => addMutation.mutate()}
          disabled={isInWatchlist || addMutation.isPending}
          className={clsx(
            'text-xs px-2.5 py-1 rounded-md border transition-all',
            isInWatchlist
              ? 'border-gray-800 text-gray-600 cursor-default'
              : 'border-gray-700/60 text-gray-500 hover:text-gray-200 hover:border-gray-600 hover:bg-gray-800/60'
          )}
        >
          {addedMsg || (isInWatchlist ? '✓ 已自选' : '+ 自选')}
        </button>

        {/* 跳转复盘 */}
        <Link
          href={`/replay/${encodeURIComponent(symbol)}`}
          className="text-xs bg-blue-600/90 hover:bg-blue-500 text-white px-3 py-1 rounded-md transition-all border border-blue-500/30"
        >
          复盘模式 →
        </Link>
      </div>

      {/* 图表区 */}
      <div className="flex-1 min-h-0 bg-gray-900/60 border border-gray-800/80 rounded-lg overflow-hidden">
        {candlesLoading && (
          <div className="h-full flex flex-col items-center justify-center gap-3 text-gray-600">
            <div className="w-7 h-7 border-2 border-blue-500/60 border-t-blue-400 rounded-full animate-spin" />
            <span className="text-xs font-mono">加载 {symbol} · {timeframe}...</span>
          </div>
        )}

        {errMsg && (
          <div className="h-full flex flex-col items-center justify-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-red-400/8 border border-red-900/30 flex items-center justify-center">
              <svg className="w-5 h-5 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d="M12 9v4m0 4h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              </svg>
            </div>
            <div className="text-red-400 text-sm text-center px-4">{errMsg}</div>
          </div>
        )}

        {!candlesLoading && !errMsg && allCandles.length === 0 && (
          <div className="h-full flex items-center justify-center text-gray-600 text-sm">
            暂无数据
          </div>
        )}

        {allCandles.length > 0 && (
          <CandlestickChart
            candles={allCandles}
            indicators={indicators}
            logScale={logScale}
            maPeriods={maPeriods}
            onMaPeriodsChange={setMaPeriods}
            isLoadingMore={isLoadingMore}
            onScrollToLeft={loadMoreCandles}
          />
        )}
      </div>
    </div>
  )
}
