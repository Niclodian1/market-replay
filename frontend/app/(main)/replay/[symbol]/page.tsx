'use client'

import { useEffect, useState, useRef, useMemo, useCallback } from 'react'
import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import { getCandles } from '@/lib/api'
import { calculateIndicators, elderImpulse } from '@/lib/indicators'
import { CandlestickChart } from '@/components/chart/CandlestickChart'
import { ReplayControls } from '@/components/ReplayControls'
import { ReplayNoteForm } from '@/components/ReplayNoteForm'
import { useAppStore } from '@/store/useAppStore'
import type { Timeframe, Candle, Indicators, ImpulseColor } from '@/types'

const TIMEFRAMES: { label: string; value: Timeframe }[] = [
  { label: '15m', value: '15min' },
  { label: '1H',  value: '1h'   },
  { label: '4H',  value: '4h'   },
  { label: '1D',  value: '1day' },
  { label: '1W',  value: '1week'},
]

const EMPTY_IND: Indicators = { ma_lines: [], rsi: [], macd: [] }

function dateToEndTs(dateStr: string): number {
  return Math.floor(new Date(dateStr + 'T23:59:59Z').getTime() / 1000)
}

function findCursorIndex(candles: Candle[], ts: number): number {
  let idx = -1
  for (let i = 0; i < candles.length; i++) {
    if (candles[i].time <= ts) idx = i
    else break
  }
  return Math.max(0, idx)
}

function defaultReplayDate(): string {
  const d = new Date()
  d.setFullYear(d.getFullYear() - 1)
  return d.toISOString().slice(0, 10)
}

function fmtDate(ts: number) {
  const d = new Date(ts * 1000)
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0')
}

export default function ReplayPage({ params }: { params: { symbol: string } }) {
  const symbol = decodeURIComponent(params.symbol)

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
  const maPeriods    = useAppStore(s => s.maPeriods)
  const setMaPeriods = useAppStore(s => s.setMaPeriods)
  const impulseEnabled = useAppStore(s => s.elderImpulse)

  useEffect(() => {
    setCurrentSymbol(symbol)
    return () => setCurrentSymbol(null)
  }, [symbol, setCurrentSymbol])

  useEffect(() => {
    setIsReplaying(false)
    setIsPlaying(false)
    setAllCandles([])
    setCursorIndex(0)
    setStartIndex(0)
  }, [symbol, timeframe])

  const { data: candleData, isLoading, isError, error } = useQuery({
    queryKey: ['replay-candles', symbol, timeframe],
    queryFn: () => getCandles(symbol, timeframe, 5000),
    retry: 1,
    staleTime: 5 * 60_000,
  })

  useEffect(() => {
    if (candleData?.candles?.length) setAllCandles(candleData.candles)
  }, [candleData])

  const visibleCandles = useMemo((): Candle[] => {
    if (!isReplaying) return allCandles
    return allCandles.slice(0, cursorIndex + 1)
  }, [allCandles, isReplaying, cursorIndex])

  const indicators: Indicators = useMemo(
    () => (visibleCandles.length ? calculateIndicators(visibleCandles, maPeriods) : EMPTY_IND),
    [visibleCandles, maPeriods]
  )

  const impulseColors: ImpulseColor[] | undefined = useMemo(
    () => (impulseEnabled && visibleCandles.length ? elderImpulse(visibleCandles) : undefined),
    [visibleCandles, impulseEnabled]
  )

  const currentCandle = isReplaying ? allCandles[cursorIndex] ?? null : null

  const handleStartReplay = useCallback(() => {
    if (!allCandles.length || !replayDate) return
    const ts  = dateToEndTs(replayDate)
    const idx = findCursorIndex(allCandles, ts)
    setStartIndex(idx)
    setCursorIndex(idx)
    setIsReplaying(true)
    setIsPlaying(false)
    setFitTrigger(v => v + 1)
  }, [allCandles, replayDate])

  const handleReset = useCallback(() => {
    setIsPlaying(false)
    setCursorIndex(startIndex)
    setFitTrigger(v => v + 1)
  }, [startIndex])

  const handleNext = useCallback(() => {
    setCursorIndex(i => Math.min(i + 1, allCandles.length - 1))
  }, [allCandles.length])

  const handlePrev = useCallback(() => {
    setCursorIndex(i => Math.max(i - 1, startIndex))
  }, [startIndex])

  useEffect(() => {
    if (isPlaying) {
      intervalRef.current = setInterval(() => {
        setCursorIndex(prev => {
          if (prev >= allCandles.length - 1) { setIsPlaying(false); return prev }
          return prev + 1
        })
      }, playSpeedMs)
    } else {
      if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null }
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current) }
  }, [isPlaying, playSpeedMs, allCandles.length])

  const errMsg = isError
    ? ((error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? '数据加载失败')
    : null

  const replayStartISO = useMemo(() => {
    if (!allCandles.length || !isReplaying) return ''
    return new Date(allCandles[startIndex].time * 1000).toISOString()
  }, [allCandles, startIndex, isReplaying])

  const currentISO = useMemo(() => {
    if (!currentCandle) return ''
    return new Date(currentCandle.time * 1000).toISOString()
  }, [currentCandle])

  const totalSteps = allCandles.length - 1
  const progress = totalSteps > 0 ? ((cursorIndex - startIndex) / Math.max(1, totalSteps - startIndex)) * 100 : 0

  return (
    <div className="page" style={{ display: 'grid', gridTemplateColumns: '1fr 320px', gap: 0, overflow: 'hidden', height: '100%' }}>
      {/* Left — chart + controls */}
      <div style={{ borderRight: '1px solid var(--line-1)', display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        {/* Replay header */}
        <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--line-1)', background: 'var(--bg-1)', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
          <div className="pulse" />
          <div>
            <div className="section-title" style={{ color: 'var(--accent)' }}>复盘模式 · REPLAY</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 2 }}>
              <span className="mono" style={{ fontSize: 15, fontWeight: 700, color: 'var(--fg-0)' }}>{symbol}</span>
              <span className="chip">{timeframe.toUpperCase()}</span>
              {isReplaying && currentCandle && (
                <span className="mono" style={{ color: 'var(--fg-1)', fontSize: 12 }}>{fmtDate(currentCandle.time)}</span>
              )}
            </div>
          </div>

          <div style={{ flex: 1 }} />

          <div className="tweak-segment">
            {TIMEFRAMES.map(tf => (
              <button key={tf.value} className={timeframe === tf.value ? 'on' : ''} onClick={() => setTimeframe(tf.value)}
                style={{ minWidth: 40, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                {tf.label}
              </button>
            ))}
          </div>

          <button className={`btn small${logScale ? '' : ' ghost'}`} onClick={() => setLogScale(v => !v)}>Log</button>
          <Link href={`/chart/${encodeURIComponent(symbol)}`} className="btn small ghost">← 图表</Link>
        </div>

        {/* Chart area */}
        <div style={{ flex: 1, minHeight: 0 }}>
          {isLoading && (
            <div style={{ height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10, color: 'var(--fg-2)' }}>
              <div style={{ width: 26, height: 26, border: '2.5px solid var(--accent)', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.7s linear infinite' }} />
              <span className="mono" style={{ fontSize: 11.5 }}>加载历史数据…</span>
            </div>
          )}
          {errMsg && (
            <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--down)', fontSize: 13 }}>
              {errMsg}
            </div>
          )}
          {!isLoading && !errMsg && allCandles.length === 0 && (
            <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fg-3)', fontSize: 13 }}>
              暂无数据
            </div>
          )}
          {allCandles.length > 0 && (
            <CandlestickChart
              candles={visibleCandles}
              indicators={indicators}
              logScale={logScale}
              maPeriods={maPeriods}
              onMaPeriodsChange={setMaPeriods}
              replayMode={true}
              fitTrigger={fitTrigger}
              impulseColors={impulseColors}
            />
          )}
        </div>

        {/* Controls */}
        {allCandles.length > 0 && (
          <div style={{ padding: '10px 16px', background: 'var(--bg-1)', borderTop: '1px solid var(--line-1)', flexShrink: 0 }}>
            {/* Progress bar */}
            {isReplaying && (
              <div style={{ position: 'relative', height: 5, background: 'var(--bg-2)', borderRadius: 3, marginBottom: 10, overflow: 'hidden' }}>
                <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: progress + '%', background: 'linear-gradient(90deg, var(--accent-dim), var(--accent))', transition: 'width .3s' }} />
              </div>
            )}
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
          </div>
        )}
      </div>

      {/* Right — notes panel */}
      <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, background: 'var(--bg-1)', overflow: 'hidden' }}>
        <ReplayNoteForm
          symbol={symbol}
          timeframe={timeframe}
          replayStartTime={replayStartISO}
          currentTime={currentISO}
        />
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
