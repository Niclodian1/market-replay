'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'
import { searchSymbols } from '@/lib/api'
import type { SearchResult } from '@/types'

const TYPE_LABELS: Record<string, { label: string; color: string }> = {
  stock:  { label: '股票', color: 'text-blue-400'   },
  etf:    { label: 'ETF',  color: 'text-purple-400' },
  index:  { label: '指数', color: 'text-yellow-400' },
  crypto: { label: '加密', color: 'text-orange-400' },
}

export function SymbolSearch() {
  const router = useRouter()
  const [input, setInput] = useState('')
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [highlighted, setHighlighted] = useState(-1)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleInput = (val: string) => {
    setInput(val)
    setHighlighted(-1)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setQuery(val.trim())
      setOpen(val.trim().length > 0)
    }, 300)
  }

  const { data, isFetching } = useQuery({
    queryKey: ['search', query],
    queryFn: () => searchSymbols(query),
    enabled: query.length > 0,
    staleTime: 60_000,
  })

  const results: SearchResult[] = data?.results ?? []

  const select = useCallback(
    (symbol: string) => {
      setInput('')
      setQuery('')
      setOpen(false)
      setHighlighted(-1)
      router.push(`/chart/${encodeURIComponent(symbol)}`)
    },
    [router]
  )

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlighted(i => Math.min(i + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlighted(i => Math.max(i - 1, -1))
    } else if (e.key === 'Enter') {
      if (highlighted >= 0 && results[highlighted]) {
        select(results[highlighted].symbol)
      } else if (input.trim()) {
        select(input.trim().toUpperCase())
      }
    } else if (e.key === 'Escape') {
      setOpen(false)
      inputRef.current?.blur()
    }
  }

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  return (
    <div ref={wrapperRef} className="relative w-full max-w-sm">
      <div className="relative">
        {/* Search icon */}
        <svg
          className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-500 pointer-events-none"
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M21 21l-4.35-4.35M17 11A6 6 0 1 1 5 11a6 6 0 0 1 12 0z" />
        </svg>
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={(e) => handleInput(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => input.trim() && setOpen(true)}
          placeholder="搜索标的：AAPL、BTC-USD..."
          className="w-full bg-gray-800 border border-gray-700 rounded-md pl-8 pr-3 py-1.5 text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30 transition-all"
        />
        {isFetching && (
          <div className="absolute right-2.5 top-1/2 -translate-y-1/2">
            <div className="w-3.5 h-3.5 border border-blue-500 border-t-transparent rounded-full animate-spin" />
          </div>
        )}
      </div>

      {open && (
        <div className="absolute top-full left-0 right-0 mt-1 bg-gray-900 border border-gray-700 rounded-lg shadow-2xl z-50 max-h-72 overflow-y-auto">
          {!isFetching && results.length === 0 && query && (
            <div className="px-3 py-3 text-xs text-gray-500 text-center">
              无匹配结果 · 按回车直接跳转 <span className="font-mono text-gray-400">{query.toUpperCase()}</span>
            </div>
          )}
          {results.map((item, idx) => {
            const typeInfo = TYPE_LABELS[item.type] ?? { label: item.type, color: 'text-gray-400' }
            return (
              <button
                key={`${item.symbol}-${item.exchange}`}
                onClick={() => select(item.symbol)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors border-b border-gray-800/50 last:border-0 ${
                  idx === highlighted ? 'bg-gray-800' : 'hover:bg-gray-800/70'
                }`}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-semibold text-sm text-gray-100">{item.symbol}</span>
                    <span className={`text-xs ${typeInfo.color}`}>{typeInfo.label}</span>
                  </div>
                  <div className="text-xs text-gray-500 truncate mt-0.5">{item.name}</div>
                </div>
                <div className="shrink-0 text-xs text-gray-600">{item.exchange}</div>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
