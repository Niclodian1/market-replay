'use client'

import { useEffect, useRef, useState } from 'react'
import {
  createChart,
  IChartApi,
  ISeriesApi,
  ColorType,
  IPriceLine,
  LineStyle,
  PriceScaleMode,
} from 'lightweight-charts'
import type { Candle, MaSeriesPoint, MaStrategyTrade, MaExitReason } from '@/types'
import { useAppStore } from '@/store/useAppStore'
import { calculateIndicators } from '@/lib/indicators'

type Props = {
  candles: Candle[]
  maLine: MaSeriesPoint[]
  maLabel: string
  trendMa: MaSeriesPoint[]
  trades: MaStrategyTrade[]
  selectedTrade?: MaStrategyTrade | null
}

const EXIT_COLOR: Record<MaExitReason, string> = {
  '止损': '#ef4444',
  '保本': '#f59e0b',
  '均线': '#3b82f6',
  '强制平仓': '#9ca3af',
}

export function MaStrategyChart({
  candles,
  maLine,
  maLabel,
  trendMa,
  trades,
  selectedTrade,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const rsiContainerRef = useRef<HTMLDivElement>(null)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const rsiChartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const maSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const trendSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const priceLinesRef = useRef<IPriceLine[]>([])

  const theme = useAppStore(s => s.theme)
  const isDark = theme !== 'light'

  const [isLog, setIsLog] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [showRsi, setShowRsi] = useState(false)
  const [hoveredCandle, setHoveredCandle] = useState<{ open: number; high: number; low: number; close: number } | null>(null)

  // Always-current refs so init effect can read latest props without re-running
  const maLineRef = useRef(maLine)
  const trendMaRef = useRef(trendMa)
  const tradesRef = useRef(trades)
  const maLabelRef = useRef(maLabel)
  maLineRef.current = maLine
  trendMaRef.current = trendMa
  tradesRef.current = trades
  maLabelRef.current = maLabel

  // ESC 退出全屏
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isFullscreen) setIsFullscreen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isFullscreen])

  // 初始化主图 + 可选 RSI 子图
  useEffect(() => {
    if (!containerRef.current || candles.length === 0) return

    const chartH = isFullscreen
      ? window.innerHeight - (showRsi ? 220 : 80)
      : (showRsi ? 420 : 520)

    const chartOpts = {
      layout: {
        background: { type: ColorType.Solid as const, color: isDark ? '#0f1318' : '#ffffff' },
        textColor: isDark ? '#7c8594' : '#6c7685',
      },
      grid: {
        vertLines: { color: isDark ? '#1e242e' : '#e4e7ec' },
        horzLines: { color: isDark ? '#1e242e' : '#e4e7ec' },
      },
      rightPriceScale: { borderColor: isDark ? '#1e242e' : '#e4e7ec', minimumWidth: 110 },
      timeScale: { borderColor: isDark ? '#1e242e' : '#e4e7ec', timeVisible: false },
      crosshair: {
        mode: 0,
        vertLine: { width: 1 as const, color: isDark ? '#6b7280' : '#9ca3af', style: LineStyle.Dashed, labelVisible: true },
        horzLine: { width: 1 as const, color: isDark ? '#6b7280' : '#9ca3af', style: LineStyle.Dashed, labelVisible: true },
      },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
    }

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: chartH,
      ...chartOpts,
      timeScale: { ...chartOpts.timeScale, visible: !showRsi },
    })
    chartRef.current = chart

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#22c55e', downColor: '#ef4444',
      borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    })
    candleSeries.setData(
      candles.map(c => ({
        time: c.time as any,
        open: c.open, high: c.high, low: c.low, close: c.close,
      })),
    )
    candleSeriesRef.current = candleSeries

    const trendSeries = chart.addLineSeries({
      color: isDark ? '#6b7280' : '#9ca3af',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      title: '趋势 MA120',
    })
    trendSeriesRef.current = trendSeries
    trendSeries.setData(trendMaRef.current.map(p => ({ time: p.time as any, value: p.value })))

    const maSeries = chart.addLineSeries({
      color: '#60a5fa',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      title: maLabelRef.current,
    })
    maSeriesRef.current = maSeries
    maSeries.setData(maLineRef.current.map(p => ({ time: p.time as any, value: p.value })))

    // Set trade markers immediately so they survive chart reinit
    const initMarkers: Array<{ time: any; position: 'aboveBar' | 'belowBar'; color: string; shape: 'arrowUp' | 'arrowDown' | 'circle'; text: string }> = []
    for (const t of tradesRef.current) {
      initMarkers.push({ time: t.entry_time as any, position: 'belowBar', color: '#22c55e', shape: 'arrowUp', text: maLabelRef.current })
      initMarkers.push({ time: t.exit_time as any, position: t.pnl_pct >= 0 ? 'aboveBar' : 'belowBar', color: EXIT_COLOR[t.exit_reason] ?? '#9ca3af', shape: 'arrowDown', text: `${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct.toFixed(2)}% ${t.exit_reason}` })
    }
    initMarkers.sort((a, b) => (a.time as number) - (b.time as number))
    candleSeries.setMarkers(initMarkers)

    chart.priceScale('right').applyOptions({
      mode: isLog ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
    })

    chart.timeScale().fitContent()

    const candleMap = new Map(candles.map(c => [c.time, c]))

    // ── RSI 子图（同步时间轴） ──
    let rsiSeriesLocal: ISeriesApi<'Line'> | null = null
    if (showRsi && rsiContainerRef.current) {
      const rsiH = isFullscreen ? 160 : 130
      const rsiChart = createChart(rsiContainerRef.current, {
        width: rsiContainerRef.current.clientWidth,
        height: rsiH,
        ...chartOpts,
        timeScale: { ...chartOpts.timeScale, visible: true },
      })
      rsiChartRef.current = rsiChart

      const indicators = calculateIndicators(candles, [])
      // RSI(14) 计算依赖前 14 根 K 线，构图时整体向后推 14 个位置，
      // 使每个 RSI 点显示在其"计算窗口结束"的位置之后 14 根处。
      const RSI_SHIFT = 14
      const rsiMap = new Map<number, number>()
      const rsiData: Array<{ time: any; value?: number }> = []
      for (let i = 0; i < candles.length; i++) {
        const t = candles[i].time
        const src = i - RSI_SHIFT >= 0 ? indicators.rsi[i - RSI_SHIFT] : null
        if (src && src.value != null) {
          rsiMap.set(t, src.value as number)
          rsiData.push({ time: t as any, value: src.value as number })
        } else {
          rsiData.push({ time: t as any })
        }
      }
      const rsiSeries = rsiChart.addLineSeries({
        color: '#a855f7',
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: true,
        title: 'RSI(14)',
      })
      rsiSeriesLocal = rsiSeries
      // 保留所有 candle 对应的时间点（null 值用 whitespace 占位），确保 bar 数与主图一致
      rsiSeries.setData(rsiData)
      // 30 / 70 参考线
      rsiSeries.createPriceLine({
        price: 70, color: '#ef4444', lineWidth: 1,
        lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: '70',
      })
      rsiSeries.createPriceLine({
        price: 30, color: '#22c55e', lineWidth: 1,
        lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: '30',
      })
      rsiChart.priceScale('right').applyOptions({ autoScale: false })
      rsiSeries.applyOptions({})
      // 强制 RSI Y 轴范围
      rsiChart.priceScale('right').applyOptions({
        scaleMargins: { top: 0.1, bottom: 0.1 },
      })

      // 先订阅同步，再用主图当前区间初始化（避免 fitContent 只 fit 非空 bar 导致偏移）
      let syncing = false
      chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (syncing || !range || !rsiChartRef.current) return
        syncing = true
        rsiChartRef.current.timeScale().setVisibleLogicalRange(range)
        syncing = false
      })
      rsiChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (syncing || !range || !chartRef.current) return
        syncing = true
        chartRef.current.timeScale().setVisibleLogicalRange(range)
        syncing = false
      })

      // 立即对齐到主图当前可见区间
      const mainRange = chart.timeScale().getVisibleLogicalRange()
      if (mainRange) {
        rsiChart.timeScale().setVisibleLogicalRange(mainRange)
      }

      // 价格轴宽度对齐（保证两图 bar 水平对齐）
      let lastSyncedW = 0
      const syncPriceScaleWidth = () => {
        if (!chartRef.current || !rsiChartRef.current) return
        try {
          const w1 = (chart.priceScale('right') as any).width?.() ?? 0
          const w2 = (rsiChart.priceScale('right') as any).width?.() ?? 0
          const w = Math.max(w1, w2, 110)
          if (w === lastSyncedW) return
          lastSyncedW = w
          chart.priceScale('right').applyOptions({ minimumWidth: w })
          rsiChart.priceScale('right').applyOptions({ minimumWidth: w })
        } catch { /* ignore */ }
      }
      // 多帧重试，等待价格标签布局稳定后再次对齐
      requestAnimationFrame(() => {
        syncPriceScaleWidth()
        requestAnimationFrame(() => {
          syncPriceScaleWidth()
          requestAnimationFrame(syncPriceScaleWidth)
        })
      })
      // 缩放 / 拖动时价格标签宽度可能变化，持续校准
      chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
        requestAnimationFrame(syncPriceScaleWidth)
      })

      // 十字线联动：主图 ↔ RSI 子图（带真实数值，触发横向辅助线 + 价格刻度标签）
      let crossSyncing = false
      chart.subscribeCrosshairMove(param => {
        if (crossSyncing || !rsiChartRef.current || !rsiSeriesLocal) return
        crossSyncing = true
        if (param.time != null) {
          const v = rsiMap.get(param.time as number)
          if (v != null) {
            rsiChartRef.current.setCrosshairPosition(v, param.time as any, rsiSeriesLocal)
          } else {
            rsiChartRef.current.clearCrosshairPosition()
          }
        } else {
          rsiChartRef.current.clearCrosshairPosition()
        }
        crossSyncing = false
      })
      rsiChart.subscribeCrosshairMove(param => {
        if (crossSyncing || !chartRef.current || !candleSeriesRef.current) return
        crossSyncing = true
        if (param.time != null) {
          const c = candleMap.get(param.time as number)
          const price = c ? c.close : NaN
          chartRef.current.setCrosshairPosition(price as any, param.time as any, candleSeriesRef.current)
        } else {
          chartRef.current.clearCrosshairPosition()
        }
        crossSyncing = false
      })
    }

    // Crosshair OHLC overlay
    chart.subscribeCrosshairMove(param => {
      if (!param.time || !param.point) {
        setHoveredCandle(null)
        return
      }
      const c = candleMap.get(param.time as number)
      if (c) setHoveredCandle({ open: c.open, high: c.high, low: c.low, close: c.close })
      else setHoveredCandle(null)
    })

    const ro = new ResizeObserver(() => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.applyOptions({ width: containerRef.current.clientWidth })
      }
      if (rsiContainerRef.current && rsiChartRef.current) {
        rsiChartRef.current.applyOptions({ width: rsiContainerRef.current.clientWidth })
      }
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      setHoveredCandle(null)
      priceLinesRef.current = []
      rsiChartRef.current?.remove()
      rsiChartRef.current = null
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
      maSeriesRef.current = null
      trendSeriesRef.current = null
    }
  }, [candles, isDark, isFullscreen, showRsi])

  // MA / 趋势 / label 数据
  useEffect(() => {
    const ma = maSeriesRef.current
    const trend = trendSeriesRef.current
    if (!ma || !trend) return
    ma.applyOptions({ title: maLabel })
    ma.setData(maLine.map(p => ({ time: p.time as any, value: p.value })))
    trend.setData(trendMa.map(p => ({ time: p.time as any, value: p.value })))
  }, [maLine, maLabel, trendMa])

  // 对数 / 线性切换
  useEffect(() => {
    chartRef.current?.priceScale('right').applyOptions({
      mode: isLog ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
    })
  }, [isLog])

  // 交易 markers
  useEffect(() => {
    const candle = candleSeriesRef.current
    if (!candle) return

    const markers: Array<{
      time: any
      position: 'aboveBar' | 'belowBar'
      color: string
      shape: 'arrowUp' | 'arrowDown' | 'circle'
      text: string
    }> = []

    for (const t of trades) {
      markers.push({
        time: t.entry_time as any,
        position: 'belowBar',
        color: '#22c55e',
        shape: 'arrowUp',
        text: maLabel,
      })
      markers.push({
        time: t.exit_time as any,
        position: t.pnl_pct >= 0 ? 'aboveBar' : 'belowBar',
        color: EXIT_COLOR[t.exit_reason] ?? '#9ca3af',
        shape: 'arrowDown',
        text: `${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct.toFixed(2)}% ${t.exit_reason}`,
      })
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number))
    candle.setMarkers(markers)
  }, [trades, maLabel])

  // 选中某笔 → 画 3 条价格线 + 滚动到该笔时间区间
  useEffect(() => {
    const candle = candleSeriesRef.current
    const chart = chartRef.current
    if (!candle || !chart) return

    priceLinesRef.current.forEach(line => {
      try { candle.removePriceLine(line) } catch { /* ignore */ }
    })
    priceLinesRef.current = []

    if (!selectedTrade) return

    priceLinesRef.current.push(
      candle.createPriceLine({
        price: selectedTrade.entry_price,
        color: '#22c55e', lineWidth: 1, lineStyle: LineStyle.Solid,
        axisLabelVisible: true, title: '入场',
      }),
    )
    priceLinesRef.current.push(
      candle.createPriceLine({
        price: selectedTrade.stop_loss,
        color: '#ef4444', lineWidth: 1, lineStyle: LineStyle.Dashed,
        axisLabelVisible: true, title: '止损',
      }),
    )
    priceLinesRef.current.push(
      candle.createPriceLine({
        price: selectedTrade.exit_price,
        color: selectedTrade.pnl_pct >= 0 ? '#22c55e' : '#ef4444',
        lineWidth: 1, lineStyle: LineStyle.Dotted,
        axisLabelVisible: true, title: '出场',
      }),
    )

    const padDays = Math.max(
      14,
      Math.round((selectedTrade.exit_time - selectedTrade.entry_time) / 86400 * 0.4),
    )
    const padSec = padDays * 86400
    chart.timeScale().setVisibleRange({
      from: (selectedTrade.entry_time - padSec) as any,
      to: (selectedTrade.exit_time + padSec) as any,
    })
  }, [selectedTrade])

  const content = (
    <div ref={wrapperRef} style={isFullscreen
      ? { position: 'fixed', inset: 0, zIndex: 50, background: 'var(--bg-0)', display: 'flex', flexDirection: 'column' }
      : undefined}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6,
        padding: isFullscreen ? '8px 12px' : '0 0 6px 0',
        flexWrap: 'wrap',
      }}>
        {isFullscreen && (
          <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-1)', marginRight: 'auto' }}>
            交易标记图 · {maLabel}
          </span>
        )}
        <button
          onClick={() => setIsLog(v => !v)}
          className={isLog ? 'btn small accent' : 'btn small ghost'}
          title="价格刻度对数 / 线性"
        >对数</button>
        <button
          onClick={() => setShowRsi(v => !v)}
          className={showRsi ? 'btn small accent' : 'btn small ghost'}
          title="底部显示 RSI(14)"
        >RSI</button>
        <button
          onClick={() => setIsFullscreen(v => !v)}
          className="btn small ghost"
          title={isFullscreen ? '退出全屏 (Esc)' : '全屏'}
        >
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
      </div>
      <div style={{ position: 'relative' }}>
        <div ref={containerRef} style={{ width: '100%' }} />
        {hoveredCandle && (() => {
          const { open, high, low, close } = hoveredCandle
          const bodyPct = Math.abs(close - open) / open * 100
          const isUp = close >= open
          const fmt = (v: number) => v >= 1000 ? v.toLocaleString('en-US', { maximumFractionDigits: 2 }) : v.toPrecision(5).replace(/\.?0+$/, '')
          return (
            <div style={{
              position: 'absolute', top: 6, left: 6, zIndex: 10,
              display: 'flex', gap: 10, alignItems: 'center',
              fontFamily: 'var(--font-mono)', fontSize: 11,
              background: 'var(--bg-0)', borderRadius: 4,
              padding: '3px 7px', pointerEvents: 'none',
              opacity: 0.92,
            }}>
              <span style={{ color: 'var(--fg-3)' }}>开 <span style={{ color: 'var(--fg-1)' }}>{fmt(open)}</span></span>
              <span style={{ color: 'var(--fg-3)' }}>高 <span style={{ color: '#22c55e' }}>{fmt(high)}</span></span>
              <span style={{ color: 'var(--fg-3)' }}>低 <span style={{ color: '#ef4444' }}>{fmt(low)}</span></span>
              <span style={{ color: 'var(--fg-3)' }}>收 <span style={{ color: isUp ? '#22c55e' : '#ef4444' }}>{fmt(close)}</span></span>
              <span style={{ color: 'var(--fg-3)' }}>实体 <span style={{ color: isUp ? '#22c55e' : '#ef4444', fontWeight: 600 }}>{isUp ? '+' : '-'}{bodyPct.toFixed(2)}%</span></span>
            </div>
          )
        })()}
      </div>
      {showRsi && <div ref={rsiContainerRef} style={{ width: '100%', marginTop: 2 }} />}
    </div>
  )

  return content
}
