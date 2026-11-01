'use client'

import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import Link from 'next/link'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getWatchlist, getWatchlistInsights, addWatchlist, deleteWatchlist, searchSymbols, getQuote } from '@/lib/api'
import type { WatchlistItem, WatchlistInsight, WatchlistInsightsResponse, Quote, SearchResult } from '@/types'
import { useHyperliquidStream } from '@/hooks/useHyperliquidStream'

// ── Formatters ─────────────────────────────────────────────────────────────────

function fmtPrice(v: number): string {
  if (v >= 10000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 })
  if (v >= 100)   return v.toFixed(2)
  if (v >= 1)     return v.toFixed(4)
  return v.toFixed(6)
}

function fmtCompact(v: number): string {
  if (!v || isNaN(v)) return '—'
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`
  if (v >= 1e9)  return `$${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6)  return `$${(v / 1e6).toFixed(2)}M`
  return `$${v.toLocaleString()}`
}

// ── Symbol Icon ────────────────────────────────────────────────────────────────

const ICON_COLORS: Record<string, string> = {
  B: '#f7931a', E: '#627eea', S: '#00c2a8', A: '#0066cc',
  N: '#7b61ff', M: '#e91e63', T: '#26a17b', D: '#ff6b35',
  G: '#34a853', U: '#2196f3', X: '#1da462', Q: '#ff9800',
}

function SymbolIcon({ symbol }: { symbol: string }) {
  const letter = symbol.replace(/[^A-Z]/g, '')[0] ?? symbol[0]?.toUpperCase() ?? '?'
  const bg = ICON_COLORS[letter] ?? '#5aa7ff'
  return (
    <div style={{
      width: 36, height: 36, borderRadius: '50%',
      background: bg, display: 'grid', placeItems: 'center',
      flexShrink: 0, fontWeight: 800, fontSize: 14,
      color: '#fff', fontFamily: 'var(--font-mono)',
      boxShadow: `0 2px 8px ${bg}55`,
    }}>
      {letter}
    </div>
  )
}

// ── Period Selector ────────────────────────────────────────────────────────────

const PERIODS = ['1H', '24H', '7D', '30D']

function PeriodSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div style={{ display: 'flex', gap: 2, background: 'var(--bg-3)', borderRadius: 6, padding: 2 }}>
      {PERIODS.map(p => (
        <button
          key={p}
          onClick={() => onChange(p)}
          style={{
            padding: '2px 8px', border: 0, borderRadius: 4, cursor: 'pointer',
            fontSize: 10.5, fontWeight: 600, fontFamily: 'var(--font-mono)',
            background: value === p ? 'var(--bg-1)' : 'transparent',
            color: value === p ? 'var(--fg-0)' : 'var(--fg-3)',
            transition: 'all .15s',
            boxShadow: value === p ? '0 1px 3px rgba(0,0,0,.3)' : 'none',
          }}
        >
          {p}
        </button>
      ))}
    </div>
  )
}

// ── Skeleton Cell ──────────────────────────────────────────────────────────────

function SkeletonCell({ w = 60 }: { w?: number }) {
  return <div style={{ width: w, height: 16, background: 'var(--bg-3)', borderRadius: 3, animation: 'pulse 1.4s ease-in-out infinite' }} />
}

// ── Table Row ──────────────────────────────────────────────────────────────────

const STATE_LABEL = {
  bullish: { label: '看涨', color: 'var(--up)', background: 'var(--up-bg)' },
  bearish: { label: '看跌', color: 'var(--down)', background: 'var(--down-bg)' },
  sideways: { label: '震荡', color: '#f59e0b', background: 'rgba(245, 158, 11, .12)' },
} as const

const OPPORTUNITY_LABEL = { A: '突破', B: '回调', C: '多均线' } as const
const OPPORTUNITY_COLOR = { A: '#22c55e', B: '#f59e0b', C: '#8b5cf6' } as const

function WatchRow({
  item,
  insight,
  insightsLoading,
  insightsFailed,
  onRemove,
}: {
  item: WatchlistItem
  insight?: WatchlistInsight
  insightsLoading: boolean
  insightsFailed: boolean
  onRemove: () => void
}) {
  const [confirming, setConfirming] = useState(false)

  const { data: quote, isLoading } = useQuery<Quote>({
    queryKey: ['quote', item.symbol],
    queryFn: () => getQuote(item.symbol),
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: 2,
  })

  const pct = quote?.change_percent ?? 0
  const up = pct >= 0
  const hasOpportunity = (insight?.opportunities.length ?? 0) > 0
  const insightUnavailable = insightsFailed || Boolean(insight?.error) || (!insightsLoading && !insight)

  const prevPrice = useRef<number | null>(null)
  const [flashClass, setFlashClass] = useState('')

  useEffect(() => {
    if (quote?.price == null) return
    if (prevPrice.current !== null && prevPrice.current !== quote.price) {
      const cls = quote.price > prevPrice.current ? 'flash-up' : 'flash-down'
      setFlashClass('')
      requestAnimationFrame(() => requestAnimationFrame(() => setFlashClass(cls)))
      const t = setTimeout(() => setFlashClass(''), 850)
      prevPrice.current = quote.price
      return () => clearTimeout(t)
    }
    prevPrice.current = quote.price
  }, [quote?.price])

  return (
    <tr className={hasOpportunity ? 'opportunity-row' : ''} style={{ borderBottom: '1px solid var(--line-1)' }}
      onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-2)')}
      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
    >
      {/* Name */}
      <td style={{ padding: '12px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <SymbolIcon symbol={item.symbol} />
          <div>
            <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--fg-0)', fontFamily: 'var(--font-mono)' }}>
              {item.symbol}
            </div>
            <div style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 2, maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {item.name || item.symbol}
            </div>
          </div>
        </div>
      </td>

      {/* Current state */}
      <td style={{ padding: '12px 16px' }}>
        {insightsLoading ? <SkeletonCell w={58} /> : insightUnavailable ? (
          <span style={{ color: 'var(--fg-3)', fontSize: 11 }}>暂不可用</span>
        ) : insight?.current_state ? (
          <span style={{
            display: 'inline-flex', alignItems: 'center', borderRadius: 999, padding: '3px 8px',
            color: STATE_LABEL[insight.current_state].color,
            background: STATE_LABEL[insight.current_state].background,
            fontSize: 11, fontWeight: 700,
          }}>
            {STATE_LABEL[insight.current_state].label}
          </span>
        ) : (
          <span style={{ color: 'var(--fg-3)', fontSize: 11 }}>待计算</span>
        )}
      </td>

      {/* MA opportunities */}
      <td style={{ padding: '12px 16px', minWidth: 190 }}>
        {insightsLoading ? <SkeletonCell w={110} /> : insightUnavailable ? (
          <span style={{ color: 'var(--fg-3)', fontSize: 11 }}>暂不可用</span>
        ) : hasOpportunity ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap' }}>
            {insight!.opportunities.map(opportunity => {
              const params = opportunity.strategy === 'C'
                ? opportunity.parameters.join('/')
                : opportunity.parameters.map(p => `MA${p}`).join('/')
              return (
                <span
                  key={`${opportunity.strategy}-${opportunity.parameters.join('-')}`}
                  title={`策略${opportunity.strategy} · ${OPPORTUNITY_LABEL[opportunity.strategy]} · ${params}`}
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: 4,
                    border: `1px solid ${OPPORTUNITY_COLOR[opportunity.strategy]}55`,
                    background: `${OPPORTUNITY_COLOR[opportunity.strategy]}16`,
                    color: OPPORTUNITY_COLOR[opportunity.strategy],
                    borderRadius: 5, padding: '3px 6px', fontSize: 10.5, fontWeight: 700,
                    whiteSpace: 'nowrap',
                  }}
                >
                  {opportunity.strategy} · {OPPORTUNITY_LABEL[opportunity.strategy]}
                  <span className="mono" style={{ opacity: .78, fontSize: 9.5 }}>{params}</span>
                </span>
              )
            })}
          </div>
        ) : (
          <span style={{ color: 'var(--fg-3)', fontSize: 12 }}>—</span>
        )}
      </td>

      {/* Price */}
      <td className={flashClass} style={{ padding: '12px 16px' }}>
        {isLoading ? <SkeletonCell w={80} /> : quote ? (
          <div>
            <div style={{ fontWeight: 700, fontSize: 14, color: 'var(--fg-0)', fontFamily: 'var(--font-mono)' }}>
              {fmtPrice(quote.price)}
            </div>
            <div style={{ fontSize: 10.5, color: 'var(--fg-3)', marginTop: 2 }}>USD</div>
          </div>
        ) : <span style={{ color: 'var(--fg-3)', fontSize: 12 }}>—</span>}
      </td>

      {/* Change % */}
      <td style={{ padding: '12px 16px' }}>
        {isLoading ? <SkeletonCell w={50} /> : quote ? (
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: 4,
            background: up ? 'var(--up-bg)' : 'var(--down-bg)',
            color: up ? 'var(--up)' : 'var(--down)',
            borderRadius: 6, padding: '3px 8px',
            fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 12,
          }}>
            <svg style={{ width: 10, height: 10, transform: up ? 'none' : 'rotate(180deg)' }} viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 4l8 12H4z" />
            </svg>
            {up ? '+' : ''}{pct.toFixed(2)}%
          </div>
        ) : <span style={{ color: 'var(--fg-3)', fontSize: 12 }}>—</span>}
      </td>

      {/* 24h Volume */}
      <td style={{ padding: '12px 16px' }}>
        {isLoading ? <SkeletonCell w={70} /> : quote ? (
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--fg-1)', fontWeight: 500 }}>
            {fmtCompact(quote.volume * quote.price)}
          </div>
        ) : <span style={{ color: 'var(--fg-3)', fontSize: 12 }}>—</span>}
      </td>

      {/* Market Cap */}
      <td style={{ padding: '12px 16px' }}>
        {isLoading ? <SkeletonCell w={70} /> : quote?.market_cap ? (
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--fg-1)', fontWeight: 500 }}>
            {fmtCompact(quote.market_cap)}
          </div>
        ) : <span style={{ color: 'var(--fg-3)', fontSize: 12 }}>—</span>}
      </td>

      {/* Actions */}
      <td style={{ padding: '12px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Link
            href={`/chart/${encodeURIComponent(item.symbol)}`}
            title="查看图表"
            style={{
              width: 32, height: 32, display: 'grid', placeItems: 'center',
              background: 'var(--bg-3)', border: '1px solid var(--line-2)',
              borderRadius: 6, color: 'var(--fg-2)', textDecoration: 'none',
              transition: 'all .15s',
            }}
            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--accent)'; (e.currentTarget as HTMLElement).style.borderColor = 'var(--accent-line)' }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--fg-2)'; (e.currentTarget as HTMLElement).style.borderColor = 'var(--line-2)' }}
          >
            <svg style={{ width: 14, height: 14 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <path d="M3 9h18M9 21V9" />
              <path d="M7 14l2.5-2.5 2 2L15 10" />
            </svg>
          </Link>

          <Link
            href={`/replay/${encodeURIComponent(item.symbol)}`}
            title="复盘"
            style={{
              width: 32, height: 32, display: 'grid', placeItems: 'center',
              background: 'var(--bg-3)', border: '1px solid var(--line-2)',
              borderRadius: 6, color: 'var(--fg-2)', textDecoration: 'none',
              transition: 'all .15s',
            }}
            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--info)'; (e.currentTarget as HTMLElement).style.borderColor = 'rgba(90,167,255,.4)' }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--fg-2)'; (e.currentTarget as HTMLElement).style.borderColor = 'var(--line-2)' }}
          >
            <svg style={{ width: 14, height: 14 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polygon points="5 3 19 12 5 21 5 3" />
            </svg>
          </Link>

          {confirming ? (
            <>
              <button
                onClick={onRemove}
                style={{ height: 32, padding: '0 10px', background: 'var(--down-bg)', border: '1px solid var(--down)', borderRadius: 6, color: 'var(--down)', fontSize: 11, fontWeight: 600, cursor: 'pointer' }}
              >
                确认
              </button>
              <button
                onClick={() => setConfirming(false)}
                style={{ height: 32, padding: '0 10px', background: 'transparent', border: '1px solid var(--line-2)', borderRadius: 6, color: 'var(--fg-3)', fontSize: 11, cursor: 'pointer' }}
              >
                取消
              </button>
            </>
          ) : (
            <button
              onClick={() => setConfirming(true)}
              title="从自选移除"
              style={{
                width: 32, height: 32, display: 'grid', placeItems: 'center',
                background: 'transparent', border: '1px solid transparent',
                borderRadius: 6, color: 'var(--fg-3)', cursor: 'pointer',
                transition: 'all .15s',
              }}
              onMouseEnter={e => { e.currentTarget.style.color = 'var(--down)'; e.currentTarget.style.background = 'var(--down-bg)' }}
              onMouseLeave={e => { e.currentTarget.style.color = 'var(--fg-3)'; e.currentTarget.style.background = 'transparent' }}
            >
              <svg style={{ width: 13, height: 13 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />
              </svg>
            </button>
          )}
        </div>
      </td>
    </tr>
  )
}

// ── Add Search ─────────────────────────────────────────────────────────────────

function AddSearch({ onAdd }: { onAdd: (sym: string, name: string, type: string) => void }) {
  const [query, setQuery]   = useState('')
  const [open, setOpen]     = useState(false)
  const [debQ, setDebQ]     = useState('')
  const wrapperRef  = useRef<HTMLDivElement>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleInput = (val: string) => {
    const v = val.toUpperCase()
    setQuery(v)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => setDebQ(v.trim()), 300)
  }

  const { data, isFetching } = useQuery({
    queryKey: ['search', debQ],
    queryFn: () => searchSymbols(debQ),
    enabled: debQ.length > 0,
    staleTime: 60_000,
  })
  const results: SearchResult[] = data?.results ?? []

  const select = (item: SearchResult) => {
    onAdd(item.symbol, item.name, item.type)
    setQuery('')
    setDebQ('')
    setOpen(false)
  }

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  return (
    <div ref={wrapperRef} style={{ position: 'relative', width: 280 }}>
      <div style={{ position: 'relative' }}>
        <svg style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', width: 13, height: 13, color: 'var(--fg-3)', pointerEvents: 'none' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" />
        </svg>
        <input
          className="mr-input mono"
          style={{ paddingLeft: 30, width: '100%', height: 36, fontSize: 12 }}
          placeholder="搜索添加标的…"
          value={query}
          onChange={e => handleInput(e.target.value)}
          onFocus={() => setOpen(true)}
          onKeyDown={e => e.key === 'Escape' && setOpen(false)}
          autoComplete="off"
        />
        {isFetching && (
          <div style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', width: 13, height: 13, border: '1.5px solid var(--accent)', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.7s linear infinite' }} />
        )}
      </div>

      {open && results.length > 0 && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 6px)', left: 0, right: 0,
          background: 'var(--bg-1)', border: '1px solid var(--line-2)',
          borderRadius: 8, boxShadow: 'var(--shadow-panel)', zIndex: 60,
          maxHeight: 280, overflowY: 'auto',
        }}>
          {results.map(item => (
            <div
              key={`${item.symbol}-${item.exchange}`}
              style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px', cursor: 'pointer', borderBottom: '1px solid var(--line-0)' }}
              onMouseDown={e => { e.preventDefault(); select(item) }}
              onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-2)')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            >
              <SymbolIcon symbol={item.symbol} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 700, color: 'var(--fg-0)', fontSize: 12, fontFamily: 'var(--font-mono)' }}>{item.display_symbol || item.symbol}</div>
                <div style={{ fontSize: 10.5, color: 'var(--fg-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: 1 }}>{item.name}</div>
              </div>
              <span className="chip" style={{ fontSize: 9, flexShrink: 0 }} title="数据来源">{item.source_label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Main Page ──────────────────────────────────────────────────────────────────

const COL_HEADERS = [
  { label: '名称',     align: 'left'  },
  { label: '当前状态', align: 'left'  },
  { label: '交易机会', align: 'left'  },
  { label: '价格',     align: 'left'  },
  { label: '涨跌幅',   align: 'left'  },
  { label: '24H 成交额', align: 'left' },
  { label: '市值',     align: 'left'  },
  { label: '操作',     align: 'left'  },
]

export default function WatchlistPage() {
  const queryClient = useQueryClient()
  const [period, setPeriod] = useState('24H')

  const { data, isLoading, isError } = useQuery({
    queryKey: ['watchlist'],
    queryFn: getWatchlist,
    refetchInterval: 60_000,
    staleTime: 30_000,
  })

  const addMutation = useMutation({
    mutationFn: ({ symbol, name, asset_type }: { symbol: string; name: string; asset_type: string }) =>
      addWatchlist({ symbol, name, asset_type }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteWatchlist(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
  })

  const items: WatchlistItem[] = data?.items ?? []
  useHyperliquidStream(items.map(item => item.symbol))
  const symbolsKey = useMemo(() => items.map(item => item.symbol).join(','), [items])
  const insightsQuery = useQuery<WatchlistInsightsResponse>({
    queryKey: ['watchlist-insights', symbolsKey],
    queryFn: getWatchlistInsights,
    enabled: items.length > 0,
    refetchInterval: 300_000,
    staleTime: 300_000,
    retry: 1,
  })
  const insightBySymbol = useMemo(
    () => new Map((insightsQuery.data?.items ?? []).map(item => [item.symbol, item])),
    [insightsQuery.data],
  )

  return (
    <div className="page">
      <div className="page-inner" style={{ maxWidth: '100%' }}>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
          <div>
            <div className="section-title">自选列表</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, color: 'var(--fg-0)', display: 'flex', alignItems: 'baseline', gap: 8 }}>
              {data ? items.length : '…'}
              <span style={{ fontSize: 13, fontWeight: 400, color: 'var(--fg-3)' }}>个标的</span>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <PeriodSelect value={period} onChange={setPeriod} />
            <AddSearch onAdd={(symbol, name, type) => addMutation.mutate({ symbol, name, asset_type: type })} />
            {addMutation.isPending && (
              <div style={{ width: 16, height: 16, border: '2px solid var(--accent)', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.7s linear infinite' }} />
            )}
          </div>
        </div>

        {/* Table */}
        <div style={{ background: 'var(--bg-1)', border: '1px solid var(--line-1)', borderRadius: 10, overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', minWidth: 1120, borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--line-1)' }}>
                {COL_HEADERS.map((col, i) => (
                  <th
                    key={i}
                    style={{
                      padding: '10px 16px',
                      textAlign: col.align as 'left' | 'right',
                      fontSize: 10.5,
                      fontWeight: 600,
                      color: 'var(--fg-3)',
                      letterSpacing: '0.06em',
                      textTransform: 'uppercase',
                      whiteSpace: 'nowrap',
                      background: 'var(--bg-2)',
                    }}
                  >
                    {col.label}
                    {i === 4 && (
                      <span style={{
                        marginLeft: 6,
                        fontSize: 9,
                        fontWeight: 700,
                        color: 'var(--accent)',
                        background: 'var(--accent-dim)',
                        borderRadius: 4,
                        padding: '1px 5px',
                        letterSpacing: 0,
                        textTransform: 'none',
                      }}>
                        {period}
                      </span>
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr>
                  <td colSpan={8} style={{ height: 160, textAlign: 'center' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12, color: 'var(--fg-2)' }}>
                      <div style={{ width: 22, height: 22, border: '2px solid var(--accent)', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.7s linear infinite' }} />
                      <span className="mono" style={{ fontSize: 12 }}>加载自选…</span>
                    </div>
                  </td>
                </tr>
              )}

              {isError && (
                <tr>
                  <td colSpan={8} style={{ height: 140, textAlign: 'center', color: 'var(--down)', fontSize: 13 }}>
                    加载失败，请检查后端连接
                  </td>
                </tr>
              )}

              {!isLoading && !isError && items.length === 0 && (
                <tr>
                  <td colSpan={8}>
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: 200, gap: 12 }}>
                      <svg style={{ width: 40, height: 40, color: 'var(--fg-3)' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
                      </svg>
                      <div style={{ textAlign: 'center' }}>
                        <div style={{ fontSize: 13, color: 'var(--fg-2)' }}>自选列表为空</div>
                        <div style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 4 }}>使用右上角搜索框添加你关注的标的</div>
                      </div>
                    </div>
                  </td>
                </tr>
              )}

              {items.map(item => (
                <WatchRow
                  key={item.id}
                  item={item}
                  insight={insightBySymbol.get(item.symbol)}
                  insightsLoading={insightsQuery.isLoading}
                  insightsFailed={insightsQuery.isError}
                  onRemove={() => deleteMutation.mutate(item.id)}
                />
              ))}
            </tbody>
            </table>
          </div>
        </div>

        {items.length > 0 && (
          <div style={{ marginTop: 10, fontSize: 11, color: 'var(--fg-3)', textAlign: 'right' }}>
            价格每 60 秒更新 · MA 机会每 5 分钟扫描
          </div>
        )}
      </div>

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .4; } }
        @keyframes opportunity-pulse {
          0%, 100% { background-color: rgba(34, 197, 94, .035); box-shadow: inset 3px 0 rgba(34, 197, 94, .45); }
          50% { background-color: rgba(34, 197, 94, .13); box-shadow: inset 3px 0 rgba(34, 197, 94, .95); }
        }
        .opportunity-row { animation: opportunity-pulse 1.8s ease-in-out infinite; }
        @media (prefers-reduced-motion: reduce) {
          .opportunity-row { animation: none; background-color: rgba(34, 197, 94, .08); box-shadow: inset 3px 0 rgba(34, 197, 94, .75); }
        }
      `}</style>
    </div>
  )
}
