import Link from 'next/link'
import { ConfidenceBadge } from '@/components/strategies/ConfidenceBadge'
import type { StrategySymbolPerf } from '@/types'

const DIRECTION_LABELS: Record<string, string> = { long: '做多', short: '做空' }

export function StrategyPerfTable({
  rows,
  strategyId,
  sortBy,
  sortDir,
  onSort,
  onShowTrades,
}: {
  rows: StrategySymbolPerf[]
  strategyId: string
  sortBy: string
  sortDir: 'asc' | 'desc'
  onSort: (field: string) => void
  onShowTrades: (row: StrategySymbolPerf) => void
}) {
  const header = (field: string, label: string) => (
    <button onClick={() => onSort(field)} style={{ all: 'unset', cursor: 'pointer' }}>
      {label}{sortBy === field ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ''}
    </button>
  )

  return (
    <div className="panel" style={{ overflowX: 'auto' }}>
      <div style={{ minWidth: 990 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '44px 1fr 70px 74px 74px 82px 90px 82px 90px 88px 96px 116px', gap: 8, padding: '9px 12px', borderBottom: '1px solid var(--line-1)', color: 'var(--fg-2)', fontSize: 11, fontWeight: 700 }}>
        <div>排名</div>
        <div>品种</div>
        <div>周期</div>
        <div>方向</div>
        <div>来源</div>
        <div>{header('sample_size', '样本')}</div>
        <div>{header('win_rate', '胜率')}</div>
        <div>{header('avg_r', '平均R')}</div>
        <div>{header('expectancy', '期望值')}</div>
        <div>回撤</div>
        <div>{header('last_fired_at', '最近触发')}</div>
        <div>操作</div>
      </div>
      {rows.length === 0 ? (
        <div style={{ padding: 18, color: 'var(--fg-2)' }}>尚无统计。点击右上“刷新统计”生成。</div>
      ) : rows.map((row, index) => (
        <div key={`${row.source}-${row.symbol}-${row.timeframe}-${row.direction}-${row.latest_backtest_run_id ?? 'edge'}`} style={{ display: 'grid', gridTemplateColumns: '44px 1fr 70px 74px 74px 82px 90px 82px 90px 88px 96px 116px', gap: 8, alignItems: 'center', padding: '10px 12px', borderBottom: '1px solid var(--line-1)', opacity: row.confidence === 'low' ? 0.58 : 1 }}>
          <div className="mono" style={{ color: 'var(--fg-3)' }}>{index + 1}</div>
          <div className="mono" style={{ color: 'var(--fg-0)', fontWeight: 700 }}>{row.symbol}</div>
          <div className="chip">{row.timeframe.toUpperCase()}</div>
          <div className={`chip ${row.direction === 'long' ? 'up' : 'down'}`}>{DIRECTION_LABELS[row.direction]}</div>
          <div className={`chip ${row.source === 'backtest' ? 'info' : ''}`}>{row.source === 'backtest' ? '回测' : '扫描'}</div>
          <div><ConfidenceBadge value={row.confidence} /></div>
          <div className="mono" style={{ color: row.win_rate >= 0.6 ? 'var(--up)' : row.win_rate < 0.4 ? 'var(--down)' : 'var(--accent)' }}>{(row.win_rate * 100).toFixed(1)}%</div>
          <div className="mono">{row.avg_r.toFixed(2)}</div>
          <div className="mono" style={{ color: row.expectancy >= 0 ? 'var(--up)' : 'var(--down)' }}>{row.expectancy.toFixed(2)}R</div>
          <div className="mono">{row.max_drawdown.toFixed(1)}%</div>
          <div className="mono" style={{ color: 'var(--fg-2)' }}>{row.last_fired_at ? new Date(row.last_fired_at).toLocaleDateString() : '-'}</div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="btn small" onClick={() => onShowTrades(row)}>明细</button>
            <Link className="btn small ghost" href={`/chart/${encodeURIComponent(row.symbol)}?strategy=${encodeURIComponent(strategyId)}&overlay=signals`}>图表</Link>
          </div>
        </div>
      ))}
      </div>
    </div>
  )
}
