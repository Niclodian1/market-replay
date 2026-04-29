'use client'

import { useEffect, useRef, useCallback, useState } from 'react'
import { clsx } from 'clsx'
import {
  createChart,
  ColorType,
  CrosshairMode,
  PriceScaleMode,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import type { Candle, Indicators, ImpulseColor } from '@/types'
import { useAppStore } from '@/store/useAppStore'
import { useChartDrawings, DRAWING_TOOLS, PRESET_COLORS } from '@/hooks/useChartDrawings'

// ── 主题常量 ─────────────────────────────────────────────────────────────────

const DARK_THEME = {
  layout: { background: { type: ColorType.Solid, color: '#111827' }, textColor: '#9ca3af' },
  grid:   { vertLines: { color: '#1f293760' }, horzLines: { color: '#1f293760' } },
  rightPriceScale: { borderColor: '#374151' },
  crosshair: { mode: CrosshairMode.Normal },
}
const LIGHT_THEME = {
  layout: { background: { type: ColorType.Solid, color: '#ffffff' }, textColor: '#374151' },
  grid:   { vertLines: { color: '#f3f4f6' }, horzLines: { color: '#f3f4f6' } },
  rightPriceScale: { borderColor: '#e5e7eb' },
  crosshair: { mode: CrosshairMode.Normal },
}
const DARK_TS_SHOW  = { borderColor: '#374151', timeVisible: true, secondsVisible: false }
const DARK_TS_HIDE  = { borderColor: '#374151', visible: false as const }
const LIGHT_TS_SHOW = { borderColor: '#e5e7eb', timeVisible: true, secondsVisible: false }
const LIGHT_TS_HIDE = { borderColor: '#e5e7eb', visible: false as const }

const MA_COLORS = ['#60a5fa', '#f97316', '#34d399', '#a78bfa', '#f43f5e', '#facc15']

// ── Types ────────────────────────────────────────────────────────────────────

type SeriesStore = {
  main:   IChartApi | null
  rsi:    IChartApi | null
  macd:   IChartApi | null
  candle: ISeriesApi<'Candlestick'> | null
  volume: ISeriesApi<'Histogram'> | null
  rsiL:   ISeriesApi<'Line'> | null
  hist:   ISeriesApi<'Histogram'> | null
  macdL:  ISeriesApi<'Line'> | null
  sigL:   ISeriesApi<'Line'> | null
}

interface Props {
  candles: Candle[]
  indicators: Indicators
  logScale: boolean
  maPeriods?: number[]
  onMaPeriodsChange?: (p: number[]) => void
  isLoadingMore?: boolean
  onScrollToLeft?: () => void
  replayMode?: boolean
  fitTrigger?: number
  impulseColors?: ImpulseColor[]
}

// ── 主组件 ────────────────────────────────────────────────────────────────────

export function CandlestickChart({
  candles, indicators, logScale,
  maPeriods = [20, 60], onMaPeriodsChange,
  isLoadingMore, onScrollToLeft,
  replayMode, fitTrigger, impulseColors,
}: Props) {
  const theme = useAppStore(s => s.theme)
  const isDark = theme === 'dark'

  // DOM refs
  const mainWrapRef = useRef<HTMLDivElement>(null)
  const mainRef     = useRef<HTMLDivElement>(null)
  const rsiRef      = useRef<HTMLDivElement>(null)
  const macdRef     = useRef<HTMLDivElement>(null)

  // MA series refs
  const maSeriesRef    = useRef<ISeriesApi<'Line'>[]>([])
  const indicatorsRef  = useRef(indicators)
  const maPeriodsRef   = useRef(maPeriods)
  useEffect(() => { indicatorsRef.current = indicators }, [indicators])
  useEffect(() => { maPeriodsRef.current  = maPeriods  }, [maPeriods])

  // MA period local editing state
  const [localPeriods, setLocalPeriods] = useState<number[]>(maPeriods)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setLocalPeriods([...maPeriods]) }, [maPeriods.join(',')])

  const s = useRef<SeriesStore>({
    main: null, rsi: null, macd: null,
    candle: null, volume: null, rsiL: null, hist: null, macdL: null, sigL: null,
  })

  const fittedRef      = useRef(false)
  const prevFitTrigger = useRef(fitTrigger)
  const onScrollRef    = useRef(onScrollToLeft)
  useEffect(() => { onScrollRef.current = onScrollToLeft }, [onScrollToLeft])

  // ── Chart refs for drawing hook ─────────────────────────────────────────
  const chartRefs = useRef<{ main: IChartApi | null; candle: ISeriesApi<'Candlestick'> | null }>({ main: null, candle: null })
  useEffect(() => {
    chartRefs.current.main   = s.current.main
    chartRefs.current.candle = s.current.candle
  })

  const svgSize = useCallback(() => ({
    w: mainWrapRef.current?.clientWidth  ?? 800,
    h: mainWrapRef.current?.clientHeight ?? 400,
  }), [])

  // ── Drawing hook ────────────────────────────────────────────────────────
  const draw = useChartDrawings({ chartRefs, mainWrapRef, svgSize, isDark })

  // ── 图表初始化（只运行一次） ──────────────────────────────────────────────
  useEffect(() => {
    if (!mainRef.current || !rsiRef.current || !macdRef.current) return

    const chartTheme = isDark ? DARK_THEME : LIGHT_THEME
    const tsShow = isDark ? DARK_TS_SHOW : LIGHT_TS_SHOW
    const tsHide = isDark ? DARK_TS_HIDE : LIGHT_TS_HIDE
    const make = (el: HTMLDivElement, showTS: boolean) =>
      createChart(el, { ...chartTheme, width: el.clientWidth, height: el.clientHeight, timeScale: showTS ? tsShow : tsHide })

    const main  = make(mainRef.current, false)
    const rsiC  = make(rsiRef.current,  false)
    const macdC = make(macdRef.current, true)

    const candle = main.addCandlestickSeries({
      upColor: '#22c55e', downColor: '#ef4444',
      borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e',   wickDownColor: '#ef4444',
    })
    const volumeS = main.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' } })
    main.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 }, visible: false })
    const rsiL = rsiC.addLineSeries({ color: '#a78bfa', lineWidth: 1, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false })
    rsiL.createPriceLine({ price: 70, color: '#ef4444', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '70' })
    rsiL.createPriceLine({ price: 50, color: '#6b7280', lineWidth: 1, lineStyle: 2, axisLabelVisible: false })
    rsiL.createPriceLine({ price: 30, color: '#22c55e', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '30' })
    const histS = macdC.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false })
    const macdL = macdC.addLineSeries({ color: '#60a5fa', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
    const sigL  = macdC.addLineSeries({ color: '#f97316', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })

    s.current = { main, rsi: rsiC, macd: macdC, candle, volume: volumeS, rsiL, hist: histS, macdL, sigL }
    chartRefs.current = { main, candle }

    // 创建初始 MA series
    const t = (ts: number) => ts as UTCTimestamp
    maPeriodsRef.current.forEach((_, i) => {
      const color = MA_COLORS[i % MA_COLORS.length]
      const ser = main.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
      const line = indicatorsRef.current.ma_lines?.[i]
      if (line) ser.setData(line.data.map(p => ({ time: t(p.time), value: p.value })))
      maSeriesRef.current.push(ser)
    })

    // 同步时间轴
    const allCharts: IChartApi[] = [main, rsiC, macdC]
    allCharts.forEach((chart, idx) => {
      chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (!range) return
        allCharts.forEach((other, j) => { if (j !== idx) other.timeScale().setVisibleLogicalRange(range) })
        if (range.from <= 5) onScrollRef.current?.()
        draw.bumpSvgTick()
      })
    })

    main.subscribeClick((params) => {
      if (!params.point) return
      draw.clickHandlerRef.current(params.point.x, params.point.y)
    })

    main.subscribeCrosshairMove((params) => {
      if (!params.point) { draw.moveHandlerRef.current(null, null); return }
      draw.moveHandlerRef.current(params.point.x, params.point.y)
    })

    // Resize
    const ro = new ResizeObserver(() => {
      const pairs: Array<[{ current: HTMLDivElement | null }, IChartApi]> = [
        [mainRef, main], [rsiRef, rsiC], [macdRef, macdC],
      ]
      pairs.forEach(([ref, chart]) => {
        if (ref.current) chart.applyOptions({ width: ref.current.clientWidth, height: ref.current.clientHeight })
      })
      draw.bumpSvgTick()
    })
    if (mainWrapRef.current?.parentElement) ro.observe(mainWrapRef.current.parentElement)

    return () => {
      ro.disconnect()
      maSeriesRef.current = []
      allCharts.forEach(c => c.remove())
      s.current = { main: null, rsi: null, macd: null, candle: null, volume: null, rsiL: null, hist: null, macdL: null, sigL: null }
      chartRefs.current = { main: null, candle: null }
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ── MA series 管理 ──────────────────────────────────────────────────────
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    const { main } = s.current
    if (!main) return
    const t = (ts: number) => ts as UTCTimestamp
    maSeriesRef.current.forEach(ser => { try { main.removeSeries(ser) } catch {} })
    maSeriesRef.current = []
    maPeriods.forEach((_, i) => {
      const color = MA_COLORS[i % MA_COLORS.length]
      const ser = main.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
      const line = indicatorsRef.current.ma_lines?.[i]
      if (line) ser.setData(line.data.map(p => ({ time: t(p.time), value: p.value })))
      maSeriesRef.current.push(ser)
    })
  }, [maPeriods.join(',')]) // eslint-disable-line

  // ── 数据更新 ─────────────────────────────────────────────────────────────
  useEffect(() => {
    const { candle, volume, rsiL, hist, macdL, sigL, main } = s.current
    if (!candle || !candles.length) return
    const t = (ts: number) => ts as UTCTimestamp
    candle.setData(candles.map((c, i) => {
      const base = { time: t(c.time), open: c.open, high: c.high, low: c.low, close: c.close }
      const ic = impulseColors?.[i]
      if (!ic || ic === 'blue') return base
      if (ic === 'green') return { ...base, color: '#22c55e', borderColor: '#22c55e', wickColor: '#22c55e' }
      return { ...base, color: '#ef4444', borderColor: '#ef4444', wickColor: '#ef4444' }
    }))
    maSeriesRef.current.forEach((ser, i) => {
      const line = indicators.ma_lines?.[i]
      ser.setData(line ? line.data.map(p => ({ time: t(p.time), value: p.value })) : [])
    })
    volume?.setData(candles.map(c => ({ time: t(c.time), value: c.volume, color: c.close >= c.open ? '#22c55e55' : '#ef444455' })))
    rsiL?.setData(indicators.rsi.map(p => ({ time: t(p.time), value: p.value })))
    hist?.setData(indicators.macd.map(p => ({ time: t(p.time), value: p.histogram, color: p.histogram >= 0 ? '#22c55e66' : '#ef444466' })))
    macdL?.setData(indicators.macd.map(p => ({ time: t(p.time), value: p.macd })))
    sigL?.setData(indicators.macd.map(p => ({ time: t(p.time), value: p.signal })))
    const triggerChanged = fitTrigger !== prevFitTrigger.current
    prevFitTrigger.current = fitTrigger
    if (!replayMode || !fittedRef.current || triggerChanged) { main?.timeScale().fitContent(); fittedRef.current = true }
    draw.bumpSvgTick()
  }, [candles, indicators, replayMode, fitTrigger, impulseColors]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    s.current.main?.priceScale('right').applyOptions({
      mode: logScale ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
    })
  }, [logScale])

  useEffect(() => {
    const chartTheme = isDark ? DARK_THEME : LIGHT_THEME
    const tsShow = isDark ? DARK_TS_SHOW : LIGHT_TS_SHOW
    const tsHide = isDark ? DARK_TS_HIDE : LIGHT_TS_HIDE
    const { main, rsi, macd } = s.current
    if (!main || !rsi || !macd) return
    main.applyOptions({ ...chartTheme, timeScale: tsHide })
    rsi.applyOptions({ ...chartTheme, timeScale: tsHide })
    macd.applyOptions({ ...chartTheme, timeScale: tsShow })
  }, [isDark])

  const { w: svgW, h: svgH } = svgSize()

  // ── JSX ──────────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full">
      {/* 图例 + 画线工具栏 */}
      <div className="flex items-center gap-2 px-3 py-1 text-xs text-gray-500 dark:text-gray-500 shrink-0 border-b border-gray-100 dark:border-gray-800 flex-wrap">
        {localPeriods.map((p, i) => {
          const color = MA_COLORS[i % MA_COLORS.length]
          return (
            <span key={i} className="flex items-center gap-0.5 shrink-0">
              <span className="w-3 h-0.5 inline-block rounded" style={{ background: color }} />
              <span className="text-[10px]" style={{ color: color + 'b3' }}>MA</span>
              <input
                type="number" min={2} max={999} value={p}
                className="w-9 bg-transparent text-xs border-b text-center outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                style={{ color, borderColor: color + '66' }}
                onChange={e => {
                  const v = Math.max(2, Math.min(999, +e.target.value || 2))
                  setLocalPeriods(prev => prev.map((x, j) => j === i ? v : x))
                }}
                onBlur={() => onMaPeriodsChange?.(localPeriods)}
                onKeyDown={e => e.key === 'Enter' && onMaPeriodsChange?.(localPeriods)}
              />
              {localPeriods.length > 1 && (
                <button
                  onClick={() => {
                    const next = localPeriods.filter((_, j) => j !== i)
                    setLocalPeriods(next)
                    onMaPeriodsChange?.(next)
                  }}
                  className="text-gray-400 dark:text-gray-600 hover:text-red-400 text-[10px] leading-none px-0.5"
                  title="移除此均线"
                >✕</button>
              )}
            </span>
          )
        })}
        {localPeriods.length < 6 && (
          <button
            onClick={() => {
              const next = [...localPeriods, Math.max(...localPeriods) + 50]
              setLocalPeriods(next)
              onMaPeriodsChange?.(next)
            }}
            className="shrink-0 px-1.5 py-0.5 rounded text-[10px] text-gray-400 dark:text-gray-600 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors border border-transparent hover:border-gray-200 dark:hover:border-gray-700"
            title="添加均线（最多 6 条）"
          >+ MA</button>
        )}
        <span className="flex items-center gap-1 shrink-0 text-gray-400 dark:text-gray-600"><span className="w-2 h-2 rounded-sm bg-green-500/30 inline-block" />Vol</span>
        <div className="w-px h-4 bg-gray-200 dark:bg-gray-800 shrink-0" />

        {/* 工具按钮 */}
        <div className="flex items-center gap-0.5 shrink-0">
          {DRAWING_TOOLS.map(tool => (
            <button key={tool.value} title={tool.title}
              onClick={() => draw.setActiveTool(t => t === tool.value ? 'none' : tool.value)}
              className={clsx('px-1.5 py-0.5 rounded text-xs transition-colors',
                draw.activeTool === tool.value ? 'bg-blue-600 text-white' : 'text-gray-500 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800'
              )}>
              {tool.label}
            </button>
          ))}
          {draw.drawings.length > 0 && (
            <button title="清除所有画线"
              onClick={draw.clearAllDrawings}
              className="ml-0.5 px-1.5 py-0.5 rounded text-xs text-gray-400 dark:text-gray-600 hover:text-red-500 dark:hover:text-red-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors">
              ✕
            </button>
          )}
        </div>

        {/* 颜色选择器 */}
        {draw.isDrawingTool && (
          <div className="flex items-center gap-1 shrink-0">
            <span className="text-gray-600 text-[10px]">色</span>
            {PRESET_COLORS.map(c => (
              <button key={c} title={c} onClick={() => draw.setActiveColor(c)}
                style={{ backgroundColor: c, outline: c === draw.activeColor ? `2px solid white` : 'none', outlineOffset: '1px' }}
                className={clsx('w-3.5 h-3.5 rounded-sm transition-transform', c === draw.activeColor ? 'scale-125' : 'hover:scale-110')} />
            ))}
            <label title="自定义颜色" className="relative w-3.5 h-3.5 cursor-pointer overflow-hidden rounded-sm border border-gray-600">
              <input type="color" value={draw.activeColor} onChange={e => draw.setActiveColor(e.target.value)}
                className="absolute -inset-1 opacity-0 w-8 h-8 cursor-pointer" />
              <span style={{ background: `linear-gradient(135deg, #f00, #ff0, #0f0, #0ff, #00f, #f0f)` }}
                className="absolute inset-0" />
            </label>
          </div>
        )}

        {/* 删除选中 */}
        {draw.selectedId && draw.activeTool === 'select' && (
          <button onClick={() => draw.deleteDrawing(draw.selectedId!)}
            className="px-2 py-0.5 rounded text-xs bg-red-900/50 text-red-400 hover:bg-red-800/60 transition-colors shrink-0">
            删除选中
          </button>
        )}

        {/* 提示文字 */}
        <div className="ml-auto flex items-center gap-2 shrink-0">
          {draw.isDrawingTool && (
            <span className="text-blue-400">
              {draw.pendingPts.length === 0 ? '单击放置第 1 点' : `单击第 ${draw.pendingPts.length + 1} 点 · 右键取消`}
            </span>
          )}
          {draw.activeTool === 'select' && <span className="text-gray-400 dark:text-gray-500">单击选中 · 右键删除 · Del 删除</span>}
          {isLoadingMore && (
            <span className="flex items-center gap-1 text-gray-600">
              <span className="w-3 h-3 border border-gray-500 border-t-transparent rounded-full animate-spin inline-block" />
              加载历史...
            </span>
          )}
        </div>
      </div>

      {/* 主图区域 */}
      <div ref={mainWrapRef} className="flex-[6] min-h-0 relative" onContextMenu={draw.handleContextMenu}>
        <div ref={mainRef} className="absolute inset-0" />

        <svg className="absolute inset-0 w-full h-full" style={{ pointerEvents: 'none', overflow: 'hidden', zIndex: 1 }}>
          <defs>
            <filter id="sel-glow" x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur stdDeviation="2" result="blur" />
              <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
            <clipPath id="chart-clip">
              <rect x={0} y={0} width={svgW > 0 ? svgW : 9999} height={svgH > 0 ? svgH : 9999} />
            </clipPath>
          </defs>
          <g clipPath="url(#chart-clip)">
            {draw.renderDrawings()}
            {draw.renderPreview()}
            {draw.renderAnchors()}
          </g>
        </svg>

        {draw.ctxMenu && (
          <div className="absolute bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded shadow-xl z-50 py-1 text-xs min-w-[100px]"
            style={{ left: Math.min(draw.ctxMenu.svgX + 4, svgW - 110), top: Math.min(draw.ctxMenu.svgY + 4, svgH - 60) }}
            onMouseDown={e => e.stopPropagation()}>
            <button onClick={() => draw.deleteDrawing(draw.ctxMenu!.drawingId)}
              className="w-full text-left px-3 py-1.5 text-red-400 hover:bg-red-900/40 transition-colors">
              删除画线
            </button>
            <button onClick={draw.dismissCtxMenu}
              className="w-full text-left px-3 py-1.5 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors">
              取消
            </button>
          </div>
        )}
      </div>

      {/* RSI */}
      <div className="border-t border-gray-100 dark:border-gray-800 shrink-0 flex flex-col" style={{ height: 76 }}>
        <div className="px-2 text-[10px] text-gray-400 dark:text-gray-600 pt-0.5">RSI (14)</div>
        <div ref={rsiRef} className="flex-1" />
      </div>

      {/* MACD */}
      <div className="border-t border-gray-100 dark:border-gray-800 shrink-0 flex flex-col" style={{ height: 90 }}>
        <div className="px-2 text-[10px] text-gray-400 dark:text-gray-600 pt-0.5 flex gap-3">
          <span>MACD (12,26,9)</span>
          <span className="text-blue-400">— MACD</span>
          <span className="text-orange-400">— Signal</span>
        </div>
        <div ref={macdRef} className="flex-1" />
      </div>
    </div>
  )
}
