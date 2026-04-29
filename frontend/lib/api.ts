import axios, { AxiosError } from 'axios'
import type { APIError } from '@/types'

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

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

export const getQuote = (symbol: string) =>
  apiClient.get('/api/market/quote', { params: { symbol } }).then((r) => r.data)

export const getQuotes = (symbols: string[]) =>
  apiClient.get('/api/market/quotes', { params: { symbols: symbols.join(',') } }).then((r) => r.data)

export const getCandles = (symbol: string, timeframe: string, outputsize?: number, before?: number) =>
  apiClient
    .get('/api/market/candles', { params: { symbol, timeframe, outputsize, before } })
    .then((r) => r.data)

export const getEarnings = (symbols: string[]) =>
  apiClient.get('/api/market/earnings', { params: { symbols: symbols.join(',') } }).then((r) => r.data)

export const getIndicators = (symbol: string, timeframe: string) =>
  apiClient
    .get('/api/indicators', { params: { symbol, timeframe } })
    .then((r) => r.data)

// ===== Watchlist（第4阶段实现） =====
export const getWatchlist = () =>
  apiClient.get('/api/watchlist').then((r) => r.data)

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
