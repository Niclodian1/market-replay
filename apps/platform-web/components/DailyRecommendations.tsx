'use client'

import Link from 'next/link'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { addWatchlist, getWatchlistRecommendations } from '@/lib/api'
import type { WatchlistRecommendation, WatchlistRecommendationsResponse } from '@/types'


function fmtPrice(value: number): string {
  if (value >= 10_000) return value.toLocaleString(undefined, { maximumFractionDigits: 0 })
  if (value >= 100) return value.toFixed(2)
  if (value >= 1) return value.toFixed(4)
  return value.toFixed(6)
}


function fmtNotional(value: number): string {
  if (value >= 1e9) return `$${(value / 1e9).toFixed(1)}B`
  if (value >= 1e6) return `$${(value / 1e6).toFixed(1)}M`
  if (value >= 1e3) return `$${(value / 1e3).toFixed(1)}K`
  return value > 0 ? `$${value.toFixed(0)}` : '—'
}


function RecommendationCard({
  item,
  adding,
  onAdd,
}: {
  item: WatchlistRecommendation
  adding: boolean
  onAdd: () => void
}) {
  const up = item.change_percent >= 0
  return (
    <div style={{ padding: 14, borderRight: '1px solid var(--line-1)', borderBottom: '1px solid var(--line-1)', minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
            <Link
              href={`/chart/${encodeURIComponent(item.symbol)}`}
              className="mono"
              style={{ color: 'var(--fg-0)', fontSize: 13, fontWeight: 800, textDecoration: 'none' }}
            >
              {item.symbol}
            </Link>
            <span className="chip" style={{ fontSize: 9 }}>{item.market_label}</span>
          </div>
          <div style={{ color: 'var(--fg-3)', fontSize: 10.5, marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {item.name}
          </div>
        </div>
        <button
          className="btn small"
          disabled={adding}
          onClick={onAdd}
          style={{ flexShrink: 0, opacity: adding ? .65 : 1 }}
        >
          {adding ? '加入中…' : '+ 自选'}
        </button>
      </div>

      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 12 }}>
        <span className="mono" style={{ color: 'var(--fg-0)', fontSize: 15, fontWeight: 700 }}>{fmtPrice(item.price)}</span>
        <span className="mono" style={{ color: up ? 'var(--up)' : 'var(--down)', fontSize: 11, fontWeight: 700 }}>
          {up ? '+' : ''}{item.change_percent.toFixed(2)}%
        </span>
        <span className="mono" style={{ marginLeft: 'auto', color: 'var(--fg-3)', fontSize: 10 }}>
          成交额 {fmtNotional(item.notional_volume)}
        </span>
      </div>

      <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginTop: 10 }}>
        {item.reasons.map(reason => (
          <span key={reason} style={{ border: '1px solid var(--line-1)', borderRadius: 999, padding: '2px 7px', color: 'var(--fg-2)', fontSize: 9.5 }}>
            {reason}
          </span>
        ))}
      </div>
    </div>
  )
}


export default function DailyRecommendations() {
  const queryClient = useQueryClient()
  const [error, setError] = useState('')
  const query = useQuery<WatchlistRecommendationsResponse>({
    queryKey: ['watchlist-recommendations'],
    queryFn: () => getWatchlistRecommendations(5),
    staleTime: 5 * 60_000,
    refetchInterval: 15 * 60_000,
    retry: 1,
  })
  const addMutation = useMutation({
    mutationFn: (item: WatchlistRecommendation) => addWatchlist({
      symbol: item.symbol,
      name: item.name,
      asset_type: item.asset_type,
    }),
    onMutate: () => setError(''),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
        queryClient.invalidateQueries({ queryKey: ['watchlist-recommendations'] }),
      ])
    },
    onError: (mutationError: unknown) => {
      const status = (mutationError as { response?: { status?: number } })?.response?.status
      if (status === 409) {
        queryClient.invalidateQueries({ queryKey: ['watchlist'] })
        queryClient.invalidateQueries({ queryKey: ['watchlist-recommendations'] })
        return
      }
      setError('加入失败，请检查后端连接后重试')
    },
  })

  const items = query.data?.items ?? []
  return (
    <div className="panel" style={{ overflow: 'hidden', marginBottom: 16 }}>
      <div className="panel-header" style={{ alignItems: 'center' }}>
        <div>
          <span>每日建议关注</span>
          <div style={{ color: 'var(--fg-3)', fontSize: 10, fontWeight: 400, marginTop: 2 }}>
            轻量发现市场龙头与高成交候选，加入后才进入深度策略扫描
          </div>
        </div>
        <span className="chip accent" style={{ fontSize: 9 }}>最多 5 个</span>
      </div>

      {query.isLoading ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))' }}>
          {[0, 1, 2].map(index => <div key={index} style={{ height: 126, background: 'var(--bg-2)', borderRight: '1px solid var(--line-1)', animation: 'pulse 1.5s ease-in-out infinite' }} />)}
        </div>
      ) : query.isError ? (
        <div style={{ padding: 18, color: 'var(--fg-3)', fontSize: 11, textAlign: 'center' }}>
          候选行情暂不可用，不影响自选和图表功能
        </div>
      ) : items.length === 0 ? (
        <div style={{ padding: 18, color: 'var(--fg-3)', fontSize: 11, textAlign: 'center' }}>
          {query.data?.available_count === 0 ? '候选行情暂不可用，稍后会自动重试' : '当前候选已经在自选中'}
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))' }}>
          {items.map(item => (
            <RecommendationCard
              key={item.symbol}
              item={item}
              adding={addMutation.isPending && addMutation.variables?.symbol === item.symbol}
              onAdd={() => addMutation.mutate(item)}
            />
          ))}
        </div>
      )}

      <div style={{ padding: '8px 12px', display: 'flex', alignItems: 'center', gap: 8, borderTop: '1px solid var(--line-1)', background: 'var(--bg-2)' }}>
        <span style={{ color: 'var(--accent)', fontSize: 10 }}>观察建议</span>
        <span style={{ color: error ? 'var(--down)' : 'var(--fg-3)', fontSize: 10 }}>
          {error || query.data?.note || '建议加入自选，不代表建议立即交易'}
        </span>
        {query.data && <span className="mono" style={{ marginLeft: 'auto', color: 'var(--fg-3)', fontSize: 9 }}>扫描 {query.data.available_count}/{query.data.scanned_count}</span>}
      </div>
    </div>
  )
}
