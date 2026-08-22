'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import type { IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts'
import type { Candle } from '@/types'
import { calculateChartMeasurement, formatMeasureDuration } from '@/lib/chartMeasurement'

type ChartRefs = {
  main: IChartApi | null
  candle: ISeriesApi<'Candlestick'> | null
}

type MeasureAnchor = {
  time: number
  price: number
}

type Measurement = {
  start: MeasureAnchor
  end: MeasureAnchor
  status: 'pending' | 'complete'
}

type PointerGesture = {
  originX: number
  originY: number
  mode: 'start' | 'finish'
  dragged: boolean
}

interface UseChartMeasureParams {
  chartRefs: React.MutableRefObject<ChartRefs>
  mainWrapRef: React.RefObject<HTMLDivElement | null>
  candles: Candle[]
  isDark: boolean
}

const UP_COLOR = '#22c55e'
const DOWN_COLOR = '#ef4444'
const LABEL_WIDTH = 194
const LABEL_HEIGHT = 43

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

function formatPrice(value: number): string {
  const abs = Math.abs(value)
  if (abs >= 10000) return abs.toLocaleString(undefined, { maximumFractionDigits: 0 })
  if (abs >= 100) return abs.toFixed(2)
  if (abs >= 1) return abs.toFixed(4)
  return abs.toFixed(6)
}

function candleIndexAtTime(candles: Candle[], time: number): number {
  let lo = 0
  let hi = candles.length - 1
  while (lo <= hi) {
    const mid = (lo + hi) >>> 1
    if (candles[mid].time === time) return mid
    if (candles[mid].time < time) lo = mid + 1
    else hi = mid - 1
  }
  return clamp(lo, 0, Math.max(0, candles.length - 1))
}

export function useChartMeasure({ chartRefs, mainWrapRef, candles, isDark }: UseChartMeasureParams) {
  const [measurement, setMeasurement] = useState<Measurement | null>(null)
  const [isShiftPressed, setIsShiftPressed] = useState(false)
  const [isToolActive, setIsToolActive] = useState(false)
  const measurementRef = useRef<Measurement | null>(null)
  const gestureRef = useRef<PointerGesture | null>(null)
  const toolActiveRef = useRef(false)

  const commitMeasurement = useCallback((next: Measurement | null) => {
    measurementRef.current = next
    setMeasurement(next)
  }, [])

  const setToolActive = useCallback((active: boolean) => {
    toolActiveRef.current = active
    setIsToolActive(active)
    if (!active) {
      gestureRef.current = null
    }
  }, [])

  const anchorFromLocalPoint = useCallback((x: number, y: number): MeasureAnchor | null => {
    const chart = chartRefs.current.main
    const series = chartRefs.current.candle
    if (!chart || !series || candles.length === 0) return null

    const plotWidth = chart.timeScale().width()
    if (x < 0 || x > plotWidth || y < 0 || y > (mainWrapRef.current?.clientHeight ?? Infinity)) return null

    const logical = chart.timeScale().coordinateToLogical(x)
    const price = series.coordinateToPrice(y)
    if (logical === null || price === null) return null

    const candleIndex = clamp(Math.round(logical as number), 0, candles.length - 1)
    return { time: candles[candleIndex].time, price: price as number }
  }, [candles, chartRefs, mainWrapRef])

  const localPointFromEvent = useCallback((event: Pick<MouseEvent, 'clientX' | 'clientY'>) => {
    const rect = mainWrapRef.current?.getBoundingClientRect()
    if (!rect) return null
    return anchorFromLocalPoint(event.clientX - rect.left, event.clientY - rect.top)
  }, [anchorFromLocalPoint, mainWrapRef])

  // Lightweight Charts owns the canvas' native mouse handlers. Registering
  // native capture listeners on the wrapper makes the Shift gesture run before
  // the library's pan/click handlers; React synthetic pointer events are not
  // reliable at this boundary in every browser.
  useEffect(() => {
    const wrap = mainWrapRef.current
    if (!wrap) return
    let suppressNextClick = false

    const consume = (event: MouseEvent) => {
      event.preventDefault()
      event.stopPropagation()
      event.stopImmediatePropagation()
    }

    const onMouseDown = (event: MouseEvent) => {
      if (event.button !== 0) return
      if (!event.shiftKey && !toolActiveRef.current) {
        if (measurementRef.current) commitMeasurement(null)
        return
      }

      const anchor = localPointFromEvent(event)
      if (!anchor) return
      consume(event)
      suppressNextClick = true

      const current = measurementRef.current
      const finishing = current?.status === 'pending'
      commitMeasurement(finishing
        ? { ...current, end: anchor }
        : { start: anchor, end: anchor, status: 'pending' })
      gestureRef.current = {
        originX: event.clientX,
        originY: event.clientY,
        mode: finishing ? 'finish' : 'start',
        dragged: false,
      }
    }

    const onMouseMove = (event: MouseEvent) => {
      const current = measurementRef.current
      if (current?.status !== 'pending') return
      const anchor = localPointFromEvent(event)
      if (!anchor) return

      const gesture = gestureRef.current
      if (gesture) {
        if (Math.hypot(event.clientX - gesture.originX, event.clientY - gesture.originY) >= 3) {
          gesture.dragged = true
        }
        consume(event)
      }
      commitMeasurement({ ...current, end: anchor })
    }

    const onMouseUp = (event: MouseEvent) => {
      const gesture = gestureRef.current
      if (!gesture || event.button !== 0) return
      consume(event)

      const current = measurementRef.current
      const anchor = localPointFromEvent(event)
      if (current && anchor) {
        const complete = gesture.mode === 'finish' || gesture.dragged
        commitMeasurement({ ...current, end: anchor, status: complete ? 'complete' : 'pending' })
      }
      gestureRef.current = null
      // A click normally follows mouseup synchronously. Reset on the next task
      // as a safety net for browsers that suppress click after preventDefault.
      window.setTimeout(() => { suppressNextClick = false }, 0)
    }

    const onClick = (event: MouseEvent) => {
      if (!suppressNextClick) return
      suppressNextClick = false
      consume(event)
    }

    wrap.addEventListener('mousedown', onMouseDown, true)
    wrap.addEventListener('click', onClick, true)
    window.addEventListener('mousemove', onMouseMove, true)
    window.addEventListener('mouseup', onMouseUp, true)
    return () => {
      wrap.removeEventListener('mousedown', onMouseDown, true)
      wrap.removeEventListener('click', onClick, true)
      window.removeEventListener('mousemove', onMouseMove, true)
      window.removeEventListener('mouseup', onMouseUp, true)
    }
  }, [commitMeasurement, localPointFromEvent, mainWrapRef])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Shift') setIsShiftPressed(true)
      if (event.key === 'Escape') {
        if (measurementRef.current) commitMeasurement(null)
        else if (toolActiveRef.current) setToolActive(false)
      }
    }
    const onKeyUp = (event: KeyboardEvent) => {
      if (event.key === 'Shift') setIsShiftPressed(false)
    }
    const onBlur = () => setIsShiftPressed(false)
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)
    window.addEventListener('blur', onBlur)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('keyup', onKeyUp)
      window.removeEventListener('blur', onBlur)
    }
  }, [commitMeasurement, setToolActive])

  useEffect(() => {
    if (candles.length === 0) commitMeasurement(null)
  }, [candles.length, commitMeasurement])

  function renderMeasurement(): React.ReactNode {
    if (!measurement || candles.length === 0) return null
    const chart = chartRefs.current.main
    const series = chartRefs.current.candle
    const wrap = mainWrapRef.current
    if (!chart || !series || !wrap) return null

    const startX = chart.timeScale().timeToCoordinate(measurement.start.time as UTCTimestamp)
    const endX = chart.timeScale().timeToCoordinate(measurement.end.time as UTCTimestamp)
    const startY = series.priceToCoordinate(measurement.start.price)
    const endY = series.priceToCoordinate(measurement.end.price)
    if (startX === null || endX === null || startY === null || endY === null) return null

    const sx = startX as number
    const ex = endX as number
    const sy = startY as number
    const ey = endY as number
    const plotWidth = chart.timeScale().width()
    const plotHeight = wrap.clientHeight
    const startIndex = candleIndexAtTime(candles, measurement.start.time)
    const endIndex = candleIndexAtTime(candles, measurement.end.time)
    const stats = calculateChartMeasurement(
      measurement.start.price,
      measurement.end.price,
      measurement.start.time,
      measurement.end.time,
      startIndex,
      endIndex,
    )
    const color = stats.isUp ? UP_COLOR : DOWN_COLOR
    const sign = stats.isUp ? '+' : '−'
    const duration = formatMeasureDuration(stats.durationSeconds)
    const percentText = `${sign}${Math.abs(stats.percentChange).toFixed(2)}%`
    const detailText = `${sign}${formatPrice(stats.priceChange)} · ${stats.barCount} 根K线 · ${duration}`

    const boxX = Math.min(sx, ex)
    const boxY = Math.min(sy, ey)
    const boxWidth = Math.max(1, Math.abs(ex - sx))
    const boxHeight = Math.max(1, Math.abs(ey - sy))
    const labelX = clamp(
      ex + 10 + LABEL_WIDTH <= plotWidth ? ex + 10 : ex - LABEL_WIDTH - 10,
      6,
      Math.max(6, plotWidth - LABEL_WIDTH - 6),
    )
    const labelY = clamp(ey - LABEL_HEIGHT / 2, 6, Math.max(6, plotHeight - LABEL_HEIGHT - 6))
    const labelBg = isDark ? '#111827ee' : '#ffffffee'
    const labelFg = isDark ? '#f3f4f6' : '#111827'
    const mutedFg = isDark ? '#9ca3af' : '#6b7280'

    return (
      <g aria-label={`区间变化 ${percentText}`}>
        <rect
          x={boxX} y={boxY} width={boxWidth} height={boxHeight}
          fill={color} fillOpacity={0.09}
          stroke={color} strokeOpacity={0.55} strokeWidth={1}
        />
        <line x1={sx} y1={sy} x2={ex} y2={ey} stroke={color} strokeWidth={1.25} strokeDasharray="4 3" strokeOpacity={0.9} />
        <line x1={boxX} y1={sy} x2={Math.max(sx, ex)} y2={sy} stroke={color} strokeWidth={1} strokeOpacity={0.65} />
        <line x1={boxX} y1={ey} x2={Math.max(sx, ex)} y2={ey} stroke={color} strokeWidth={1} strokeOpacity={0.65} />
        <circle cx={sx} cy={sy} r={3.5} fill={labelBg} stroke={color} strokeWidth={1.5} />
        <circle cx={ex} cy={ey} r={3.5} fill={color} stroke={labelBg} strokeWidth={1.5} />

        <g transform={`translate(${labelX} ${labelY})`}>
          <rect width={LABEL_WIDTH} height={LABEL_HEIGHT} rx={3} fill={labelBg} stroke={color} strokeOpacity={0.7} />
          <rect width={3} height={LABEL_HEIGHT} rx={1.5} fill={color} />
          <text x={12} y={17} fill={color} fontSize={13} fontWeight={700} fontFamily="monospace">{percentText}</text>
          <text x={12} y={33} fill={mutedFg} fontSize={9.5} fontFamily="monospace">{detailText}</text>
          {measurement.status === 'pending' && (
            <text x={LABEL_WIDTH - 8} y={17} fill={labelFg} fillOpacity={0.5} fontSize={8} textAnchor="end" fontFamily="monospace">SHIFT</text>
          )}
        </g>
      </g>
    )
  }

  return {
    measurement,
    isPending: measurement?.status === 'pending',
    isArmed: isShiftPressed || isToolActive,
    isToolActive,
    setToolActive,
    renderMeasurement,
    clearMeasurement: () => commitMeasurement(null),
  }
}
