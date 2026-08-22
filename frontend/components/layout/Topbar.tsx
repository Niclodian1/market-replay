'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { useRouter, useParams, usePathname, useSearchParams } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'
import { searchSymbols, getQuote, normalizeMarketDataSource } from '@/lib/api'
import { useAppStore } from '@/store/useAppStore'
import type { SearchResult, Quote, MarketDataSource } from '@/types'

function fmtPrice(v: number): string {
  if (v >= 10000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 })
  if (v >= 100) return v.toFixed(2)
  if (v >= 1) return v.toFixed(4)
  return v.toFixed(6)
}

export function Topbar() {
  const router = useRouter()
  const params = useParams()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const currentSymbol = useAppStore(s => s.currentSymbol)

  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [debQuery, setDebQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const debRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const sym = (params?.symbol as string) ? decodeURIComponent(params.symbol as string) : currentSymbol
  const source = normalizeMarketDataSource(searchParams.get('source'))

  const { data: quote } = useQuery<Quote>({
    queryKey: ['quote', sym, source],
    queryFn: () => getQuote(sym!, source),
    enabled: !!sym,
    refetchInterval: 30_000,
    staleTime: 20_000,
  })

  const { data: searchData, isFetching } = useQuery({
    queryKey: ['search', debQuery],
    queryFn: () => searchSymbols(debQuery),
    enabled: debQuery.length > 0,
    staleTime: 60_000,
  })

  const results: SearchResult[] = searchData?.results ?? []

  const handleInput = (val: string) => {
    setQuery(val)
    if (debRef.current) clearTimeout(debRef.current)
    debRef.current = setTimeout(() => setDebQuery(val.trim()), 280)
  }

  const navigate = useCallback((symbol: string, selectedSource: MarketDataSource = 'auto') => {
    setOpen(false)
    setQuery('')
    setDebQuery('')
    const sourceQuery = selectedSource === 'auto' ? '' : `?source=${selectedSource}`
    if (pathname.startsWith('/replay')) {
      router.push(`/replay/${encodeURIComponent(symbol)}${sourceQuery}`)
    } else {
      router.push(`/chart/${encodeURIComponent(symbol)}${sourceQuery}`)
    }
  }, [router, pathname])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setOpen(true)
        setTimeout(() => inputRef.current?.focus(), 50)
      }
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const up = (quote?.change_percent ?? 0) >= 0

  const routeLabel: Record<string, string> = {
    '/': '总览',
    '/watchlist': '自选',
    '/alerts': '告警',
    '/backtest': '回测',
    '/strategy-overview': '策略总览',
    '/ma-strategies': '均线策略详情',
    '/notes': '笔记',
  }
  const pageLabel = Object.entries(routeLabel).find(([k]) => pathname === k)?.[1]
    ?? (pathname.startsWith('/chart') ? '行情' : pathname.startsWith('/replay') ? '复盘' : '')

  return (
    <>
      <header className="mr-topbar">
        <span className="mr-topbar-title">Market Replay</span>
        {pageLabel && (
          <>
            <div className="mr-topbar-divider" />
            <span style={{ fontSize: 11, color: 'var(--fg-2)' }}>{pageLabel}</span>
          </>
        )}

        {sym && quote && (
          <>
            <div className="mr-topbar-divider" />
            <div className="mr-topbar-ticker">
              <span className="sym mono">{sym}</span>
              <span className="mono" style={{ fontSize: 13, fontWeight: 600 }}>
                {fmtPrice(quote.price)}
              </span>
              <span className="mono" style={{ color: up ? 'var(--up)' : 'var(--down)', fontSize: 11.5 }}>
                {up ? '▲' : '▼'} {up ? '+' : ''}{quote.change_percent.toFixed(2)}%
              </span>
            </div>
          </>
        )}

        <div className="mr-topbar-spacer" />

        <button
          className="btn ghost"
          onClick={() => { setOpen(true); setTimeout(() => inputRef.current?.focus(), 50) }}
          style={{ gap: 8, fontSize: 11, color: 'var(--fg-2)' }}
        >
          <svg className="ico-sm" viewBox="0 0 24 24">
            <circle cx="11" cy="11" r="7" />
            <path d="M20 20l-3.5-3.5" />
          </svg>
          搜索标的
          <span className="kbd">⌘K</span>
        </button>
      </header>

      {open && (
        <div className="mr-search-overlay" onClick={() => setOpen(false)}>
          <div className="mr-search-box fade-in" onClick={e => e.stopPropagation()}>
            <div className="mr-search-input-wrap">
              <svg style={{ width: 16, height: 16, color: 'var(--fg-2)', flexShrink: 0 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="11" cy="11" r="7" />
                <path d="M20 20l-3.5-3.5" />
              </svg>
              <input
                ref={inputRef}
                className="mr-search-input"
                placeholder="搜索股票、ETF、加密货币…"
                value={query}
                onChange={e => handleInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && query.trim()) navigate(query.trim().toUpperCase())
                  if (e.key === 'Escape') setOpen(false)
                }}
                autoComplete="off"
              />
              {isFetching && (
                <div style={{ width: 14, height: 14, border: '1.5px solid var(--accent)', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.6s linear infinite', flexShrink: 0 }} />
              )}
              <span className="kbd">Esc</span>
            </div>

            {results.length > 0 ? (
              <div style={{ maxHeight: 320, overflowY: 'auto' }}>
                {results.slice(0, 8).map((r) => (
                  <div
                    key={`${r.symbol}-${r.exchange}-${r.source}`}
                    className="mr-search-result"
                    onClick={() => navigate(r.symbol, normalizeMarketDataSource(r.source))}
                  >
                    <span className="mono" style={{ fontWeight: 700, color: 'var(--fg-0)', fontSize: 13, minWidth: 80 }}>{r.display_symbol || r.symbol}</span>
                    <span style={{ color: 'var(--fg-1)', fontSize: 12, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span>
                    <span className="chip" style={{ fontSize: 9 }} title="数据来源">{r.source_label}</span>
                    <span className="chip" style={{ fontSize: 9 }}>{r.type}</span>
                  </div>
                ))}
              </div>
            ) : debQuery.length > 0 && !isFetching ? (
              <div style={{ padding: '20px 14px', textAlign: 'center', color: 'var(--fg-2)', fontSize: 12 }}>
                无结果 · 按 Enter 直接跳转 <span className="mono" style={{ color: 'var(--fg-0)' }}>{query.toUpperCase()}</span>
              </div>
            ) : (
              <div style={{ padding: '16px 14px' }}>
                <div className="section-title" style={{ marginBottom: 8 }}>热门标的</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {['AAPL', 'NVDA', 'TSLA', 'MSFT', 'SPY', 'QQQ', 'BTC-USD', 'ETH-USD'].map(s => (
                    <button key={s} className="btn small" onClick={() => navigate(s)} style={{ fontFamily: 'var(--font-mono)' }}>{s}</button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </>
  )
}
