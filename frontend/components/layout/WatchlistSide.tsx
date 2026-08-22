'use client'

import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { useRouter, useParams, usePathname } from 'next/navigation'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getWatchlist, deleteWatchlist, getQuotes, addWatchlist, searchSymbols } from '@/lib/api'
import type { WatchlistItem, Quote, SearchResult } from '@/types'

function fmtPrice(v: number): string {
  if (v >= 10000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 })
  if (v >= 100) return v.toFixed(2)
  if (v >= 1) return v.toFixed(4)
  return v.toFixed(6)
}

function WatchRow({ item, quote, active, onRemove }: { item: WatchlistItem; quote?: Quote | null; active: boolean; onRemove: () => void }) {
  const router = useRouter()
  const pathname = usePathname()

  const up = (quote?.change_percent ?? 0) >= 0

  const navigate = () => {
    if (pathname.startsWith('/replay')) {
      router.push(`/replay/${encodeURIComponent(item.symbol)}`)
    } else {
      router.push(`/chart/${encodeURIComponent(item.symbol)}`)
    }
  }

  return (
    <div
      className={`mr-row${active ? ' active' : ''}`}
      onClick={navigate}
      style={{ height: 40, paddingRight: 8 }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span className="mono" style={{ fontWeight: 700, fontSize: 12, color: 'var(--fg-0)' }}>{item.symbol}</span>
        </div>
        <div style={{ fontSize: 10.5, color: 'var(--fg-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {item.name || item.symbol}
        </div>
      </div>

      <div style={{ textAlign: 'right', flexShrink: 0 }}>
        {quote ? (
          <>
            <div className="mono" style={{ fontSize: 11.5, color: 'var(--fg-0)', fontWeight: 600 }}>
              {fmtPrice(quote.price)}
            </div>
            <div className="mono" style={{ fontSize: 10.5, color: up ? 'var(--up)' : 'var(--down)', fontWeight: 600 }}>
              {up ? '+' : ''}{quote.change_percent.toFixed(2)}%
            </div>
          </>
        ) : (
          <div style={{ width: 40, height: 16, background: 'var(--bg-2)', borderRadius: 2, animation: 'pulse 1.5s ease-in-out infinite' }} />
        )}
      </div>

      <button
        onClick={(e) => { e.stopPropagation(); onRemove() }}
        style={{
          width: 20, height: 20, display: 'grid', placeItems: 'center',
          background: 'transparent', border: 0, color: 'var(--fg-3)', borderRadius: 3,
          opacity: 0, transition: 'opacity .1s',
          flexShrink: 0,
        }}
        className="remove-btn"
        title="移除"
      >
        <svg style={{ width: 11, height: 11 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      </button>

      <style>{`.mr-row:hover .remove-btn { opacity: 1; }`}</style>
    </div>
  )
}

function AddSearch({ onAdd }: { onAdd: (s: string, n: string, t: string) => void }) {
  const [val, setVal] = useState('')
  const [open, setOpen] = useState(false)
  const [debQ, setDebQ] = useState('')
  const debRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)

  const handleInput = (v: string) => {
    setVal(v.toUpperCase())
    if (debRef.current) clearTimeout(debRef.current)
    debRef.current = setTimeout(() => setDebQ(v.trim()), 300)
  }

  const { data, isFetching } = useQuery({
    queryKey: ['search', debQ],
    queryFn: () => searchSymbols(debQ),
    enabled: debQ.length > 0,
    staleTime: 60_000,
  })

  const results: SearchResult[] = data?.results ?? []

  const commit = useCallback((sym: string, name: string, type: string) => {
    onAdd(sym, name, type)
    setVal('')
    setDebQ('')
    setOpen(false)
  }, [onAdd])

  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  return (
    <div ref={wrapRef} style={{ padding: '8px 10px', position: 'relative', borderTop: '1px solid var(--line-1)' }}>
      <div style={{ position: 'relative' }}>
        <svg style={{ position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)', width: 12, height: 12, color: 'var(--fg-3)', pointerEvents: 'none' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="11" cy="11" r="7" />
          <path d="M20 20l-3.5-3.5" />
        </svg>
        <input
          className="mr-input"
          style={{ paddingLeft: 26, fontSize: 11.5 }}
          placeholder="添加标的…"
          value={val}
          onChange={e => handleInput(e.target.value)}
          onFocus={() => setOpen(true)}
          onKeyDown={e => {
            if (e.key === 'Enter' && val.trim()) commit(val.trim(), val.trim(), 'stock')
            if (e.key === 'Escape') setOpen(false)
          }}
        />
        {isFetching && (
          <div style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', width: 12, height: 12, border: '1.5px solid var(--accent)', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.6s linear infinite' }} />
        )}
      </div>

      {open && (results.length > 0 || debQ.length > 0) && (
        <div style={{
          position: 'absolute', bottom: '100%', left: 10, right: 10,
          background: 'var(--bg-1)', border: '1px solid var(--line-2)',
          borderRadius: 6, boxShadow: 'var(--shadow-panel)', overflow: 'hidden',
          maxHeight: 240, overflowY: 'auto', zIndex: 50,
        }}>
          {results.slice(0, 6).map(r => (
            <div
              key={`${r.symbol}-${r.exchange}`}
              onMouseDown={e => { e.preventDefault(); commit(r.symbol, r.name, r.type) }}
              style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', cursor: 'pointer', borderBottom: '1px solid var(--line-1)' }}
              className="mr-search-result"
            >
              <span className="mono" style={{ fontWeight: 700, color: 'var(--fg-0)', fontSize: 12, minWidth: 60 }}>{r.display_symbol || r.symbol}</span>
              <span style={{ flex: 1, fontSize: 11, color: 'var(--fg-1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span>
              <span className="chip" style={{ fontSize: 9 }} title="数据来源">{r.source_label}</span>
            </div>
          ))}
          {results.length === 0 && !isFetching && (
            <div style={{ padding: '12px 14px', color: 'var(--fg-2)', fontSize: 11, textAlign: 'center' }}>
              无结果 · 按 Enter 直接添加
            </div>
          )}
        </div>
      )}
    </div>
  )
}

const GROUPS = [
  { label: '股指', types: ['index'] },
  { label: '股票', types: ['stock', 'etf'] },
  { label: '加密', types: ['crypto'] },
]

const groupLabelStyle: React.CSSProperties = {
  padding: '6px 12px 2px',
  fontSize: 9,
  letterSpacing: '0.12em',
  textTransform: 'uppercase',
  color: 'var(--fg-3)',
  fontWeight: 600,
}

export function WatchlistSide() {
  const params = useParams()
  const activeSymbol = params?.symbol ? decodeURIComponent(params.symbol as string) : null
  const queryClient = useQueryClient()

  const { data } = useQuery<{ items: WatchlistItem[] }>({
    queryKey: ['watchlist'],
    queryFn: () => getWatchlist(),
    staleTime: 30_000,
  })
  const items = data?.items ?? []
  const quoteSymbols = useMemo(() => items.map(item => item.symbol), [items])
  const { data: quoteBatch } = useQuery<{ quotes: (Quote | null)[] }>({
    queryKey: ['batch-quotes-watchlist', quoteSymbols.join(',')],
    queryFn: () => getQuotes(quoteSymbols),
    refetchInterval: 60_000,
    staleTime: 60_000,
    retry: 0,
    enabled: quoteSymbols.length > 0,
  })
  const quotesMap = useMemo(() => {
    const map = new Map<string, Quote>()
    quoteBatch?.quotes.forEach((quote, index) => {
      if (quote) map.set(quoteSymbols[index], quote)
    })
    return map
  }, [quoteBatch, quoteSymbols])

  const removeMut = useMutation({
    mutationFn: (id: number) => deleteWatchlist(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
  })

  const addMut = useMutation({
    mutationFn: (body: { symbol: string; name: string; asset_type: string }) => addWatchlist(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
  })

  const handleAdd = async (symbol: string, name: string, type: string) => {
    if (items.some(w => w.symbol === symbol)) return
    await addMut.mutateAsync({ symbol, name, asset_type: type })
  }

  const grouped = GROUPS.map(g => ({
    label: g.label,
    items: items.filter(item => g.types.includes(item.asset_type)),
  })).filter(g => g.items.length > 0)

  const ungrouped = items.filter(
    item => !GROUPS.some(g => g.types.includes(item.asset_type))
  )

  return (
    <aside className="mr-side">
      <div className="panel-header" style={{ border: 0, borderBottom: '1px solid var(--line-1)' }}>
        <span>自选列表</span>
        <span className="mono" style={{ color: 'var(--fg-2)', textTransform: 'none', letterSpacing: 0, fontSize: 11 }}>
          {items.length} 项
        </span>
      </div>

      <div style={{ flex: 1, overflowY: 'auto' }}>
        {items.length === 0 && (
          <div style={{ padding: 14, color: 'var(--fg-3)', fontSize: 11, textAlign: 'center' }}>
            暂无自选
          </div>
        )}

        {grouped.map(group => (
          <div key={group.label}>
            <div style={groupLabelStyle}>{group.label}</div>
            {group.items.map(item => (
              <WatchRow
                key={item.id}
                item={item}
                quote={quotesMap.get(item.symbol)}
                active={item.symbol === activeSymbol}
                onRemove={() => removeMut.mutate(item.id)}
              />
            ))}
          </div>
        ))}

        {ungrouped.map(item => (
          <WatchRow
            key={item.id}
            item={item}
            quote={quotesMap.get(item.symbol)}
            active={item.symbol === activeSymbol}
            onRemove={() => removeMut.mutate(item.id)}
          />
        ))}
      </div>

      <AddSearch onAdd={handleAdd} />
    </aside>
  )
}
