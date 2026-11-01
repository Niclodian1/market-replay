'use client'

import { useState, useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getEconomicCalendar, getEarnings } from '@/lib/api'
import type { EconomicEvent } from '@/types'

// ─── Helpers ────────────────────────────────────────────────────────────────

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

function toDateStr(d: Date) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function buildMonthGrid(year: number, month: number) {
  const first = new Date(year, month, 1)
  const startDow = first.getDay()
  const start = new Date(year, month, 1 - startDow)

  const days: Date[] = []
  for (let i = 0; i < 42; i++) {
    days.push(new Date(start.getFullYear(), start.getMonth(), start.getDate() + i))
  }

  const lastRow = days.slice(35)
  const needsSixRows = lastRow.some(d => d.getMonth() === month)
  return needsSixRows ? days : days.slice(0, 35)
}

// ─── Earnings Symbols Hook ─────────────────────────────────────────────────

const STORAGE_KEY = 'market-replay-earnings-symbols'
const DEFAULT_EARNINGS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA']

function useEarningsSymbols() {
  const [symbols, setSymbols] = useState<string[]>([])

  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      setSymbols(saved ? JSON.parse(saved) : DEFAULT_EARNINGS)
    } catch {
      setSymbols(DEFAULT_EARNINGS)
    }
  }, [])

  const save = (next: string[]) => {
    setSymbols(next)
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  }

  const add = (sym: string) => {
    const upper = sym.toUpperCase().trim()
    if (!upper || symbols.includes(upper)) return
    save([...symbols, upper])
  }

  const remove = (sym: string) => {
    save(symbols.filter(s => s !== sym))
  }

  return { symbols, add, remove }
}

// ─── Event type colors ─────────────────────────────────────────────────────

const MACRO_COLORS: Record<string, { bg: string; fg: string }> = {
  'FOMC 利率决议': { bg: 'rgba(239,68,68,0.25)', fg: '#f87171' },
  '非农就业数据': { bg: 'rgba(251,191,36,0.25)', fg: '#fbbf24' },
  'CPI 消费者物价指数': { bg: 'rgba(96,165,250,0.25)', fg: '#60a5fa' },
}
const DEFAULT_MACRO_COLOR = { bg: 'rgba(156,163,175,0.2)', fg: '#9ca3af' }
const EARNINGS_COLOR = { bg: 'rgba(52,211,153,0.2)', fg: '#34d399' }

// ─── Monthly Calendar ──────────────────────────────────────────────────────

function MonthlyCalendar() {
  const now = new Date()
  const todayStr = toDateStr(now)
  const [viewYear, setViewYear] = useState(now.getFullYear())
  const [viewMonth, setViewMonth] = useState(now.getMonth())

  // — macro data —
  const { data: calendarData } = useQuery<{ events: EconomicEvent[] }>({
    queryKey: ['calendar'],
    queryFn: () => getEconomicCalendar(180),
    refetchInterval: 300_000,
  })

  // — earnings data —
  const { symbols, add, remove } = useEarningsSymbols()
  const [input, setInput] = useState('')

  const { data: earningsData } = useQuery<{ earnings: Record<string, string | null> }>({
    queryKey: ['earnings', symbols.join(',')],
    queryFn: () => getEarnings(symbols),
    enabled: symbols.length > 0,
    staleTime: 60_000,
  })

  // — build event map: date -> items —
  const eventMap = useMemo(() => {
    const map: Record<string, { label: string; type: 'macro' | 'earnings'; color: { bg: string; fg: string } }[]> = {}

    const events = calendarData?.events ?? []
    for (const evt of events) {
      if (!map[evt.date]) map[evt.date] = []
      const color = MACRO_COLORS[evt.name] ?? DEFAULT_MACRO_COLOR
      map[evt.date].push({ label: evt.name, type: 'macro', color })
    }

    const earnings = earningsData?.earnings ?? {}
    for (const [sym, date] of Object.entries(earnings)) {
      if (!date) continue
      if (!map[date]) map[date] = []
      map[date].push({ label: sym, type: 'earnings', color: EARNINGS_COLOR })
    }

    return map
  }, [calendarData, earningsData])

  const grid = useMemo(() => buildMonthGrid(viewYear, viewMonth), [viewYear, viewMonth])
  const rows = useMemo(() => {
    const r: Date[][] = []
    for (let i = 0; i < grid.length; i += 7) r.push(grid.slice(i, i + 7))
    return r
  }, [grid])

  const goToday = () => { setViewYear(now.getFullYear()); setViewMonth(now.getMonth()) }
  const goPrev = () => {
    if (viewMonth === 0) { setViewYear(y => y - 1); setViewMonth(11) }
    else setViewMonth(m => m - 1)
  }
  const goNext = () => {
    if (viewMonth === 11) { setViewYear(y => y + 1); setViewMonth(0) }
    else setViewMonth(m => m + 1)
  }

  const handleAdd = () => {
    if (input.trim()) { add(input); setInput('') }
  }

  return (
    <div className="flex flex-col rounded-md overflow-hidden flex-1" style={{ background: 'var(--bg-1)', border: '1px solid var(--line-1)' }}>
      {/* ── Header ── */}
      <div className="flex items-center gap-3 px-4 py-2.5" style={{ borderBottom: '1px solid var(--line-1)' }}>
        <button
          onClick={goToday}
          className="px-2.5 py-1 rounded text-xs font-medium"
          style={{ border: '1px solid var(--line-2)', color: 'var(--fg-1)', background: 'var(--bg-2)' }}
        >
          今天
        </button>
        <button onClick={goPrev} className="p-1 rounded hover:opacity-80" style={{ color: 'var(--fg-2)' }}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M10 4L6 8l4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
        </button>
        <button onClick={goNext} className="p-1 rounded hover:opacity-80" style={{ color: 'var(--fg-2)' }}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M6 4l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
        </button>
        <span className="text-sm font-bold" style={{ color: 'var(--fg-0)' }}>
          {viewYear}年{viewMonth + 1}月
        </span>
        <div className="flex-1" />

        {/* Earnings management */}
        <div className="flex items-center gap-1.5">
          <div className="flex items-center gap-1 flex-wrap max-w-[360px]">
            {symbols.map(sym => (
              <span
                key={sym}
                className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-mono font-medium group cursor-default"
                style={{ background: EARNINGS_COLOR.bg, color: EARNINGS_COLOR.fg }}
              >
                {sym}
                <button
                  onClick={() => remove(sym)}
                  className="opacity-0 group-hover:opacity-100 transition-opacity ml-0.5"
                  style={{ color: 'var(--down)', fontSize: 9 }}
                >
                  ✕
                </button>
              </span>
            ))}
          </div>
          <input
            value={input}
            onChange={e => setInput(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === 'Enter' && handleAdd()}
            placeholder="+ 股票"
            className="w-16 text-[10px] px-1.5 py-0.5 rounded outline-none"
            style={{ background: 'var(--bg-2)', color: 'var(--fg-1)', border: '1px solid var(--line-2)' }}
          />
        </div>
      </div>

      {/* ── Weekday header ── */}
      <div className="grid grid-cols-7" style={{ borderBottom: '1px solid var(--line-1)' }}>
        {WEEKDAYS.map(d => (
          <div key={d} className="px-2 py-1.5 text-center text-[10px] font-medium" style={{ color: 'var(--fg-3)' }}>
            {d}
          </div>
        ))}
      </div>

      {/* ── Grid ── */}
      <div className="flex-1 flex flex-col">
        {rows.map((week, wi) => (
          <div key={wi} className="grid grid-cols-7 flex-1" style={{ borderBottom: wi < rows.length - 1 ? '1px solid var(--line-1)' : undefined }}>
            {week.map((day, di) => {
              const dateStr = toDateStr(day)
              const isCurrentMonth = day.getMonth() === viewMonth
              const isToday = dateStr === todayStr
              const items = eventMap[dateStr] ?? []
              const isWeekend = di === 0 || di === 6

              return (
                <div
                  key={di}
                  className="flex flex-col px-1.5 py-1 min-h-[80px] overflow-hidden"
                  style={{
                    borderRight: di < 6 ? '1px solid var(--line-1)' : undefined,
                    opacity: isCurrentMonth ? 1 : 0.3,
                    background: isToday ? 'var(--accent-dim)' : undefined,
                  }}
                >
                  {/* Day number */}
                  <div className="flex items-center gap-1 mb-0.5">
                    <span
                      className="text-xs font-medium tabular-nums"
                      style={{
                        color: isToday ? 'var(--accent)' : isWeekend ? 'var(--fg-3)' : 'var(--fg-1)',
                        fontWeight: isToday ? 700 : 500,
                      }}
                    >
                      {day.getMonth() !== viewMonth && day.getDate() === 1
                        ? `${day.getMonth() + 1}月${day.getDate()}日`
                        : day.getDate()}
                    </span>
                  </div>

                  {/* Events */}
                  <div className="flex flex-col gap-px overflow-hidden flex-1">
                    {items.slice(0, 3).map((item, idx) => (
                      <div
                        key={idx}
                        className="flex items-center gap-1 px-1 py-px rounded text-[9px] font-medium truncate"
                        style={{ background: item.color.bg, color: item.color.fg }}
                        title={item.label}
                      >
                        {item.type === 'macro' ? (
                          <span className="truncate">{item.label}</span>
                        ) : (
                          <>
                            <span className="opacity-60">📋</span>
                            <span className="truncate">{item.label}</span>
                          </>
                        )}
                      </div>
                    ))}
                    {items.length > 3 && (
                      <div className="text-[9px] px-1" style={{ color: 'var(--fg-3)' }}>
                        +{items.length - 3} 更多
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        ))}
      </div>

      {/* ── Legend ── */}
      <div className="flex items-center gap-4 px-4 py-1.5" style={{ borderTop: '1px solid var(--line-1)' }}>
        {Object.entries(MACRO_COLORS).map(([name, c]) => (
          <div key={name} className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full" style={{ background: c.fg }} />
            <span className="text-[10px]" style={{ color: 'var(--fg-3)' }}>{name}</span>
          </div>
        ))}
        <div className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full" style={{ background: EARNINGS_COLOR.fg }} />
          <span className="text-[10px]" style={{ color: 'var(--fg-3)' }}>财报日期</span>
        </div>
      </div>
    </div>
  )
}

// ─── KPI Cards ──────────────────────────────────────────────────────────────

function MacroKpiCards() {
  const { data: quote_dxy } = useQuery({ queryKey: ['quote', 'DX-Y.NYB'], queryFn: () => import('@/lib/api').then(m => m.getQuote('DX-Y.NYB')), retry: 1 })
  const { data: quote_tnx } = useQuery({ queryKey: ['quote', '^TNX'], queryFn: () => import('@/lib/api').then(m => m.getQuote('^TNX')), retry: 1 })
  const { data: quote_vix } = useQuery({ queryKey: ['quote', '^VIX'], queryFn: () => import('@/lib/api').then(m => m.getQuote('^VIX')), retry: 1 })
  const { data: quote_gld } = useQuery({ queryKey: ['quote', 'GLD'], queryFn: () => import('@/lib/api').then(m => m.getQuote('GLD')), retry: 1 })

  const cards = [
    { label: 'DXY 美元指数', value: quote_dxy?.price, change: quote_dxy?.change_percent, symbol: 'DX-Y.NYB' },
    { label: '10Y 国债收益率', value: quote_tnx?.price, change: quote_tnx?.change_percent, suffix: '%', symbol: '^TNX' },
    { label: 'VIX 恐慌指数', value: quote_vix?.price, change: quote_vix?.change_percent, symbol: '^VIX' },
    { label: 'GLD 黄金ETF', value: quote_gld?.price, change: quote_gld?.change_percent, symbol: 'GLD' },
  ]

  return (
    <div className="rounded-md overflow-hidden" style={{ background: 'var(--bg-1)', border: '1px solid var(--line-1)' }}>
      <div className="px-3 py-2" style={{ borderBottom: '1px solid var(--line-1)' }}>
        <span className="text-xs font-semibold" style={{ color: 'var(--fg-0)' }}>宏观指标</span>
      </div>
      <div className="grid grid-cols-4 gap-px" style={{ background: 'var(--line-1)' }}>
        {cards.map(c => (
          <div key={c.symbol} className="px-3 py-2.5" style={{ background: 'var(--bg-1)' }}>
            <div className="text-[10px] mb-1" style={{ color: 'var(--fg-3)' }}>{c.label}</div>
            <div className="text-base font-bold font-mono tabular-nums" style={{ color: 'var(--fg-0)' }}>
              {c.value != null ? c.value.toFixed(2) : '—'}{c.suffix ?? ''}
            </div>
            {c.change != null && (
              <div className="text-[10px] font-mono tabular-nums" style={{ color: c.change >= 0 ? 'var(--up)' : 'var(--down)' }}>
                {c.change >= 0 ? '+' : ''}{c.change.toFixed(2)}%
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Main Page ──────────────────────────────────────────────────────────────

export default function MacroPage() {
  return (
    <div className="flex flex-col h-full overflow-y-auto p-4 gap-4" style={{ background: 'var(--bg-0)' }}>
      <MacroKpiCards />
      <MonthlyCalendar />
    </div>
  )
}
