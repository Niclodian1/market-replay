import { apiClient } from '@/lib/api'
import type { StrategyBacktestTradesResponse, StrategyBacktestsResponse, StrategyCard, StrategyDetail, StrategyTradesResponse } from '@/types'

export const listStrategies = () =>
  apiClient.get<StrategyCard[]>('/api/strategies').then(r => r.data)

export const getStrategyDetail = (
  id: string,
  params: {
    timeframe?: string
    direction?: string
    sort_by?: string
    sort_dir?: 'asc' | 'desc'
    min_samples?: number
    symbols?: string
  } = {}
) =>
  apiClient.get<StrategyDetail>(`/api/strategies/${encodeURIComponent(id)}`, { params }).then(r => r.data)

export const refreshStrategy = (id: string, timeframe: string) =>
  apiClient.post(`/api/strategies/${encodeURIComponent(id)}/refresh`, null, { params: { timeframe } }).then(r => r.data)

export const getStrategyTrades = (
  id: string,
  params: {
    symbol: string
    timeframe?: string
    direction?: string
    limit?: number
  }
) =>
  apiClient.get<StrategyTradesResponse>(`/api/strategies/${encodeURIComponent(id)}/trades`, { params }).then(r => r.data)

export const getStrategyBacktests = (id: string, limit = 50) =>
  apiClient.get<StrategyBacktestsResponse>(`/api/strategies/${encodeURIComponent(id)}/backtests`, { params: { limit } }).then(r => r.data)

export const getStrategyBacktestTrades = (runId: number) =>
  apiClient.get<StrategyBacktestTradesResponse>(`/api/strategies/backtests/${runId}/trades`).then(r => r.data)
