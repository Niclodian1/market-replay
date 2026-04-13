'use client'

import { useEffect, useState, useRef, useMemo, useCallback } from 'react'
import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import { clsx } from 'clsx'
import { getCandles } from '@/lib/api'
import { calculateIndicators } from '@/lib/indicators'
import { CandlestickChart } from '@/components/chart/CandlestickChart'
import { ReplayControls } from '@/components/ReplayControls'
import { ReplayNoteForm } from '@/components/ReplayNoteForm'
import { useAppStore } from '@/store/useAppStore'
import type { Timeframe, Candle, Indicators } from '@/types'

const TIMEFRAMES: { label: string; value: Timeframe }[] = [
  { label: '15m', value: '15min' },
  { label: '1H',  value: '1h'   },
  { label: '4H',  value: '4h'   },
  { label: '1D',  value: '1day' },
  { label: '1W',  value: '1week'},
]

const EMPTY_IND: Indicators = { ma20: [], ma60: [], rsi: [], macd: [] }

// 将 YYYY-MM-DD 日期字符串转为当天结束的 UTC 时间戳（23:59:59）
function dateToEndTs(dateStr: string): number {
  return Math.floor(new Date(dateStr + 'T23:59:59Z').getTime() / 1000)
}

// 找到 <= ts 的最后一根 K 线的 index
function findCursorIndex(candles: Candle[], ts: number): number {
  let idx = -1
  for (let i = 0; i < candles.length; i++) {
    if (candles[i].time <= ts) idx = i
    else break
  }
  return Math.max(0, idx)
}

// 获取今天的默认起点日期（最近 1 年前）
function defaultReplayDate(): string {
  const d = new Date()
  d.setFullYear(d.getFullYear() - 1)
  return d.toISOString().slice(0, 10)
}

export default function ReplayPage({ params }: { params: { symbol: string } }) {
  const symbol = decodeURIComponent(params.symbol)

  // ── 基础状态 ─────────────────────────────────────────────────────────
  const [timeframe,    setTimeframe]   = useState<Timeframe>('1day')
  const [allCandles,   setAllCandles]  = useState<Candle[]>([])
  const [replayDate,   setReplayDate]  = useState(defaultReplayDate())
  const [isReplaying,  setIsReplaying] = useState(false)
  const [cursorIndex,  setCursorIndex] = useState(0)
  const [startIndex,   setStartIndex]  = useState(0)
  const [isPlaying,    setIsPlaying]   = useState(false)
  const [playSpeedMs,  setPlaySpeedMs] = useState(1000)
  const [logScale,     setLogScale]    = useState(false)
  const [fitTrigger,   setFitTrigger]  = useState(0)

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const setCurrentSymbol = useAppStore(s => s.setCurrentSymbol)

  useEffect(() => {
    setCurrentSymbol(symbol)
    return () => setCurrentSymbol(null)
  }, [symbol, setCurrentSymbol])

  // 切换周期时重置复盘状态
  useEffect(() => {
    setIsReplaying(false)
    setIsPlaying(false)
    setAllCandles([])
    setCursorIndex(0)
    setStartIndex(0)
  }, [symbol, timeframe])

  // ── 加载全量 K线（复盘需要尽可能多的历史数据）──────────────────────
  const { data: candleData, isLoading, isError, error } = useQuery({
    queryKey: ['replay-candles', symbol, timeframe],
    queryFn: () => getCandles(symbol, timeframe, 5000), // 5000 根覆盖日线约 20 年
    retry: 1,
    staleTime: 5 * 60_000,
  })

  useEffect(() => {
    if (candleData?.candles?.length) {
      setAllCandles(candleData.candles)
    }
  }, [candleData])

  // ── 复盘逻辑 ─────────────────────────────────────────────────────────
  const visibleCandles = useMemo((): Candle[] => {
    if (!isReplaying) return allCandles
    return allCandles.slice(0, cursorIndex + 1)
  }, [allCandles, isReplaying, cursorIndex])

  const indicators: Indicators = useMemo(
    () => (visibleCandles.length ? calculateIndicators(visibleCandles) : EMPTY_IND),
    [visibleCandles]
  )

  const currentCandle = isReplaying ? allCandles[cursorIndex] ?? null : null

  // 开始复盘
  const handleStartReplay = useCallback(() => {
    if (!allCandles.length || !replayDate) return
    const ts  = dateToEndTs(replayDate)
    const idx = findCursorIndex(allCandles, ts)
    setStartIndex(idx)
    setCursorIndex(idx)
    setIsReplaying(true)
    setIsPlaying(false)
    setFitTrigger(v => v + 1)  // 触发 fitContent
  }, [allCandles, replayDate])

  // 重置
  const handleReset = useCallback(() => {
    setIsPlaying(false)
    setCursorIndex(startIndex)
    setFitTrigger(v => v + 1)
  }, [startIndex])

  // 下一根
  const handleNext = useCallback(() => {
    setCursorIndex(i => Math.min(i + 1, allCandles.length - 1))
  }, [allCandles.length])

  // 上一根
  const handlePrev = useCallback(() => {
    setCursorIndex(i => Math.max(i - 1, startIndex))
  }, [startIndex])

  // 自动播放
  useEffect(() => {
    if (isPlaying) {
      intervalRef.current = setInterval(() => {
        setCursorIndex(prev => {
          if (prev >= allCandles.length - 1) {
            setIsPlaying(false)
            return prev
          }
          return prev + 1
        })
      }, playSpeedMs)
    } else {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [isPlaying, playSpeedMs, allCandles.length])

  // ── 错误信息 ─────────────────────────────────────────────────────────
  const errMsg = isError
    ? ((error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? '数据加载失败')
    : null

  // 复盘时间字符串
  const replayStartISO = useMemo(() => {
    if (!allCandles.length || !isReplaying) return ''
    return new Date(allCandles[startIndex].time * 1000).toISOString()
  }, [allCandles, startIndex, isReplaying])

  const currentISO = useMemo(() => {
    if (!currentCandle) return ''
    return new Date(currentCandle.time * 1000).toISOString()
  }, [currentCandle])

  return (
    <div className="flex flex-col h-full gap-3">
      {/* ── 顶部栏 ─────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-4 shrink-0 flex-wrap">
        <div>
          <h1 className="text-lg font-bold font-mono text-gray-100 leading-none">
            {symbol}
          </h1>
          <p className="text-xs text-blue-400 mt-0.5">复盘模式</p>
        </div>

        <div className="flex-1" />

        {/* 周期选择 */}
        <div className="flex items-center gap-0.5 bg-gray-900 border border-gray-800 rounded-md p-1">
          {TIMEFRAMES.map(tf => (
            <button
              key={tf.value}
              onClick={() => setTimeframe(tf.value)}
              className={clsx(
                'px-2.5 py-1 text-xs rounded transition-colors',
                timeframe === tf.value
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-400 hover:text-gray-100 hover:bg-gray-800'
              )}
            >
              {tf.label}
            </button>
          ))}
        </div>

        {/* Log 切换 */}
        <button
          onClick={() => setLogScale(v => !v)}
          className={clsx(
            'text-xs px-2.5 py-1.5 rounded border transition-colors',
            logScale
              ? 'border-blue-500 text-blue-400 bg-blue-900/20'
              : 'border-gray-700 text-gray-500 hover:text-gray-300'
          )}
        >
          Log
        </button>

        {/* 跳回图表 */}
        <Link
          href={`/chart/${encodeURIComponent(symbol)}`}
          className="text-xs text-gray-400 hover:text-gray-200 bg-gray-800 hover:bg-gray-700 px-3 py-1.5 rounded transition-colors"
        >
          ← 图表
        </Link>
      </div>

      {/* ── 主体：图表 + 右侧笔记 ─────────────────────────────────────── */}
      <div className="flex flex-1 min-h-0 gap-3">

        {/* 左侧：图表 + 控制栏 */}
        <div className="flex flex-col flex-1 min-w-0 bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
          {/* 图表区 */}
          <div className="flex-1 min-h-0">
            {isLoading && (
              <div className="h-full flex flex-col items-center justify-center gap-2 text-gray-500">
                <div className="w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                <span className="text-sm">加载 {symbol} 全量历史数据...</span>
              </div>
            )}

            {errMsg && (
              <div className="h-full flex items-center justify-center text-red-400 text-sm px-4 text-center">
                {errMsg}
              </div>
            )}

            {!isLoading && !errMsg && allCandles.length === 0 && (
              <div className="h-full flex items-center justify-center text-gray-500 text-sm">
                暂无数据
              </div>
            )}

            {allCandles.length > 0 && (
              <CandlestickChart
                candles={visibleCandles}
                indicators={indicators}
                logScale={logScale}
                replayMode={true}
                fitTrigger={fitTrigger}
              />
            )}
          </div>

          {/* 复盘控制栏 */}
          {allCandles.length > 0 && (
            <ReplayControls
              isReplaying={isReplaying}
              isPlaying={isPlaying}
              cursorIndex={cursorIndex}
              startIndex={startIndex}
              totalCandles={allCandles.length}
              currentCandle={currentCandle}
              replayDate={replayDate}
              playSpeedMs={playSpeedMs}
              onDateChange={setReplayDate}
              onStartReplay={handleStartReplay}
              onPlay={() => setIsPlaying(true)}
              onPause={() => setIsPlaying(false)}
              onNext={handleNext}
              onPrev={handlePrev}
              onReset={handleReset}
              onSpeedChange={setPlaySpeedMs}
            />
          )}
        </div>

        {/* 右侧：复盘笔记 */}
        <div className="w-72 shrink-0 rounded-lg overflow-hidden border border-gray-800">
          <ReplayNoteForm
            symbol={symbol}
            timeframe={timeframe}
            replayStartTime={replayStartISO}
            currentTime={currentISO}
          />
        </div>
      </div>
    </div>
  )
}
