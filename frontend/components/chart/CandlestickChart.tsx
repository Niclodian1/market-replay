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
import type {
  Candle, Indicators,
  DrawingToolType, DrawingAnchor,
  ChartDrawing, HorizontalLineDrawing,
  TrendLineDrawing, ChannelDrawing, FibRetracementDrawing,
} from '@/types'

// ── 常量 ─────────────────────────────────────────────────────────────────────

const THEME = {
  layout: { background: { type: ColorType.Solid, color: '#111827' }, textColor: '#9ca3af' },
  grid:   { vertLines: { color: '#1f293760' }, horzLines: { color: '#1f293760' } },
  rightPriceScale: { borderColor: '#374151' },
  crosshair: { mode: CrosshairMode.Normal },
}
const TS_SHOW = { borderColor: '#374151', timeVisible: true, secondsVisible: false }
const TS_HIDE = { borderColor: '#374151', visible: false as const }

const FIB_LEVELS = [
  { pct: 0,     label: '0.0%',   weight: 1.5, dash: false, color: '#ef4444' },
  { pct: 0.236, label: '23.6%',  weight: 1,   dash: true,  color: '#f97316' },
  { pct: 0.382, label: '38.2%',  weight: 1,   dash: true,  color: '#eab308' },
  { pct: 0.5,   label: '50.0%',  weight: 1,   dash: true,  color: '#9ca3af' },
  { pct: 0.618, label: '61.8%',  weight: 1.5, dash: false, color: '#22c55e' },
  { pct: 0.786, label: '78.6%',  weight: 1,   dash: true,  color: '#3b82f6' },
  { pct: 1,     label: '100.0%', weight: 1.5, dash: false, color: '#ef4444' },
]

const DRAWING_TOOLS: { value: DrawingToolType; label: string; title: string }[] = [
  { value: 'select',    label: '↖',   title: '选择/删除画线'         },
  { value: 'horizontal',label: '─',   title: '水平线（1次点击）'      },
  { value: 'trendline', label: '╱',   title: '趋势线（2次点击）'      },
  { value: 'channel',   label: '⟋╲', title: '通道线（3次点击）'      },
  { value: 'fibonacci', label: 'Fib', title: '斐波那契回撤（2次点击）' },
]

const CLICKS_NEEDED: Record<string, number> = {
  horizontal: 1, trendline: 2, channel: 3, fibonacci: 2,
}

const PRESET_COLORS = [
  '#f59e0b', '#ef4444', '#22c55e', '#3b82f6',
  '#8b5cf6', '#ec4899', '#06b6d4', '#f8fafc',
]

// ── Types ────────────────────────────────────────────────────────────────────

type SeriesStore = {
  main:   IChartApi | null
  rsi:    IChartApi | null; macd: IChartApi | null
  candle: ISeriesApi<'Candlestick'> | null
  ma20:   ISeriesApi<'Line'> | null; ma60:    ISeriesApi<'Line'> | null
  volume: ISeriesApi<'Histogram'> | null; rsiL: ISeriesApi<'Line'> | null
  hist:   ISeriesApi<'Histogram'> | null
  macdL:  ISeriesApi<'Line'> | null; sigL: ISeriesApi<'Line'> | null
}

interface Props {
  candles: Candle[]
  indicators: Indicators
  logScale: boolean
  maPeriods?: [number, number]
  onMaPeriodsChange?: (p: [number, number]) => void
  isLoadingMore?: boolean
  onScrollToLeft?: () => void
  replayMode?: boolean
  fitTrigger?: number
}

type CtxMenu = { svgX: number; svgY: number; drawingId: string }

// ── 工具函数 ─────────────────────────────────────────────────────────────────

function fmtPrice(v: number): string {
  if (v >= 10000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 })
  if (v >= 100)   return v.toFixed(2)
  if (v >= 1)     return v.toFixed(4)
  return v.toFixed(6)
}

function extendLine(ax: number, ay: number, bx: number, by: number, w: number): [number, number, number, number] {
  if (Math.abs(bx - ax) < 0.5) return [ax, -5000, ax, 5000]
  const m = (by - ay) / (bx - ax)
  return [-500, ay + m * (-500 - ax), w + 500, ay + m * (w + 500 - ax)]
}

function ptLineDist(px: number, py: number, x1: number, y1: number, x2: number, y2: number): number {
  const dx = x2 - x1, dy = y2 - y1
  const len2 = dx * dx + dy * dy
  if (len2 === 0) return Math.hypot(px - x1, py - y1)
  const t = ((px - x1) * dx + (py - y1) * dy) / len2
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
}

function lighten(hex: string): string {
  try {
    const n = parseInt(hex.slice(1), 16)
    const r = Math.min(255, ((n >> 16) & 0xff) + 50)
    const g = Math.min(255, ((n >> 8)  & 0xff) + 50)
    const b = Math.min(255, ( n        & 0xff) + 50)
    return `#${r.toString(16).padStart(2,'0')}${g.toString(16).padStart(2,'0')}${b.toString(16).padStart(2,'0')}`
  } catch { return hex }
}

// ── 主组件 ────────────────────────────────────────────────────────────────────

export function CandlestickChart({
  candles, indicators, logScale,
  maPeriods = [30, 60], onMaPeriodsChange,
  isLoadingMore, onScrollToLeft,
  replayMode, fitTrigger,
}: Props) {
  // DOM refs
  const mainWrapRef = useRef<HTMLDivElement>(null)
  const mainRef     = useRef<HTMLDivElement>(null)
  const rsiRef      = useRef<HTMLDivElement>(null)
  const macdRef     = useRef<HTMLDivElement>(null)

  // MA period local editing state
  const [localP1, setLocalP1] = useState(maPeriods[0])
  const [localP2, setLocalP2] = useState(maPeriods[1])
  // sync local state when prop changes externally
  useEffect(() => { setLocalP1(maPeriods[0]); setLocalP2(maPeriods[1]) }, [maPeriods[0], maPeriods[1]])

  const s = useRef<SeriesStore>({
    main: null, rsi: null, macd: null,
    candle: null, ma20: null, ma60: null,
    volume: null, rsiL: null, hist: null, macdL: null, sigL: null,
  })

  const fittedRef      = useRef(false)
  const prevFitTrigger = useRef(fitTrigger)
  const onScrollRef    = useRef(onScrollToLeft)
  useEffect(() => { onScrollRef.current = onScrollToLeft }, [onScrollToLeft])

  // ── 画线状态 ──────────────────────────────────────────────────────────────
  const [svgTick,    setSvgTick]    = useState(0)
  const [activeTool, setActiveTool] = useState<DrawingToolType>('none')
  const [activeColor,setActiveColor]= useState('#3b82f6')
  const [drawings,   setDrawings]   = useState<ChartDrawing[]>([])
  const [pendingPts, setPendingPts] = useState<DrawingAnchor[]>([])
  const [mousePos,   setMousePos]   = useState<{ x: number; y: number } | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hoveredId,  setHoveredId]  = useState<string | null>(null)
  const [ctxMenu,    setCtxMenu]    = useState<CtxMenu | null>(null)

  // ── 给 LW Charts 订阅用的稳定回调 ref（每次渲染更新，保持最新闭包） ──────
  // 这样订阅只需要设置一次，但始终能访问到最新的 state
  const clickHandlerRef = useRef<(x: number, y: number) => void>(() => {})
  const moveHandlerRef  = useRef<(x: number | null, y: number | null) => void>(() => {})

  // ── 坐标转换 ─────────────────────────────────────────────────────────────

  const svgSize = useCallback(() => ({
    w: mainWrapRef.current?.clientWidth  ?? 800,
    h: mainWrapRef.current?.clientHeight ?? 400,
  }), [])

  const toXY = useCallback((anchor: DrawingAnchor): [number, number] | null => {
    const chart  = s.current.main
    const series = s.current.candle
    if (!chart || !series) return null
    const x = chart.timeScale().timeToCoordinate(anchor.time as UTCTimestamp)
    if (x === null) return null
    const yCoord = series.priceToCoordinate(anchor.price)
    if (yCoord !== null) return [x as number, yCoord as number]
    const { h } = svgSize()
    const tp = series.coordinateToPrice(0)
    const bp = series.coordinateToPrice(h)
    if (tp === null || bp === null) return null
    return [x as number, ((tp - anchor.price) / (tp - bp)) * h]
  }, [svgSize])

  const fromXY = useCallback((localX: number, localY: number): DrawingAnchor | null => {
    const series = s.current.candle
    const chart  = s.current.main
    if (!chart || !series) return null
    let timeVal: number | null = null
    const rawTime = chart.timeScale().coordinateToTime(localX)
    if (rawTime !== null) {
      timeVal = rawTime as number
    } else {
      const vr = chart.timeScale().getVisibleRange()
      if (vr) {
        const x1 = chart.timeScale().timeToCoordinate(vr.from as UTCTimestamp) as number | null
        const x2 = chart.timeScale().timeToCoordinate(vr.to   as UTCTimestamp) as number | null
        if (x1 !== null && x2 !== null && Math.abs(x2 - x1) > 1) {
          timeVal = (vr.from as number) + ((localX - x1) / (x2 - x1)) * ((vr.to as number) - (vr.from as number))
        }
      }
    }
    if (timeVal === null) return null
    const price = series.coordinateToPrice(localY)
    if (price === null) return null
    return { time: timeVal, price: price as number }
  }, [])

  const getDrawingAtPoint = useCallback((px: number, py: number): ChartDrawing | null => {
    const series = s.current.candle
    const { h }  = svgSize()
    const TOL = 8

    function yOfPrice(price: number): number | null {
      if (!series) return null
      const y = series.priceToCoordinate(price)
      if (y !== null) return y as number
      const tp = series.coordinateToPrice(0)
      const bp = series.coordinateToPrice(h)
      if (tp === null || bp === null) return null
      return ((tp - price) / (tp - bp)) * h
    }

    for (const d of drawings) {
      if (d.type === 'horizontal') {
        const y = yOfPrice(d.price)
        if (y !== null && Math.abs(py - y) < TOL) return d
      } else if (d.type === 'trendline') {
        const c1 = toXY(d.p1), c2 = toXY(d.p2)
        if (c1 && c2 && ptLineDist(px, py, c1[0], c1[1], c2[0], c2[1]) < TOL) return d
      } else if (d.type === 'channel') {
        const c1 = toXY(d.p1), c2 = toXY(d.p2), c3 = toXY(d.p3)
        if (c1 && c2 && c3) {
          if (ptLineDist(px, py, c1[0], c1[1], c2[0], c2[1]) < TOL) return d
          const m = Math.abs(c2[0]-c1[0]) < 0.5 ? 0 : (c2[1]-c1[1])/(c2[0]-c1[0])
          const off = c3[1] - (c1[1] + m * (c3[0]-c1[0]))
          if (ptLineDist(px, py, c1[0], c1[1]+off, c2[0], c2[1]+off) < TOL) return d
        }
      } else if (d.type === 'fibonacci') {
        const pd = d.p2.price - d.p1.price
        for (const lvl of FIB_LEVELS) {
          const y = yOfPrice(d.p1.price + pd * lvl.pct)
          if (y !== null && Math.abs(py - y) < TOL) return d
        }
      }
    }
    return null
  }, [drawings, toXY, svgSize])

  // ── 每次渲染更新回调 ref（LW Charts 订阅使用） ────────────────────────────
  // 注意：直接赋值而非 useCallback，确保闭包始终是最新的 state
  clickHandlerRef.current = (x: number, y: number) => {
    if (activeTool === 'none') return
    setCtxMenu(null)

    if (activeTool === 'select') {
      setSelectedId(getDrawingAtPoint(x, y)?.id ?? null)
      return
    }

    const anchor = fromXY(x, y)
    if (!anchor) return

    const needed = CLICKS_NEEDED[activeTool]
    const np = [...pendingPts, anchor]

    if (np.length >= needed) {
      const id = `${activeTool}-${Date.now()}`
      const color = activeColor
      let drawing: ChartDrawing
      switch (activeTool) {
        case 'horizontal': drawing = { id, type: 'horizontal', price: np[0].price, color }; break
        case 'trendline':  drawing = { id, type: 'trendline',  p1: np[0], p2: np[1], color }; break
        case 'channel':    drawing = { id, type: 'channel', p1: np[0], p2: np[1], p3: np[2], color }; break
        case 'fibonacci':  drawing = { id, type: 'fibonacci', p1: np[0], p2: np[1], color }; break
        default: return
      }
      setDrawings(prev => [...prev, drawing])
      setPendingPts([])
    } else {
      setPendingPts(np)
    }
  }

  moveHandlerRef.current = (x: number | null, y: number | null) => {
    if (x === null || y === null) { setMousePos(null); setHoveredId(null); return }
    setMousePos({ x, y })
    setHoveredId(activeTool === 'select' ? (getDrawingAtPoint(x, y)?.id ?? null) : null)
  }

  // ── 切换工具：仅清待定点，不清已完成画线 ──────────────────────────────────
  useEffect(() => {
    setPendingPts([])
    setMousePos(null)
    setCtxMenu(null)
    if (activeTool !== 'select') setSelectedId(null)
    setHoveredId(null)
  }, [activeTool])

  // ── 禁用/恢复图表滚动缩放（画线模式时禁用，避免与绘制冲突） ───────────────
  useEffect(() => {
    const chart = s.current.main
    if (!chart) return
    const off = activeTool !== 'none'
    chart.applyOptions({
      handleScroll: !off,
      handleScale:  !off,
    })
  }, [activeTool])

  // ── Escape / Delete 键 ────────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (pendingPts.length > 0) { setPendingPts([]); setMousePos(null) }
        else { setSelectedId(null); setCtxMenu(null) }
      }
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedId) {
        if (document.activeElement?.tagName === 'INPUT' || document.activeElement?.tagName === 'TEXTAREA') return
        setDrawings(prev => prev.filter(d => d.id !== selectedId))
        setSelectedId(null)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [pendingPts.length, selectedId])

  // ── 图表初始化（只运行一次） ──────────────────────────────────────────────
  useEffect(() => {
    if (!mainRef.current || !rsiRef.current || !macdRef.current) return

    const make = (el: HTMLDivElement, showTS: boolean) =>
      createChart(el, { ...THEME, width: el.clientWidth, height: el.clientHeight, timeScale: showTS ? TS_SHOW : TS_HIDE })

    const main  = make(mainRef.current, false)
    const rsiC  = make(rsiRef.current,  false)
    const macdC = make(macdRef.current, true)

    const candle = main.addCandlestickSeries({
      upColor: '#22c55e', downColor: '#ef4444',
      borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e',   wickDownColor: '#ef4444',
    })
    const ma20s = main.addLineSeries({ color: '#60a5fa', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
    const ma60s = main.addLineSeries({ color: '#f97316', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
    // 成交量叠加在主图底部（独立价格轴，不显示刻度）
    const volumeS = main.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' } })
    main.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 }, visible: false })
    const rsiL = rsiC.addLineSeries({ color: '#a78bfa', lineWidth: 1, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false })
    rsiL.createPriceLine({ price: 70, color: '#ef4444', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '70' })
    rsiL.createPriceLine({ price: 50, color: '#6b7280', lineWidth: 1, lineStyle: 2, axisLabelVisible: false })
    rsiL.createPriceLine({ price: 30, color: '#22c55e', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '30' })
    const histS = macdC.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false })
    const macdL = macdC.addLineSeries({ color: '#60a5fa', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
    const sigL  = macdC.addLineSeries({ color: '#f97316', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })

    s.current = { main, rsi: rsiC, macd: macdC, candle, ma20: ma20s, ma60: ma60s, volume: volumeS, rsiL, hist: histS, macdL, sigL }

    // 同步时间轴
    const allCharts: IChartApi[] = [main, rsiC, macdC]
    allCharts.forEach((chart, idx) => {
      chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (!range) return
        allCharts.forEach((other, j) => { if (j !== idx) other.timeScale().setVisibleLogicalRange(range) })
        if (range.from <= 5) onScrollRef.current?.()
        setSvgTick(k => k + 1)
      })
    })

    // ── 关键：用 LW Charts 的点击事件代替 SVG 拦截 ──────────────────────────
    main.subscribeClick((params) => {
      if (!params.point) return
      clickHandlerRef.current(params.point.x, params.point.y)
    })

    // 鼠标移动 → 更新预览位置
    main.subscribeCrosshairMove((params) => {
      if (!params.point) { moveHandlerRef.current(null, null); return }
      moveHandlerRef.current(params.point.x, params.point.y)
    })

    // Resize
    const ro = new ResizeObserver(() => {
      const pairs: Array<[{ current: HTMLDivElement | null }, IChartApi]> = [
        [mainRef, main], [rsiRef, rsiC], [macdRef, macdC],
      ]
      pairs.forEach(([ref, chart]) => {
        if (ref.current) chart.applyOptions({ width: ref.current.clientWidth, height: ref.current.clientHeight })
      })
      setSvgTick(k => k + 1)
    })
    if (mainWrapRef.current?.parentElement) ro.observe(mainWrapRef.current.parentElement)

    return () => {
      ro.disconnect()
      allCharts.forEach(c => c.remove())
      s.current = { main: null, rsi: null, macd: null, candle: null, ma20: null, ma60: null, volume: null, rsiL: null, hist: null, macdL: null, sigL: null }
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ── 数据更新 ─────────────────────────────────────────────────────────────
  useEffect(() => {
    const { candle, ma20, ma60, volume, rsiL, hist, macdL, sigL, main } = s.current
    if (!candle || !candles.length) return
    const t = (ts: number) => ts as UTCTimestamp
    candle.setData(candles.map(c => ({ time: t(c.time), open: c.open, high: c.high, low: c.low, close: c.close })))
    ma20?.setData(indicators.ma20.map(p => ({ time: t(p.time), value: p.value })))
    ma60?.setData(indicators.ma60.map(p => ({ time: t(p.time), value: p.value })))
    volume?.setData(candles.map(c => ({ time: t(c.time), value: c.volume, color: c.close >= c.open ? '#22c55e55' : '#ef444455' })))
    rsiL?.setData(indicators.rsi.map(p => ({ time: t(p.time), value: p.value })))
    hist?.setData(indicators.macd.map(p => ({ time: t(p.time), value: p.histogram, color: p.histogram >= 0 ? '#22c55e66' : '#ef444466' })))
    macdL?.setData(indicators.macd.map(p => ({ time: t(p.time), value: p.macd })))
    sigL?.setData(indicators.macd.map(p => ({ time: t(p.time), value: p.signal })))
    const triggerChanged = fitTrigger !== prevFitTrigger.current
    prevFitTrigger.current = fitTrigger
    if (!replayMode || !fittedRef.current || triggerChanged) { main?.timeScale().fitContent(); fittedRef.current = true }
    setSvgTick(k => k + 1)
  }, [candles, indicators, replayMode, fitTrigger])

  useEffect(() => {
    s.current.main?.priceScale('right').applyOptions({
      mode: logScale ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
    })
  }, [logScale])

  // ── 右键菜单（挂在 wrapper div 上，冒泡捕获） ────────────────────────────
  const handleContextMenu = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    e.preventDefault()
    if (pendingPts.length > 0) { setPendingPts([]); setMousePos(null); return }
    const rect = mainWrapRef.current?.getBoundingClientRect()
    if (!rect) return
    const lx = e.clientX - rect.left
    const ly = e.clientY - rect.top
    const found = getDrawingAtPoint(lx, ly)
    if (found) {
      setSelectedId(found.id)
      setCtxMenu({ svgX: lx, svgY: ly, drawingId: found.id })
    }
  }, [pendingPts, getDrawingAtPoint])

  const deleteDrawing = useCallback((id: string) => {
    setDrawings(prev => prev.filter(d => d.id !== id))
    setSelectedId(null)
    setCtxMenu(null)
  }, [])

  // ── SVG 渲染 ─────────────────────────────────────────────────────────────

  function renderDrawings(): React.ReactNode {
    const { w, h } = svgSize()
    const series = s.current.candle
    if (!series) return null

    function yOfPrice(price: number): number | null {
      const y = series!.priceToCoordinate(price)
      if (y !== null) return y as number
      const tp = series!.coordinateToPrice(0)
      const bp = series!.coordinateToPrice(h)
      if (tp === null || bp === null) return null
      const yv = ((tp - price) / (tp - bp)) * h
      return (yv < -30 || yv > h + 30) ? null : yv
    }

    function sp(id: string, color: string, bw: number) {
      const sel = id === selectedId, hov = id === hoveredId
      return {
        stroke:      sel ? '#ffffff' : hov ? lighten(color) : color,
        strokeWidth: sel ? bw + 1.5  : hov ? bw + 0.5      : bw,
        filter:      sel ? 'url(#sel-glow)' : undefined,
      }
    }

    function renderH(d: HorizontalLineDrawing) {
      const y = yOfPrice(d.price)
      if (y === null) return null
      const p = sp(d.id, d.color, 1.5)
      return (
        <g key={d.id}>
          <line x1={0} y1={y} x2={w} y2={y} {...p} />
          <rect x={w - 68} y={y - 10} width={66} height={15} fill="#111827" stroke={d.color} strokeWidth={0.5} rx={2} />
          <text x={w - 4} y={y + 4} fill={d.color} fontSize={10} textAnchor="end" fontFamily="monospace">{fmtPrice(d.price)}</text>
        </g>
      )
    }

    function renderT(d: TrendLineDrawing) {
      const c1 = toXY(d.p1), c2 = toXY(d.p2)
      if (!c1 || !c2) return null
      const [x1, y1, x2, y2] = extendLine(c1[0], c1[1], c2[0], c2[1], w)
      return <line key={d.id} x1={x1} y1={y1} x2={x2} y2={y2} {...sp(d.id, d.color, 1.5)} />
    }

    function renderCh(d: ChannelDrawing) {
      const c1 = toXY(d.p1), c2 = toXY(d.p2), c3 = toXY(d.p3)
      if (!c1 || !c2 || !c3) return null
      const [lx1, ly1, lx2, ly2] = extendLine(c1[0], c1[1], c2[0], c2[1], w)
      const m   = Math.abs(c2[0]-c1[0]) < 0.5 ? 0 : (c2[1]-c1[1])/(c2[0]-c1[0])
      const off = c3[1] - (c1[1] + m * (c3[0]-c1[0]))
      const p   = sp(d.id, d.color, 1.5)
      return (
        <g key={d.id}>
          <line x1={lx1} y1={ly1} x2={lx2} y2={ly2} {...p} />
          <line x1={lx1} y1={ly1+off} x2={lx2} y2={ly2+off} stroke={p.stroke} strokeWidth={p.strokeWidth * 0.7} strokeDasharray="5 3" filter={p.filter} />
          <line x1={lx1} y1={ly1+off/2} x2={lx2} y2={ly2+off/2} stroke={p.stroke} strokeWidth={0.5} strokeDasharray="2 4" strokeOpacity={0.4} />
        </g>
      )
    }

    function renderFib(d: FibRetracementDrawing) {
      const c2 = toXY(d.p2)
      const pd  = d.p2.price - d.p1.price
      const xl  = c2 ? Math.min(w - 6, c2[0]) : w * 0.7
      const sel = d.id === selectedId, hov = d.id === hoveredId
      return (
        <g key={d.id}>
          {FIB_LEVELS.map(lv => {
            const price = d.p1.price + pd * lv.pct
            const y = yOfPrice(price)
            if (y === null) return null
            const col = sel ? '#ffffff' : hov ? lighten(lv.color) : lv.color
            const wt  = sel ? lv.weight + 1 : lv.weight
            return (
              <g key={lv.pct}>
                <line x1={0} y1={y} x2={w} y2={y} stroke={col} strokeWidth={wt} strokeDasharray={lv.dash ? '5 3' : undefined} strokeOpacity={0.9} filter={sel ? 'url(#sel-glow)' : undefined} />
                <text x={xl} y={y - 3} fill={col} fontSize={9} textAnchor="end" fontFamily="monospace">{lv.label} {fmtPrice(price)}</text>
              </g>
            )
          })}
        </g>
      )
    }

    return (
      <>
        {drawings.map(d => {
          switch (d.type) {
            case 'horizontal': return renderH(d)
            case 'trendline':  return renderT(d)
            case 'channel':    return renderCh(d)
            case 'fibonacci':  return renderFib(d)
          }
        })}
      </>
    )
  }

  function renderPreview() {
    const tool = activeTool
    if (tool === 'none' || tool === 'select' || pendingPts.length === 0 || !mousePos) return null
    const { w } = svgSize()
    const lastC = toXY(pendingPts[pendingPts.length - 1])
    if (!lastC) return null
    const { x: mx, y: my } = mousePos
    const ps = { stroke: activeColor, strokeWidth: 1, strokeDasharray: '5 3' as string, strokeOpacity: 0.7 }
    switch (tool) {
      case 'trendline': case 'fibonacci': {
        const [x1, y1, x2, y2] = extendLine(lastC[0], lastC[1], mx, my, w)
        return <line x1={x1} y1={y1} x2={x2} y2={y2} {...ps} />
      }
      case 'channel': {
        if (pendingPts.length === 1) {
          const [x1, y1, x2, y2] = extendLine(lastC[0], lastC[1], mx, my, w)
          return <line x1={x1} y1={y1} x2={x2} y2={y2} {...ps} />
        }
        if (pendingPts.length === 2) {
          const c1 = toXY(pendingPts[0]), c2 = toXY(pendingPts[1])
          if (!c1 || !c2) return null
          const [lx1, ly1, lx2, ly2] = extendLine(c1[0], c1[1], c2[0], c2[1], w)
          const m = Math.abs(c2[0]-c1[0]) < 0.5 ? 0 : (c2[1]-c1[1])/(c2[0]-c1[0])
          const off = my - (c1[1] + m * (mx - c1[0]))
          return (
            <g>
              <line x1={lx1} y1={ly1} x2={lx2} y2={ly2} stroke={activeColor} strokeWidth={1} strokeOpacity={0.7} />
              <line x1={lx1} y1={ly1+off} x2={lx2} y2={ly2+off} {...ps} />
            </g>
          )
        }
        return null
      }
      default: return null
    }
  }

  function renderAnchors() {
    return pendingPts.map((pt, i) => {
      const c = toXY(pt)
      if (!c) return null
      return <circle key={i} cx={c[0]} cy={c[1]} r={4} fill="none" stroke={activeColor} strokeWidth={1.5} strokeOpacity={0.9} />
    })
  }

  const isDrawingTool = activeTool !== 'none' && activeTool !== 'select'
  const { w: svgW, h: svgH } = svgSize()

  // ── JSX ──────────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full">
      {/* 图例 + 画线工具栏 */}
      <div className="flex items-center gap-2 px-3 py-1 text-xs text-gray-500 shrink-0 border-b border-gray-800 flex-wrap">
        <span className="flex items-center gap-1 shrink-0">
          <span className="w-3 h-0.5 bg-blue-400 inline-block" />
          <span className="text-blue-400/70">MA</span>
          <input
            type="number" min={2} max={999} value={localP1}
            className="w-9 bg-transparent text-blue-400 text-xs border-b border-blue-400/40 text-center outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
            onChange={e => setLocalP1(Math.max(2, Math.min(999, +e.target.value || 2)))}
            onBlur={() => onMaPeriodsChange?.([localP1, localP2])}
            onKeyDown={e => e.key === 'Enter' && onMaPeriodsChange?.([localP1, localP2])}
          />
        </span>
        <span className="flex items-center gap-1 shrink-0">
          <span className="w-3 h-0.5 bg-orange-400 inline-block" />
          <span className="text-orange-400/70">MA</span>
          <input
            type="number" min={2} max={999} value={localP2}
            className="w-9 bg-transparent text-orange-400 text-xs border-b border-orange-400/40 text-center outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
            onChange={e => setLocalP2(Math.max(2, Math.min(999, +e.target.value || 2)))}
            onBlur={() => onMaPeriodsChange?.([localP1, localP2])}
            onKeyDown={e => e.key === 'Enter' && onMaPeriodsChange?.([localP1, localP2])}
          />
        </span>
        <span className="flex items-center gap-1 shrink-0 text-gray-600"><span className="w-2 h-2 rounded-sm bg-green-500/30 inline-block" />Vol</span>
        <div className="w-px h-4 bg-gray-800 shrink-0" />

        {/* 工具按钮 */}
        <div className="flex items-center gap-0.5 shrink-0">
          {DRAWING_TOOLS.map(tool => (
            <button key={tool.value} title={tool.title}
              onClick={() => setActiveTool(t => t === tool.value ? 'none' : tool.value)}
              className={clsx('px-1.5 py-0.5 rounded text-xs transition-colors',
                activeTool === tool.value ? 'bg-blue-600 text-white' : 'text-gray-500 hover:text-gray-300 hover:bg-gray-800'
              )}>
              {tool.label}
            </button>
          ))}
          {drawings.length > 0 && (
            <button title="清除所有画线"
              onClick={() => { setDrawings([]); setPendingPts([]); setSelectedId(null); setCtxMenu(null) }}
              className="ml-0.5 px-1.5 py-0.5 rounded text-xs text-gray-600 hover:text-red-400 hover:bg-gray-800 transition-colors">
              ✕
            </button>
          )}
        </div>

        {/* 颜色选择器 */}
        {isDrawingTool && (
          <div className="flex items-center gap-1 shrink-0">
            <span className="text-gray-600 text-[10px]">色</span>
            {PRESET_COLORS.map(c => (
              <button key={c} title={c} onClick={() => setActiveColor(c)}
                style={{ backgroundColor: c, outline: c === activeColor ? `2px solid white` : 'none', outlineOffset: '1px' }}
                className={clsx('w-3.5 h-3.5 rounded-sm transition-transform', c === activeColor ? 'scale-125' : 'hover:scale-110')} />
            ))}
            <label title="自定义颜色" className="relative w-3.5 h-3.5 cursor-pointer overflow-hidden rounded-sm border border-gray-600">
              <input type="color" value={activeColor} onChange={e => setActiveColor(e.target.value)}
                className="absolute -inset-1 opacity-0 w-8 h-8 cursor-pointer" />
              <span style={{ background: `linear-gradient(135deg, #f00, #ff0, #0f0, #0ff, #00f, #f0f)` }}
                className="absolute inset-0" />
            </label>
          </div>
        )}

        {/* 删除选中 */}
        {selectedId && activeTool === 'select' && (
          <button onClick={() => deleteDrawing(selectedId)}
            className="px-2 py-0.5 rounded text-xs bg-red-900/50 text-red-400 hover:bg-red-800/60 transition-colors shrink-0">
            删除选中
          </button>
        )}

        {/* 提示文字 */}
        <div className="ml-auto flex items-center gap-2 shrink-0">
          {isDrawingTool && (
            <span className="text-blue-400">
              {pendingPts.length === 0 ? '单击放置第 1 点' : `单击第 ${pendingPts.length + 1} 点 · 右键取消`}
            </span>
          )}
          {activeTool === 'select' && <span className="text-gray-500">单击选中 · 右键删除 · Del 删除</span>}
          {isLoadingMore && (
            <span className="flex items-center gap-1 text-gray-600">
              <span className="w-3 h-3 border border-gray-500 border-t-transparent rounded-full animate-spin inline-block" />
              加载历史...
            </span>
          )}
        </div>
      </div>

      {/* 主图区域 */}
      <div ref={mainWrapRef} className="flex-[6] min-h-0 relative" onContextMenu={handleContextMenu}>
        {/* LW Charts 挂载点 */}
        <div ref={mainRef} className="absolute inset-0" />

        {/* SVG 仅用于渲染画线，pointer-events: none 不拦截任何事件 */}
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
            {renderDrawings()}
            {renderPreview()}
            {renderAnchors()}
          </g>
        </svg>

        {/* 右键上下文菜单 */}
        {ctxMenu && (
          <div className="absolute bg-gray-800 border border-gray-700 rounded shadow-xl z-50 py-1 text-xs min-w-[100px]"
            style={{ left: Math.min(ctxMenu.svgX + 4, svgW - 110), top: Math.min(ctxMenu.svgY + 4, svgH - 60) }}
            onMouseDown={e => e.stopPropagation()}>
            <button onClick={() => deleteDrawing(ctxMenu.drawingId)}
              className="w-full text-left px-3 py-1.5 text-red-400 hover:bg-red-900/40 transition-colors">
              删除画线
            </button>
            <button onClick={() => setCtxMenu(null)}
              className="w-full text-left px-3 py-1.5 text-gray-400 hover:bg-gray-700 transition-colors">
              取消
            </button>
          </div>
        )}
      </div>

      {/* RSI */}
      <div className="border-t border-gray-800 shrink-0 flex flex-col" style={{ height: 76 }}>
        <div className="px-2 text-[10px] text-gray-600 pt-0.5">RSI (14)</div>
        <div ref={rsiRef} className="flex-1" />
      </div>

      {/* MACD */}
      <div className="border-t border-gray-800 shrink-0 flex flex-col" style={{ height: 90 }}>
        <div className="px-2 text-[10px] text-gray-600 pt-0.5 flex gap-3">
          <span>MACD (12,26,9)</span>
          <span className="text-blue-400">— MACD</span>
          <span className="text-orange-400">— Signal</span>
        </div>
        <div ref={macdRef} className="flex-1" />
      </div>
    </div>
  )
}
