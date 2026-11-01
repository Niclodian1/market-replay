'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { runMtfBacktest, getBacktestDataRange } from '@/lib/api'
import type { MtfBacktestResult, MtfBacktestTrade, EquityPoint, EntrySignalId, BacktestDataRange, Candle } from '@/types'
import { SymbolCombobox } from '@/components/SymbolCombobox'
import { createChart, IChartApi, ColorType } from 'lightweight-charts'
import { useAppStore } from '@/store/useAppStore'
import { calculateIndicators } from '@/lib/indicators'

function fmt(v: number, digits = 2) { return v.toFixed(digits) }
function fmtDate(ts: number) {
  return new Date(ts * 1000).toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
}

// ─── 入场信号元信息 ─────────────────────────────────────────────────────────

const ENTRY_SIGNAL_META: Record<EntrySignalId, { label: string; desc: string; params: { key: string; label: string; min: number; step: number; default: number }[] }> = {
  ma_breakout: {
    label: '均线突破',
    desc: '收盘价突破/跌破 SMA',
    params: [{ key: 'ma_breakout_period', label: 'MA 周期', min: 5, step: 1, default: 30 }],
  },
  macd_divergence: {
    label: 'MACD 背离',
    desc: '价格与 MACD 柱体方向背离',
    params: [{ key: 'macd_divergence_lookback', label: '峰谷窗口', min: 2, step: 1, default: 5 }],
  },
  sr_breakout: {
    label: '支撑阻力突破',
    desc: '突破关键支撑/阻力价位',
    params: [
      { key: 'sr_lookback', label: '极值窗口', min: 2, step: 1, default: 5 },
      { key: 'sr_min_touches', label: '最少触及', min: 2, step: 1, default: 2 },
    ],
  },
  pinbar: {
    label: 'Pinbar 锤子线',
    desc: '影线波动 ≥ 2% 的锤子线/射击之星',
    params: [],
  },
  engulfing: {
    label: '吞没形态',
    desc: '前K跌/涨幅 ≥ 2%，后K实体收回',
    params: [],
  },
  w_bottom: {
    label: 'W底双底',
    desc: '相隔≥60K的两低点支撑位接近，中间反弹≥5%',
    params: [],
  },
}

const ALL_ENTRY_SIGNALS: EntrySignalId[] = ['ma_breakout', 'macd_divergence', 'sr_breakout', 'pinbar', 'engulfing', 'w_bottom']

const TF_LABELS: Record<string, string> = { '1week': '周线', '1day': '日线', '4h': '4H' }

const SIGNAL_NAME_MAP: Record<string, string> = {
  ma_breakout: '均线突破',
  macd_divergence: 'MACD背离',
  sr_breakout: '支撑阻力',
  pinbar: 'Pinbar',
  engulfing: '吞没',
  w_bottom: 'W底',
}

// ─── 权益曲线图 ──────────────────────────────────────────────────────────────

function EquityChart({ data, buyHoldReturn, initialCapital }: { data: EquityPoint[]; buyHoldReturn: number; initialCapital: number }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const theme = useAppStore(s => s.theme)
  const isDark = theme !== 'light'

  useEffect(() => {
    if (!containerRef.current || data.length === 0) return
    chartRef.current = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 240,
      layout: {
        background: { type: ColorType.Solid, color: isDark ? '#0f1318' : '#ffffff' },
        textColor: isDark ? '#7c8594' : '#6c7685',
      },
      grid: {
        vertLines: { color: isDark ? '#1e242e' : '#e4e7ec' },
        horzLines: { color: isDark ? '#1e242e' : '#e4e7ec' },
      },
      rightPriceScale: { borderColor: isDark ? '#1e242e' : '#e4e7ec', minimumWidth: 68 },
      timeScale: { borderColor: isDark ? '#1e242e' : '#e4e7ec', timeVisible: true },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true },
    })
    const seriesRef = chartRef.current.addLineSeries({ color: '#ffb020', lineWidth: 2, title: '策略' })
    const bhSeriesRef = chartRef.current.addLineSeries({ color: '#7c8594', lineWidth: 1, lineStyle: 2, title: '买持' })
    seriesRef.setData(data.map(d => ({ time: d.time as any, value: d.value })))
    if (data.length > 0) {
      bhSeriesRef.setData([
        { time: data[0].time as any, value: initialCapital },
        { time: data[data.length - 1].time as any, value: initialCapital * (1 + buyHoldReturn / 100) },
      ])
    }
    chartRef.current.timeScale().fitContent()
    const ro = new ResizeObserver(() => {
      if (containerRef.current && chartRef.current) chartRef.current.applyOptions({ width: containerRef.current.clientWidth })
    })
    ro.observe(containerRef.current)
    return () => { ro.disconnect(); chartRef.current?.remove() }
  }, [data, isDark, buyHoldReturn, initialCapital])

  return <div ref={containerRef} style={{ width: '100%' }} />
}

// ─── 交易标记图 ─────────────────────────────────────────────────────────────

function BacktestCandleChart({ candles, trades, entrySignals, entryParams }: {
  candles: Candle[]
  trades: MtfBacktestTrade[]
  entrySignals: string[]
  entryParams: Record<string, number>
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const macdContainerRef = useRef<HTMLDivElement>(null)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const macdChartRef = useRef<IChartApi | null>(null)
  const theme = useAppStore(s => s.theme)
  const isDark = theme !== 'light'
  const [isFullscreen, setIsFullscreen] = useState(false)

  const showMa = entrySignals.includes('ma_breakout')
  const showMacd = entrySignals.includes('macd_divergence')

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isFullscreen) setIsFullscreen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isFullscreen])

  useEffect(() => {
    if (!containerRef.current || candles.length === 0) return

    const chartHeight = isFullscreen ? window.innerHeight - (showMacd ? 200 : 60) : 420

    const chartOpts = {
      layout: {
        background: { type: ColorType.Solid as const, color: isDark ? '#0f1318' : '#ffffff' },
        textColor: isDark ? '#7c8594' : '#6c7685',
      },
      grid: {
        vertLines: { color: isDark ? '#1e242e' : '#e4e7ec' },
        horzLines: { color: isDark ? '#1e242e' : '#e4e7ec' },
      },
      rightPriceScale: { borderColor: isDark ? '#1e242e' : '#e4e7ec', minimumWidth: 68 },
      timeScale: { borderColor: isDark ? '#1e242e' : '#e4e7ec', timeVisible: true },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true },
    }

    chartRef.current = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: chartHeight,
      ...chartOpts,
    })

    const candleSeries = chartRef.current.addCandlestickSeries({
      upColor: '#22c55e', downColor: '#ef4444',
      borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    })

    const volumeSeries = chartRef.current.addHistogramSeries({
      priceScaleId: 'vol', priceFormat: { type: 'volume' },
    })
    chartRef.current.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 }, visible: false })

    candleSeries.setData(candles.map(c => ({
      time: c.time as any,
      open: c.open, high: c.high, low: c.low, close: c.close,
    })))

    volumeSeries.setData(candles.map(c => ({
      time: c.time as any,
      value: c.volume,
      color: c.close >= c.open ? '#22c55e33' : '#ef444433',
    })))

    // ── 指标叠加 ──
    const maPeriod = entryParams['ma_breakout_period'] ?? 30
    const indicators = (showMa || showMacd) ? calculateIndicators(candles, showMa ? [maPeriod] : []) : null

    if (showMa && indicators && indicators.ma_lines.length > 0) {
      const maSeries = chartRef.current.addLineSeries({
        color: '#60a5fa',
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        title: `SMA ${maPeriod}`,
      })
      maSeries.setData(indicators.ma_lines[0].data.map(p => ({ time: p.time as any, value: p.value })))
    }

    // ── 交易标记 ──
    const markers: Array<{
      time: any
      position: 'aboveBar' | 'belowBar'
      color: string
      shape: 'arrowUp' | 'arrowDown' | 'circle'
      text: string
    }> = []

    for (const t of trades) {
      const isLong = t.direction === 'long'
      markers.push({
        time: t.entry_time as any,
        position: isLong ? 'belowBar' : 'aboveBar',
        color: isLong ? '#22c55e' : '#ef4444',
        shape: isLong ? 'arrowUp' : 'arrowDown',
        text: (t.signal_names ?? []).map(s => SIGNAL_NAME_MAP[s] ?? s).join('+'),
      })
      markers.push({
        time: t.exit_time as any,
        position: t.pnl_pct >= 0 ? 'aboveBar' : 'belowBar',
        color: t.pnl_pct >= 0 ? '#22c55e' : '#ef4444',
        shape: 'circle',
        text: `${t.pnl_pct >= 0 ? '+' : ''}${fmt(t.pnl_pct)}%`,
      })
    }

    markers.sort((a, b) => (a.time as number) - (b.time as number))
    candleSeries.setMarkers(markers)

    // ── MACD 子图 ──
    if (showMacd && macdContainerRef.current && indicators) {
      const macdData = indicators.macd

      macdChartRef.current = createChart(macdContainerRef.current, {
        width: macdContainerRef.current.clientWidth,
        height: 120,
        ...chartOpts,
      })

      const histS = macdChartRef.current.addHistogramSeries({
        priceLineVisible: false, lastValueVisible: false,
      })
      const macdL = macdChartRef.current.addLineSeries({
        color: '#60a5fa', lineWidth: 1, priceLineVisible: false,
        lastValueVisible: false, crosshairMarkerVisible: false,
      })
      const sigL = macdChartRef.current.addLineSeries({
        color: '#f97316', lineWidth: 1, priceLineVisible: false,
        lastValueVisible: false, crosshairMarkerVisible: false,
      })

      histS.setData(macdData.map(d => ({
        time: d.time as any,
        value: d.histogram,
        color: d.histogram >= 0 ? '#22c55e80' : '#ef444480',
      })))
      macdL.setData(macdData.map(d => ({ time: d.time as any, value: d.macd })))
      sigL.setData(macdData.map(d => ({ time: d.time as any, value: d.signal })))

      macdChartRef.current.timeScale().fitContent()

      let syncing = false
      chartRef.current.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (syncing || !range || !macdChartRef.current) return
        syncing = true
        macdChartRef.current.timeScale().setVisibleLogicalRange(range)
        syncing = false
      })
      macdChartRef.current.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (syncing || !range || !chartRef.current) return
        syncing = true
        chartRef.current.timeScale().setVisibleLogicalRange(range)
        syncing = false
      })
    }

    chartRef.current.timeScale().fitContent()

    const ro = new ResizeObserver(() => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.applyOptions({ width: containerRef.current.clientWidth })
      }
      if (macdContainerRef.current && macdChartRef.current) {
        macdChartRef.current.applyOptions({ width: macdContainerRef.current.clientWidth })
      }
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      chartRef.current?.remove()
      macdChartRef.current?.remove()
    }
  }, [candles, trades, isDark, entrySignals, entryParams, showMa, showMacd, isFullscreen])

  const chart = (
    <div ref={wrapperRef}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: isFullscreen ? '8px 12px' : '0 0 4px 0' }}>
        {isFullscreen && <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-1)' }}>交易标记图</span>}
        <div style={{ marginLeft: 'auto' }}>
          <button className="btn small ghost" onClick={() => setIsFullscreen(v => !v)} title={isFullscreen ? '退出全屏 (Esc)' : '全屏'}>
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
      </div>
      <div ref={containerRef} style={{ width: '100%' }} />
      {showMacd && <div ref={macdContainerRef} style={{ width: '100%' }} />}
    </div>
  )

  return isFullscreen ? (
    <div style={{ position: 'fixed', inset: 0, zIndex: 50, background: 'var(--bg-0)', display: 'flex', flexDirection: 'column' }}>
      {chart}
    </div>
  ) : chart
}

// ─── 交易明细 (MTF) ──────────────────────────────────────────────────────────

function MtfTradeList({ trades }: { trades: MtfBacktestTrade[] }) {
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? trades : trades.slice(0, 10)
  const cols = '36px 1fr 1fr 1fr 1fr 60px 70px 82px 70px 70px 50px 80px 1fr'

  const EXIT_REASON_MAP: Record<string, string> = {
    reverse_signal: '反向信号',
    stop_loss: '止损',
    breakeven: '保本',
    entry_candle_low: 'K线低点',
    end_of_data: '强制平仓',
  }

  return (
    <div className="panel" style={{ overflow: 'hidden' }}>
      <div className="panel-header">
        <span>交易明细</span>
        <span className="mono" style={{ textTransform: 'none', letterSpacing: 0 }}>{trades.length} 笔</span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <div style={{ display: 'grid', gridTemplateColumns: cols, padding: '8px 12px', borderBottom: '1px solid var(--line-2)', fontSize: 10, letterSpacing: '.14em', textTransform: 'uppercase', color: 'var(--fg-2)', fontWeight: 600, minWidth: 1040 }}>
          <span>#</span><span>入场日期</span><span>入场价</span><span>出场日期</span><span>出场价</span>
          <span style={{ textAlign: 'right' }}>天数</span>
          <span style={{ textAlign: 'right' }}>盈亏%</span>
          <span style={{ textAlign: 'right' }}>实际盈亏</span>
          <span style={{ textAlign: 'right' }}>浮盈%</span>
          <span style={{ textAlign: 'right' }}>浮亏%</span>
          <span style={{ textAlign: 'center' }}>权重</span>
          <span>出场原因</span>
          <span>触发信号</span>
        </div>
        {visible.map((t, i) => (
          <div key={i} style={{ display: 'grid', gridTemplateColumns: cols, padding: '8px 12px', borderBottom: '1px solid var(--line-1)', fontFamily: 'var(--font-mono)', fontSize: 11.5, minWidth: 1040, alignItems: 'center' }}
            onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-2)')}
            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
          >
            <span style={{ color: 'var(--fg-3)' }}>#{i + 1}</span>
            <span style={{ color: 'var(--fg-1)' }}>{fmtDate(t.entry_time)}</span>
            <span style={{ color: 'var(--fg-0)' }}>{t.entry_price.toLocaleString()}</span>
            <span style={{ color: 'var(--fg-1)' }}>{fmtDate(t.exit_time)}</span>
            <span style={{ color: 'var(--fg-0)' }}>{t.exit_price.toLocaleString()}</span>
            <span style={{ textAlign: 'right', color: 'var(--fg-2)' }}>{Math.round((t.exit_time - t.entry_time) / 86400)}d</span>
            <span style={{ textAlign: 'right', fontWeight: 600, color: t.pnl_pct >= 0 ? 'var(--up)' : 'var(--down)' }}>
              {t.pnl_pct >= 0 ? '+' : ''}{fmt(t.pnl_pct)}%
            </span>
            <span style={{ textAlign: 'right', fontWeight: 600, color: (t.pnl_amount ?? 0) >= 0 ? 'var(--up)' : 'var(--down)' }}>
              {t.pnl_amount == null ? '-' : `${t.pnl_amount >= 0 ? '+' : ''}${fmt(t.pnl_amount)}`}
            </span>
            <span style={{ textAlign: 'right', color: 'var(--up)' }}>+{fmt(t.max_profit_pct)}%</span>
            <span style={{ textAlign: 'right', color: 'var(--down)' }}>{fmt(t.max_loss_pct)}%</span>
            <span style={{ textAlign: 'center', color: t.signal_weight >= 2 ? 'var(--accent)' : 'var(--fg-2)', fontWeight: t.signal_weight >= 2 ? 700 : 400 }}>
              {t.signal_weight}
            </span>
            <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 3, background: t.exit_reason === 'stop_loss' || t.exit_reason === 'entry_candle_low' ? 'rgba(239,68,68,0.15)' : 'var(--bg-3)', color: t.exit_reason === 'stop_loss' || t.exit_reason === 'entry_candle_low' ? 'var(--down)' : 'var(--fg-2)', display: 'inline-block', width: 'fit-content' }}>
              {EXIT_REASON_MAP[t.exit_reason ?? ''] ?? t.exit_reason ?? '-'}
            </span>
            <span style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
              {(t.signal_names ?? []).map(s => (
                <span key={s} style={{ fontSize: 9, padding: '1px 5px', borderRadius: 3, background: 'var(--accent-dim)', color: 'var(--accent)' }}>
                  {SIGNAL_NAME_MAP[s] ?? s}
                </span>
              ))}
            </span>
          </div>
        ))}
      </div>
      {trades.length > 10 && (
        <button onClick={() => setExpanded(!expanded)} className="btn ghost"
          style={{ width: '100%', justifyContent: 'center', borderRadius: 0, borderTop: '1px solid var(--line-1)' }}>
          {expanded ? '收起' : `查看全部 ${trades.length} 笔交易`}
        </button>
      )}
    </div>
  )
}

// ─── 信号分布统计 ────────────────────────────────────────────────────────────

function SignalDistribution({ trades }: { trades: MtfBacktestTrade[] }) {
  const counts: Record<string, number> = {}
  for (const t of trades) {
    for (const s of t.signal_names ?? []) {
      counts[s] = (counts[s] || 0) + 1
    }
  }
  const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1
  const weightDist = [0, 0, 0, 0]
  for (const t of trades) {
    const w = Math.min(t.signal_weight, 4) - 1
    if (w >= 0) weightDist[w]++
  }

  return (
    <div className="panel">
      <div className="panel-header"><span>信号分析</span></div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, padding: 16 }}>
        {/* 信号触发占比 */}
        <div>
          <div style={{ fontSize: 10, color: 'var(--fg-3)', fontWeight: 600, marginBottom: 8, letterSpacing: '.1em', textTransform: 'uppercase' }}>信号触发分布</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([name, count]) => (
              <div key={name} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 11, color: 'var(--fg-1)', width: 80 }}>{SIGNAL_NAME_MAP[name] ?? name}</span>
                <div style={{ flex: 1, height: 6, borderRadius: 3, background: 'var(--bg-3)', overflow: 'hidden' }}>
                  <div style={{ width: `${(count / total) * 100}%`, height: '100%', borderRadius: 3, background: 'var(--accent)' }} />
                </div>
                <span className="mono" style={{ fontSize: 10, color: 'var(--fg-2)', width: 36, textAlign: 'right' }}>{count}</span>
              </div>
            ))}
          </div>
        </div>
        {/* 权重分布 */}
        <div>
          <div style={{ fontSize: 10, color: 'var(--fg-3)', fontWeight: 600, marginBottom: 8, letterSpacing: '.1em', textTransform: 'uppercase' }}>信号重合度</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {weightDist.map((count, w) => count > 0 && (
              <div key={w} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 11, color: 'var(--fg-1)', width: 80 }}>权重 {w + 1}</span>
                <div style={{ flex: 1, height: 6, borderRadius: 3, background: 'var(--bg-3)', overflow: 'hidden' }}>
                  <div style={{ width: `${(count / trades.length) * 100}%`, height: '100%', borderRadius: 3, background: w >= 1 ? 'var(--up)' : 'var(--fg-3)' }} />
                </div>
                <span className="mono" style={{ fontSize: 10, color: 'var(--fg-2)', width: 36, textAlign: 'right' }}>{count}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── 回测结果面板 ────────────────────────────────────────────────────────────

function ResultPanel({ result, entryParams }: { result: MtfBacktestResult; entryParams: Record<string, number> }) {
  const alpha = result.total_return_pct - result.buy_hold_return_pct

  const metrics = [
    { l: '总收益率', v: (result.total_return_pct >= 0 ? '+' : '') + fmt(result.total_return_pct) + '%', c: result.total_return_pct >= 0 ? 'var(--up)' : 'var(--down)' },
    { l: '年化收益', v: (result.annualized_return_pct >= 0 ? '+' : '') + fmt(result.annualized_return_pct) + '%', c: result.annualized_return_pct >= 0 ? 'var(--up)' : 'var(--down)' },
    { l: '最大回撤', v: '-' + fmt(result.max_drawdown_pct) + '%', c: 'var(--down)' },
    { l: '夏普比率', v: fmt(result.sharpe_ratio), c: result.sharpe_ratio >= 1 ? 'var(--up)' : 'var(--fg-0)' },
    { l: '胜率', v: fmt(result.win_rate_pct, 1) + '%', c: 'var(--fg-0)' },
    { l: '交易笔数', v: String(result.total_trades), c: 'var(--fg-0)' },
    { l: '盈/亏', v: `${result.winning_trades}/${result.losing_trades}`, c: 'var(--fg-0)' },
    { l: 'Buy&Hold', v: (result.buy_hold_return_pct >= 0 ? '+' : '') + fmt(result.buy_hold_return_pct) + '%', c: 'var(--fg-1)' },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Header */}
      <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--line-1)', background: 'var(--bg-1)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
          <span className="chip accent">MTF 回测</span>
          <span className="mono" style={{ fontSize: 16, fontWeight: 700, color: 'var(--fg-0)' }}>{result.symbol}</span>
          <span className="chip">{TF_LABELS[result.timeframe] ?? result.timeframe}</span>
          {result.trend_tf && <span className="chip" style={{ fontSize: 10 }}>趋势: {TF_LABELS[result.trend_tf]} MA{result.trend_ma_period}</span>}
          {result.use_impulse && <span className="chip" style={{ fontSize: 10 }}>Elder Impulse</span>}
          <span style={{ color: 'var(--fg-2)', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
            {result.start_date} → {result.end_date}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 10, flexWrap: 'wrap' }}>
          {result.entry_signals.map(s => (
            <span key={s} className="chip" style={{ fontSize: 10, letterSpacing: 0 }}>{SIGNAL_NAME_MAP[s] ?? s}</span>
          ))}
        </div>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 24 }}>
          <div>
            <div className="stat-label">策略总收益</div>
            <div className="mono" style={{ fontSize: 40, fontWeight: 600, color: result.total_return_pct >= 0 ? 'var(--up)' : 'var(--down)', letterSpacing: '-0.02em', lineHeight: 1 }}>
              {result.total_return_pct >= 0 ? '+' : ''}{fmt(result.total_return_pct)}%
            </div>
          </div>
          <div>
            <div className="stat-label">超额收益 (Alpha)</div>
            <div className="mono" style={{ fontSize: 20, fontWeight: 600, color: alpha >= 0 ? 'var(--up)' : 'var(--down)' }}>
              {alpha >= 0 ? '+' : ''}{fmt(alpha)}%
            </div>
          </div>
        </div>
      </div>

      {/* Metrics strip */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(8, 1fr)', background: 'var(--bg-1)', border: '1px solid var(--line-1)', borderRadius: 'var(--radius-lg)', overflow: 'hidden' }}>
        {metrics.map((m, i) => (
          <div key={i} style={{ borderRight: i < metrics.length - 1 ? '1px solid var(--line-1)' : 'none', padding: '12px 10px' }}>
            <div className="stat-label">{m.l}</div>
            <div className="mono" style={{ fontSize: 15, color: m.c, fontWeight: 600, marginTop: 3 }}>{m.v}</div>
          </div>
        ))}
      </div>

      {/* Candlestick chart with trade markers */}
      {result.candles?.length > 0 && (
        <div className="panel">
          <div className="panel-header">
            <span>交易标记图</span>
            <span className="mono" style={{ textTransform: 'none', letterSpacing: 0, fontSize: 10, color: 'var(--fg-2)' }}>
              {result.candles.length} 根K线 · {result.trades.length} 笔交易
            </span>
          </div>
          <div style={{ padding: '8px 0' }}>
            <BacktestCandleChart candles={result.candles} trades={result.trades} entrySignals={result.entry_signals} entryParams={entryParams} />
          </div>
        </div>
      )}

      {/* Equity curve */}
      <div className="panel">
        <div className="panel-header">
          <span>权益曲线</span>
          <div style={{ display: 'flex', gap: 12, fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--fg-2)' }}>
            <span><span style={{ display: 'inline-block', width: 16, height: 2, background: 'var(--accent)', marginRight: 4, verticalAlign: 'middle' }} />策略</span>
            <span><span style={{ display: 'inline-block', width: 16, borderTop: '1.5px dashed var(--fg-3)', marginRight: 4, verticalAlign: 'middle' }} />买持</span>
          </div>
        </div>
        <div style={{ padding: '12px 0' }}>
          <EquityChart data={result.equity_curve} buyHoldReturn={result.buy_hold_return_pct} initialCapital={result.initial_capital} />
        </div>
      </div>

      {result.trades.length > 0 && <SignalDistribution trades={result.trades} />}
      {result.trades.length > 0 && <MtfTradeList trades={result.trades} />}
    </div>
  )
}

// ─── 主页面 ──────────────────────────────────────────────────────────────────

export default function BacktestPage() {
  const [symbol, setSymbol] = useState('AAPL')
  const [executionTf, setExecutionTf] = useState('1day')
  const [trendTf, setTrendTf] = useState<string | null>('1week')
  const [useTrend, setUseTrend] = useState(true)
  const [trendMaPeriod, setTrendMaPeriod] = useState(60)
  const [useImpulse, setUseImpulse] = useState(true)
  const [startDate, setStartDate] = useState('2022-01-01')
  const [endDate, setEndDate] = useState(() => new Date().toISOString().split('T')[0])
  const [capital, setCapital] = useState('10000')

  const [selectedSignals, setSelectedSignals] = useState<EntrySignalId[]>(['ma_breakout'])
  const [entryParams, setEntryParams] = useState<Record<string, number>>({
    ma_breakout_period: 30,
    macd_divergence_lookback: 5,
    sr_lookback: 5,
    sr_min_touches: 2,
  })

  const [exitReverseSignal, setExitReverseSignal] = useState(true)
  const [exitStopLossPct, setExitStopLossPct] = useState(0)
  const [exitBreakevenPct, setExitBreakevenPct] = useState(0)
  const [exitEntryCandleLow, setExitEntryCandleLow] = useState(false)

  const mutation = useMutation({ mutationFn: (data: object) => runMtfBacktest(data) })

  const { data: dataRange } = useQuery<BacktestDataRange>({
    queryKey: ['backtest-data-range', symbol, executionTf],
    queryFn: () => getBacktestDataRange(symbol, executionTf),
    staleTime: 30_000,
    retry: false,
  })

  const toggleSignal = useCallback((id: EntrySignalId) => {
    setSelectedSignals(prev => {
      if (prev.includes(id)) {
        if (prev.length === 1) return prev
        return prev.filter(s => s !== id)
      }
      return [...prev, id]
    })
  }, [])

  const updateParam = useCallback((key: string, val: number) => {
    setEntryParams(prev => ({ ...prev, [key]: val }))
  }, [])

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    mutation.mutate({
      symbol: symbol.trim().toUpperCase(),
      execution_tf: executionTf,
      trend_tf: useTrend ? trendTf : null,
      start_date: startDate,
      end_date: endDate,
      trend_ma_period: trendMaPeriod,
      use_impulse: useImpulse,
      entry_signals: selectedSignals,
      entry_params: entryParams,
      exit_rules: {
        reverse_signal: exitReverseSignal,
        stop_loss_pct: exitStopLossPct,
        breakeven_trigger_pct: exitBreakevenPct,
        entry_candle_low: exitEntryCandleLow,
      },
      initial_capital: parseFloat(capital) || 10000,
    })
  }

  const rowStyle: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 5 }
  const labelStyle: React.CSSProperties = { fontSize: 10.5, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--fg-2)', fontWeight: 600 }
  const sectionStyle: React.CSSProperties = { padding: '12px 14px', borderBottom: '1px solid var(--line-1)', display: 'flex', flexDirection: 'column', gap: 10 }

  const validTrendTfs = Object.entries(TF_LABELS).filter(([k]) => {
    const execOrder: Record<string, number> = { '4h': 2, '1day': 3, '1week': 4 }
    return (execOrder[k] ?? 0) > (execOrder[executionTf] ?? 0)
  })

  return (
    <div className="page" style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 0, height: '100%', overflow: 'hidden' }}>
      {/* ── 左侧配置 ── */}
      <div style={{ borderRight: '1px solid var(--line-1)', background: 'var(--bg-1)', overflowY: 'auto', display: 'flex', flexDirection: 'column' }}>
        <div className="panel-header" style={{ border: 0, borderBottom: '1px solid var(--line-1)' }}><span>多级别策略回测</span></div>

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column' }}>
          {/* 基础配置 */}
          <div style={sectionStyle}>
            <div style={rowStyle}>
              <label style={labelStyle}>标的</label>
              <SymbolCombobox value={symbol} onChange={setSymbol} enableSearch={false} />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <div style={rowStyle}>
                <label style={labelStyle}>开始日期</label>
                <input type="date" className="mr-input mono" value={startDate} onChange={e => setStartDate(e.target.value)} style={{ fontSize: 11 }} />
              </div>
              <div style={rowStyle}>
                <label style={labelStyle}>结束日期</label>
                <input type="date" className="mr-input mono" value={endDate} onChange={e => setEndDate(e.target.value)} style={{ fontSize: 11 }} />
              </div>
            </div>
            <div style={rowStyle}>
              <label style={labelStyle}>初始资金 (USD)</label>
              <input type="number" className="mr-input mono" value={capital} onChange={e => setCapital(e.target.value)} min="100" />
            </div>
            {dataRange && (
              <div style={{ fontSize: 10, color: dataRange.has_data ? 'var(--up)' : 'var(--fg-3)', display: 'flex', alignItems: 'center', gap: 5 }}>
                <div style={{ width: 6, height: 6, borderRadius: '50%', background: dataRange.has_data ? 'var(--up)' : 'var(--fg-3)', flexShrink: 0 }} />
                {dataRange.has_data ? `本地数据 ${dataRange.start_date} ~ ${dataRange.end_date}` : '无本地数据，回测时将自动同步'}
              </div>
            )}
          </div>

          {/* ① 趋势过滤 */}
          <div style={sectionStyle}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ width: 18, height: 18, borderRadius: '50%', background: 'rgba(239,68,68,0.2)', color: '#f87171', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700 }}>1</div>
              <span style={{ ...labelStyle, letterSpacing: 0, fontSize: 11 }}>趋势过滤 · 均线定方向</span>
            </div>

            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
              <input type="checkbox" checked={useTrend} onChange={e => setUseTrend(e.target.checked)} style={{ accentColor: 'var(--accent)' }} />
              <span style={{ fontSize: 11, color: 'var(--fg-1)' }}>启用高级别趋势过滤</span>
            </label>

            {useTrend && (
              <>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                  <div style={rowStyle}>
                    <label style={labelStyle}>趋势级别</label>
                    <div className="tweak-segment">
                      {validTrendTfs.map(([k, label]) => (
                        <button key={k} type="button" className={trendTf === k ? 'on' : ''} onClick={() => setTrendTf(k)}
                          style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>{label}</button>
                      ))}
                    </div>
                  </div>
                  <div style={rowStyle}>
                    <label style={labelStyle}>执行级别</label>
                    <div className="tweak-segment">
                      {Object.entries(TF_LABELS).map(([k, label]) => (
                        <button key={k} type="button" className={executionTf === k ? 'on' : ''} onClick={() => {
                          setExecutionTf(k)
                          const execOrder: Record<string, number> = { '4h': 2, '1day': 3, '1week': 4 }
                          if (trendTf && (execOrder[trendTf] ?? 0) <= (execOrder[k] ?? 0)) {
                            const higher = Object.keys(TF_LABELS).find(t => (execOrder[t] ?? 0) > (execOrder[k] ?? 0))
                            setTrendTf(higher ?? null)
                          }
                        }}
                          style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>{label}</button>
                      ))}
                    </div>
                  </div>
                </div>
                <div style={rowStyle}>
                  <label style={labelStyle}>MA 周期</label>
                  <input type="number" className="mr-input mono" value={trendMaPeriod} onChange={e => setTrendMaPeriod(parseInt(e.target.value) || 60)} min={5} style={{ fontSize: 11, width: 80 }} />
                </div>
                <div style={{ fontSize: 10, color: 'var(--fg-3)', lineHeight: 1.5 }}>
                  {trendTf && <>当 {TF_LABELS[trendTf]} 价格在 MA{trendMaPeriod} 上方时，{TF_LABELS[executionTf]} 只做多；下方只做空</>}
                </div>
              </>
            )}

            {!useTrend && (
              <div style={rowStyle}>
                <label style={labelStyle}>执行级别</label>
                <div className="tweak-segment">
                  {Object.entries(TF_LABELS).map(([k, label]) => (
                    <button key={k} type="button" className={executionTf === k ? 'on' : ''} onClick={() => setExecutionTf(k)}
                      style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>{label}</button>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* ② 动量过滤 */}
          <div style={sectionStyle}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ width: 18, height: 18, borderRadius: '50%', background: 'rgba(251,191,36,0.2)', color: '#fbbf24', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700 }}>2</div>
              <span style={{ ...labelStyle, letterSpacing: 0, fontSize: 11 }}>动量过滤 · Elder Impulse</span>
            </div>

            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
              <input type="checkbox" checked={useImpulse} onChange={e => setUseImpulse(e.target.checked)} style={{ accentColor: 'var(--accent)' }} />
              <span style={{ fontSize: 11, color: 'var(--fg-1)' }}>启用 Elder Impulse 动量过滤</span>
            </label>

            <div style={{ display: 'flex', gap: 12, fontSize: 10 }}>
              <span style={{ color: '#34d399' }}>● 绿色: 不做空</span>
              <span style={{ color: '#f87171' }}>● 红色: 不做多</span>
              <span style={{ color: '#60a5fa' }}>● 蓝色: 多空皆可</span>
            </div>
            <div style={{ fontSize: 10, color: 'var(--fg-3)', lineHeight: 1.5 }}>
              EMA(13) 方向 + MACD 柱体方向共同决定动量颜色
            </div>
          </div>

          {/* ③ 入场信号 */}
          <div style={sectionStyle}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ width: 18, height: 18, borderRadius: '50%', background: 'rgba(96,165,250,0.2)', color: '#60a5fa', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700 }}>3</div>
              <span style={{ ...labelStyle, letterSpacing: 0, fontSize: 11 }}>入场信号 · 任一触发即可</span>
            </div>
            <div style={{ fontSize: 10, color: 'var(--fg-3)' }}>多信号同时触发 → 权重更高</div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {ALL_ENTRY_SIGNALS.map(id => {
                const meta = ENTRY_SIGNAL_META[id]
                const isSelected = selectedSignals.includes(id)
                return (
                  <div key={id} style={{
                    border: `1px solid ${isSelected ? 'var(--accent)' : 'var(--line-1)'}`,
                    borderRadius: 'var(--radius)',
                    background: isSelected ? 'var(--accent-bg, rgba(255,176,32,.06))' : 'var(--bg-2)',
                    overflow: 'hidden',
                  }}>
                    <div
                      style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px', cursor: 'pointer' }}
                      onClick={() => toggleSignal(id)}
                    >
                      <div style={{
                        width: 16, height: 16, borderRadius: 4, flexShrink: 0,
                        border: `2px solid ${isSelected ? 'var(--accent)' : 'var(--line-2)'}`,
                        background: isSelected ? 'var(--accent)' : 'transparent',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                      }}>
                        {isSelected && (
                          <svg width="10" height="8" viewBox="0 0 10 8" fill="none">
                            <path d="M1 4l3 3 5-6" stroke="#0f1318" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                          </svg>
                        )}
                      </div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <span style={{ fontSize: 12, fontWeight: 600, color: isSelected ? 'var(--accent)' : 'var(--fg-0)' }}>{meta.label}</span>
                        <div style={{ fontSize: 10, color: 'var(--fg-3)', marginTop: 1 }}>{meta.desc}</div>
                      </div>
                    </div>
                    {isSelected && meta.params.length > 0 && (
                      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${meta.params.length}, 1fr)`, gap: 8, padding: '0 12px 10px' }}>
                        {meta.params.map(p => (
                          <div key={p.key} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                            <label style={{ fontSize: 9.5, letterSpacing: '.12em', textTransform: 'uppercase', color: 'var(--fg-2)', fontWeight: 600 }}>
                              {p.label}
                            </label>
                            <input
                              type="number"
                              className="mr-input mono"
                              min={p.min}
                              step={p.step}
                              value={entryParams[p.key] ?? p.default}
                              onChange={e => updateParam(p.key, parseFloat(e.target.value) || p.min)}
                              style={{ fontSize: 11 }}
                            />
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>

          {/* ④ 出场规则 */}
          <div style={sectionStyle}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ width: 18, height: 18, borderRadius: '50%', background: 'rgba(168,85,247,0.2)', color: '#a855f7', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700 }}>4</div>
              <span style={{ ...labelStyle, letterSpacing: 0, fontSize: 11 }}>出场规则 · 任一触发即平仓</span>
            </div>
            <div style={{ fontSize: 10, color: 'var(--fg-3)' }}>按优先级依次判断: 止损 → 保本 → 反向信号</div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {/* 反向信号 */}
              <label style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px', cursor: 'pointer',
                border: `1px solid ${exitReverseSignal ? 'var(--accent)' : 'var(--line-1)'}`,
                borderRadius: 'var(--radius)', background: exitReverseSignal ? 'var(--accent-bg, rgba(255,176,32,.06))' : 'var(--bg-2)',
              }}>
                <input type="checkbox" checked={exitReverseSignal} onChange={e => setExitReverseSignal(e.target.checked)} style={{ accentColor: 'var(--accent)' }} />
                <div style={{ flex: 1 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: exitReverseSignal ? 'var(--accent)' : 'var(--fg-0)' }}>反向技术指标</span>
                  <div style={{ fontSize: 10, color: 'var(--fg-3)', marginTop: 1 }}>入场信号产生反向信号时平仓</div>
                </div>
              </label>

              {/* 固定止损 */}
              <div style={{
                border: `1px solid ${exitStopLossPct > 0 ? 'var(--accent)' : 'var(--line-1)'}`,
                borderRadius: 'var(--radius)', background: exitStopLossPct > 0 ? 'var(--accent-bg, rgba(255,176,32,.06))' : 'var(--bg-2)',
                overflow: 'hidden',
              }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px', cursor: 'pointer' }}>
                  <input type="checkbox" checked={exitStopLossPct > 0} onChange={e => setExitStopLossPct(e.target.checked ? 5 : 0)} style={{ accentColor: 'var(--accent)' }} />
                  <div style={{ flex: 1 }}>
                    <span style={{ fontSize: 12, fontWeight: 600, color: exitStopLossPct > 0 ? 'var(--accent)' : 'var(--fg-0)' }}>固定百分比止损</span>
                    <div style={{ fontSize: 10, color: 'var(--fg-3)', marginTop: 1 }}>价格下跌超过设定比例时止损</div>
                  </div>
                </label>
                {exitStopLossPct > 0 && (
                  <div style={{ padding: '0 12px 10px', display: 'flex', alignItems: 'center', gap: 8 }}>
                    <label style={{ fontSize: 9.5, letterSpacing: '.12em', textTransform: 'uppercase', color: 'var(--fg-2)', fontWeight: 600 }}>止损%</label>
                    <input type="number" className="mr-input mono" min={0.5} step={0.5} value={exitStopLossPct}
                      onChange={e => setExitStopLossPct(parseFloat(e.target.value) || 0)} style={{ fontSize: 11, width: 70 }} />
                  </div>
                )}
              </div>

              {/* 保本出场 */}
              <div style={{
                border: `1px solid ${exitBreakevenPct > 0 ? 'var(--accent)' : 'var(--line-1)'}`,
                borderRadius: 'var(--radius)', background: exitBreakevenPct > 0 ? 'var(--accent-bg, rgba(255,176,32,.06))' : 'var(--bg-2)',
                overflow: 'hidden',
              }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px', cursor: 'pointer' }}>
                  <input type="checkbox" checked={exitBreakevenPct > 0} onChange={e => setExitBreakevenPct(e.target.checked ? 3 : 0)} style={{ accentColor: 'var(--accent)' }} />
                  <div style={{ flex: 1 }}>
                    <span style={{ fontSize: 12, fontWeight: 600, color: exitBreakevenPct > 0 ? 'var(--accent)' : 'var(--fg-0)' }}>保本出场</span>
                    <div style={{ fontSize: 10, color: 'var(--fg-3)', marginTop: 1 }}>浮盈达到阈值后，回落到入场价则平仓</div>
                  </div>
                </label>
                {exitBreakevenPct > 0 && (
                  <div style={{ padding: '0 12px 10px', display: 'flex', alignItems: 'center', gap: 8 }}>
                    <label style={{ fontSize: 9.5, letterSpacing: '.12em', textTransform: 'uppercase', color: 'var(--fg-2)', fontWeight: 600 }}>触发%</label>
                    <input type="number" className="mr-input mono" min={0.5} step={0.5} value={exitBreakevenPct}
                      onChange={e => setExitBreakevenPct(parseFloat(e.target.value) || 0)} style={{ fontSize: 11, width: 70 }} />
                  </div>
                )}
              </div>

              {/* 入场K线最低价止损 */}
              <label style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px', cursor: 'pointer',
                border: `1px solid ${exitEntryCandleLow ? 'var(--accent)' : 'var(--line-1)'}`,
                borderRadius: 'var(--radius)', background: exitEntryCandleLow ? 'var(--accent-bg, rgba(255,176,32,.06))' : 'var(--bg-2)',
              }}>
                <input type="checkbox" checked={exitEntryCandleLow} onChange={e => setExitEntryCandleLow(e.target.checked)} style={{ accentColor: 'var(--accent)' }} />
                <div style={{ flex: 1 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: exitEntryCandleLow ? 'var(--accent)' : 'var(--fg-0)' }}>入场K线最低价止损</span>
                  <div style={{ fontSize: 10, color: 'var(--fg-3)', marginTop: 1 }}>价格跌破入场那根K线的最低价时止损</div>
                </div>
              </label>
            </div>
          </div>

          {/* 提交 */}
          <div style={{ padding: '12px 14px' }}>
            <button type="submit" className="btn primary" style={{ width: '100%', height: 36, justifyContent: 'center', fontSize: 13 }} disabled={mutation.isPending}>
              <svg className="ico-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 3h6" /><path d="M10 3v6l-5 9a2 2 0 002 3h10a2 2 0 002-3l-5-9V3" /><path d="M8 15h8" />
              </svg>
              {mutation.isPending ? '回测中…' : '运行回测'}
            </button>
          </div>
        </form>
      </div>

      {/* ── 右侧结果区 ── */}
      <div style={{ overflowY: 'auto' }}>
        {!mutation.data && !mutation.isPending && !mutation.isError && (
          <div style={{ height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, color: 'var(--fg-3)' }}>
            <svg style={{ width: 40, height: 40 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 3h6" /><path d="M10 3v6l-5 9a2 2 0 002 3h10a2 2 0 002-3l-5-9V3" /><path d="M8 15h8" />
            </svg>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 13, color: 'var(--fg-2)' }}>三层漏斗交易系统</div>
              <div style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 4 }}>趋势过滤 → 动量过滤 → 入场信号</div>
            </div>
          </div>
        )}

        {mutation.isPending && (
          <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12, color: 'var(--fg-2)' }}>
            <div style={{ width: 26, height: 26, border: '2.5px solid var(--accent)', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.7s linear infinite' }} />
            <span className="mono" style={{ fontSize: 12 }}>同步数据 & 多级别回测运算中…</span>
          </div>
        )}

        {mutation.isError && (
          <div style={{ padding: 20 }}>
            <div style={{ padding: '12px 14px', background: 'var(--down-bg)', border: '1px solid var(--down)', borderRadius: 'var(--radius)', color: 'var(--down)', fontSize: 12 }}>
              {(mutation.error as any)?.response?.data?.detail ?? '回测失败，请检查参数后重试'}
            </div>
          </div>
        )}

        {mutation.data && (
          <div style={{ padding: 0 }}>
            <ResultPanel result={mutation.data as MtfBacktestResult} entryParams={entryParams} />
          </div>
        )}
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
