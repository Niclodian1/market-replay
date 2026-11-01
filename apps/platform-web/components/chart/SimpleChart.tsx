'use client'

import { useEffect, useRef } from 'react'
import { createChart, ColorType, CrosshairMode, type UTCTimestamp } from 'lightweight-charts'

export type ChartLine = {
  id: string
  label: string
  color: string
  data: { time: number; value: number }[]
}

export type ChartMarker = {
  time: number
  price?: number
  kind: 'entry' | 'exit' | 'event' | 'buy' | 'sell' | 'alert'
  text?: string
  color?: string
  position?: 'aboveBar' | 'belowBar' | 'inBar'
  shape?: 'circle' | 'square' | 'arrowUp' | 'arrowDown'
}

type Props = {
  candles?: { time: number; open: number; high: number; low: number; close: number; volume?: number }[]
  lines?: ChartLine[]
  markers?: ChartMarker[]
  histogram?: { time: number; value: number; color?: string }[]
  showCandles?: boolean
  height?: number
  focusTime?: number
  /** 盲测/练习：锁住右边界，禁止把视口拖到最后一根 K 线之后 */
  lockRightEdge?: boolean
  onTimeClick?: (time: number) => void
}

const UP = '#22c55e'
const DOWN = '#ef4444'
const MUTED = '#64748b'

export default function SimpleChart({
  candles = [],
  lines = [],
  markers = [],
  histogram,
  showCandles = true,
  height = 420,
  focusTime,
  lockRightEdge = false,
  onTimeClick,
}: Props) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const chart = createChart(el, {
      width: el.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#94a3b8',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(148,163,184,0.08)' },
        horzLines: { color: 'rgba(148,163,184,0.08)' },
      },
      rightPriceScale: { borderColor: 'rgba(148,163,184,0.2)' },
      timeScale: {
        borderColor: 'rgba(148,163,184,0.2)',
        timeVisible: true,
        secondsVisible: false,
        fixRightEdge: lockRightEdge,
        lockVisibleTimeRangeOnResize: lockRightEdge,
        rightOffset: lockRightEdge ? 0 : undefined,
      },
      crosshair: { mode: CrosshairMode.Normal },
    })

    if (showCandles && candles.length) {
      const series = chart.addCandlestickSeries({
        upColor: UP,
        downColor: DOWN,
        borderVisible: false,
        wickUpColor: UP,
        wickDownColor: DOWN,
      })
      series.setData(
        candles.map((c) => ({
          time: c.time as UTCTimestamp,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        }))
      )
      const dedup = new Map<string, ChartMarker>()
      for (const marker of markers) {
        const color =
          marker.color ??
          (marker.kind === 'entry' || marker.kind === 'buy'
            ? UP
            : marker.kind === 'exit' || marker.kind === 'sell'
              ? '#f59e0b'
              : '#60a5fa')
        const position =
          marker.position ??
          (marker.kind === 'entry' || marker.kind === 'buy' ? 'belowBar' : 'aboveBar')
        dedup.set(`${marker.time}-${marker.kind}-${marker.text ?? ''}`, {
          ...marker,
          color,
          position,
        })
      }
      series.setMarkers(
        Array.from(dedup.values()).map((marker) => ({
          time: marker.time as UTCTimestamp,
          position: marker.position ?? 'aboveBar',
          color: marker.color ?? MUTED,
          shape:
            marker.shape ??
            (marker.kind === 'entry' || marker.kind === 'buy' ? 'arrowUp' : 'arrowDown'),
          text: marker.text,
        }))
      )
    }

    for (const line of lines) {
      const series = chart.addLineSeries({
        color: line.color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        title: line.label,
      })
      series.setData(
        line.data
          .filter((point) => Number.isFinite(point.value))
          .map((point) => ({ time: point.time as UTCTimestamp, value: point.value }))
      )
    }

    if (histogram?.length && !showCandles) {
      const series = chart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false })
      series.setData(
        histogram.map((point) => ({
          time: point.time as UTCTimestamp,
          value: point.value,
          color: point.color ?? (point.value >= 0 ? UP : DOWN),
        }))
      )
    }

    if (focusTime) {
      chart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, candles.findIndex((candle) => candle.time >= focusTime) - 60),
        to: candles.findIndex((candle) => candle.time >= focusTime) + 60,
      })
    } else {
      chart.timeScale().fitContent()
    }

    const observer = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }))
    observer.observe(el)
    const clickHandler = onTimeClick
      ? (param: { time?: unknown }) => {
          if (typeof param.time === 'number') onTimeClick(param.time)
        }
      : null
    if (clickHandler && typeof chart.subscribeClick === 'function') {
      chart.subscribeClick(clickHandler as never)
    }
    return () => {
      observer.disconnect()
      chart.remove()
    }
  }, [candles, lines, markers, histogram, showCandles, height, focusTime, lockRightEdge, onTimeClick])

  return <div ref={ref} className="w-full" style={{ height }} />
}
