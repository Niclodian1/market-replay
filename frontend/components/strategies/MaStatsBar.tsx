'use client'

import type { MaStrategyStats, MaStrategy3xStats } from '@/types'
import type { MaMode } from './MaControlBar'

function fmt(n: number, digits = 2) {
  return n.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

interface Props {
  stats: MaStrategyStats | MaStrategy3xStats
  mode: MaMode
  accent?: string
}

export function MaStatsBar({ stats, mode, accent }: Props) {
  const items = [
    { l: '逐笔收益合计', v: (stats.total_return >= 0 ? '+' : '') + fmt(stats.total_return) + '%', c: stats.total_return >= 0 ? 'var(--up)' : 'var(--down)' },
    { l: '交易笔数', v: String(stats.n_trades), c: 'var(--fg-0)' },
    { l: '胜率', v: fmt(stats.win_rate, 1) + '%', c: 'var(--fg-0)' },
    { l: '单笔期望', v: (stats.expectancy != null && stats.expectancy >= 0 ? '+' : '') + fmt(stats.expectancy ?? 0) + '%', c: (stats.expectancy ?? 0) >= 0 ? 'var(--up)' : 'var(--down)' },
    { l: '累计收益回撤', v: '-' + fmt(stats.max_drawdown) + '%', c: 'var(--down)' },
    { l: '最大单笔', v: '+' + fmt(stats.max_profit) + '%', c: 'var(--up)' },
    { l: '盈利因子', v: stats.profit_factor != null ? fmt(stats.profit_factor) : '-', c: (stats.profit_factor ?? 0) >= 1 ? 'var(--up)' : 'var(--fg-0)' },
  ]

  // 加仓模式追加杠杆列
  if (mode === 'pro') {
    const leverage = (stats as MaStrategy3xStats).leverage
    items.push({ l: '杠杆倍数', v: `${leverage ?? '?'}x`, c: accent ?? 'var(--fg-0)' })
  }

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${items.length}, 1fr)`,
        background: 'var(--bg-1)',
        border: '1px solid var(--line-1)',
        borderRadius: 'var(--radius-lg)',
        overflow: 'hidden',
      }}
    >
      {items.map((m, i, arr) => (
        <div key={i} style={{ borderRight: i < arr.length - 1 ? '1px solid var(--line-1)' : 'none', padding: '12px 10px' }}>
          <div className="stat-label">{m.l}</div>
          <div className="mono" style={{ fontSize: 15, color: m.c, fontWeight: 600, marginTop: 3 }}>{m.v}</div>
        </div>
      ))}
    </div>
  )
}
