'use client'

import { useEffect, useMemo } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { Quote } from '@/types'


function streamUrl(symbols: string[]): string {
  const apiBase = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
  const wsBase = apiBase.replace(/^http:/, 'ws:').replace(/^https:/, 'wss:')
  return `${wsBase}/api/market/stream/hyperliquid?symbols=${encodeURIComponent(symbols.join(','))}`
}


export function useHyperliquidStream(symbols: string[]) {
  const queryClient = useQueryClient()
  const key = useMemo(
    () => Array.from(new Set(symbols.filter(symbol => symbol.toUpperCase().startsWith('HL:')))).sort().join(','),
    [symbols],
  )

  useEffect(() => {
    if (!key) return
    const requested = key.split(',')
    let websocket: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let stopped = false
    let retryMs = 1_000

    const connect = () => {
      websocket = new WebSocket(streamUrl(requested))
      websocket.onopen = () => { retryMs = 1_000 }
      websocket.onmessage = event => {
        try {
          const payload = JSON.parse(event.data) as { timestamp: number; mids: Record<string, number> }
          Object.entries(payload.mids ?? {}).forEach(([symbol, price]) => {
            const updateQuote = (previous: Quote | undefined) => {
              if (!previous) return previous
              const previousClose = previous.price - previous.change
              const change = price - previousClose
              return {
                ...previous,
                price,
                mark_price: price,
                change,
                change_percent: previousClose ? change / previousClose * 100 : 0,
                timestamp: new Date(payload.timestamp).toISOString(),
              }
            }
            queryClient.setQueryData<Quote>(['quote', symbol], updateQuote)
            queryClient.setQueriesData<Quote>({ queryKey: ['quote'] }, previous => {
              if (previous?.provider_symbol?.toUpperCase() !== symbol.toUpperCase()) return previous
              return updateQuote(previous)
            })
          })
        } catch {
          // Ignore malformed frames; the next allMids update is a full replacement.
        }
      }
      websocket.onclose = () => {
        if (stopped) return
        reconnectTimer = setTimeout(connect, retryMs)
        retryMs = Math.min(retryMs * 2, 30_000)
      }
    }

    connect()
    return () => {
      stopped = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      websocket?.close()
    }
  }, [key, queryClient])
}
