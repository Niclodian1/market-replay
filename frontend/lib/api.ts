import axios, { AxiosError } from 'axios'
import type { APIError, Candle, MarketDataSource } from '@/types'

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export function normalizeMarketDataSource(value: string | null | undefined): MarketDataSource {
  return value === 'yahoo' || value === 'hyperliquid' || value === 'binance_spot' || value === 'binance_futures'
    ? value
    : 'auto'
}

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 15_000,
})

// 响应拦截：统一错误日志
apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<APIError>) => {
    if (error.response) {
      console.error(
        `[API Error] ${error.config?.method?.toUpperCase()} ${error.config?.url} → ${error.response.status}`,
        error.response.data
      )
    } else {
      console.error('[API Error] Network error or timeout:', error.message)
    }
    return Promise.reject(error)
  }
)

// ===== Health =====
export const getHealth = () =>
  apiClient.get('/api/health').then((r) => r.data)

// ===== Market（第2阶段实现） =====
export const searchSymbols = (query: string) =>
  apiClient.get('/api/market/search', { params: { q: query } }).then((r) => r.data)

export const getMarketCapabilities = () =>
  apiClient.get('/api/market/capabilities').then((r) => r.data)

export const getQuote = (symbol: string, source: MarketDataSource = 'auto') =>
  apiClient.get('/api/market/quote', { params: { symbol, source } }).then((r) => r.data)

export const getQuotes = (symbols: string[]) =>
  apiClient.get('/api/market/quotes', { params: { symbols: symbols.join(',') } }).then((r) => r.data)

export const getCandles = (
  symbol: string,
  timeframe: string,
  outputsize?: number,
  before?: number,
  source: MarketDataSource = 'auto',
) =>
  apiClient
    .get('/api/market/candles', { params: { symbol, timeframe, outputsize, before, source } })
    .then((r) => r.data)

export const getEarnings = (symbols: string[]) =>
  apiClient.get('/api/market/earnings', { params: { symbols: symbols.join(',') } }).then((r) => r.data)

export const getIndicators = (symbol: string, timeframe: string) =>
  apiClient
    .get('/api/indicators', { params: { symbol, timeframe } })
    .then((r) => r.data)

// ===== 继论（图表 overlay） =====
export const analyzeChanlun = (
  symbol: string,
  timeframe: string,
  candles: Candle[],
) =>
  apiClient
    .post('/api/chanlun/analyze', { symbol, timeframe, candles }, { timeout: 30_000 })
    .then((r) => r.data)

// ===== Watchlist（第4阶段实现） =====
export const getWatchlist = () =>
  apiClient.get('/api/watchlist').then((r) => r.data)

export const getWatchlistInsights = () =>
  apiClient.get('/api/watchlist/insights').then((r) => r.data)

export const getWatchlistRecommendations = (limit = 5) =>
  apiClient.get('/api/watchlist/recommendations', { params: { limit } }).then((r) => r.data)

export const addWatchlist = (data: { symbol: string; name: string; asset_type: string }) =>
  apiClient.post('/api/watchlist', data).then((r) => r.data)

export const deleteWatchlist = (id: number) =>
  apiClient.delete(`/api/watchlist/${id}`).then((r) => r.data)

// ===== Replay Notes（第4阶段实现） =====
export const getReplayNotes = (symbol?: string) =>
  apiClient.get('/api/replay/notes', { params: symbol ? { symbol } : undefined }).then((r) => r.data)

export const getReplayNote = (id: number) =>
  apiClient.get(`/api/replay/notes/${id}`).then((r) => r.data)

export const createReplayNote = (data: object) =>
  apiClient.post('/api/replay/notes', data).then((r) => r.data)

export const deleteReplayNote = (id: number) =>
  apiClient.delete(`/api/replay/notes/${id}`).then((r) => r.data)

// ===== Alerts =====
export const getAlerts = () =>
  apiClient.get('/api/alerts').then((r) => r.data)

export const createAlert = (data: object) =>
  apiClient.post('/api/alerts', data).then((r) => r.data)

export const updateAlert = (id: number, data: object) =>
  apiClient.put(`/api/alerts/${id}`, data).then((r) => r.data)

export const deleteAlert = (id: number) =>
  apiClient.delete(`/api/alerts/${id}`).then((r) => r.data)

export const testAlertWebhook = (id: number) =>
  apiClient.post(`/api/alerts/${id}/test`).then((r) => r.data)

// ===== Watchlist Monitor Plans =====
export const getNotificationChannels = () =>
  apiClient.get('/api/monitoring/channels').then((r) => r.data)

export const createNotificationChannel = (data: object) =>
  apiClient.post('/api/monitoring/channels', data).then((r) => r.data)

export const updateNotificationChannel = (id: number, data: object) =>
  apiClient.put(`/api/monitoring/channels/${id}`, data).then((r) => r.data)

export const testNotificationChannel = (id: number) =>
  apiClient.post(`/api/monitoring/channels/${id}/test`).then((r) => r.data)

export const testEnvironmentEmail = () =>
  apiClient.post('/api/monitoring/email/test').then((r) => r.data)

export const deleteNotificationChannel = (id: number) =>
  apiClient.delete(`/api/monitoring/channels/${id}`).then((r) => r.data)

export const getMonitorPlans = () =>
  apiClient.get('/api/monitoring/plans').then((r) => r.data)

export const createMonitorPlan = (data: object) =>
  apiClient.post('/api/monitoring/plans', data).then((r) => r.data)

export const updateMonitorPlan = (id: number, data: object) =>
  apiClient.put(`/api/monitoring/plans/${id}`, data).then((r) => r.data)

export const deleteMonitorPlan = (id: number) =>
  apiClient.delete(`/api/monitoring/plans/${id}`).then((r) => r.data)

export const getMonitorAlertEvents = (limit = 100) =>
  apiClient.get('/api/monitoring/events', { params: { limit } }).then((r) => r.data)

export const markMonitorAlertEventRead = (id: number) =>
  apiClient.post(`/api/monitoring/events/${id}/read`).then((r) => r.data)

export const runMonitoringNow = () =>
  apiClient.post('/api/monitoring/run-now', null, { timeout: 90_000 }).then((r) => r.data)

// ===== System Opportunity Notifications =====
export const getOpportunityNotifications = (limit = 50) =>
  apiClient.get('/api/opportunity-notifications', { params: { limit } }).then((r) => r.data)

export const getOpportunityNotificationSummary = () =>
  apiClient.get('/api/opportunity-notifications/summary').then((r) => r.data)

export const scanOpportunitiesNow = (symbol?: string) =>
  apiClient.post('/api/opportunity-notifications/scan-now', { symbol: symbol || null }, { timeout: 90_000 }).then((r) => r.data)

export const markOpportunityNotificationRead = (id: number) =>
  apiClient.post(`/api/opportunity-notifications/${id}/read`).then((r) => r.data)

export const retryOpportunityNotification = (id: number) =>
  apiClient.post(`/api/opportunity-notifications/${id}/retry`).then((r) => r.data)

export const simulateOpportunityNotification = (data: { symbol: string; parameters: number[]; send_external?: boolean }) =>
  apiClient.post('/api/opportunity-notifications/simulate', data).then((r) => r.data)

// ===== Macro =====
export const getEconomicCalendar = (daysAhead?: number) =>
  apiClient.get('/api/macro/calendar', { params: { days_ahead: daysAhead } }).then((r) => r.data)

// ===== Backtest =====
export const runBacktest = (data: object) =>
  apiClient.post('/api/backtest', data).then((r) => r.data)

export const getBacktestDataRange = (symbol: string, timeframe: string) =>
  apiClient.get('/api/backtest/data-range', { params: { symbol, timeframe } }).then((r) => r.data)

export const runMtfBacktest = (data: object) =>
  apiClient.post('/api/backtest/mtf', data).then((r) => r.data)

// ===== MA Strategies (静态 JSON) =====
export const getMaStrategiesManifest = () =>
  apiClient.get('/api/ma-strategies/manifest').then((r) => r.data)

export const getMaStrategiesSymbol = (symbol: string) =>
  apiClient
    .get(`/api/ma-strategies/${encodeURIComponent(symbol)}`)
    .then((r) => r.data)

export const refreshMaStrategiesSymbol = (symbol: string) =>
  apiClient
    .post(`/api/ma-strategies/${encodeURIComponent(symbol)}/refresh`)
    .then((r) => r.data)

// ===== MA Strategies 3x (杠杆浮盈加仓) =====
export const getMaStrategies3xManifest = (leverage: 2 | 3 = 2) =>
  apiClient.get('/api/ma-strategies-3x/manifest', { params: { leverage } }).then((r) => r.data)

export const getMaStrategies3xSymbol = (symbol: string, leverage: 2 | 3 = 2) =>
  apiClient
    .get(`/api/ma-strategies-3x/${encodeURIComponent(symbol)}`, { params: { leverage } })
    .then((r) => r.data)

export const refreshMaStrategies3xSymbol = (symbol: string, leverage: 2 | 3 = 2) =>
  apiClient
    .post(`/api/ma-strategies-3x/${encodeURIComponent(symbol)}/refresh`, null, { params: { leverage } })
    .then((r) => r.data)

// ===== MA Strategies Pro: 浮盈加仓 =====
export const getMaStrategiesProManifest = (leverage: 2 | 3 = 2) =>
  getMaStrategies3xManifest(leverage)

export const getMaStrategiesProSymbol = (symbol: string, leverage: 2 | 3 = 2) =>
  getMaStrategies3xSymbol(symbol, leverage)

export const refreshMaStrategiesProSymbol = (symbol: string, leverage: 2 | 3 = 2) =>
  refreshMaStrategies3xSymbol(symbol, leverage)

// ===== 策略 C：多均线同时上穿 =====
export const getMaStrategiesCManifest = () =>
  apiClient.get('/api/ma-strategies-c/manifest').then((r) => r.data)

export const getMaStrategiesCSymbol = (symbol: string, timeframe: string) =>
  apiClient
    .get(`/api/ma-strategies-c/${encodeURIComponent(symbol)}/${encodeURIComponent(timeframe)}`)
    .then((r) => r.data)

export const refreshMaStrategiesCSymbol = (symbol: string, timeframe: string) =>
  apiClient
    .post(`/api/ma-strategies-c/${encodeURIComponent(symbol)}/${encodeURIComponent(timeframe)}/refresh`)
    .then((r) => r.data)

// ===== Unified MA Strategies =====
export const getUnifiedMaStrategiesManifest = () =>
  apiClient.get('/api/ma-strategies-unified/manifest').then((r) => r.data)

export const getStrategyOverview = () =>
  apiClient.get('/api/ma-strategies-unified/overview').then((r) => r.data)

export const getUnifiedMaStrategiesSymbol = (
  symbol: string,
  strategy: string,
  timeframe: string,
  leverage: string,
) =>
  apiClient
    .get(`/api/ma-strategies-unified/${encodeURIComponent(symbol)}`, {
      params: { strategy, timeframe, leverage },
    })
    .then((r) => r.data)

export const refreshUnifiedMaStrategiesSymbol = (
  symbol: string,
  strategy: string,
  timeframe: string,
  leverage: string,
) =>
  apiClient
    .post(`/api/ma-strategies-unified/${encodeURIComponent(symbol)}/refresh`, null, {
      params: { strategy, timeframe, leverage },
    })
    .then((r) => r.data)

// ===== 统一策略实验台（strategy-spine 展示层） =====
export const getStrategyLabCatalog = () =>
  apiClient.get('/api/strategy-lab/catalog').then((r) => r.data)

export const runStrategyLab = (strategyId: string, symbol: string, timeframe: string) =>
  apiClient
    .get('/api/strategy-lab/run', { params: { strategy_id: strategyId, symbol, timeframe } })
    .then((r) => r.data)
