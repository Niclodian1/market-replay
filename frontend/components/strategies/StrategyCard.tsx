import Link from 'next/link'
import type { StrategyCard as StrategyCardType } from '@/types'

const CATEGORY_LABELS: Record<string, string> = {
  indicator: '指标',
  pattern: '形态',
  structure: '结构',
  combo: '组合',
}

export function StrategyCard({ strategy }: { strategy: StrategyCardType }) {
  return (
    <Link href={`/strategies/${encodeURIComponent(strategy.id)}`} className="panel fade-in" style={{ display: 'block', padding: 14, textDecoration: 'none', minHeight: 210 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--fg-0)' }}>{strategy.name}</div>
        <span className="chip info">{CATEGORY_LABELS[strategy.category] ?? strategy.category}</span>
      </div>
      <div style={{ minHeight: 42, color: 'var(--fg-2)', fontSize: 12, lineHeight: 1.6 }}>{strategy.description}</div>
      {strategy.logic && (
        <div style={{ marginTop: 10, padding: 9, borderRadius: 5, border: '1px solid var(--line-1)', background: 'var(--bg-0)' }}>
          <div className="section-title">策略逻辑</div>
          <div style={{ marginTop: 5, color: 'var(--fg-1)', fontSize: 11.5, lineHeight: 1.55 }}>{strategy.logic}</div>
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 14 }}>
        <Stat label="覆盖品种" value={String(strategy.coverage)} />
        <Stat label="平均胜率" value={strategy.avg_win_rate == null ? '-' : `${(strategy.avg_win_rate * 100).toFixed(1)}%`} />
      </div>
      {strategy.latest_backtest && (
        <div style={{ marginTop: 10, padding: 9, borderRadius: 5, border: '1px solid var(--line-1)', background: 'var(--bg-0)' }}>
          <div className="section-title">最近回测表现</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginTop: 7 }}>
            <MiniStat label="收益" value={`${strategy.latest_backtest.total_return_pct.toFixed(1)}%`} tone={strategy.latest_backtest.total_return_pct >= 0 ? 'var(--up)' : 'var(--down)'} />
            <MiniStat label="胜率" value={`${strategy.latest_backtest.win_rate_pct.toFixed(1)}%`} />
            <MiniStat label="交易" value={`${strategy.latest_backtest.total_trades} 笔`} />
          </div>
          <div className="mono" style={{ color: 'var(--fg-3)', fontSize: 10.5, marginTop: 7 }}>
            {strategy.latest_backtest.symbol} · {strategy.latest_backtest.timeframe.toUpperCase()} · 回撤 {strategy.latest_backtest.max_drawdown_pct.toFixed(1)}%
          </div>
        </div>
      )}
      <div className="mono" style={{ color: 'var(--fg-3)', fontSize: 10.5, marginTop: 12 }}>
        最佳品种：{strategy.top_symbol ?? '暂无'}
      </div>
    </Link>
  )
}

function MiniStat({ label, value, tone = 'var(--fg-0)' }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <div className="section-title">{label}</div>
      <div className="mono" style={{ color: tone, fontSize: 13, fontWeight: 700, marginTop: 2 }}>{value}</div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ background: 'var(--bg-0)', border: '1px solid var(--line-1)', borderRadius: 5, padding: 8 }}>
      <div className="section-title">{label}</div>
      <div className="mono" style={{ color: 'var(--fg-0)', fontSize: 15, fontWeight: 700, marginTop: 3 }}>{value}</div>
    </div>
  )
}
