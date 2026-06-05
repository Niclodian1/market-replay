'use client'

import { useEffect, useRef, useState, useMemo, useCallback } from 'react'
import {
  createChart,
  IChartApi,
  ISeriesApi,
  IPriceLine,
  ColorType,
  LineStyle,
  PriceScaleMode,
} from 'lightweight-charts'
import type { Candle, MaSeriesPoint, MaStrategy3xTrade, MaExitReason } from '@/types'
import { useAppStore } from '@/store/useAppStore'

const EXIT_COLOR: Record<MaExitReason, string> = {
  '止损': '#ef4444',
  '保本': '#f59e0b',
  '均线': '#3b82f6',
  '强制平仓': '#9ca3af',
}

const CONTEXT_BARS = 30  // 入场前展示的上下文K线数

type Speed = 1 | 2 | 5 | 10

interface Props {
  trade: MaStrategy3xTrade
  candles: Candle[]
  maLine: MaSeriesPoint[]
  trendMa: MaSeriesPoint[]
  maLabel: string
  onClose: () => void
}

export function TradeReplayPlayer({ trade, candles, maLine, trendMa, maLabel, onClose }: Props) {
  const containerRef  = useRef<HTMLDivElement>(null)
  const chartRef      = useRef<IChartApi | null>(null)
  const candleRef     = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const maRef         = useRef<ISeriesApi<'Line'> | null>(null)
  const trendRef      = useRef<ISeriesApi<'Line'> | null>(null)
  const costLineRef   = useRef<IPriceLine | null>(null)

  const [step,          setStep]         = useState(0)
  const [playing,       setPlaying]      = useState(false)
  const [speed,         setSpeed]        = useState<Speed>(1)
  const [isLog,         setIsLog]        = useState(false)
  const [hoveredCandle, setHoveredCandle] = useState<{ time: number; open: number; high: number; low: number; close: number; volume: number } | null>(null)

  const theme = useAppStore(s => s.theme)
  const isDark = theme !== 'light'

  // 计算交易涉及的K线子集（含前后上下文）
  const tradeCandles = useMemo(() => {
    const entryTs = trade.entry_time
    const exitTs  = trade.exit_time
    const entryIdx = candles.findIndex(c => c.time >= entryTs)
    const exitIdx  = candles.findIndex(c => c.time >= exitTs)
    if (entryIdx < 0) return []
    const start = Math.max(0, entryIdx - CONTEXT_BARS)
    const end   = exitIdx >= 0 ? Math.min(candles.length - 1, exitIdx + 5) : candles.length - 1
    return candles.slice(start, end + 1)
  }, [candles, trade.entry_time, trade.exit_time])

  // 当前步骤对应的K线时间
  const currentTime = tradeCandles[step]?.time ?? 0

  // 实时权益计算（基于已出现的事件）
  const currentEquity = useMemo(() => {
    if (!tradeCandles[step]) return 1.0
    const c = tradeCandles[step].close
    if (!c) return 1.0
    // 收集到当前时间点已入场的所有子仓
    const subs: { price: number; size: number }[] = []
    for (const ev of trade.events) {
      if (ev.time > currentTime) break
      if ((ev.type === 'entry' || ev.type === 'add') && ev.size != null) {
        subs.push({ price: ev.price, size: ev.size })
      }
    }
    if (subs.length === 0) return 1.0
    const pnl = subs.reduce((acc, sp) => acc + sp.size * (c / sp.price - 1), 0)
    return 1.0 + pnl
  }, [step, tradeCandles, currentTime, trade.events])

  // 当前权益百分比
  const equityPct = (currentEquity - 1) * 100

  // 盈亏平衡成本价（谐均值）
  // 推导：Σ[size_i*(P/entry_i - 1)] = 0  →  P = Σ(size_i) / Σ(size_i/entry_i)
  const costPrice = useMemo(() => {
    let sumSize = 0
    let sumSizeOverPrice = 0
    for (const ev of trade.events) {
      if (ev.time > currentTime) break
      if ((ev.type === 'entry' || ev.type === 'add') && ev.size != null && ev.price > 0) {
        sumSize += ev.size
        sumSizeOverPrice += ev.size / ev.price
      }
    }
    if (sumSize === 0 || sumSizeOverPrice === 0) return null
    return sumSize / sumSizeOverPrice
  }, [currentTime, trade.events])

  // 初始化图表
  useEffect(() => {
    if (!containerRef.current || tradeCandles.length === 0) return

    const bg   = isDark ? '#0f1318' : '#ffffff'
    const text = isDark ? '#7c8594' : '#6c7685'
    const grid = isDark ? '#1e242e' : '#e4e7ec'

    const chart = createChart(containerRef.current, {
      width:  containerRef.current.clientWidth,
      height: 360,
      layout: { background: { type: ColorType.Solid as const, color: bg }, textColor: text },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      rightPriceScale: { borderColor: grid, minimumWidth: 100 },
      timeScale: { borderColor: grid, timeVisible: false },
      crosshair: {
        mode: 0,
        vertLine: { width: 1 as const, color: isDark ? '#6b7280' : '#9ca3af', style: LineStyle.Dashed, labelVisible: true },
        horzLine: { width: 1 as const, color: isDark ? '#6b7280' : '#9ca3af', style: LineStyle.Dashed, labelVisible: true },
      },
      handleScroll: { mouseWheel: false, pressedMouseMove: true },
      handleScale: { mouseWheel: true, pinch: true },
    })
    chartRef.current = chart

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#22c55e', downColor: '#ef4444',
      borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    })
    candleRef.current = candleSeries

    const trendSeries = chart.addLineSeries({
      color: isDark ? '#6b7280' : '#9ca3af',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      title: '趋势 MA120',
    })
    trendRef.current = trendSeries

    const maSeries = chart.addLineSeries({
      color: '#60a5fa',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      title: maLabel,
    })
    maRef.current = maSeries

    // 设置趋势线和MA线（完整数据，不随动画变化）
    const times = new Set(tradeCandles.map(c => c.time))
    trendSeries.setData(trendMa.filter(p => times.has(p.time)).map(p => ({ time: p.time as any, value: p.value })))
    maSeries.setData(maLine.filter(p => times.has(p.time)).map(p => ({ time: p.time as any, value: p.value })))

    // Crosshair OHLC overlay
    const candleMap = new Map(tradeCandles.map(c => [c.time, c]))
    chart.subscribeCrosshairMove(param => {
      if (!param.time || !param.point) { setHoveredCandle(null); return }
      const c = candleMap.get(param.time as number)
      if (c) setHoveredCandle({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume })
      else setHoveredCandle(null)
    })

    const ro = new ResizeObserver(() => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.applyOptions({ width: containerRef.current.clientWidth })
      }
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current    = null
      candleRef.current   = null
      maRef.current       = null
      trendRef.current    = null
      costLineRef.current = null
      setHoveredCandle(null)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark, tradeCandles, maLabel])

  // 每次 step 变化：更新K线 + markers
  useEffect(() => {
    const series = candleRef.current
    if (!series || tradeCandles.length === 0) return

    const visible = tradeCandles.slice(0, step + 1)
    series.setData(visible.map(c => ({
      time:  c.time as any,
      open:  c.open,
      high:  c.high,
      low:   c.low,
      close: c.close,
    })))

    // 收集已达到时间点的事件标记
    const markers: any[] = []
    for (const ev of trade.events) {
      if (ev.time > currentTime) break
      if (ev.type === 'entry') {
        markers.push({
          time:     ev.time as any,
          position: 'belowBar',
          color:    '#22c55e',
          shape:    'arrowUp',
          text:     `入场 ${ev.size?.toFixed(1)}x`,
        })
      } else if (ev.type === 'add') {
        markers.push({
          time:     ev.time as any,
          position: 'belowBar',
          color:    '#eab308',
          shape:    'circle',
          text:     `+${ev.size?.toFixed(2)}`,
        })
      } else if (ev.type === 'exit') {
        const color = EXIT_COLOR[ev.reason ?? '均线'] ?? '#9ca3af'
        markers.push({
          time:     ev.time as any,
          position: 'aboveBar',
          color,
          shape:    'arrowDown',
          text:     ev.reason ?? '出场',
        })
      }
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number))
    series.setMarkers(markers)

    // 第一帧：自动缩放到可见范围
    if (step === 0 && chartRef.current) {
      chartRef.current.timeScale().fitContent()
    }

    // 更新成本虚线
    const fmt = (v: number) => v >= 1000 ? v.toFixed(2) : v >= 1 ? v.toFixed(4) : v.toPrecision(5)
    if (costPrice !== null) {
      if (!costLineRef.current) {
        costLineRef.current = series.createPriceLine({
          price:            costPrice,
          color:            '#f97316',
          lineWidth:        1 as const,
          lineStyle:        LineStyle.Dashed,
          axisLabelVisible: true,
          title:            `保本 ${fmt(costPrice)}`,
        })
      } else {
        costLineRef.current.applyOptions({
          price: costPrice,
          title: `保本 ${fmt(costPrice)}`,
        })
      }
    } else if (costLineRef.current) {
      series.removePriceLine(costLineRef.current)
      costLineRef.current = null
    }
  }, [step, tradeCandles, trade.events, currentTime, costPrice])

  // 对数/线性坐标切换
  useEffect(() => {
    chartRef.current?.priceScale('right').applyOptions({
      mode: isLog ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
    })
  }, [isLog])

  // ESC 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // 动画循环
  useEffect(() => {
    if (!playing) return
    const ms = Math.round(1000 / speed)
    const id = setInterval(() => {
      setStep(prev => {
        if (prev >= tradeCandles.length - 1) {
          setPlaying(false)
          return prev
        }
        return prev + 1
      })
    }, ms)
    return () => clearInterval(id)
  }, [playing, speed, tradeCandles.length])

  const handlePlayPause = useCallback(() => {
    if (step >= tradeCandles.length - 1) {
      setStep(0)
      setPlaying(true)
    } else {
      setPlaying(p => !p)
    }
  }, [step, tradeCandles.length])

  const fmtDate = (ts: number) => new Date(ts * 1000).toISOString().slice(0, 10)
  const fmtPct  = (v: number, digits = 2) => (v >= 0 ? '+' : '') + v.toFixed(digits) + '%'

  // 加仓次数统计（到当前时间）
  const addsShown = trade.events.filter(e => e.type === 'add' && e.time <= currentTime).length
  const totalAdds = trade.events.filter(e => e.type === 'add').length

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 2000,
        background: 'rgba(0,0,0,0.7)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="panel"
        style={{
          width: 'min(96vw, 900px)',
          maxHeight: '92vh',
          overflow: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 0,
        }}
      >
        {/* ── 标题栏 ────────────────────────────────────────────── */}
        <div
          className="panel-header"
          style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}
        >
          <span style={{ fontSize: 13, fontWeight: 700 }}>
            📽 交易回放  ·  {trade.leverage}x 杠杆  ·  {totalAdds} 次加仓
          </span>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-2)' }}>
              {fmtDate(trade.entry_time)} → {fmtDate(trade.exit_time)}
            </span>
            <span
              style={{
                fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700,
                color: equityPct >= 0 ? 'var(--up)' : 'var(--down)',
              }}
            >
              {fmtPct(equityPct)}
            </span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)' }}>
              最终: {fmtPct(trade.pnl_pct)}  出场: {trade.exit_reason}
            </span>
            <button className="btn small ghost" onClick={onClose} title="关闭 (ESC)">✕</button>
          </div>
        </div>

        {/* ── 图表区 ────────────────────────────────────────────── */}
        <div style={{ position: 'relative' }}>
          <div ref={containerRef} style={{ width: '100%', minHeight: 360 }} />
          {hoveredCandle && (() => {
            const { time, open, high, low, close, volume } = hoveredCandle
            const isUp = close >= open
            const bodyPct = Math.abs(close - open) / open * 100
            const fmt = (v: number) => v >= 1000
              ? v.toLocaleString('en-US', { maximumFractionDigits: 2 })
              : v >= 1 ? v.toFixed(4) : v.toPrecision(5).replace(/\.?0+$/, '')
            const date = new Date(time * 1000).toISOString().slice(0, 10)
            return (
              <div style={{
                position: 'absolute', top: 8, left: 10, zIndex: 10,
                display: 'flex', flexWrap: 'wrap', gap: '4px 12px', alignItems: 'center',
                fontFamily: 'var(--font-mono)', fontSize: 11,
                background: isDark ? 'rgba(15,19,24,0.88)' : 'rgba(255,255,255,0.88)',
                border: `1px solid ${isDark ? '#2a3240' : '#e4e7ec'}`,
                borderRadius: 6, padding: '4px 10px',
                pointerEvents: 'none',
              }}>
                <span style={{ color: 'var(--fg-3)' }}>{date}</span>
                <span style={{ color: 'var(--fg-3)' }}>开 <span style={{ color: 'var(--fg-1)' }}>{fmt(open)}</span></span>
                <span style={{ color: 'var(--fg-3)' }}>高 <span style={{ color: '#22c55e' }}>{fmt(high)}</span></span>
                <span style={{ color: 'var(--fg-3)' }}>低 <span style={{ color: '#ef4444' }}>{fmt(low)}</span></span>
                <span style={{ color: 'var(--fg-3)' }}>收 <span style={{ color: isUp ? '#22c55e' : '#ef4444', fontWeight: 600 }}>{fmt(close)}</span></span>
                <span style={{ color: 'var(--fg-3)' }}>实体 <span style={{ color: isUp ? '#22c55e' : '#ef4444' }}>{isUp ? '+' : '-'}{bodyPct.toFixed(2)}%</span></span>
                {volume > 0 && <span style={{ color: 'var(--fg-3)' }}>量 <span style={{ color: 'var(--fg-1)' }}>{volume >= 1e6 ? (volume / 1e6).toFixed(2) + 'M' : volume >= 1e3 ? (volume / 1e3).toFixed(1) + 'K' : volume.toFixed(0)}</span></span>}
                {costPrice !== null && (
                  <span style={{ color: 'var(--fg-3)' }}>
                    持仓浮盈 <span style={{ color: equityPct >= 0 ? '#22c55e' : '#ef4444', fontWeight: 600 }}>
                      {equityPct >= 0 ? '+' : ''}{equityPct.toFixed(2)}%
                    </span>
                  </span>
                )}
              </div>
            )
          })()}
        </div>

        {/* ── 进度条 ─────────────────────────────────────────── */}
        <div style={{ padding: '4px 12px 0' }}>
          <input
            type="range"
            min={0}
            max={Math.max(0, tradeCandles.length - 1)}
            value={step}
            onChange={e => {
              setPlaying(false)
              setStep(Number(e.target.value))
            }}
            style={{ width: '100%', accentColor: '#60a5fa', cursor: 'pointer' }}
          />
        </div>

        {/* ── 控件栏 ────────────────────────────────────────────── */}
        <div style={{ padding: '8px 12px 12px', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          {/* 播放控制 */}
          <button
            className="btn small ghost"
            disabled={step === 0}
            onClick={() => { setPlaying(false); setStep(0) }}
            title="跳到开头"
          >|◀</button>
          <button
            className="btn small ghost"
            disabled={step === 0}
            onClick={() => { setPlaying(false); setStep(s => Math.max(0, s - 1)) }}
            title="上一根K线"
          >◀</button>
          <button
            className="btn small accent"
            onClick={handlePlayPause}
            style={{ minWidth: 64 }}
          >
            {playing ? '⏸ 暂停' : step >= tradeCandles.length - 1 ? '↺ 重放' : '▶ 播放'}
          </button>
          <button
            className="btn small ghost"
            disabled={step >= tradeCandles.length - 1}
            onClick={() => { setPlaying(false); setStep(s => Math.min(tradeCandles.length - 1, s + 1)) }}
            title="下一根K线"
          >▶</button>
          <button
            className="btn small ghost"
            disabled={step >= tradeCandles.length - 1}
            onClick={() => { setPlaying(false); setStep(tradeCandles.length - 1) }}
            title="跳到末尾"
          >▶|</button>

          {/* 速度 */}
          <div style={{ display: 'flex', gap: 3, marginLeft: 4, padding: 2, background: 'var(--bg-2)', borderRadius: 6 }}>
            {([1, 2, 5, 10] as Speed[]).map(s => (
              <button
                key={s}
                className={speed === s ? 'btn small accent' : 'btn small ghost'}
                onClick={() => setSpeed(s)}
                style={{ minWidth: 32, padding: '3px 6px' }}
              >{s}x</button>
            ))}
          </div>

          {/* 对数坐标 */}
          <button
            className={isLog ? 'btn small accent' : 'btn small ghost'}
            onClick={() => setIsLog(l => !l)}
            title="对数坐标"
            style={{ marginLeft: 4 }}
          >LOG</button>

          {/* 状态信息 */}
          <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-2)' }}>
            K线 {step + 1}/{tradeCandles.length}
            {addsShown > 0 && (
              <span style={{ marginLeft: 8, color: '#eab308' }}>已加仓 {addsShown}/{totalAdds} 次</span>
            )}
          </span>
        </div>

        {/* ── 图例 ──────────────────────────────────────────────── */}
        <div style={{ padding: '0 12px 12px', display: 'flex', gap: 16, fontSize: 11, color: 'var(--fg-2)', flexWrap: 'wrap' }}>
          <LegendItem color="#22c55e" shape="▲" label="入场" />
          <LegendItem color="#eab308" shape="●" label="浮盈加仓" />
          <LegendItem color={EXIT_COLOR['止损']}    shape="▼" label="止损出场" />
          <LegendItem color={EXIT_COLOR['保本']}    shape="▼" label="保本出场" />
          <LegendItem color={EXIT_COLOR['均线']}    shape="▼" label="跌破均线" />
          <LegendItem color={EXIT_COLOR['强制平仓']} shape="▼" label="强制平仓" />
          <LegendItem color="#f97316" shape="- -" label="保本价（调和均价）" />
        </div>
      </div>
    </div>
  )
}

function LegendItem({ color, shape, label }: { color: string; shape: string; label: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <span style={{ color, fontSize: 12, lineHeight: 1 }}>{shape}</span>
      {label}
    </span>
  )
}
