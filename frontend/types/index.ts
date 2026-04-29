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

export interface MALine {
  period: number
  data: MAPoint[]
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
  ma_lines: MALine[]
  rsi: RSIPoint[]
  macd: MACDPoint[]
}

export type ImpulseColor = 'green' | 'red' | 'blue'

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

// ===== 告警 =====

export type AlertCondition =
  | 'price_above' | 'price_below'
  | 'ma_cross' | 'rsi' | 'macd' | 'ma_breakout'
  | 'macd_divergence' | 'bollinger'
  | 'sr_breakout' | 'pinbar' | 'engulfing'

export interface Alert {
  id: number
  name: string
  symbol: string
  condition_type: AlertCondition
  threshold: number | null
  timeframe: string
  discord_webhook_url: string
  ma_period: number | null
  repeating: boolean
  is_active: boolean
  is_triggered: boolean
  triggered_at: string | null
  created_at: string
  updated_at: string
}

export interface AlertCreate {
  name: string
  symbol: string
  condition_type: AlertCondition
  threshold?: number
  timeframe: string
  discord_webhook_url: string
  ma_period?: number
  repeating?: boolean
}

// ===== 回测 =====

export type StrategyId = 'ma_cross' | 'rsi' | 'macd' | 'ma_breakout' | 'macd_divergence' | 'bollinger' | 'atr_filter' | 'volume_filter'

export type ConditionRole = 'signal' | 'filter'
export type CombineMode = 'and' | 'or' | 'majority'

export interface BacktestCondition {
  strategy: StrategyId
  params: Record<string, number>
  role?: ConditionRole
}

export interface BacktestRequest {
  symbol: string
  timeframe: string
  start_date: string
  end_date: string
  conditions: BacktestCondition[]
  initial_capital: number
  combine_mode?: CombineMode
}

export interface BacktestDataRange {
  has_data: boolean
  start_date: string | null
  end_date: string | null
}

export interface BacktestTrade {
  entry_time: number
  entry_price: number
  exit_time: number
  exit_price: number
  pnl_pct: number
  max_profit_pct: number
  max_loss_pct: number
}

export interface EquityPoint {
  time: number
  value: number
}

export interface BacktestResult {
  symbol: string
  timeframe: string
  strategy: string
  start_date: string
  end_date: string
  initial_capital: number
  total_return_pct: number
  annualized_return_pct: number
  max_drawdown_pct: number
  sharpe_ratio: number
  win_rate_pct: number
  total_trades: number
  winning_trades: number
  losing_trades: number
  buy_hold_return_pct: number
  equity_curve: EquityPoint[]
  trades: BacktestTrade[]
}

// ===== 多级别回测 (MTF) =====

export type EntrySignalId = 'ma_breakout' | 'macd_divergence' | 'sr_breakout' | 'pinbar' | 'engulfing'

export interface ExitRules {
  reverse_signal: boolean
  stop_loss_pct: number
  breakeven_trigger_pct: number
  entry_candle_low: boolean
}

export interface MtfBacktestRequest {
  symbol: string
  execution_tf: string
  trend_tf: string | null
  start_date: string
  end_date: string
  trend_ma_period: number
  use_impulse: boolean
  entry_signals: EntrySignalId[]
  entry_params: Record<string, number>
  exit_rules: ExitRules
  initial_capital: number
}

export interface MtfBacktestTrade extends BacktestTrade {
  signal_weight: number
  signal_names: string[]
  direction: string
  exit_reason?: string
}

export interface MtfBacktestResult {
  symbol: string
  timeframe: string
  trend_tf: string | null
  trend_ma_period: number
  use_impulse: boolean
  entry_signals: string[]
  start_date: string
  end_date: string
  initial_capital: number
  total_return_pct: number
  annualized_return_pct: number
  max_drawdown_pct: number
  sharpe_ratio: number
  win_rate_pct: number
  total_trades: number
  winning_trades: number
  losing_trades: number
  buy_hold_return_pct: number
  equity_curve: EquityPoint[]
  trades: MtfBacktestTrade[]
  candles: Candle[]
}

// ===== 宏观数据 =====

export interface EconomicEvent {
  date: string
  time_bjt: string | null
  name: string
  importance: 'high' | 'medium' | 'low'
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
