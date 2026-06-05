import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '@/lib/api'
import type { EdgeStat, OppFilters, OpportunitiesResponse, Opportunity, RecommenderThresholds } from '@/types'

export const fetchOpportunities = (filters: OppFilters = {}) =>
  apiClient.get<OpportunitiesResponse>('/api/opportunities', { params: filters }).then(r => r.data)

export const fetchOpportunity = (id: number) =>
  apiClient.get<Opportunity>(`/api/opportunities/${id}`).then(r => r.data)

export const dismissOpportunity = (id: number) =>
  apiClient.post<Opportunity>(`/api/opportunities/${id}/dismiss`).then(r => r.data)

export const fetchEdge = (symbol: string, tf?: string) =>
  apiClient.get<{ items: EdgeStat[]; total: number }>(`/api/edge/${encodeURIComponent(symbol)}`, { params: { tf } }).then(r => r.data)

export const fetchThresholds = () =>
  apiClient.get<RecommenderThresholds>('/api/recommender/thresholds').then(r => r.data)

export const updateThresholds = (data: RecommenderThresholds) =>
  apiClient.put<RecommenderThresholds>('/api/recommender/thresholds', data).then(r => r.data)

export const runEdgeDiscovery = () =>
  apiClient.post<{ accepted: boolean; symbols: number; combos: number; upserts: number; opportunities: number; signal_events: number }>('/api/edge/discover/run').then(r => r.data)

export const scanEdgeForSymbol = (symbol: string, timeframe: string) =>
  apiClient
    .post<{ accepted: boolean; inserted_candles: number; candles: number; symbols: number; combos: number; upserts: number; opportunities: number; signal_events: number }>(
      '/api/edge/discover/scan',
      null,
      { params: { symbol, timeframe, sync: true } }
    )
    .then(r => r.data)

export const scanEdgeForWatchlist = (timeframe: string) =>
  apiClient
    .post<{ accepted: boolean; watchlist_count: number; inserted_candles: number; candles: number; symbols: number; combos: number; upserts: number; opportunities: number; signal_events: number }>(
      '/api/edge/discover/watchlist',
      null,
      { params: { timeframe, sync: true } }
    )
    .then(r => r.data)

export const backfillSignalOutcomes = (symbol?: string, timeframe?: string) =>
  apiClient
    .post<{ accepted: boolean; processed: number; closed: number; edge_upserts: number }>(
      '/api/edge/outcomes/backfill',
      null,
      { params: { symbol: symbol || undefined, timeframe: timeframe || undefined, horizon: 12, limit: 1000 } }
    )
    .then(r => r.data)

export const useOpportunities = (filters: OppFilters = {}) =>
  useQuery({
    queryKey: ['opportunities', filters],
    queryFn: () => fetchOpportunities(filters),
    refetchInterval: 30_000,
  })

export const useOpportunity = (id: number) =>
  useQuery({
    queryKey: ['opportunity', id],
    queryFn: () => fetchOpportunity(id),
    enabled: Number.isFinite(id),
  })

export const useEdge = (symbol: string, tf?: string) =>
  useQuery({
    queryKey: ['edge', symbol, tf],
    queryFn: () => fetchEdge(symbol, tf),
    enabled: symbol.length > 0,
  })

export const useDismissOpportunity = () => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: dismissOpportunity,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['opportunities'] }),
  })
}
