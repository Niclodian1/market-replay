// ===== 行情数据 =====

export interface Candle {
  /** Unix timestamp（秒） */
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface Quote {
  symbol: string
  name: string
  price: number
  change: number
  change_percent: number
  volume: number
  market_cap?: number
  /** ISO 8601 字符串，UTC */
  timestamp: string
}

export interface SearchResult {
  symbol: string
  name: string
  /** stock | etf | index | crypto */
  type: string
  exchange: string
  currency: string
}

export type Timeframe = '15min' | '1h' | '4h' | '1day' | '1week'

// ===== 技术指标 =====

export interface MAPoint {
  time: number
  value: number
}

export interface RSIPoint {
  time: number
  value: number
}

export interface MACDPoint {
  time: number
  macd: number
  signal: number
  histogram: number
}

export interface Indicators {
  ma20: MAPoint[]
  ma60: MAPoint[]
  rsi: RSIPoint[]
  macd: MACDPoint[]
}

export interface Indicators {
  ma20: MAPoint[]
  ma60: MAPoint[]
  rsi: RSIPoint[]
  macd: MACDPoint[]
}

// ===== 自选列表 =====

export interface WatchlistItem {
  id: number
  symbol: string
  name: string
  /** stock | etf | index | crypto */
  asset_type: string
  created_at: string
}

export interface WatchlistCreate {
  symbol: string
  name: string
  asset_type: string
}

// ===== 复盘笔记 =====

export interface ReplayNote {
  id: number
  symbol: string
  timeframe: string
  /** ISO 8601，UTC */
  replay_start_time: string
  note_time: string
  trend_view?: string
  support_level?: number
  resistance_level?: number
  entry_idea?: string
  stop_loss?: number
  take_profit?: number
  summary?: string
  created_at: string
  updated_at: string
}

export interface ReplayNoteCreate {
  symbol: string
  timeframe: string
  replay_start_time: string
  note_time: string
  trend_view?: string
  support_level?: number
  resistance_level?: number
  entry_idea?: string
  stop_loss?: number
  take_profit?: number
  summary?: string
}

// ===== API 通用 =====

export interface APIError {
  detail: string
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

// ===== 画线工具 =====

export type DrawingToolType = 'none' | 'select' | 'horizontal' | 'trendline' | 'channel' | 'fibonacci'

export interface DrawingAnchor {
  time: number   // Unix 时间戳
  price: number
}

export interface HorizontalLineDrawing {
  id: string
  type: 'horizontal'
  price: number
  color: string
}

export interface TrendLineDrawing {
  id: string
  type: 'trendline'
  p1: DrawingAnchor
  p2: DrawingAnchor
  color: string
}

export interface ChannelDrawing {
  id: string
  type: 'channel'
  p1: DrawingAnchor
  p2: DrawingAnchor
  p3: DrawingAnchor   // 第三点决定通道宽度
  color: string
}

export interface FibRetracementDrawing {
  id: string
  type: 'fibonacci'
  p1: DrawingAnchor   // 起点（高点或低点）
  p2: DrawingAnchor   // 终点
  color: string
}

export type ChartDrawing = HorizontalLineDrawing | TrendLineDrawing | ChannelDrawing | FibRetracementDrawing
