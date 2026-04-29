'use client'

import { useEffect, useRef, useCallback, useState } from 'react'
import type { IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts'
import type {
  DrawingToolType, DrawingAnchor,
  ChartDrawing, HorizontalLineDrawing,
  TrendLineDrawing, ChannelDrawing, FibRetracementDrawing,
} from '@/types'

// ── Constants ───────────────────────────────────────────────────────────────

export const DRAWING_TOOLS: { value: DrawingToolType; label: string; title: string }[] = [
  { value: 'select',    label: '↖',   title: '选择/删除画线'         },
  { value: 'horizontal',label: '─',   title: '水平线（1次点击）'      },
  { value: 'trendline', label: '╱',   title: '趋势线（2次点击）'      },
  { value: 'channel',   label: '⟋╲', title: '通道线（3次点击）'      },
  { value: 'fibonacci', label: 'Fib', title: '斐波那契回撤（2次点击）' },
]

export const PRESET_COLORS = [
  '#f59e0b', '#ef4444', '#22c55e', '#3b82f6',
  '#8b5cf6', '#ec4899', '#06b6d4', '#f8fafc',
]

const CLICKS_NEEDED: Record<string, number> = {
  horizontal: 1, trendline: 2, channel: 3, fibonacci: 2,
}

const FIB_LEVELS = [
  { pct: 0,     label: '0.0%',   weight: 1.5, dash: false, color: '#ef4444' },
  { pct: 0.236, label: '23.6%',  weight: 1,   dash: true,  color: '#f97316' },
  { pct: 0.382, label: '38.2%',  weight: 1,   dash: true,  color: '#eab308' },
  { pct: 0.5,   label: '50.0%',  weight: 1,   dash: true,  color: '#9ca3af' },
  { pct: 0.618, label: '61.8%',  weight: 1.5, dash: false, color: '#22c55e' },
  { pct: 0.786, label: '78.6%',  weight: 1,   dash: true,  color: '#3b82f6' },
  { pct: 1,     label: '100.0%', weight: 1.5, dash: false, color: '#ef4444' },
]

// ── Helpers ─────────────────────────────────────────────────────────────────

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

// ── Types ───────────────────────────────────────────────────────────────────

type CtxMenu = { svgX: number; svgY: number; drawingId: string }

interface ChartRefs {
  main:   IChartApi | null
  candle: ISeriesApi<'Candlestick'> | null
}

interface UseChartDrawingsParams {
  chartRefs: React.MutableRefObject<ChartRefs>
  mainWrapRef: React.RefObject<HTMLDivElement | null>
  svgSize: () => { w: number; h: number }
  isDark: boolean
}

// ── Hook ────────────────────────────────────────────────────────────────────

export function useChartDrawings({ chartRefs, mainWrapRef, svgSize, isDark }: UseChartDrawingsParams) {
  const [svgTick,    setSvgTick]    = useState(0)
  const [activeTool, setActiveTool] = useState<DrawingToolType>('none')
  const [activeColor,setActiveColor]= useState('#3b82f6')
  const [drawings,   setDrawings]   = useState<ChartDrawing[]>([])
  const [pendingPts, setPendingPts] = useState<DrawingAnchor[]>([])
  const [mousePos,   setMousePos]   = useState<{ x: number; y: number } | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hoveredId,  setHoveredId]  = useState<string | null>(null)
  const [ctxMenu,    setCtxMenu]    = useState<CtxMenu | null>(null)

  const clickHandlerRef = useRef<(x: number, y: number) => void>(() => {})
  const moveHandlerRef  = useRef<(x: number | null, y: number | null) => void>(() => {})

  // ── Coordinate conversion ───────────────────────────────────────────────

  const toXY = useCallback((anchor: DrawingAnchor): [number, number] | null => {
    const chart  = chartRefs.current.main
    const series = chartRefs.current.candle
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
  }, [chartRefs, svgSize])

  const fromXY = useCallback((localX: number, localY: number): DrawingAnchor | null => {
    const series = chartRefs.current.candle
    const chart  = chartRefs.current.main
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
  }, [chartRefs])

  const getDrawingAtPoint = useCallback((px: number, py: number): ChartDrawing | null => {
    const series = chartRefs.current.candle
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
  }, [drawings, toXY, svgSize, chartRefs])

  // ── Update handler refs each render ─────────────────────────────────────

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

  // ── Effects ─────────────────────────────────────────────────────────────

  useEffect(() => {
    setPendingPts([])
    setMousePos(null)
    setCtxMenu(null)
    if (activeTool !== 'select') setSelectedId(null)
    setHoveredId(null)
  }, [activeTool])

  useEffect(() => {
    const chart = chartRefs.current.main
    if (!chart) return
    const off = activeTool !== 'none'
    chart.applyOptions({ handleScroll: !off, handleScale: !off })
  }, [activeTool, chartRefs])

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

  // ── Event handlers ──────────────────────────────────────────────────────

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
  }, [pendingPts, getDrawingAtPoint, mainWrapRef])

  const deleteDrawing = useCallback((id: string) => {
    setDrawings(prev => prev.filter(d => d.id !== id))
    setSelectedId(null)
    setCtxMenu(null)
  }, [])

  const dismissCtxMenu = useCallback(() => setCtxMenu(null), [])

  const clearAllDrawings = useCallback(() => {
    setDrawings([])
    setPendingPts([])
    setSelectedId(null)
    setCtxMenu(null)
  }, [])

  // ── SVG render helpers ──────────────────────────────────────────────────

  function renderDrawings(): React.ReactNode {
    const { w, h } = svgSize()
    const series = chartRefs.current.candle
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
      const labelBg = isDark ? '#111827' : '#ffffff'
      return (
        <g key={d.id}>
          <line x1={0} y1={y} x2={w} y2={y} {...p} />
          <rect x={w - 68} y={y - 10} width={66} height={15} fill={labelBg} stroke={d.color} strokeWidth={0.5} rx={2} />
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

  function renderPreview(): React.ReactNode {
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

  function renderAnchors(): React.ReactNode {
    return (
      <>
        {pendingPts.map((pt, i) => {
          const c = toXY(pt)
          if (!c) return null
          return <circle key={i} cx={c[0]} cy={c[1]} r={4} fill="none" stroke={activeColor} strokeWidth={1.5} strokeOpacity={0.9} />
        })}
      </>
    )
  }

  const bumpSvgTick = useCallback(() => setSvgTick(k => k + 1), [])

  return {
    // state
    svgTick,
    activeTool, setActiveTool,
    activeColor, setActiveColor,
    drawings,
    pendingPts,
    selectedId,
    ctxMenu,
    isDrawingTool: activeTool !== 'none' && activeTool !== 'select',

    // refs for chart subscription
    clickHandlerRef,
    moveHandlerRef,
    bumpSvgTick,

    // event handlers
    handleContextMenu,
    deleteDrawing,
    dismissCtxMenu,
    clearAllDrawings,

    // SVG render helpers
    renderDrawings,
    renderPreview,
    renderAnchors,
  }
}
