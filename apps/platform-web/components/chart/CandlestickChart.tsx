'use client'

import { useEffect, useLayoutEffect, useRef, useCallback, useMemo, useState } from 'react'
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
import type { Candle, ChanlunLayerKey, ChanlunLevel, ChanlunPoint, Indicators, ImpulseColor } from '@/types'
import { useAppStore } from '@/store/useAppStore'
import { useChartDrawings, DRAWING_TOOLS, PRESET_COLORS } from '@/hooks/useChartDrawings'
import { useChartMeasure } from '@/hooks/useChartMeasure'
import {
  buildBuySellMarkers,
  buildBeichiMarkers,
  buildChanlunLayerData,
  matchChanlunPoints,
  pointColor,
  createChanlunOverlay,
  type ChanlunOverlay,
  type ChanlunTradeLink,
} from './chanlunOverlay'
import { calculateCandleMovement } from '@/lib/chartMeasurement'
import { computeChanlunStats, pairTrades, summarizeTrades } from '@/lib/chanlunStats'
import { scanChanlunStrategies } from '@/lib/chanlunStrategy'

// ── 主题常量 ─────────────────────────────────────────────────────────────────

const PRICE_SCALE_WIDTH = 68
const DARK_THEME = {
  layout: { background: { type: ColorType.Solid, color: '#111827' }, textColor: '#9ca3af' },
  grid:   { vertLines: { color: '#1f293760' }, horzLines: { color: '#1f293760' } },
  rightPriceScale: { borderColor: '#374151', minimumWidth: PRICE_SCALE_WIDTH },
  crosshair: { mode: CrosshairMode.Normal },
}
const LIGHT_THEME = {
  layout: { background: { type: ColorType.Solid, color: '#ffffff' }, textColor: '#374151' },
  grid:   { vertLines: { color: '#f3f4f6' }, horzLines: { color: '#f3f4f6' } },
  rightPriceScale: { borderColor: '#e5e7eb', minimumWidth: PRICE_SCALE_WIDTH },
  crosshair: { mode: CrosshairMode.Normal },
}
const DARK_TS_SHOW  = { borderColor: '#374151', timeVisible: true, secondsVisible: false }
const DARK_TS_HIDE  = { borderColor: '#374151', visible: false as const }
const LIGHT_TS_SHOW = { borderColor: '#e5e7eb', timeVisible: true, secondsVisible: false }
const LIGHT_TS_HIDE = { borderColor: '#e5e7eb', visible: false as const }

const MA_COLORS = ['#60a5fa', '#f97316', '#34d399', '#a78bfa', '#f43f5e', '#facc15']

// ── 继论图层开关 ──────────────────────────────────────────────────────────────

const CHANLUN_TOGGLES: { key: ChanlunLayerKey; label: string; title: string }[] = [
  { key: 'stroke', label: '笔', title: '显示笔' },
  { key: 'segment', label: '线段', title: '显示线段' },
  { key: 'pivot', label: '中枢', title: '显示中枢' },
  { key: 'point', label: '买卖点', title: '显示一/二/三类买卖点' },
  { key: 'beichi', label: '背驰', title: '显示背驰买卖点（抢先层）' },
]

const CHANLUN_LAYERS_INIT: Record<ChanlunLayerKey, boolean> = {
  stroke: false,
  segment: false,
  pivot: false,
  point: false,
  beichi: false,
}

function candleAtTime(candles: Candle[], time: number): Candle | null {
  let lo = 0
  let hi = candles.length - 1
  while (lo <= hi) {
    const mid = (lo + hi) >>> 1
    if (candles[mid].time === time) return candles[mid]
    if (candles[mid].time < time) lo = mid + 1
    else hi = mid - 1
  }
  return null
}

function formatCursorPrice(value: number): string {
  const abs = Math.abs(value)
  if (abs >= 10000) return value.toLocaleString(undefined, { maximumFractionDigits: 2 })
  if (abs >= 100) return value.toFixed(2)
  if (abs >= 1) return value.toFixed(4)
  return value.toFixed(6)
}

function formatCursorTime(timestamp: number): string {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(timestamp * 1000))
}

// ── Types ────────────────────────────────────────────────────────────────────

type SeriesStore = {
  main:   IChartApi | null
  rsi:    IChartApi | null
  macd:   IChartApi | null
  time:   IChartApi | null
  candle: ISeriesApi<'Candlestick'> | null
  volume: ISeriesApi<'Histogram'> | null
  rsiL:   ISeriesApi<'Line'> | null
  hist:   ISeriesApi<'Histogram'> | null
  macdL:  ISeriesApi<'Line'> | null
  sigL:   ISeriesApi<'Line'> | null
  timeS:  ISeriesApi<'Line'> | null
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
  /** 继论分析结果（由父级按需请求） */
  chanlun?: ChanlunLevel | null
  chanlunLoading?: boolean
  /** 存在时显示「继论」图层按钮；任一图层首次开启且无数据时触发 */
  onChanlunRequest?: () => void
}

// ── 主组件 ────────────────────────────────────────────────────────────────────

export function CandlestickChart({
  candles, indicators, logScale,
  maPeriods = [20, 60], onMaPeriodsChange,
  isLoadingMore, onScrollToLeft,
  replayMode, fitTrigger, impulseColors,
  chanlun, chanlunLoading, onChanlunRequest,
}: Props) {
  const theme = useAppStore(s => s.theme)
  const isDark = theme === 'dark'

  // DOM refs
  const mainWrapRef = useRef<HTMLDivElement>(null)
  const mainRef     = useRef<HTMLDivElement>(null)
  const rsiRef      = useRef<HTMLDivElement>(null)
  const macdRef     = useRef<HTMLDivElement>(null)
  const timeRef     = useRef<HTMLDivElement>(null)

  // MA series refs
  const maSeriesRef    = useRef<ISeriesApi<'Line'>[]>([])
  const indicatorsRef  = useRef(indicators)
  const maPeriodsRef   = useRef(maPeriods)
  const candlesRef     = useRef(candles)
  useEffect(() => { indicatorsRef.current = indicators }, [indicators])
  useEffect(() => { maPeriodsRef.current  = maPeriods  }, [maPeriods])
  useEffect(() => { candlesRef.current = candles }, [candles])

  // MA period local editing state
  const [localPeriods, setLocalPeriods] = useState<number[]>(maPeriods)
  const [cursorCandle, setCursorCandle] = useState<Candle | null>(null)
  // 继论买卖点悬浮提示（复用已验证的 crosshair 链路）
  const [chanlunTip, setChanlunTip] = useState<{ x: number; y: number; points: ChanlunPoint[] } | null>(null)

  // 继论图层开关（笔/线段/中枢/买卖点）
  const [chanlunLayers, setChanlunLayers] = useState<Record<ChanlunLayerKey, boolean>>(CHANLUN_LAYERS_INIT)
  const chanlunOverlayRef = useRef<ChanlunOverlay | null>(null)
  const chanlunRef = useRef<ChanlunLevel | null | undefined>(chanlun)
  const chanlunLayersRef = useRef(chanlunLayers)
  useEffect(() => { chanlunRef.current = chanlun }, [chanlun])
  useEffect(() => { chanlunLayersRef.current = chanlunLayers }, [chanlunLayers])

  // 买卖点信号统计（仅按确认点执行、买→卖配对、跌破止损离场）
  // 组合交易逐笔核对：选中的 买点类型 × 卖点类型
  const BUY_KIND_OPTS = ['一买', '二买', '三买', '背驰买']
  const SELL_KIND_OPTS = ['一卖', '二卖', '三卖', '背驰卖']
  const [comboBuy, setComboBuy] = useState<string>('一买')
  const [comboSell, setComboSell] = useState<string>('一卖')
  const [showTradeLines, setShowTradeLines] = useState(false)
  const [activeTradeId, setActiveTradeId] = useState<number | null>(null)

  const selectedTrades: ChanlunTradeLink[] = useMemo(
    () =>
      pairTrades(
        candles,
        [...(chanlun?.buy_sell_points ?? []), ...(chanlun?.beichi_points ?? [])],
        p => p.k === comboBuy,
        p => p.k === comboSell,
      ).trades.map((t, i) => ({
        id: i,
        entryT: t.entryT,
        entryPrice: t.entryPrice,
        exitT: t.exitT,
        exitPrice: t.exitPrice,
        stop: t.stop,
        exitReason: t.exitReason,
        pnlPct: t.pnlPct,
        entryKind: t.entryKind,
        exitKind: t.exitKind,
      })),
    [candles, chanlun, comboBuy, comboSell],
  )
  const comboSummary = useMemo(() => summarizeTrades(selectedTrades), [selectedTrades])

  // 选中组合的连线渲染到 overlay；activeTradeId 高亮
  useEffect(() => {
    chanlunOverlayRef.current?.setTrades(
      showTradeLines && selectedTrades.length
        ? selectedTrades.map(t => ({ ...t, active: t.id === activeTradeId }))
        : null,
    )
  }, [showTradeLines, selectedTrades, activeTradeId])

  const focusTrade = (t: { entryT: number; exitT: number }) => {
    const main = s.current.main
    if (!main) return
    const span = Math.max(3600, t.exitT - t.entryT)
    main.timeScale().setVisibleRange({
      from: (t.entryT - span * 0.5) as UTCTimestamp,
      to: (t.exitT + span * 0.5) as UTCTimestamp,
    })
  }

  const chanlunStats = useMemo(
    () =>
      computeChanlunStats(candles, [
        ...(chanlun?.buy_sell_points ?? []),
        ...(chanlun?.beichi_points ?? []),
      ]),
    [candles, chanlun],
  )
  // 买卖信号组合扫描：找收益较稳定的「买点→卖点」组合
  const chanlunStrategies = useMemo(
    () =>
      scanChanlunStrategies(candles, [
        ...(chanlun?.buy_sell_points ?? []),
        ...(chanlun?.beichi_points ?? []),
      ]),
    [candles, chanlun],
  )
  // Dynamic spacer width – kept in sync with the main chart's actual price scale width
  const [spacerWidth, setSpacerWidth] = useState(PRICE_SCALE_WIDTH)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setLocalPeriods([...maPeriods]) }, [maPeriods.join(',')])

  const s = useRef<SeriesStore>({
    main: null, rsi: null, macd: null, time: null,
    candle: null, volume: null, rsiL: null, hist: null, macdL: null, sigL: null, timeS: null,
  })

  const fittedRef      = useRef(false)
  const prevFitTrigger = useRef(fitTrigger)
  const dataRangeRef   = useRef<{ first: number; last: number } | null>(null)
  const isApplyingDataRef = useRef(false)
  const onScrollRef    = useRef(onScrollToLeft)
  const scaleSyncRafRef = useRef<number | null>(null)
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

  const syncRightPriceScaleWidths = useCallback(() => {
    if (typeof window === 'undefined') return
    if (scaleSyncRafRef.current !== null) window.cancelAnimationFrame(scaleSyncRafRef.current)

    scaleSyncRafRef.current = window.requestAnimationFrame(() => {
      scaleSyncRafRef.current = null

      const charts = [s.current.main, s.current.rsi, s.current.macd, s.current.time].filter(Boolean) as IChartApi[]
      if (!charts.length) return

      const width = Math.max(PRICE_SCALE_WIDTH, ...charts.map(chart => chart.priceScale('right').width()))
      charts.forEach(chart => {
        chart.applyOptions({
          rightPriceScale: {
            minimumWidth: width,
          },
        })
      })
      // Keep CSS spacers in sync so sub-chart content areas match the main chart
      setSpacerWidth(width)

      const mainScaleOptions = s.current.main?.timeScale().options()
      const mainRange = s.current.main?.timeScale().getVisibleLogicalRange()
      if (mainScaleOptions && s.current.rsi && s.current.macd && s.current.time) {
        s.current.rsi.applyOptions({ timeScale: { barSpacing: mainScaleOptions.barSpacing, rightOffset: mainScaleOptions.rightOffset } })
        s.current.macd.applyOptions({ timeScale: { barSpacing: mainScaleOptions.barSpacing, rightOffset: mainScaleOptions.rightOffset } })
        s.current.time.applyOptions({ timeScale: { barSpacing: mainScaleOptions.barSpacing, rightOffset: mainScaleOptions.rightOffset } })
      }
      if (mainRange && s.current.rsi && s.current.macd && s.current.time) {
        s.current.rsi.timeScale().setVisibleLogicalRange(mainRange)
        s.current.macd.timeScale().setVisibleLogicalRange(mainRange)
        s.current.time.timeScale().setVisibleLogicalRange(mainRange)
      }
    })
  }, [])

  // Resize sub-charts whenever the CSS spacer width changes so their content
  // area stays aligned with the main chart's content area.
  useLayoutEffect(() => {
    const { rsi, macd, time } = s.current
    if (rsiRef.current  && rsi)  rsi.applyOptions({ width: rsiRef.current.clientWidth })
    if (macdRef.current && macd) macd.applyOptions({ width: macdRef.current.clientWidth })
    if (timeRef.current && time) time.applyOptions({ width: timeRef.current.clientWidth })
  }, [spacerWidth])

  // ── Drawing hook ────────────────────────────────────────────────────────
  const draw = useChartDrawings({ chartRefs, mainWrapRef, svgSize, isDark })
  const measure = useChartMeasure({ chartRefs, mainWrapRef, candles, isDark })

  // ── 图表初始化（只运行一次） ──────────────────────────────────────────────
  useEffect(() => {
    if (!mainRef.current || !rsiRef.current || !macdRef.current || !timeRef.current) return

    const chartTheme = isDark ? DARK_THEME : LIGHT_THEME
    const tsShow = isDark ? DARK_TS_SHOW : LIGHT_TS_SHOW
    const tsHide = isDark ? DARK_TS_HIDE : LIGHT_TS_HIDE
    const make = (el: HTMLDivElement, showTS: boolean) =>
      createChart(el, { ...chartTheme, width: el.clientWidth, height: el.clientHeight, timeScale: showTS ? tsShow : tsHide })

    const main  = make(mainRef.current, false)
    const rsiC  = make(rsiRef.current,  false)
    const macdC = make(macdRef.current, false)
    const timeC = make(timeRef.current, true)
    rsiC.applyOptions({
      rightPriceScale: { visible: false },
      handleScroll: { mouseWheel: false, pressedMouseMove: false, horzTouchDrag: false, vertTouchDrag: false },
      handleScale: { axisPressedMouseMove: { time: false, price: true }, axisDoubleClickReset: { time: false, price: true }, mouseWheel: false, pinch: false },
    })
    macdC.applyOptions({
      rightPriceScale: { visible: false },
      handleScroll: { mouseWheel: false, pressedMouseMove: false, horzTouchDrag: false, vertTouchDrag: false },
      handleScale: { axisPressedMouseMove: { time: false, price: true }, axisDoubleClickReset: { time: false, price: true }, mouseWheel: false, pinch: false },
    })
    timeC.applyOptions({
      leftPriceScale: { visible: false },
      rightPriceScale: { visible: false },
      handleScroll: { mouseWheel: false, pressedMouseMove: false, horzTouchDrag: false, vertTouchDrag: false },
      handleScale: { axisPressedMouseMove: { time: false, price: false }, axisDoubleClickReset: { time: false, price: false }, mouseWheel: false, pinch: false },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
    })

    const candle = main.addCandlestickSeries({
      upColor: '#22c55e', downColor: '#ef4444',
      borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e',   wickDownColor: '#ef4444',
    })

    // 继论 overlay（canvas 绘制笔/线段/中枢，随缩放自动重绘）
    chanlunOverlayRef.current = createChanlunOverlay(candle, main)
    chanlunOverlayRef.current.setDark(isDark)
    chanlunOverlayRef.current.setData(buildChanlunLayerData(chanlunRef.current, chanlunLayers))
    const volumeS = main.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' } })
    main.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 }, visible: false })
    const rsiL = rsiC.addLineSeries({ color: '#a78bfa', lineWidth: 1, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false })
    rsiL.createPriceLine({ price: 70, color: '#ef4444', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '70' })
    rsiL.createPriceLine({ price: 50, color: '#6b7280', lineWidth: 1, lineStyle: 2, axisLabelVisible: false })
    rsiL.createPriceLine({ price: 30, color: '#22c55e', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '30' })
    const histS = macdC.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false })
    const macdL = macdC.addLineSeries({ color: '#60a5fa', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
    const sigL  = macdC.addLineSeries({ color: '#f97316', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })

    // 时间轴 chart 的隐形占位 series：仅用于承载十字光标，使底部时间轴能跟随鼠标显示日期
    const timeS = timeC.addLineSeries({
      color: 'rgba(0,0,0,0)', lineWidth: 1,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    })

    s.current = { main, rsi: rsiC, macd: macdC, time: timeC, candle, volume: volumeS, rsiL, hist: histS, macdL, sigL, timeS }
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

    // 同步时间轴。使用防重入广播，避免三个独立 chart 互相回写造成逻辑区间漂移。
    const allCharts: IChartApi[] = [main, rsiC, macdC, timeC]
    let syncingRange = false
    main.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range || syncingRange) return
      syncingRange = true
      const mainScaleOptions = main.timeScale().options()
      rsiC.applyOptions({ timeScale: { barSpacing: mainScaleOptions.barSpacing, rightOffset: mainScaleOptions.rightOffset } })
      macdC.applyOptions({ timeScale: { barSpacing: mainScaleOptions.barSpacing, rightOffset: mainScaleOptions.rightOffset } })
      timeC.applyOptions({ timeScale: { barSpacing: mainScaleOptions.barSpacing, rightOffset: mainScaleOptions.rightOffset } })
      rsiC.timeScale().setVisibleLogicalRange(range)
      macdC.timeScale().setVisibleLogicalRange(range)
      timeC.timeScale().setVisibleLogicalRange(range)
      syncingRange = false
      if (!isApplyingDataRef.current && range.from <= 5) onScrollRef.current?.()
      draw.bumpSvgTick()
    })

    main.subscribeClick((params) => {
      if (!params.point) return
      draw.clickHandlerRef.current(params.point.x, params.point.y)
    })

    // 同步十字光标（跨三个面板）
    let syncingCrosshair = false
    const t2 = (ts: number) => ts as UTCTimestamp
    const bsearch = <T extends { time: number }>(arr: T[], target: number, getValue: (item: T) => number): number | undefined => {
      let lo = 0, hi = arr.length - 1
      while (lo <= hi) {
        const mid = (lo + hi) >>> 1
        if (arr[mid].time === target) return getValue(arr[mid])
        if (arr[mid].time < target) lo = mid + 1
        else hi = mid - 1
      }
      if (lo >= arr.length) lo = arr.length - 1
      return arr[lo] ? getValue(arr[lo]) : undefined
    }
    allCharts.forEach(chart => {
      chart.subscribeCrosshairMove((params) => {
        if (!params.point || !params.time) {
          if (!syncingCrosshair) allCharts.forEach(c => c.clearCrosshairPosition())
          if (chart === main) {
            setCursorCandle(null)
            setChanlunTip(null)
          }
          draw.moveHandlerRef.current(null, null)
          return
        }
        const time = params.time as number
        const currentCandle = candleAtTime(candlesRef.current, time)
        if (chart === main) {
          setCursorCandle(currentCandle)
          // 继论买卖点 tooltip：±1bar 容差匹配（markers 文字画在 bar 上方偏右，
          // 悬停会吸附到相邻 bar，需容差就近命中；受图层开关控制）
          const tl = chanlunRef.current
          const layers = chanlunLayersRef.current
          const pts: ChanlunPoint[] = []
          if (tl && layers.point) pts.push(...tl.buy_sell_points)
          if (tl && layers.beichi) pts.push(...(tl.beichi_points ?? []))
          const matched = matchChanlunPoints(pts, time, tl?.timeframe)
          setChanlunTip(
            matched.length
              ? { x: params.point.x, y: params.point.y, points: matched }
              : null,
          )
          draw.moveHandlerRef.current(params.point.x, params.point.y)
        }
        if (syncingCrosshair) return
        syncingCrosshair = true
        const { rsi, macd } = indicatorsRef.current
        if (s.current.rsiL && rsi.length) {
          const v = bsearch(rsi, time, d => d.value)
          if (v !== undefined) s.current.rsi!.setCrosshairPosition(v, t2(time), s.current.rsiL)
        }
        if (s.current.macdL && macd.length) {
          const v = bsearch(macd, time, d => d.macd)
          if (v !== undefined) s.current.macd!.setCrosshairPosition(v, t2(time), s.current.macdL)
        }
        if (chart !== main && s.current.candle) {
          if (currentCandle) s.current.main!.setCrosshairPosition(currentCandle.close, t2(time), s.current.candle)
        }
        // 让底部时间轴 chart 也定位十字光标 → 时间轴标签随鼠标显示日期
        if (chart !== timeC && s.current.timeS) {
          s.current.time!.setCrosshairPosition(0, t2(time), s.current.timeS)
        }
        syncingCrosshair = false
      })
    })

    // Resize
    const ro = new ResizeObserver(() => {
      const pairs: Array<[{ current: HTMLDivElement | null }, IChartApi]> = [
        [mainRef, main], [rsiRef, rsiC], [macdRef, macdC], [timeRef, timeC],
      ]
      pairs.forEach(([ref, chart]) => {
        if (ref.current) chart.applyOptions({ width: ref.current.clientWidth, height: ref.current.clientHeight })
      })
      syncRightPriceScaleWidths()
      draw.bumpSvgTick()
    })
    if (mainWrapRef.current?.parentElement) ro.observe(mainWrapRef.current.parentElement)
    syncRightPriceScaleWidths()

    return () => {
      ro.disconnect()
      if (scaleSyncRafRef.current !== null) window.cancelAnimationFrame(scaleSyncRafRef.current)
      fittedRef.current = false
      dataRangeRef.current = null
      isApplyingDataRef.current = false
      maSeriesRef.current = []
      chanlunOverlayRef.current?.destroy()
      chanlunOverlayRef.current = null
      allCharts.forEach(c => c.remove())
      s.current = { main: null, rsi: null, macd: null, time: null, candle: null, volume: null, rsiL: null, hist: null, macdL: null, sigL: null, timeS: null }
      chartRefs.current = { main: null, candle: null }
      setCursorCandle(null)
    }
  }, [syncRightPriceScaleWidths]) // eslint-disable-line react-hooks/exhaustive-deps

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
    const { candle, volume, rsiL, hist, macdL, sigL, main, timeS } = s.current
    if (!candle || !candles.length) return
    const previousVisibleRange = main?.timeScale().getVisibleRange() ?? null
    const previousDataRange = dataRangeRef.current
    const nextDataRange = { first: candles[0].time, last: candles[candles.length - 1].time }
    const prependedHistory = Boolean(
      previousDataRange
      && nextDataRange.first < previousDataRange.first
      && nextDataRange.last === previousDataRange.last
    )
    const t = (ts: number) => ts as UTCTimestamp
    const buildWhitespaceAlignedLine = (points: Array<{ time: number; value: number }>) => {
      const byTime = new Map(points.map(p => [p.time, p.value]))
      return candles.map(c => {
        const value = byTime.get(c.time)
        return value === undefined ? { time: t(c.time) } : { time: t(c.time), value }
      })
    }
    const buildWhitespaceAlignedMacd = <K extends 'histogram' | 'macd' | 'signal'>(key: K) => {
      const byTime = new Map(indicators.macd.map(p => [p.time, p[key]]))
      return candles.map(c => {
        const value = byTime.get(c.time)
        return value === undefined ? { time: t(c.time) } : { time: t(c.time), value }
      })
    }

    isApplyingDataRef.current = true
    try {
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
      timeS?.setData(candles.map(c => ({ time: t(c.time), value: 0 })))
      rsiL?.setData(buildWhitespaceAlignedLine(indicators.rsi))
      hist?.setData(candles.map(c => {
        const point = indicators.macd.find(p => p.time === c.time)
        return point
          ? { time: t(c.time), value: point.histogram, color: point.histogram >= 0 ? '#22c55e66' : '#ef444466' }
          : { time: t(c.time) }
      }))
      macdL?.setData(buildWhitespaceAlignedMacd('macd'))
      sigL?.setData(buildWhitespaceAlignedMacd('signal'))

      const triggerChanged = fitTrigger !== prevFitTrigger.current
      prevFitTrigger.current = fitTrigger
      if (!fittedRef.current || triggerChanged) {
        main?.timeScale().fitContent()
        fittedRef.current = true
      } else if (!replayMode && prependedHistory && previousVisibleRange) {
        // 后端分页返回更早的 K 线时，只扩充数据集，不改变用户当前看到的时间区间。
        main?.timeScale().setVisibleRange(previousVisibleRange)
      }
      dataRangeRef.current = nextDataRange
    } finally {
      isApplyingDataRef.current = false
    }
    syncRightPriceScaleWidths()
    draw.bumpSvgTick()
  }, [candles, indicators, replayMode, fitTrigger, impulseColors, syncRightPriceScaleWidths]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    s.current.main?.priceScale('right').applyOptions({
      mode: logScale ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
    })
  }, [logScale])

  // ── 继论：任一图层首次开启且尚无数据时，通知父级按需请求分析 ─────────
  const anyChanlunLayerOn = Object.values(chanlunLayers).some(Boolean)
  useEffect(() => {
    if (anyChanlunLayerOn && !chanlunRef.current && !chanlunLoading && onChanlunRequest) {
      onChanlunRequest()
    }
  }, [anyChanlunLayerOn, chanlun, chanlunLoading, onChanlunRequest])

  // ── 继论：数据/图层/主题变化时刷新 canvas overlay ─────────────────────
  useEffect(() => {
    const overlay = chanlunOverlayRef.current
    if (!overlay) return
    overlay.setDark(isDark)
    overlay.setData(buildChanlunLayerData(chanlun, chanlunLayers))
  }, [chanlun, chanlunLayers, isDark])

  // ── 继论：买卖点 + 背驰 markers（独立开关，合并排序后画） ────────────
  useEffect(() => {
    const { candle } = s.current
    if (!candle) return
    const wantPoint = chanlunLayers.point
    const wantBeichi = chanlunLayers.beichi
    if ((!wantPoint && !wantBeichi) || !chanlun) {
      if (!wantPoint && !wantBeichi) candle.setMarkers([] as never)
      return
    }
    const markers = [
      ...(wantPoint ? buildBuySellMarkers(chanlun, isDark) : []),
      ...(wantBeichi ? buildBeichiMarkers(chanlun, isDark) : []),
    ].sort(
      (a, b) => a.time - b.time,
    )
    candle.setMarkers(markers as never)
  }, [chanlun, chanlunLayers.point, chanlunLayers.beichi, isDark])

  useEffect(() => {
    const chartTheme = isDark ? DARK_THEME : LIGHT_THEME
    const tsShow = isDark ? DARK_TS_SHOW : LIGHT_TS_SHOW
    const tsHide = isDark ? DARK_TS_HIDE : LIGHT_TS_HIDE
    const { main, rsi, macd, time } = s.current
    if (!main || !rsi || !macd || !time) return
    main.applyOptions({ ...chartTheme, timeScale: tsHide })
    rsi.applyOptions({ ...chartTheme, timeScale: tsHide })
    macd.applyOptions({ ...chartTheme, timeScale: tsHide })
    time.applyOptions({ ...chartTheme, timeScale: tsShow })
    syncRightPriceScaleWidths()
  }, [isDark, syncRightPriceScaleWidths])

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

        {/* 继论图层按钮（父级传入 onChanlunRequest 时才显示） */}
        {onChanlunRequest && (
          <>
            <div className="w-px h-4 bg-gray-200 dark:bg-gray-800 shrink-0" />
            <span className="shrink-0 text-[10px] text-gray-400 dark:text-gray-600">缠论</span>
            <div className="flex items-center gap-0.5 shrink-0">
              {CHANLUN_TOGGLES.map(tool => (
                <button key={tool.key} title={tool.title}
                  onClick={() => setChanlunLayers(prev => ({ ...prev, [tool.key]: !prev[tool.key] }))}
                  className={clsx('px-1.5 py-0.5 rounded text-xs transition-colors',
                    chanlunLayers[tool.key]
                      ? 'bg-blue-600 text-white'
                      : 'text-gray-500 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800'
                  )}>
                  {tool.label}
                </button>
              ))}
              {chanlunLoading && (
                <span className="w-3 h-3 border border-gray-500 border-t-transparent rounded-full animate-spin inline-block" />
              )}
            </div>
          </>
        )}

        {/* 工具按钮 */}
        <div className="flex items-center gap-0.5 shrink-0">
          {DRAWING_TOOLS.map(tool => (
            <button key={tool.value} title={tool.title}
              onClick={() => {
                measure.setToolActive(false)
                draw.setActiveTool(t => t === tool.value ? 'none' : tool.value)
              }}
              className={clsx('px-1.5 py-0.5 rounded text-xs transition-colors',
                draw.activeTool === tool.value ? 'bg-blue-600 text-white' : 'text-gray-500 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800'
              )}>
              {tool.label}
            </button>
          ))}
          <button
            title="测量涨跌幅：启用后拖动，或随时按住 Shift 拖动"
            onClick={() => {
              draw.setActiveTool('none')
              measure.setToolActive(!measure.isToolActive)
            }}
            className={clsx(
              'px-1.5 py-0.5 rounded text-xs transition-colors',
              measure.isToolActive
                ? 'bg-blue-600 text-white'
                : 'text-gray-500 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800',
            )}
          >
            测量
          </button>
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
          {!draw.isDrawingTool && draw.activeTool !== 'select' && (
            <span className={measure.isPending || measure.isArmed ? 'text-blue-400' : 'text-gray-400 dark:text-gray-600'}>
              {measure.isPending
                ? '测量中 · 单击或拖到终点 · Esc 取消'
                : measure.isToolActive
                  ? '测量工具已启用 · 拖动选取区间'
                  : measure.isArmed ? '测量已启用 · 按住鼠标拖到终点' : '点击“测量”或 Shift + 拖动'}
            </span>
          )}
          {isLoadingMore && (
            <span className="flex items-center gap-1 text-gray-600">
              <span className="w-3 h-3 border border-gray-500 border-t-transparent rounded-full animate-spin inline-block" />
              加载历史...
            </span>
          )}
        </div>
      </div>

      {/* 主图区域 */}
      <div
        ref={mainWrapRef}
        className="flex-[6] min-h-0 relative"
        style={{ cursor: measure.isPending || measure.isArmed ? 'crosshair' : undefined }}
        data-measure-state={measure.measurement?.status ?? 'idle'}
        onContextMenu={draw.handleContextMenu}
      >
        <div ref={mainRef} className="absolute inset-0" />

        {cursorCandle && (() => {
          const movement = calculateCandleMovement(
            cursorCandle.open,
            cursorCandle.high,
            cursorCandle.low,
            cursorCandle.close,
          )
          const directionColor = movement.isUp ? '#22c55e' : '#ef4444'
          const bodySign = movement.bodyPercent >= 0 ? '+' : ''
          return (
            <div
              className="absolute left-2 top-2 z-10 pointer-events-none rounded border border-gray-200/80 dark:border-gray-700/80 bg-white/90 dark:bg-gray-900/90 shadow-sm backdrop-blur-sm px-2.5 py-1.5"
              aria-label="光标所在K线信息"
            >
              <div className="flex items-center gap-x-3 gap-y-1 flex-wrap font-mono text-[10px] leading-4">
                <span className="text-gray-400 dark:text-gray-500">{formatCursorTime(cursorCandle.time)}</span>
                <span><span className="text-gray-400 dark:text-gray-500">开 </span>{formatCursorPrice(cursorCandle.open)}</span>
                <span><span className="text-gray-400 dark:text-gray-500">高 </span>{formatCursorPrice(cursorCandle.high)}</span>
                <span><span className="text-gray-400 dark:text-gray-500">低 </span>{formatCursorPrice(cursorCandle.low)}</span>
                <span><span className="text-gray-400 dark:text-gray-500">收 </span>{formatCursorPrice(cursorCandle.close)}</span>
                <span style={{ color: directionColor }} title="(收盘价 - 开盘价) / 开盘价">
                  实体 {bodySign}{movement.bodyPercent.toFixed(2)}%
                </span>
                <span className="text-blue-500 dark:text-blue-400" title="(最高价 - 最低价) / 开盘价">
                  最大波幅 {movement.rangePercent.toFixed(2)}%
                </span>
              </div>
            </div>
          )
        })()}

        {/* 继论买卖点交易统计 + 组合收益（右上角） */}
        {(chanlunStats && (chanlunStats.total > 0 || chanlunStats.openTrades.length > 0)) && (
          <div className="absolute right-2 top-2 z-10 pointer-events-none flex flex-col items-end gap-2">
            <div
              className="rounded border border-gray-200/80 dark:border-gray-700/80 bg-white/90 dark:bg-gray-900/90 shadow-sm backdrop-blur-sm px-2.5 py-1.5"
              aria-label="缠论买卖点统计"
            >
              <div className="text-[10px] font-semibold text-gray-500 dark:text-gray-400 mb-1">
                买卖点统计{(chanlun?.timeframe ? ` · ${chanlun.timeframe}` : '')}（确认K收盘 · 买→卖）
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 font-mono text-[10px] leading-4 whitespace-nowrap">
                <span className="text-gray-500">交易次数 <b className="text-foreground">{chanlunStats.total}</b></span>
                <span className="text-gray-500">胜率 <b style={{ color: chanlunStats.winRate > 0 ? '#22c55e' : '#ef4444' }}>{(chanlunStats.winRate * 100).toFixed(0)}%</b></span>
                <span className="text-gray-500">盈利 <b className="text-emerald-500">{chanlunStats.wins}</b> / 亏损 <b className="text-red-500">{chanlunStats.losses}</b></span>
                <span className="text-gray-500">盈亏比 <b className="text-foreground">{chanlunStats.profitFactor.toFixed(2)}</b></span>
                <span className="text-gray-500">平均收益 <b style={{ color: chanlunStats.avgPnlPct >= 0 ? '#22c55e' : '#ef4444' }}>{chanlunStats.avgPnlPct >= 0 ? '+' : ''}{chanlunStats.avgPnlPct.toFixed(2)}%</b></span>
                <span className="text-gray-500">均盈 {chanlunStats.avgWinPct.toFixed(2)}% / 均亏 {chanlunStats.avgLossPct.toFixed(2)}%</span>
                {chanlunStats.openTrades.length > 0 && (
                  <span className="text-amber-500">持有中 ×{chanlunStats.openTrades.length}</span>
                )}
              </div>
            </div>

            {/* 组合收益：按期望收益排序，展示官方样例 + 收益较稳的组合 */}
            {chanlunStrategies.length > 0 && (
              <div
                className="rounded border border-gray-200/80 dark:border-gray-700/80 bg-white/90 dark:bg-gray-900/90 shadow-sm backdrop-blur-sm px-2.5 py-1.5 max-w-[300px]"
                aria-label="缠论买卖信号组合收益"
              >
                <div className="text-[10px] font-semibold text-gray-500 dark:text-gray-400 mb-1">
                  组合扫描（确认K成交，小样本仅供对照）
                </div>
                <div className="font-mono text-[10px] leading-4">
                  <div className="flex gap-2 border-b border-gray-200/60 dark:border-gray-700/60 pb-0.5 mb-0.5 text-gray-500 whitespace-nowrap">
                    <span className="min-w-[80px]">买→卖</span>
                    <span className="min-w-[26px] text-right">笔</span>
                    <span className="min-w-[30px] text-right">胜率</span>
                    <span className="min-w-[34px] text-right">盈亏比</span>
                    <span className="min-w-[46px] text-right">均收益</span>
                  </div>
                  {chanlunStrategies.slice(0, 8).map(r => (
                    <div key={r.id} className="flex gap-2 whitespace-nowrap">
                      <span className="min-w-[80px] truncate" title={`${r.wins}胜/${r.losses}负${r.trades < 8 ? ' · 小样本' : ''}`}>
                        {r.label}{r.trades < 8 ? ' ·小' : ''}
                      </span>
                      <span className="min-w-[26px] text-right text-gray-500">{r.trades}</span>
                      <span className="min-w-[30px] text-right" style={{ color: r.winRate >= 0.5 ? '#22c55e' : '#ef4444' }}>{(r.winRate * 100).toFixed(0)}%</span>
                      <span className="min-w-[34px] text-right">{r.profitFactor.toFixed(1)}</span>
                      <span className="min-w-[46px] text-right" style={{ color: r.avgPnlPct >= 0 ? '#22c55e' : '#ef4444' }}>{r.avgPnlPct >= 0 ? '+' : ''}{r.avgPnlPct.toFixed(2)}%</span>
                    </div>
                  ))}
                </div>
                <div className="text-[9px] text-gray-400 mt-1">样本≥3 · &lt;8 笔勿当实盘 · 止损=买点 low 收盘</div>
              </div>
            )}

            {/* 组合交易逐笔核对：选买/卖类型，图上连线，明细可点选 */}
            <div
              className="rounded border border-gray-200/80 dark:border-gray-700/80 bg-white/90 dark:bg-gray-900/90 shadow-sm backdrop-blur-sm px-2.5 py-1.5 max-w-[420px]"
              aria-label="缠论组合交易核对"
            >
              <div className="text-[10px] font-semibold text-gray-500 dark:text-gray-400 mb-1">组合交易核对 · {comboBuy}→{comboSell}</div>
              <div className="flex flex-wrap gap-1 mb-1.5">
                {BUY_KIND_OPTS.map(k => (
                  <button
                    key={k}
                    onClick={() => { setComboBuy(k); setActiveTradeId(null) }}
                    className={`pointer-events-auto text-[10px] px-1.5 py-0.5 rounded border ${
                      comboBuy === k
                        ? 'border-emerald-500 text-emerald-600 dark:text-emerald-400 bg-emerald-500/10'
                        : 'border-gray-300 dark:border-gray-700 text-gray-500 dark:text-gray-400'
                    }`}
                  >买入 {k}</button>
                ))}
              </div>
              <div className="flex flex-wrap gap-1 mb-1.5">
                {SELL_KIND_OPTS.map(k => (
                  <button
                    key={k}
                    onClick={() => { setComboSell(k); setActiveTradeId(null) }}
                    className={`pointer-events-auto text-[10px] px-1.5 py-0.5 rounded border ${
                      comboSell === k
                        ? 'border-red-500 text-red-500 dark:text-red-400 bg-red-500/10'
                        : 'border-gray-300 dark:border-gray-700 text-gray-500 dark:text-gray-400'
                    }`}
                  >卖出 {k}</button>
                ))}
              </div>
              <div className="grid grid-cols-4 gap-x-2 font-mono text-[10px] mb-1.5 whitespace-nowrap">
                <span className="text-gray-500">笔数 <b>{selectedTrades.length}</b></span>
                <span className="text-gray-500">胜率 <b style={{ color: comboSummary.winRate >= 0.5 ? '#22c55e' : '#ef4444' }}>{(comboSummary.winRate * 100).toFixed(0)}%</b></span>
                <span className="text-gray-500">盈亏比 <b>{comboSummary.profitFactor.toFixed(1)}</b></span>
                <span className="text-gray-500">期望 <b style={{ color: comboSummary.avgPnlPct >= 0 ? '#22c55e' : '#ef4444' }}>{comboSummary.avgPnlPct >= 0 ? '+' : ''}{comboSummary.avgPnlPct.toFixed(2)}%</b></span>
              </div>
              <label className="flex items-center gap-1.5 text-[10px] text-gray-500 mb-1.5 pointer-events-auto cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={showTradeLines}
                  onChange={e => setShowTradeLines(e.target.checked)}
                  className="accent-emerald-500"
                />
                显示交易连线（绿点=入场 · 方点=出场 · 虚线=该笔）
              </label>
              {selectedTrades.length > 0 && (
                <div className="max-h-36 overflow-y-auto pointer-events-auto border-t border-gray-200/60 dark:border-gray-700/60 pt-1">
                  {selectedTrades.map(t => (
                    <button
                      key={t.id}
                      onClick={() => {
                        setActiveTradeId(t.id)
                        setShowTradeLines(true)
                        focusTrade(t)
                      }}
                      className={`flex w-full gap-1.5 text-left font-mono text-[10px] leading-4 whitespace-nowrap px-1 rounded ${
                        activeTradeId === t.id
                          ? 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
                          : 'hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-600 dark:text-gray-300'
                      }`}
                    >
                      <span className="text-gray-400">{t.id + 1}</span>
                      <span className="text-emerald-600 dark:text-emerald-400">{t.entryKind ?? comboBuy}</span>
                      <span>{formatCursorTime(t.entryT)}</span>
                      <span className="text-gray-400">@{formatCursorPrice(t.entryPrice)}</span>
                      <span className="text-gray-400">→</span>
                      <span>{formatCursorTime(t.exitT)}</span>
                      <span className="text-gray-400">@{formatCursorPrice(t.exitPrice)}</span>
                      <span className={t.exitReason === 'sell' ? 'text-red-500/80' : 'text-gray-400'}>
                        {t.exitReason === 'stop' ? 'stop' : t.exitReason === 'macd' ? 'macd' : (t.exitKind ?? '卖')}
                      </span>
                      <span className={t.pnlPct >= 0 ? 'text-emerald-500' : 'text-red-500'}>{t.pnlPct >= 0 ? '+' : ''}{t.pnlPct.toFixed(2)}%</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* 继论买卖点 tooltip：贴近鼠标，展示极值 / 可交易确认时间 */}
        {chanlunTip && (
          <div
            className="absolute z-20 pointer-events-none rounded border px-2.5 py-1.5 shadow-sm"
            style={{
              left: chanlunTip.x + 14,
              top: chanlunTip.y + 12,
              background: isDark ? 'rgba(17,24,39,0.94)' : 'rgba(255,255,255,0.96)',
              color: isDark ? '#e5e7eb' : '#1f2937',
              borderColor: 'rgba(148,163,184,0.35)',
              fontSize: 11,
              lineHeight: 1.55,
              maxWidth: 340,
            }}
            aria-label="缠论买卖点信息"
          >
            {chanlunTip.points.map((p, i) => {
              const modeLabel = p.mode === 'seg' ? '线段' : p.mode === 'stroke' ? '笔' : '背驰'
              const c = pointColor(p, isDark)
              return (
                <div
                  key={`${p.t}-${p.k}-${i}`}
                  className={i < chanlunTip.points.length - 1 ? 'mb-1.5' : ''}
                  style={{ paddingLeft: 6, borderLeft: `3px solid ${c}` }}
                >
                  <div className="flex items-center gap-1.5 font-semibold">
                    <span>{p.k}</span>
                    {p.tier && <span style={{ color: '#d97706', fontSize: 10, background: 'rgba(245,158,11,.16)', padding: '0 4px', borderRadius: 3 }}>{p.tier}</span>}
                    <span style={{ color: '#94a3b8', fontSize: 10, background: 'rgba(148,163,184,.16)', padding: '0 4px', borderRadius: 3 }}>{modeLabel}</span>
                  </div>
                  <div><span className="text-gray-500">产生 </span>{formatCursorTime(p.t)}</div>
                  <div>
                    <span className="text-gray-500">确认 </span>
                    {p.state === 'forming'
                      ? <span style={{ color: '#eab308' }}>未确认（forming）</span>
                      : (p.confirmedT != null ? formatCursorTime(p.confirmedT) : '—')}
                  </div>
                  <div><span className="text-gray-500">价格 </span>{formatCursorPrice(p.p)}</div>
                  {p.mode === 'fast' && (() => {
                    const extra: string[] = []
                    if (p.ratio != null) extra.push(`比率 ${p.ratio}`)
                    if (p.trigger) extra.push(`触发 ${p.trigger}`)
                    return (
                      <div className="text-gray-500">
                        {extra.join(' · ')}
                        {p.alertT != null && <span>{extra.length ? ' · ' : ''}告警 {formatCursorTime(p.alertT)}</span>}
                      </div>
                    )
                  })()}
                  {p.why && <div className="text-gray-500 mt-0.5" style={{ whiteSpace: 'normal' }}>{p.why}</div>}
                </div>
              )
            })}
          </div>
        )}

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
            {measure.renderMeasurement()}
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
        <div className="flex-1 flex">
          <div ref={rsiRef} className="flex-1 min-w-0" />
          <div className="shrink-0 border-l border-gray-100 dark:border-gray-800" style={{ width: spacerWidth }} />
        </div>
      </div>

      {/* MACD */}
      <div className="border-t border-gray-100 dark:border-gray-800 shrink-0 flex flex-col" style={{ height: 90 }}>
        <div className="px-2 text-[10px] text-gray-400 dark:text-gray-600 pt-0.5 flex gap-3">
          <span>MACD (12,26,9)</span>
          <span className="text-blue-400">— MACD</span>
          <span className="text-orange-400">— Signal</span>
        </div>
        <div className="flex-1 flex">
          <div ref={macdRef} className="flex-1 min-w-0" />
          <div className="shrink-0 border-l border-gray-100 dark:border-gray-800" style={{ width: spacerWidth }} />
        </div>
      </div>

      <div className="border-t border-gray-100 dark:border-gray-800 shrink-0" style={{ height: 28 }}>
        <div className="flex h-full">
          <div ref={timeRef} className="flex-1 min-w-0" />
          <div className="shrink-0 border-l border-gray-100 dark:border-gray-800" style={{ width: spacerWidth }} />
        </div>
      </div>
    </div>
  )
}
