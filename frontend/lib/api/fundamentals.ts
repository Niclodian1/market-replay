import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/lib/api'

export interface RevenueSegment {
  segment: string
  pct: number
}

export interface CompanyProfile {
  symbol: string
  market: 'US' | 'HK' | 'CN' | string
  name_cn?: string | null
  name_en?: string | null
  sector?: string | null
  industry?: string | null
  business_overview?: string | null
  equity_story?: string | null
  revenue_model?: string | null
  revenue_breakdown: RevenueSegment[]
  moat: string[]
  key_risks: string[]
  updated_by?: string | null
  updated_at?: string | null
  reviewed: boolean
}

export interface CompanyProfileCard {
  symbol: string
  market: string
  name_cn?: string | null
  name_en?: string | null
  sector?: string | null
  industry?: string | null
  business_overview?: string | null
  reviewed: boolean
}

export interface FinancialSnapshot {
  symbol: string
  revenue?: number | null
  net_income?: number | null
  revenue_yoy?: number | null
  revenue_qoq?: number | null
  net_income_yoy?: number | null
  net_income_qoq?: number | null
  gross_margin?: number | null
  market_cap?: number | null
  trailing_pe?: number | null
  forward_pe?: number | null
  roe?: number | null
  dividend_yield?: number | null
  currency?: string | null
  quarter?: string | null
  updated_at?: string | null
}

export interface EarningsHistoryItem {
  date: string
  eps_actual?: number | null
  eps_estimate?: number | null
  surprise_pct?: number | null
}

export interface EarningsCalendar {
  symbol: string
  next_earnings_date?: string | null
  history: EarningsHistoryItem[]
}

export interface FundamentalsDetail {
  symbol: string
  profile?: CompanyProfile | null
  snapshot?: FinancialSnapshot | null
  earnings?: EarningsCalendar | null
  source_status: Record<string, string>
}

export const listFundamentals = (params: { market?: string; q?: string } = {}) =>
  apiClient.get<{ items: CompanyProfileCard[]; total: number }>('/api/fundamentals/list', { params }).then(r => r.data)

export const getFundamentals = (symbol: string) =>
  apiClient.get<FundamentalsDetail>(`/api/fundamentals/${encodeURIComponent(symbol)}`).then(r => r.data)

export const getFundamentalsEarnings = (symbol: string) =>
  apiClient.get<EarningsCalendar>(`/api/fundamentals/${encodeURIComponent(symbol)}/earnings`).then(r => r.data)

export const reloadFundamentals = () =>
  apiClient.post<{ ok: boolean; total: number }>('/api/fundamentals/reload').then(r => r.data)

export const useFundamentalsList = (params: { market?: string; q?: string }) =>
  useQuery({
    queryKey: ['fundamentals', 'list', params],
    queryFn: () => listFundamentals(params),
    staleTime: 60_000,
  })

export const useFundamentals = (symbol: string) =>
  useQuery({
    queryKey: ['fundamentals', symbol],
    queryFn: () => getFundamentals(symbol),
    enabled: symbol.length > 0,
    staleTime: 60_000,
  })
