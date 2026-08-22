'use client'

import { useState, useRef, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { searchSymbols, getWatchlist } from '@/lib/api'
import type { WatchlistItem, SearchResult } from '@/types'

const DEFAULT_PRESETS = [
  { group: '指数',    items: ['^GSPC', '^NDX', '^VIX', '^DJI'] },
  { group: '美股',    items: ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'TSLA', 'META'] },
  { group: 'ETF',     items: ['SPY', 'QQQ', 'GLD', 'TLT'] },
  { group: '加密货币', items: ['BTC-USD', 'ETH-USD', 'SOL-USD', 'BNB-USD'] },
  { group: '币安永续', items: ['BINANCE:BTCUSDT', 'BINANCE:ETHUSDT', 'BINANCE:AAPLUSDT'] },
]

interface SymbolComboboxProps {
  value: string
  onChange: (symbol: string) => void
  enableSearch?: boolean
  presets?: { group: string; items: string[] }[]
  placeholder?: string
}

export function SymbolCombobox({
  value, onChange,
  enableSearch = true,
  presets = DEFAULT_PRESETS,
  placeholder = 'AAPL / BTC-USD',
}: SymbolComboboxProps) {
  const [open, setOpen]     = useState(false)
  const [query, setQuery]   = useState('')
  const [input, setInput]   = useState(value)
  const wrapperRef  = useRef<HTMLDivElement>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const { data: watchlistData } = useQuery<{ items: WatchlistItem[] }>({
    queryKey: ['watchlist'],
    queryFn: () => getWatchlist(),
    staleTime: 60_000,
  })
  const watchlist = watchlistData?.items ?? []

  const handleInput = (val: string) => {
    const v = val.toUpperCase()
    setInput(v)
    onChange(v)
    if (enableSearch) {
      if (debounceRef.current) clearTimeout(debounceRef.current)
      debounceRef.current = setTimeout(() => setQuery(v.trim()), 300)
    }
  }

  const { data: searchData, isFetching } = useQuery({
    queryKey: ['search', query],
    queryFn: () => searchSymbols(query),
    enabled: enableSearch && query.length > 0,
    staleTime: 60_000,
  })
  const results: SearchResult[] = searchData?.results ?? []

  const select = (sym: string) => { setInput(sym); onChange(sym); setOpen(false); setQuery('') }

  useEffect(() => { setInput(value) }, [value])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const showSearch = enableSearch && query.length > 0
  const lc = input.toLowerCase()
  const filteredWL = watchlist.filter(w => w.symbol.toLowerCase().includes(lc))
  const filteredPresets = presets.map(g => ({ ...g, items: g.items.filter(s => s.toLowerCase().includes(lc)) })).filter(g => g.items.length > 0)

  const dropStyle: React.CSSProperties = {
    position: 'absolute', top: 'calc(100% + 4px)', left: 0, right: 0,
    background: 'var(--bg-1)', border: '1px solid var(--line-1)', borderRadius: 'var(--radius-lg, 6px)',
    boxShadow: '0 8px 24px rgba(0,0,0,.25)', zIndex: 60,
    maxHeight: 240, overflowY: 'auto',
  }

  const groupHdStyle: React.CSSProperties = {
    padding: '4px 10px', fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase',
    color: 'var(--fg-3)', fontWeight: 600, background: 'var(--bg-2)', position: 'sticky', top: 0,
  }

  const itemStyle: React.CSSProperties = {
    display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px',
    cursor: 'pointer', fontSize: 11.5,
  }

  return (
    <div ref={wrapperRef} style={{ position: 'relative' }}>
      <div style={{ position: 'relative' }}>
        <input
          className="mr-input mono"
          style={{ width: '100%', paddingRight: isFetching ? 28 : undefined }}
          placeholder={placeholder}
          value={input}
          onChange={e => handleInput(e.target.value)}
          onFocus={() => setOpen(true)}
          onBlur={() => {
            if (!enableSearch) {
              const s = input.trim().toUpperCase()
              if (s) onChange(s)
            }
          }}
          onKeyDown={e => {
            if (e.key === 'Escape') setOpen(false)
            if (e.key === 'Enter' && !enableSearch) {
              const s = input.trim().toUpperCase()
              if (s) onChange(s)
              setOpen(false)
            }
          }}
          autoComplete="off"
        />
        {enableSearch && isFetching && (
          <div style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', width: 12, height: 12, border: '1.5px solid var(--accent)', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.7s linear infinite' }} />
        )}
      </div>
      {open && (
        <div style={dropStyle}>
          {!showSearch && filteredWL.length > 0 && (
            <div>
              <div style={groupHdStyle}>自选列表</div>
              {filteredWL.map(item => (
                <div key={item.id} style={itemStyle}
                  onMouseDown={e => { e.preventDefault(); select(item.symbol) }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-2)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                  <span className="mono" style={{ fontWeight: 600, color: 'var(--fg-0)' }}>{item.symbol}</span>
                  <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>{item.name}</span>
                </div>
              ))}
            </div>
          )}
          {!showSearch && filteredPresets.map(group => (
            <div key={group.group}>
              <div style={groupHdStyle}>{group.group}</div>
              {group.items.map(sym => (
                <div key={sym} style={itemStyle}
                  onMouseDown={e => { e.preventDefault(); select(sym) }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-2)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                  <span className="mono" style={{ color: 'var(--fg-0)' }}>{sym}</span>
                </div>
              ))}
            </div>
          ))}
          {showSearch && !isFetching && results.length === 0 && (
            <div style={{ padding: '12px 10px', textAlign: 'center', fontSize: 11, color: 'var(--fg-3)' }}>
              无结果
            </div>
          )}
          {showSearch && results.map(item => (
            <div key={`${item.symbol}-${item.exchange}`} style={{ ...itemStyle, justifyContent: 'space-between', borderBottom: '1px solid var(--line-0, var(--line-1))' }}
              onMouseDown={e => { e.preventDefault(); select(item.symbol) }}
              onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-2)')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
              <div style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span className="mono" style={{ fontWeight: 700, color: 'var(--fg-0)', fontSize: 12 }}>{item.display_symbol || item.symbol}</span>
                  <span style={{ fontSize: 9, color: 'var(--fg-3)' }}>{item.type}</span>
                </div>
                <div style={{ fontSize: 10, color: 'var(--fg-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.name}</div>
              </div>
              <span className="chip" style={{ fontSize: 9, flexShrink: 0 }} title="数据来源">{item.source_label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
