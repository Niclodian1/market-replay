import type { Candle, ChanlunPoint } from '@/types'
import { pairTrades, summarizeTrades, type ChanlunTrade } from './chanlunStats.ts'

/**
 * 买卖信号组合扫描：枚举「买点类型 → 卖点类型」的配对组合，
 * 只用已确认点在确认 K 收盘成交。小样本（<8 笔）仅供对照，不能当实盘策略。
 */

export interface ChanlunStrategyRow {
  id: string
  /** 入场买点类型（可多个，如 二买） */
  buyKinds: string[]
  /** 离场卖点类型（可多个，任一触发即平仓） */
  sellKinds: string[]
  /** 展示标签：二买 → 一卖 */
  label: string
  trades: number
  wins: number
  losses: number
  winRate: number
  profitFactor: number
  avgPnlPct: number
  /** 累计收益（%），简单加总 */
  totalPnlPct: number
  /** 最差单笔（%） */
  worstPnl: number
  /** 最好单笔（%） */
  bestPnl: number
  /** 稳定性 = 平均收益 / 收益标准差（越大越稳） */
  stability: number
  /** 最近一批交易明细（按住入场时间倒序，最多 8 笔） */
  recentTrades: ChanlunTrade[]
}

const BUY_KINDS = ['一买', '二买', '三买', '背驰买'] as const
const SELL_KINDS = ['一卖', '二卖', '三卖', '背驰卖'] as const

function makeCombos(): Array<{ buy: string[]; sell: string[] }> {
  const combos: Array<{ buy: string[]; sell: string[] }> = []
  for (const b of BUY_KINDS) {
    for (const s of SELL_KINDS) combos.push({ buy: [b], sell: [s] })
  }
  // 常规买点亦可由「一卖或其后背驰卖」离场（用户示例：二买对一卖/一卖后的背驰卖）
  for (const b of ['一买', '二买', '三买'] as const) {
    const sell = ['一卖', '背驰卖'].sort()
    const key = JSON.stringify([b, sell])
    if (!combos.some(c => JSON.stringify([c.buy, c.sell]) === key)) {
      combos.push({ buy: [b], sell })
    }
  }
  return combos
}

function std(values: number[]): number | null {
  if (values.length < 2) return null
  const mean = values.reduce((a, b) => a + b, 0) / values.length
  const v = values.reduce((s, x) => s + (x - mean) ** 2, 0) / (values.length - 1)
  return Math.sqrt(v)
}

export function scanChanlunStrategies(
  candles: Candle[],
  points: ChanlunPoint[],
  minTrades = 3,
): ChanlunStrategyRow[] {
  const combos = makeCombos()
  const rows: ChanlunStrategyRow[] = []
  for (const combo of combos) {
    const isEntry = (p: ChanlunPoint) => combo.buy.includes(p.k)
    const isExit = (p: ChanlunPoint) => combo.sell.includes(p.k)
    const { trades } = pairTrades(candles, points, isEntry, isExit)
    if (trades.length < minTrades) continue
    const s = summarizeTrades(trades)
    const pnl = trades.map(t => t.pnlPct)
    const sd = std(pnl)
    rows.push({
      id: `${combo.buy.join('+')}|${combo.sell.join('+')}`,
      buyKinds: combo.buy,
      sellKinds: combo.sell,
      label: `${combo.buy.join('+')} → ${combo.sell.join('+')}`,
      trades: trades.length,
      wins: s.wins,
      losses: s.losses,
      winRate: s.winRate,
      profitFactor: s.profitFactor,
      avgPnlPct: s.avgPnlPct,
      totalPnlPct: pnl.reduce((a, b) => a + b, 0),
      worstPnl: Math.min(...pnl),
      bestPnl: Math.max(...pnl),
      stability: sd && sd !== 0 && s.avgPnlPct !== 0 ? s.avgPnlPct / sd : 0,
      recentTrades: [...trades].sort((a, b) => b.entryT - a.entryT).slice(0, 8),
    })
  }
  // 收益稳定优先：平均期望收益降序
  rows.sort((a, b) => b.avgPnlPct - a.avgPnlPct)
  return rows
}
