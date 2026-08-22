import type { Candle, ChanlunPoint } from '@/types'

/**
 * 基于缠论买卖点的单边多头信号统计。
 *
 * 规则（与图表 overlay 一致）：
 *  1. 入场 = 买点「可交易确认」那根 bar 的收盘价；出场 = 卖点确认 bar 收盘价。
 *     只使用 state≠forming 且带 confirmedT 的点；禁止退化为极值 t（那是未来才知道的底/顶）。
 *  2. 买点 → 卖点交替配对，只在买点买、只在卖点卖，不考虑做空；
 *     持仓期间忽略后续买点，空仓期间忽略卖点
 *  3. 价格止损 = 买点极值 bar 的 low（收盘价触及）。
 *  4. 背驰买额外：入场后若同向 MACD 柱放大（不再缩小）→ 按该 bar 收盘止损。
 */

export interface ChanlunTrade {
  /** 入场 bar 时间（买点确认时间） */
  entryT: number
  /** 入场价 = 入场 bar 收盘价 */
  entryPrice: number
  /** 止损位 = 买点 bar 的 low */
  stop: number
  /** 出场 bar 时间 */
  exitT: number
  /** 出场价（卖点 bar 收盘价，或止损价） */
  exitPrice: number
  /** 出场原因：sell=卖点信号 / stop=破价格止损 / macd=背驰单柱放大 */
  exitReason: 'sell' | 'stop' | 'macd'
  /** 实际离场的卖点类型（如 一卖/背驰卖），止损单为 null */
  exitKind: string | null
  /** 入场买点类型（如 一买/背驰买） */
  entryKind: string | null
  /** 单笔收益率（%） */
  pnlPct: number
}

export interface OpenTrade {
  entryT: number
  entryPrice: number
  stop: number
}

export interface ChanlunStats {
  /** 已平仓交易（纳入胜率统计） */
  trades: ChanlunTrade[]
  /** 仍未等到卖点/止损的持仓 */
  openTrades: OpenTrade[]
  total: number
  wins: number
  losses: number
  /** 胜率 0~1 */
  winRate: number
  /** 盈亏比 = 平均盈利 / 平均亏损绝对值 */
  profitFactor: number
  avgWinPct: number
  avgLossPct: number
  avgPnlPct: number
}

const isBuy = (p: ChanlunPoint) => p.k.includes('买') && !p.k.includes('卖')
const isSell = (p: ChanlunPoint) => p.k.includes('卖')

/** 未确认点不得进入回测：极值 t 在结构可判定之前，用它成交会虚高胜率。 */
export function isTradablePoint(p: ChanlunPoint): boolean {
  if (p.state === 'forming') return false
  return p.confirmedT != null
}

function execTime(p: ChanlunPoint): number {
  return p.confirmedT as number
}

interface Event {
  time: number
  point: ChanlunPoint
}

/** 在 (fromT, untilT] 内找首个收盘价 ≤ stop 的 bar */
function findStopBar(sorted: Candle[], fromT: number, untilT: number, stop: number): Candle | null {
  for (const c of sorted) {
    if (c.time <= fromT) continue
    if (c.time > untilT) break
    if (c.close <= stop) return c
  }
  return null
}

/** 与 fenxing/chanlun.py macd_series 同一递推（首根收盘作 EMA 种子）。 */
export function chanlunMacdHist(closes: number[]): number[] {
  const kF = 2 / (12 + 1)
  const kS = 2 / (26 + 1)
  const kD = 2 / (9 + 1)
  let emaF: number | null = null
  let emaS: number | null = null
  let emaDea: number | null = null
  const hist: number[] = []
  for (const v of closes) {
    emaF = emaF == null ? v : v * kF + emaF * (1 - kF)
    emaS = emaS == null ? v : v * kS + emaS * (1 - kS)
    const diff = emaF - emaS
    emaDea = emaDea == null ? diff : diff * kD + emaDea * (1 - kD)
    hist.push(diff - emaDea)
  }
  return hist
}

/** 背驰多单：入场后同向绿柱放大 → 该 bar 收盘离场。空头对称。 */
function findMacdExpandBar(
  sorted: Candle[],
  histByTime: Map<number, number>,
  fromT: number,
  untilT: number,
  long: boolean,
): Candle | null {
  let prev: number | undefined
  for (const c of sorted) {
    const h = histByTime.get(c.time)
    if (c.time <= fromT) {
      if (h != null) prev = h
      continue
    }
    if (c.time > untilT) break
    if (h == null || prev == null) {
      if (h != null) prev = h
      continue
    }
    const sameSign = long ? h < 0 && prev < 0 : h > 0 && prev > 0
    if (sameSign && Math.abs(h) > Math.abs(prev)) return c
    prev = h
  }
  return null
}

/**
 * 通用买卖配对引擎：entry 谓词决定「买入信号」，exit 谓词决定「平仓卖点」。
 * 空仓时遇 entry → 开仓；持仓时遇 exit → 平仓（期间任一 bar 跌破止损先离场）。
 */
export function pairTrades(
  candles: Candle[],
  points: ChanlunPoint[],
  isEntry: (p: ChanlunPoint) => boolean,
  isExit: (p: ChanlunPoint) => boolean,
): { trades: ChanlunTrade[]; openTrades: OpenTrade[] } {
  const trades: ChanlunTrade[] = []
  const openTrades: OpenTrade[] = []
  if (!points.length || !candles.length) return { trades, openTrades }

  const byTime = new Map<number, Candle>()
  for (const c of candles) byTime.set(c.time, c)

  const events: Event[] = points
    .filter(p => isTradablePoint(p) && (isEntry(p) || isExit(p)))
    .map(p => ({ time: execTime(p), point: p }))
  // 按时间升序；同时间卖点在先（先平旧仓、再开新仓，避免入场信号被持仓吞掉）
  events.sort((a, b) => a.time - b.time || (isEntry(a.point) ? 1 : isEntry(b.point) ? -1 : 0))
  if (!events.length) return { trades, openTrades }

  const sorted = [...candles].sort((a, b) => a.time - b.time)
  const hist = chanlunMacdHist(sorted.map(c => c.close))
  const histByTime = new Map<number, number>()
  sorted.forEach((c, i) => histByTime.set(c.time, hist[i]))
  let pos: { entryT: number; entryPrice: number; stop: number; entryKind: string | null } | null = null

  for (const ev of events) {
    if (isEntry(ev.point)) {
      if (pos) continue // 已持仓，忽略后续买点
      const enter = byTime.get(ev.time)
      if (!enter) continue
      const stopBar = byTime.get(ev.point.t) // 买点产生 bar（底分型极值）
      const stop = stopBar ? stopBar.low : enter.low
      pos = { entryT: enter.time, entryPrice: enter.close, stop, entryKind: ev.point.k }
      continue
    }
    // exit
    if (!pos) continue // 空仓，忽略卖点
    const exit = byTime.get(ev.time)
    if (!exit) continue
    const pxStop = findStopBar(sorted, pos.entryT, exit.time, pos.stop)
    const beichi = (pos.entryKind ?? '').includes('背驰')
    const macdStop = beichi
      ? findMacdExpandBar(sorted, histByTime, pos.entryT, exit.time, true)
      : null
    let t: ChanlunTrade
    const first = [pxStop, macdStop].filter(Boolean).sort((a, b) => a!.time - b!.time)[0] ?? null
    if (first && first === macdStop) {
      t = {
        entryT: pos.entryT,
        entryPrice: pos.entryPrice,
        stop: pos.stop,
        exitT: first.time,
        exitPrice: first.close,
        exitReason: 'macd',
        exitKind: null,
        entryKind: pos.entryKind,
        pnlPct: ((first.close - pos.entryPrice) / pos.entryPrice) * 100,
      }
    } else if (first) {
      t = {
        entryT: pos.entryT,
        entryPrice: pos.entryPrice,
        stop: pos.stop,
        exitT: first.time,
        exitPrice: pos.stop,
        exitReason: 'stop',
        exitKind: null,
        entryKind: pos.entryKind,
        pnlPct: ((pos.stop - pos.entryPrice) / pos.entryPrice) * 100,
      }
    } else {
      t = {
        entryT: pos.entryT,
        entryPrice: pos.entryPrice,
        stop: pos.stop,
        exitT: exit.time,
        exitPrice: exit.close,
        exitReason: 'sell',
        exitKind: ev.point.k,
        entryKind: pos.entryKind,
        pnlPct: ((exit.close - pos.entryPrice) / pos.entryPrice) * 100,
      }
    }
    trades.push(t)
    pos = null
  }
  if (pos) openTrades.push(pos)
  return { trades, openTrades }
}

export function summarizeTrades(trades: Array<Pick<ChanlunTrade, 'pnlPct'>>): {
  wins: number
  losses: number
  winRate: number
  profitFactor: number
  avgWinPct: number
  avgLossPct: number
  avgPnlPct: number
} {
  const wins = trades.filter(t => t.pnlPct > 0)
  const losses = trades.filter(t => t.pnlPct <= 0)
  const totalWin = wins.reduce((s, t) => s + t.pnlPct, 0)
  const totalLoss = losses.reduce((s, t) => s + t.pnlPct, 0)
  const avgWinPct = wins.length ? totalWin / wins.length : 0
  const avgLossPct = losses.length ? totalLoss / losses.length : 0
  const total = trades.length
  return {
    wins: wins.length,
    losses: losses.length,
    winRate: total ? wins.length / total : 0,
    profitFactor: avgLossPct !== 0 ? Math.abs(avgWinPct / avgLossPct) : 0,
    avgWinPct,
    avgLossPct,
    avgPnlPct: total ? (totalWin + totalLoss) / total : 0,
  }
}

export function computeChanlunStats(candles: Candle[], points: ChanlunPoint[]): ChanlunStats {
  const { trades, openTrades } = pairTrades(candles, points, isBuy, isSell)
  const s = summarizeTrades(trades)
  return {
    trades,
    openTrades,
    total: trades.length,
    wins: s.wins,
    losses: s.losses,
    winRate: s.winRate,
    profitFactor: s.profitFactor,
    avgWinPct: s.avgWinPct,
    avgLossPct: s.avgLossPct,
    avgPnlPct: s.avgPnlPct,
  }
}
