'use client'

import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import { useRef, useState, useEffect } from 'react'
import { getQuote, getQuotes, getAlerts, getWatchlist, getReplayNotes, getEconomicCalendar } from '@/lib/api'
import { useOpportunities } from '@/lib/api/opportunities'
import { useMemo } from 'react'
import type { Quote, Alert, WatchlistItem, ReplayNote, EconomicEvent } from '@/types'

function fmtPrice(v: number): string {
  if (v >= 10000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 })
  if (v >= 100) return v.toFixed(2)
  if (v >= 1) return v.toFixed(4)
  return v.toFixed(6)
}

const CONDITION_LABELS: Record<string, string> = {
  price_above: '价格 ≥', price_below: '价格 ≤',
  rsi_above: 'RSI ≥', rsi_below: 'RSI ≤',
  ma_cross_up: '均线金叉', ma_cross_down: '均线死叉',
}

function QuoteMiniCard({ symbol, quote: externalQuote }: { symbol: string; quote?: Quote | null }) {
  const { data: fetchedData, isLoading: fetchLoading } = useQuery<Quote>({
    queryKey: ['quote', symbol],
    queryFn: () => getQuote(symbol),
    refetchInterval: 30_000,
    staleTime: 20_000,
    retry: 2,
    enabled: !externalQuote,
  })
  const data = externalQuote ?? fetchedData
  const isLoading = externalQuote === undefined ? fetchLoading : !externalQuote
  const up = (data?.change_percent ?? 0) >= 0
  const prevPrice = useRef<number | null>(null)
  const [flashClass, setFlashClass] = useState('')

  useEffect(() => {
    if (data?.price == null) return
    if (prevPrice.current !== null && prevPrice.current !== data.price) {
      const cls = data.price > prevPrice.current ? 'flash-up' : 'flash-down'
      setFlashClass('')
      requestAnimationFrame(() => requestAnimationFrame(() => setFlashClass(cls)))
      const t = setTimeout(() => setFlashClass(''), 850)
      prevPrice.current = data.price
      return () => clearTimeout(t)
    }
    prevPrice.current = data.price
  }, [data?.price])

  return (
    <Link href={`/chart/${encodeURIComponent(symbol)}`}
      className={flashClass}
      style={{ display: 'block', padding: '9px 12px', borderBottom: '1px solid var(--line-1)', cursor: 'pointer', transition: 'background .1s', textDecoration: 'none' }}
      onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-2)')}
      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
    >
      {isLoading ? (
        <div style={{ height: 28, background: 'var(--bg-2)', borderRadius: 3, animation: 'pulse 1.5s ease-in-out infinite' }} />
      ) : data ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="mono" style={{ fontWeight: 700, fontSize: 12, color: 'var(--fg-0)' }}>{symbol}</div>
            {data.name && <div style={{ fontSize: 10.5, color: 'var(--fg-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{data.name}</div>}
          </div>
          <div style={{ textAlign: 'right', flexShrink: 0 }}>
            <div className="mono" style={{ fontSize: 12, color: 'var(--fg-0)', fontWeight: 600 }}>{fmtPrice(data.price)}</div>
            <div className="mono" style={{ fontSize: 10.5, color: up ? 'var(--up)' : 'var(--down)', fontWeight: 600 }}>
              {up ? '+' : ''}{data.change_percent.toFixed(2)}%
            </div>
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span className="mono" style={{ color: 'var(--fg-2)', fontSize: 12 }}>{symbol}</span>
          <span style={{ color: 'var(--fg-3)', fontSize: 11 }}>不可用</span>
        </div>
      )}
    </Link>
  )
}

const INDEX_SYMBOLS = ['^GSPC', '^NDX', '^DJI']

function getNextMacroEvent(events: EconomicEvent[]): { name: string; bjt: string; ts: number; daysUntil: number } | null {
  const now = Date.now()
  for (const e of events) {
    const bjt = `${e.date} ${e.time_bjt ?? '00:00'}`
    const [date, time] = bjt.split(' ')
    const [y, m, d] = date.split('-').map(Number)
    const [h, min] = (time || '00:00').split(':').map(Number)
    const ts = Date.UTC(y, m - 1, d, h - 8, min)
    if (ts > now - 86400_000) {
      const daysUntil = Math.ceil((ts - now) / 86400_000)
      return { name: e.name, bjt, ts, daysUntil }
    }
  }
  return null
}
const CRYPTO_SYMBOLS = ['BTC-USD', 'ETH-USD', 'SOL-USD']

function fngColor(v: number): string {
  if (v <= 25) return 'var(--down)'
  if (v <= 45) return '#f7931a'
  if (v <= 55) return 'var(--fg-2)'
  if (v <= 75) return '#4caf50'
  return 'var(--up)'
}

function fngLabel(cls: string): string {
  const map: Record<string, string> = {
    'Extreme Fear': '极度恐慌', 'Fear': '恐慌',
    'Neutral': '中性', 'Greed': '贪婪', 'Extreme Greed': '极度贪婪',
  }
  return map[cls] ?? cls
}

function vixColor(v: number): string {
  if (v > 30) return 'var(--down)'
  if (v > 20) return '#f7931a'
  return 'var(--up)'
}

export default function DashboardPage() {
  const { data: watchlistData } = useQuery<{ items: WatchlistItem[] }>({
    queryKey: ['watchlist'],
    queryFn: () => getWatchlist(),
    staleTime: 30_000,
  })
  const watchlist = watchlistData?.items ?? []

  const { data: alertsData } = useQuery<{ items: Alert[] }>({
    queryKey: ['alerts'],
    queryFn: () => getAlerts(),
    refetchInterval: 60_000,
  })
  const alerts = alertsData?.items ?? []

  const { data: notesData } = useQuery<{ items: ReplayNote[]; total: number }>({
    queryKey: ['notes'],
    queryFn: () => getReplayNotes(),
    staleTime: 60_000,
  })
  const notes = notesData?.items ?? []

  const activeAlerts = alerts.filter(a => a.is_active).length
  const triggeredAlerts = alerts.filter(a => a.is_triggered && a.is_active).length

  const { data: fngData } = useQuery({
    queryKey: ['fng'],
    queryFn: () => fetch('https://api.alternative.me/fng/?limit=1').then(r => r.json()),
    staleTime: 5 * 60_000,
    retry: 1,
  })
  const fng = fngData?.data?.[0]

  const { data: calendarData } = useQuery<{ events: EconomicEvent[] }>({
    queryKey: ['calendar-dashboard'],
    queryFn: () => getEconomicCalendar(60),
    staleTime: 300_000,
  })
  const calendarEvents = calendarData?.events ?? []

  const allQuoteSymbols = useMemo(() => {
    const syms = [...INDEX_SYMBOLS, ...CRYPTO_SYMBOLS, '^VIX']
    watchlist.slice(0, 6).forEach(w => {
      if (!syms.includes(w.symbol)) syms.push(w.symbol)
    })
    return syms
  }, [watchlist])

  const { data: batchData } = useQuery<{ quotes: (Quote | null)[] }>({
    queryKey: ['batch-quotes', allQuoteSymbols.join(',')],
    queryFn: () => getQuotes(allQuoteSymbols),
    refetchInterval: 30_000,
    staleTime: 20_000,
    retry: 2,
    enabled: allQuoteSymbols.length > 0,
  })

  const quotesMap = useMemo(() => {
    const map = new Map<string, Quote>()
    if (batchData?.quotes) {
      allQuoteSymbols.forEach((sym, i) => {
        const q = batchData.quotes[i]
        if (q) map.set(sym, q)
      })
    }
    return map
  }, [batchData, allQuoteSymbols])

  const vixData = quotesMap.get('^VIX') ?? null

  const nextEvent = getNextMacroEvent(calendarEvents)
  const { data: opportunitiesData } = useOpportunities({ status: 'open', min_score: 0.7, limit: 5 })
  const topOpportunities = opportunitiesData?.items ?? []

  return (
    <div className="page">
      <div className="page-inner">
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 16 }}>
          <div>
            <div className="section-title">总览</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, letterSpacing: '-0.01em', color: 'var(--fg-0)' }}>
              行情复盘助手
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <Link href="/alerts" className="btn">
              <svg className="ico-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M6 8a6 6 0 1112 0v5l2 3H4l2-3V8z" /><path d="M10 19a2 2 0 004 0" />
              </svg>
              新增告警
            </Link>
            <Link href="/chart/AAPL" className="btn primary">
              <svg className="ico-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 3v18h18" /><path d="M7 15l3-4 3 2 5-7" />
              </svg>
              查看行情
            </Link>
          </div>
        </div>

        {/* KPI row */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
          {/* 活跃告警 */}
          <div className="panel" style={{ padding: '14px 16px' }}>
            <div className="stat-label">活跃告警</div>
            <div className="mono" style={{ fontSize: 28, color: triggeredAlerts > 0 ? 'var(--down)' : 'var(--accent)', fontWeight: 600, marginTop: 4, letterSpacing: '-0.02em' }}>{activeAlerts}</div>
            <div className="mono" style={{ color: 'var(--fg-2)', fontSize: 10.5, letterSpacing: '.06em', textTransform: 'uppercase', marginTop: 2 }}>
              {triggeredAlerts > 0 ? `${triggeredAlerts} 已触发` : `共 ${alerts.length}`}
            </div>
          </div>

          {/* 加密贪婪恐慌指数 */}
          <div className="panel" style={{ padding: '14px 16px' }}>
            <div className="stat-label">加密贪婪恐慌指数</div>
            {fng ? (
              <>
                <div className="mono" style={{ fontSize: 28, color: fngColor(Number(fng.value)), fontWeight: 600, marginTop: 4, letterSpacing: '-0.02em' }}>
                  {fng.value}
                </div>
                <div className="mono" style={{ color: fngColor(Number(fng.value)), fontSize: 10.5, letterSpacing: '.06em', textTransform: 'uppercase', marginTop: 2 }}>
                  {fngLabel(fng.value_classification)}
                </div>
              </>
            ) : (
              <div className="mono" style={{ fontSize: 28, color: 'var(--fg-3)', fontWeight: 600, marginTop: 4 }}>—</div>
            )}
          </div>

          {/* 近期宏观事件 */}
          <div className="panel" style={{ padding: '14px 16px' }}>
            <div className="stat-label">近期宏观事件</div>
            {nextEvent ? (
              <>
                <div style={{ fontSize: 13, color: 'var(--fg-0)', fontWeight: 600, marginTop: 4, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {nextEvent.name}
                </div>
                <div className="mono" style={{ color: 'var(--fg-2)', fontSize: 10.5, marginTop: 4 }}>
                  北京时间 {nextEvent.bjt}
                </div>
                <div className="mono" style={{ color: nextEvent.daysUntil <= 3 ? 'var(--down)' : 'var(--fg-3)', fontSize: 10, marginTop: 2 }}>
                  {nextEvent.daysUntil > 0 ? `还有 ${nextEvent.daysUntil} 天` : nextEvent.daysUntil === 0 ? '今日' : '已过期'}
                </div>
              </>
            ) : (
              <div className="mono" style={{ fontSize: 13, color: 'var(--fg-3)', marginTop: 4 }}>加载中...</div>
            )}
          </div>

          {/* VIX 指数 */}
          <div className="panel" style={{ padding: '14px 16px' }}>
            <div className="stat-label">VIX 恐慌指数</div>
            {vixData ? (
              <>
                <div className="mono" style={{ fontSize: 28, color: vixColor(vixData.price), fontWeight: 600, marginTop: 4, letterSpacing: '-0.02em' }}>
                  {vixData.price.toFixed(2)}
                </div>
                <div className="mono" style={{ color: vixData.change_percent >= 0 ? 'var(--up)' : 'var(--down)', fontSize: 10.5, letterSpacing: '.06em', marginTop: 2 }}>
                  {vixData.change_percent >= 0 ? '+' : ''}{vixData.change_percent.toFixed(2)}%
                </div>
              </>
            ) : (
              <div className="mono" style={{ fontSize: 28, color: 'var(--fg-3)', fontWeight: 600, marginTop: 4 }}>—</div>
            )}
          </div>
        </div>

        {/* Market panels */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 16 }}>
          <div className="panel" style={{ overflow: 'hidden' }}>
            <div className="panel-header"><span>指数行情</span></div>
            {INDEX_SYMBOLS.map(s => <QuoteMiniCard key={s} symbol={s} quote={quotesMap.get(s)} />)}
          </div>
          <div className="panel" style={{ overflow: 'hidden' }}>
            <div className="panel-header"><span>加密货币</span></div>
            {CRYPTO_SYMBOLS.map(s => <QuoteMiniCard key={s} symbol={s} quote={quotesMap.get(s)} />)}
          </div>
          <div className="panel" style={{ overflow: 'hidden' }}>
            <div className="panel-header">
              <span>我的自选</span>
              <Link href="/watchlist" className="btn small ghost">管理 →</Link>
            </div>
            {watchlist.length === 0 ? (
              <div style={{ padding: 14, color: 'var(--fg-3)', fontSize: 11, textAlign: 'center' }}>暂无自选，在左侧添加</div>
            ) : (
              watchlist.slice(0, 6).map(item => <QuoteMiniCard key={item.id} symbol={item.symbol} quote={quotesMap.get(item.symbol)} />)
            )}
          </div>
        </div>

        {/* Opportunities + Alerts + Notes */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 16 }}>
          <div className="panel" style={{ overflow: 'hidden' }}>
            <div className="panel-header">
              <span>高分机会</span>
              <Link href="/opportunities" className="btn small ghost">全部 →</Link>
            </div>
            {topOpportunities.length === 0 ? (
              <div style={{ padding: 14, color: 'var(--fg-3)', fontSize: 11, textAlign: 'center' }}>暂无高置信机会</div>
            ) : topOpportunities.map(o => (
              <Link key={o.id} href={`/opportunities`} style={{ padding: '9px 12px', borderBottom: '1px solid var(--line-1)', display: 'flex', alignItems: 'center', gap: 10, textDecoration: 'none' }}>
                <span className={`chip ${o.direction === 'long' ? 'up' : 'down'}`} style={{ minWidth: 48, justifyContent: 'center' }}>
                  {Math.round(o.score * 100)}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="mono" style={{ fontSize: 12, color: 'var(--fg-0)', fontWeight: 700 }}>{o.symbol} · {o.timeframe.toUpperCase()}</div>
                  <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-2)' }}>
                    胜率 {o.edge_stat ? `${(o.edge_stat.win_rate * 100).toFixed(1)}%` : '-'} · 期望 {o.edge_stat?.expectancy.toFixed(2) ?? '-'}R
                  </div>
                </div>
              </Link>
            ))}
          </div>

          <div className="panel" style={{ overflow: 'hidden' }}>
            <div className="panel-header">
              <span>告警监控</span>
              <Link href="/alerts" className="btn small ghost">全部 →</Link>
            </div>
            {alerts.length === 0 ? (
              <div style={{ padding: 14, color: 'var(--fg-3)', fontSize: 11, textAlign: 'center' }}>
                暂无告警，<Link href="/alerts" style={{ color: 'var(--accent)' }}>点击创建</Link>
              </div>
            ) : alerts.slice(0, 5).map(a => (
              <div key={a.id} style={{ padding: '9px 12px', borderBottom: '1px solid var(--line-1)', display: 'flex', alignItems: 'center', gap: 10 }}>
                <span className={`chip ${a.is_triggered ? 'down' : a.is_active ? 'accent' : ''}`} style={{ minWidth: 52, justifyContent: 'center' }}>
                  {a.is_triggered ? '已触发' : a.is_active ? '监听' : '暂停'}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, color: 'var(--fg-0)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.name}</div>
                  <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-2)' }}>
                    {a.symbol} · {CONDITION_LABELS[a.condition_type] ?? a.condition_type} {a.threshold || ''}
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="panel" style={{ overflow: 'hidden' }}>
            <div className="panel-header">
              <span>最近笔记</span>
              <Link href="/notes" className="btn small ghost">全部 →</Link>
            </div>
            {notes.length === 0 ? (
              <div style={{ padding: 14, color: 'var(--fg-3)', fontSize: 11, textAlign: 'center' }}>暂无笔记，进入复盘后添加</div>
            ) : notes.slice(0, 4).map(n => (
              <div key={n.id} style={{ padding: '9px 12px', borderBottom: '1px solid var(--line-1)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                  <Link href={`/replay/${encodeURIComponent(n.symbol)}`} className="mono" style={{ color: 'var(--fg-0)', fontWeight: 700, fontSize: 12 }}>{n.symbol}</Link>
                  <span className="chip" style={{ fontSize: 9 }}>{n.timeframe.toUpperCase()}</span>
                  <span style={{ flex: 1 }} />
                  <span className="mono" style={{ color: 'var(--fg-2)', fontSize: 10.5 }}>{n.note_time?.slice(0, 10) ?? ''}</span>
                </div>
                {n.trend_view && (
                  <div style={{ fontSize: 11.5, color: 'var(--fg-1)', lineHeight: 1.5, overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' } as React.CSSProperties}>
                    {n.trend_view}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Feature links */}
        <div className="hline">功能模块</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
          {[
            { title: '行情图表', desc: '蜡烛图、自定义 MA、RSI、MACD，画线工具', href: '/chart/AAPL', color: 'var(--info)' },
            { title: '复盘模式', desc: '从历史起点逐根 K 线推进，记录复盘笔记', href: '/replay/AAPL', color: 'var(--up)' },
            { title: '策略回测', desc: 'MA 双均线、RSI、MACD 策略量化回测', href: '/backtest', color: 'var(--accent)' },
            { title: '策略库', desc: '浏览所有策略卡片，查看每个策略在哪些品种上表现最好', href: '/strategies', color: 'var(--ma60)' },
          ].map(f => (
            <Link key={f.title} href={f.href}
              style={{ display: 'block', padding: '14px 16px', background: 'var(--bg-1)', border: '1px solid var(--line-1)', borderRadius: 6, textDecoration: 'none', transition: 'border-color .15s, background .15s' }}
              onMouseEnter={e => { const el = e.currentTarget as HTMLElement; el.style.borderColor = 'var(--line-2)'; el.style.background = 'var(--bg-2)' }}
              onMouseLeave={e => { const el = e.currentTarget as HTMLElement; el.style.borderColor = 'var(--line-1)'; el.style.background = 'var(--bg-1)' }}
            >
              <div style={{ fontWeight: 600, color: f.color, fontSize: 13, marginBottom: 6 }}>{f.title}</div>
              <div style={{ fontSize: 11.5, color: 'var(--fg-2)', lineHeight: 1.5 }}>{f.desc}</div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}
