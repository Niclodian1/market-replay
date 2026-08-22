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
  provider_symbol?: string
  display_symbol?: string
  name: string
  price: number
  change: number
  change_percent: number
  volume: number
  market_cap?: number
  day_notional_volume?: number
  funding_rate?: number
  open_interest?: number
  open_interest_value?: number
  mark_price?: number
  oracle_price?: number
  max_leverage?: number
  source?: string
  source_label?: string
  /** ISO 8601 字符串，UTC */
  timestamp: string
}

export interface SearchResult {
  symbol: string
  display_symbol?: string
  name: string
  /** stock | etf | index | crypto | perpetual */
  type: string
  exchange: string
  currency: string
  source: string
  source_label: string
}

export type Timeframe = '15min' | '1h' | '4h' | '1day' | '1week'
export type MarketDataSource = 'auto' | 'hyperliquid' | 'yahoo' | 'binance_spot' | 'binance_futures'

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

export type MarketState = 'bullish' | 'bearish' | 'sideways' | null

export interface WatchlistOpportunity {
  strategy: 'A' | 'B' | 'C'
  parameters: string[]
  timeframe: '1day'
  signal_time: number
}

export interface WatchlistInsight {
  symbol: string
  current_state: MarketState
  opportunities: WatchlistOpportunity[]
  error: string | null
}

export interface WatchlistInsightsResponse {
  generated_at: string
  timeframe: '1day'
  items: WatchlistInsight[]
}

export interface WatchlistRecommendation {
  symbol: string
  name: string
  asset_type: string
  market: 'us_equity' | 'crypto' | 'cross_asset'
  market_label: string
  tags: string[]
  reasons: string[]
  score: number
  price: number
  change_percent: number
  notional_volume: number
  source?: string
  source_label?: string
}

export interface WatchlistRecommendationsResponse {
  generated_at: string
  items: WatchlistRecommendation[]
  scanned_count: number
  available_count: number
  max_items: number
  note: string
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
  | 'ma_cross' | 'rsi' | 'macd' | 'ma_breakout' | 'strategy_a_breakout' | 'macd_momentum'
  | 'macd_divergence' | 'bollinger'
  | 'sr_breakout' | 'pinbar' | 'engulfing'
  | 'w_bottom'

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
  last_triggered_signal_time?: number | null
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

export type MonitorStrategy = 'ma' | 'macd' | 'structure'
export type MonitorTimeframe = '15min' | '1h' | '4h' | '1day'

export interface NotificationChannel {
  id: number
  name: string
  kind: 'discord' | 'feishu' | 'email'
  source?: 'database' | 'environment'
  masked_endpoint: string
  settings: {
    smtp_host?: string
    smtp_port?: number
    smtp_username?: string
    smtp_use_tls?: boolean
    smtp_use_ssl?: boolean
    from_address?: string
    to_addresses?: string
    has_password?: boolean
  }
  is_default: boolean
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface MonitorPlan {
  id: number
  name: string
  target_scope: 'watchlist'
  timeframes: MonitorTimeframe[]
  /** @deprecated First selected timeframe, retained for older clients. */
  timeframe: MonitorTimeframe
  strategies: MonitorStrategy[]
  parameters: Record<string, unknown>
  channel_id: number | null
  channel_name: string | null
  repeating: boolean
  is_active: boolean
  last_run_at: string | null
  last_error: string | null
  created_at: string
  updated_at: string
}

export interface MonitorAlertEvent {
  id: number
  plan_id: number
  symbol: string
  timeframe: string
  strategy: MonitorStrategy
  event_type: string
  direction: 'long' | 'short'
  severity: 'watch' | 'signal' | 'confirmed'
  signal_time: number
  reference_price: number
  payload: Record<string, unknown> & { summary?: string }
  status: 'pending' | 'sent' | 'failed' | 'in_app_only'
  attempts: number
  sent_at: string | null
  last_error: string | null
  is_read: boolean
  created_at: string
}

export type OpportunityDeliveryStatus = 'pending' | 'sent' | 'failed' | 'in_app_only'

export interface OpportunityNotification {
  id: number
  symbol: string
  strategy: string
  timeframe: string
  signal_time: number
  parameters: string
  title: string
  description: string
  data_source: string | null
  status: OpportunityDeliveryStatus
  attempts: number
  next_retry_at: string | null
  sent_at: string | null
  last_error: string | null
  is_read: boolean
  simulated: boolean
  created_at: string
  updated_at: string
}

export interface OpportunityNotificationsResponse {
  items: OpportunityNotification[]
  total: number
  unread: number
}

export interface OpportunityNotificationSummary {
  enabled: boolean
  discord_configured: boolean
  symbols: string[]
  ma_periods: number[]
  timeframe: string
  counts: Record<string, number>
  schedules: { symbol: string; time: string; timezone: string; days: string }[]
}

// ===== 回测 =====

export type StrategyId = 'ma_cross' | 'rsi' | 'macd' | 'ma_breakout' | 'macd_divergence' | 'macd_momentum' | 'bollinger' | 'atr_filter' | 'volume_filter'

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

export type EntrySignalId = 'ma_breakout' | 'macd_divergence' | 'sr_breakout' | 'pinbar' | 'engulfing' | 'w_bottom'

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
  pnl_amount?: number
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

// ===== MA 策略回测（静态 JSON） =====

export type MaStrategyKey = 'breakout' | 'pullback'
export type MaExitReason = '止损' | '保本' | '均线' | '强制平仓'

export interface MaStrategyTrade {
  entry_time: number
  exit_time: number
  entry_price: number
  exit_price: number
  stop_loss: number
  pnl_pct: number
  max_profit_pct: number
  rr: number | null
  exit_reason: MaExitReason
}

export interface MaStrategyStats {
  total_return: number
  n_trades: number
  win_rate: number
  max_drawdown: number
  max_profit: number
  avg_profit: number
  avg_rr: number | null
  avg_loss?: number
  expectancy?: number
  profit_factor?: number | null
  aggregation_method?: 'sum_of_independent_trade_returns'
  is_best: boolean
}

export interface MaSeriesPoint {
  time: number
  value: number
}

export interface MaStrategySymbolData {
  symbol: string
  name: string
  generated_at: string
  candles: Candle[]
  trend_ma: MaSeriesPoint[]
  ma_lines: Record<string, MaSeriesPoint[]>
  strategies: Record<MaStrategyKey, Record<string, { stats: MaStrategyStats; trades: MaStrategyTrade[] }>>
}

export interface MaStrategyManifest {
  generated_at: string
  symbols: { code: string; name: string }[]
}

// ===== 杠杆浮盈加仓策略 (2x / 3x) =====

export interface TradeEvent {
  time: number
  type: 'entry' | 'add' | 'exit'
  price: number
  size: number | null
  reason?: MaExitReason
}

export interface MaStrategy3xTrade extends MaStrategyTrade {
  leverage: number
  n_adds: number
  events: TradeEvent[]
}

export interface MaStrategy3xStats extends MaStrategyStats {
  leverage: number
}

export interface MaStrategy3xSymbolData {
  symbol: string
  name: string
  leverage: number
  generated_at: string
  candles: Candle[]
  trend_ma: MaSeriesPoint[]
  ma_lines: Record<string, MaSeriesPoint[]>
  strategies: Record<MaStrategyKey, Record<string, {
    stats: MaStrategy3xStats
    trades: MaStrategy3xTrade[]
  }>>
}

// ===== 策略 C：多均线同时上穿（C-x2 / C-x3 / C-x4） =====

export type MaStrategyCVariant = 'x2' | 'x3' | 'x4'
export type MaStrategyCTimeframe = '1day' | '4h' | '1h'

export interface MaStrategyCTrade extends MaStrategyTrade {
  exit_ma: number       // 出场参考线（被上穿均线中周期最小那根）
  crossed: number[]     // 入场当根上穿的均线列表
}

export interface MaStrategyCSymbolData {
  symbol: string
  name: string
  timeframe: MaStrategyCTimeframe
  generated_at: string
  candles: Candle[]
  ma_lines: Record<string, MaSeriesPoint[]>
  variants: Record<MaStrategyCVariant, { stats: MaStrategyStats; trades: MaStrategyCTrade[] }>
}

export interface MaStrategyCManifest {
  generated_at: string
  symbols: { code: string; name: string }[]
  timeframes: MaStrategyCTimeframe[]
  variants: MaStrategyCVariant[]
}

// ===== 统一均线策略工作台 =====

export type UnifiedMaStrategy = 'A' | 'B' | 'C'
export type UnifiedMaTimeframe = '4h' | '1day'
export type UnifiedMaLeverage = 'none' | '2' | '3'

export interface UnifiedMaCombo {
  strategy: UnifiedMaStrategy
  timeframe: UnifiedMaTimeframe
  leverage: UnifiedMaLeverage
}

export interface UnifiedMaManifestSymbol {
  code: string
  name: string
  available: UnifiedMaCombo[]
}

export interface UnifiedMaManifest {
  generated_at: string
  symbols: UnifiedMaManifestSymbol[]
  timeframes: UnifiedMaTimeframe[]
  strategies: UnifiedMaStrategy[]
  leverages: UnifiedMaLeverage[]
}

export type UnifiedMaTrade = (MaStrategyTrade | MaStrategy3xTrade | MaStrategyCTrade) & {
  leverage?: number | null
  n_adds?: number
  events?: TradeEvent[]
  exit_ma?: number
  crossed?: number[]
}

export interface UnifiedMaResultBlock {
  stats: MaStrategyStats | MaStrategy3xStats
  trades: UnifiedMaTrade[]
}

export interface UnifiedMaSymbolData {
  symbol: string
  name: string
  strategy: UnifiedMaStrategy
  timeframe: UnifiedMaTimeframe
  leverage: UnifiedMaLeverage
  generated_at: string
  data_source?: string
  adjusted?: boolean
  bar_count?: number
  data_range?: { start?: number | null; end?: number | null }
  candles: Candle[]
  trend_ma: MaSeriesPoint[]
  ma_lines: Record<string, MaSeriesPoint[]>
  parameter_kind: 'ma' | 'variant'
  options: string[]
  results: Record<string, UnifiedMaResultBlock>
}

export interface StrategyOverviewVariant {
  parameter: string
  stats: MaStrategyStats
  last_signal_time: number | null
  latest_setup: {
    time: number
    close: number
    ma: number
    body_rise: number
    crossed: boolean
    triggered: boolean
  } | null
}

export interface StrategyOverviewSymbol {
  symbol: string
  name: string
  data_source: string
  bar_count: number
  data_range: { start?: number | null; end?: number | null }
  strategy: 'A'
  strategy_name: string
  sample_count: number
  sample_count_note: string
  positive_variants: number
  variant_count: number
  weighted_expectancy: number
  weighted_win_rate: number
  evidence: '稳定' | '候选' | '观察'
  preferred_parameter: string | null
  current_signals: string[]
  variants: StrategyOverviewVariant[]
}

export interface StrategyOverviewData {
  generated_at: string
  methodology: {
    ranking: string
    sample_note: string
    evidence_note: string
  }
  strategies: Array<{
    id: 'A'
    name: string
    description: string
    parameters: string[]
    positive_symbol_breadth: Record<string, number>
  }>
  symbols: StrategyOverviewSymbol[]
  ranked_symbols: StrategyOverviewSymbol[]
}

// ===== 统一策略实验台（strategy-spine 展示层） =====
export interface StrategyLabCatalogEntry {
  id: string
  name: string
  kind: 'multi_position' | 'single_position'
  category: string
  description: string
  params_schema: Record<string, unknown>
  timeframes: string[]
  variant_axis: string | null
  tags: string[]
}

export interface StrategyLabCatalog {
  strategies: StrategyLabCatalogEntry[]
}

export interface StrategyLabVariantStats {
  total_return: number
  n_trades: number
  win_rate: number
  max_drawdown?: number
  max_profit?: number
  avg_profit?: number
  avg_rr?: number | null
  sharpe?: number
  is_best?: boolean
}

export interface StrategyLabVariant {
  id: string
  label: string
  params: Record<string, unknown>
  stats: StrategyLabVariantStats
  trades: Record<string, unknown>[]
}

export interface StrategyLabRun {
  strategy_id: string
  name: string
  symbol: string
  timeframe: string
  kind: 'multi_position' | 'single_position'
  candles: { time: number; open: number; high: number; low: number; close: number; volume: number }[]
  overlays: { ma_lines: Record<string, (number | null)[]> } | null
  best_variant: string | null
  variants: StrategyLabVariant[]
}

// ===== 缠论（图表 overlay） =====

/** 买卖点：k 为 一买/二买/三买/一卖/二卖/三卖，mode 标注级别 */
export interface ChanlunPoint {
  t: number
  p: number
  k: string
  mode: 'seg' | 'stroke' | 'fast'
  state?: string | null
  tier?: string | null
  confirmedT?: number | null
  alertT?: number | null
  ratio?: number | null
  trigger?: string | null
  why?: string | null
  sub?: string | null
  upper?: string | null
}

/** [t_start, price_start, t_end, price_end, dir]，dir=1 上升 / -1 下降 */
export type ChanlunStroke = [number, number, number, number, number]

/** [t_start, t_end, zd, zg, gg, dd] */
export type ChanlunPivot = [number, number, number, number, number, number]

export interface ChanlunFractal {
  time: number
  price: number
}

export interface ChanlunLevel {
  symbol: string
  timeframe: string
  strokes: ChanlunStroke[]
  segments: ChanlunStroke[]
  pivots_stroke: ChanlunPivot[]
  pivots_segment: ChanlunPivot[]
  buy_sell_points: ChanlunPoint[]
  beichi_points?: ChanlunPoint[]
  top_fractals: ChanlunFractal[]
  bot_fractals: ChanlunFractal[]
  meta?: Record<string, unknown>
}

export type ChanlunLayerKey = 'stroke' | 'segment' | 'pivot' | 'point' | 'beichi'
