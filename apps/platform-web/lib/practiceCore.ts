/**
 * practice-core：练习模式的纯逻辑（无 React、无网络）。
 *
 * 桌面页、移动壳、测试都调用同一套函数：
 * - `buildView` 决定「此刻能看到什么」（K 线前缀 + 已确认事件）
 * - `revealedStats` 只统计已揭晓的事件，防止练习时偷看答案
 * - `PracticeSession` 是可变外壳，方便原生/命令行复用
 */

export type PracticeBar = {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume?: number
}

export type PracticeEvent = {
  uid: string
  indicator_id: string
  direction: string
  bar_time: number
  confirmed_at: number
  price: number
}

export type PracticeOutcome = {
  won: boolean
  r_multiple: number
  exit_reason: string
  /** 截尾（到期仍未触及目标/止损）：不计入胜负，与后端 summarize(exclude_censored=True) 一致。 */
  censored?: boolean
}

export type PracticeView = {
  cursor: number
  candles: PracticeBar[]
  visibleTime: number
  events: PracticeEvent[]
  progress: number
}

export type IndicatorStats = {
  indicator_id: string
  n: number
  wins: number
  losses: number
  hitRate: number
  avgR: number
}

/** 加仓：一个持仓由多笔「腿」组成，每条腿权重可不同（首仓 1，加仓 0.5 等）。 */
export type PracticeLeg = {
  uid: string
  time: number
  price: number
  weight: number
}

export type PracticePosition = {
  direction: string
  legs: PracticeLeg[]
  avgPrice: number
  totalWeight: number
  openedAt: number
}

export type ClosedPosition = {
  direction: string
  legs: PracticeLeg[]
  avgPrice: number
  totalWeight: number
  openedAt: number
  closedAt: number
  rMultiple: number
  won: boolean
  covered: number
}

export type PositionStats = {
  n: number
  wins: number
  losses: number
  hitRate: number
  avgR: number
}

export type PracticeStats = {
  total: number
  revealed: number
  wins: number
  losses: number
  /** 已揭晓但截尾的样本数（不计入胜负与命中率）。 */
  censored: number
  hitRate: number
  avgR: number
  byIndicator: IndicatorStats[]
}

/** 胜负口径与后端一致：r_multiple > 0 记胜，截尾样本不算胜负。 */
export function isWinningOutcome(outcome: PracticeOutcome): boolean {
  return !outcome.censored && outcome.r_multiple > 0
}

export const MIN_WARMUP = 60

export function clampCursor(cursor: number, length: number, minWarmup = MIN_WARMUP): number {
  if (length <= 1) return 0
  const lower = Math.min(minWarmup, length - 1)
  return Math.max(lower, Math.min(length - 1, Math.floor(cursor)))
}

export function defaultStart(length: number): number {
  if (length <= 1) return 0
  return clampCursor(Math.floor(length * 0.6), length)
}

/** mulberry32：同一 seed 在桌面/移动/测试三处给出同一起点（双端一致性）。 */
function mulberry32(seed: number): () => number {
  let state = seed >>> 0
  return () => {
    state = (state + 0x6d2b79f5) >>> 0
    let t = state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** 随机起点：默认用时间戳做种子；传入 seed 可复现同一局。 */
export function randomStart(length: number, seed = Date.now()): number {
  if (length <= 1) return 0
  const lower = Math.min(MIN_WARMUP, length - 1)
  const span = length - 1 - lower
  if (span <= 0) return lower
  return lower + Math.floor(mulberry32(seed)() * (span + 1))
}

/** 只看选中的指标（练习页开关）。 */
export function filterEvents(events: PracticeEvent[], indicatorIds: string[]): PracticeEvent[] {
  if (!indicatorIds.length) return []
  const wanted = new Set(indicatorIds)
  return events.filter((event) => wanted.has(event.indicator_id))
}

/**
 * 错题重练：找当前时点之后**最早**一条「已揭晓且亏损」的事件，
 * 返回其确认时间（用于把光标定位到那根 K 线）；没有则返回 null。
 */
export function nextReviewTime(
  events: PracticeEvent[],
  outcomes: Record<string, PracticeOutcome | undefined>,
  revealed: Record<string, boolean>,
  afterTime: number,
): number | null {
  const candidates = events
    .filter((event) => {
      const outcome = outcomes[event.uid]
      // 错题 = 已揭晓的真实亏损；截尾（未走完）与盈利都不算错题
      return revealed[event.uid] && outcome && !outcome.censored && outcome.r_multiple <= 0
    })
    .filter((event) => event.confirmed_at > afterTime)
    .sort((a, b) => a.confirmed_at - b.confirmed_at)
  return candidates.length ? candidates[0].confirmed_at : null
}

// ── 加仓 / 持仓 ──────────────────────────────────────────────────────────────

export const DEFAULT_MAX_LEGS = 3

export function createPosition(event: PracticeEvent, weight = 1): PracticePosition {
  const leg: PracticeLeg = { uid: event.uid, time: event.confirmed_at, price: event.price, weight }
  return {
    direction: event.direction,
    legs: [leg],
    avgPrice: event.price,
    totalWeight: weight,
    openedAt: event.confirmed_at,
  }
}

function _withLeg(position: PracticePosition, leg: PracticeLeg): PracticePosition {
  const legs = [...position.legs, leg]
  const totalWeight = legs.reduce((sum, item) => sum + item.weight, 0)
  const avgPrice = legs.reduce((sum, item) => sum + item.price * item.weight, 0) / totalWeight
  return { ...position, legs, avgPrice, totalWeight }
}

/**
 * 加仓：只接受同向、未超过腿数上限、且价格比上一腿更有利（可要求最小间距）的事件。
 * 不满足条件返回 null，调用方据此禁用按钮。
 */
export function addLeg(
  position: PracticePosition,
  event: PracticeEvent,
  options: { weight?: number; maxLegs?: number; minStepPct?: number } = {},
): PracticePosition | null {
  const maxLegs = options.maxLegs ?? DEFAULT_MAX_LEGS
  if (position.legs.length >= maxLegs) return null
  if (event.direction !== position.direction) return null
  const last = position.legs[position.legs.length - 1]
  if (last && options.minStepPct) {
    const better =
      position.direction === 'long'
        ? event.price >= last.price * (1 + options.minStepPct)
        : event.price <= last.price * (1 - options.minStepPct)
    if (!better) return null
  }
  return _withLeg(position, {
    uid: event.uid,
    time: event.confirmed_at,
    price: event.price,
    weight: options.weight ?? 1,
  })
}

/** 持仓的加权 R：按每条腿自己的结果加权平均；没有结果的腿计入 covered 统计。 */
export function positionR(
  position: PracticePosition,
  outcomes: Record<string, PracticeOutcome | undefined>,
): { rMultiple: number; covered: number; total: number } | null {
  let weighted = 0
  let weight = 0
  let covered = 0
  for (const leg of position.legs) {
    const outcome = outcomes[leg.uid]
    if (!outcome) continue
    covered += 1
    weighted += outcome.r_multiple * leg.weight
    weight += leg.weight
  }
  if (!weight) return null
  return { rMultiple: weighted / weight, covered, total: position.legs.length }
}

/** 平仓：用反向事件（或当前时点）结算持仓，得到加权 R 与胜负。 */
export function closePosition(
  position: PracticePosition,
  outcomes: Record<string, PracticeOutcome | undefined>,
  closedAt: number,
): ClosedPosition | null {
  const result = positionR(position, outcomes)
  if (!result) return null
  return {
    direction: position.direction,
    legs: position.legs,
    avgPrice: position.avgPrice,
    totalWeight: position.totalWeight,
    openedAt: position.openedAt,
    closedAt,
    rMultiple: result.rMultiple,
    won: result.rMultiple > 0,
    covered: result.covered,
  }
}

export function positionStats(closed: ClosedPosition[]): PositionStats {
  const n = closed.length
  const wins = closed.filter((item) => item.won).length
  const totalR = closed.reduce((sum, item) => sum + item.rMultiple, 0)
  return {
    n,
    wins,
    losses: n - wins,
    hitRate: n ? wins / n : 0,
    avgR: n ? totalR / n : 0,
  }
}

export function buildView(
  bars: PracticeBar[],
  events: PracticeEvent[],
  cursor: number,
  minWarmup = MIN_WARMUP,
): PracticeView {
  const safeCursor = clampCursor(cursor, bars.length, minWarmup)
  const candles = bars.slice(0, safeCursor + 1)
  const visibleTime = candles.length ? candles[candles.length - 1].time : 0
  const visible = events.filter((event) => event.confirmed_at <= visibleTime)
  return {
    cursor: safeCursor,
    candles,
    visibleTime,
    events: visible,
    progress: bars.length > 1 ? safeCursor / (bars.length - 1) : 0,
  }
}

export function revealedStats(
  events: PracticeEvent[],
  outcomes: Record<string, PracticeOutcome | undefined>,
  revealed: Record<string, boolean>,
): PracticeStats {
  const buckets = new Map<string, { wins: number; losses: number; r: number }>()
  let n = 0
  let wins = 0
  let losses = 0
  let censored = 0
  let totalR = 0

  for (const event of events) {
    if (!revealed[event.uid]) continue
    const outcome = outcomes[event.uid]
    if (!outcome) continue
    // 截尾样本单独计数：后端 summarize 默认 exclude_censored=True，胜负与 R 都不含它
    if (outcome.censored) {
      censored += 1
      continue
    }
    n += 1
    totalR += outcome.r_multiple
    const win = isWinningOutcome(outcome)
    if (win) wins += 1
    else losses += 1
    const bucket = buckets.get(event.indicator_id) ?? { wins: 0, losses: 0, r: 0 }
    if (win) bucket.wins += 1
    else bucket.losses += 1
    bucket.r += outcome.r_multiple
    buckets.set(event.indicator_id, bucket)
  }

  const byIndicator: IndicatorStats[] = Array.from(buckets.entries())
    .map(([indicator_id, bucket]) => {
      const count = bucket.wins + bucket.losses
      return {
        indicator_id,
        n: count,
        wins: bucket.wins,
        losses: bucket.losses,
        hitRate: count ? bucket.wins / count : 0,
        avgR: count ? bucket.r / count : 0,
      }
    })
    .sort((a, b) => b.n - a.n)

  return {
    total: events.length,
    revealed: n,
    wins,
    losses,
    censored,
    hitRate: n ? wins / n : 0,
    avgR: n ? totalR / n : 0,
    byIndicator,
  }
}

/** 可变外层：移动壳/CLI 直接 new，不必依赖 React 状态。 */
export class PracticeSession {
  readonly bars: PracticeBar[]
  readonly events: PracticeEvent[]
  readonly outcomes: Record<string, PracticeOutcome | undefined>
  private cursor: number
  private revealed: Record<string, boolean> = {}
  private position: PracticePosition | null = null
  private closed: ClosedPosition[] = []

  constructor(
    bars: PracticeBar[],
    events: PracticeEvent[],
    outcomes: Record<string, PracticeOutcome | undefined> = {},
    start?: number,
  ) {
    this.bars = bars
    this.events = events
    this.outcomes = outcomes
    this.cursor = start === undefined ? defaultStart(bars.length) : clampCursor(start, bars.length)
  }

  get view(): PracticeView {
    return buildView(this.bars, this.events, this.cursor)
  }

  get stats(): PracticeStats {
    return revealedStats(this.view.events, this.outcomes, this.revealed)
  }

  step(delta: number): PracticeView {
    this.cursor = clampCursor(this.cursor + delta, this.bars.length)
    return this.view
  }

  seek(time: number): PracticeView {
    const index = this.bars.findIndex((bar) => bar.time >= time)
    this.cursor = clampCursor(index === -1 ? this.bars.length - 1 : index, this.bars.length)
    return this.view
  }

  reset(start?: number): PracticeView {
    this.cursor = start === undefined ? defaultStart(this.bars.length) : clampCursor(start, this.bars.length)
    this.position = null
    return this.view
  }

  get currentPosition(): PracticePosition | null {
    return this.position
  }

  get closedPositions(): ClosedPosition[] {
    return [...this.closed]
  }

  get positionStats(): PositionStats {
    return positionStats(this.closed)
  }

  /** 进场：以某个事件开一个持仓（首仓权重 1）。 */
  openPosition(event: PracticeEvent, weight = 1): PracticePosition {
    this.position = createPosition(event, weight)
    return this.position
  }

  /** 加仓：同向且未超上限才生效，返回 null 表示这次不加。 */
  addToPosition(
    event: PracticeEvent,
    options: { weight?: number; maxLegs?: number; minStepPct?: number } = {},
  ): PracticePosition | null {
    if (!this.position) return null
    const next = addLeg(this.position, event, options)
    if (!next) return null
    this.position = next
    return next
  }

  /** 平仓：按加权 R 结算，写入已平仓列表。 */
  closePositionNow(atTime?: number): ClosedPosition | null {
    if (!this.position) return null
    const closed = closePosition(this.position, this.outcomes, atTime ?? this.view.visibleTime)
    this.position = null
    if (closed) this.closed.push(closed)
    return closed
  }

  /** 随机起点重开一局（同一 seed 可复现）。 */
  randomReset(seed?: number): PracticeView {
    this.cursor = randomStart(this.bars.length, seed)
    this.position = null
    return this.view
  }

  /** 跳到下一条已揭晓亏损事件所在 K 线；没有错题则返回 null。 */
  nextReview(): PracticeView | null {
    const target = nextReviewTime(this.events, this.outcomes, this.revealed, this.view.visibleTime)
    if (target == null) return null
    return this.seek(target)
  }

  reveal(uid: string): PracticeStats {
    this.revealed = { ...this.revealed, [uid]: true }
    return this.stats
  }

  revealedMap(): Record<string, boolean> {
    return { ...this.revealed }
  }
}
