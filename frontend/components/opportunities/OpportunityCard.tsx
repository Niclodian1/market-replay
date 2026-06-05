import Link from 'next/link'
import { EdgeBadge } from '@/components/opportunities/EdgeBadge'
import type { Opportunity } from '@/types'

const DIRECTION_LABELS: Record<string, string> = { long: '做多', short: '做空' }
const STATUS_LABELS: Record<string, string> = { open: '进行中', triggered: '已触发', expired: '已过期', closed: '已关闭' }
const SIGNAL_LABELS: Record<string, string> = {
  ema_trend: '均线趋势',
  rsi_extreme: 'RSI 极值',
  pinbar: 'Pinbar 反转',
}

function signalLabel(name: string): string {
  return SIGNAL_LABELS[name] ?? name
}

function price(v: number | undefined): string {
  if (v == null) return '-'
  if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 2 })
  if (Math.abs(v) >= 1) return v.toFixed(4)
  return v.toFixed(6)
}

export function OpportunityCard({ opportunity }: { opportunity: Opportunity }) {
  const score = Math.round(opportunity.score * 100)
  const plan = opportunity.trade_plan
  const signals = opportunity.confluence_payload.signals ?? []
  const directionClass = opportunity.direction === 'long' ? 'up' : 'down'

  return (
    <div className="panel fade-in" style={{ padding: 14, display: 'grid', gridTemplateColumns: '72px 1fr 220px', gap: 14, alignItems: 'center' }}>
      <div style={{ width: 62, height: 62, borderRadius: '50%', border: `4px solid ${score >= 70 ? 'var(--up)' : score >= 55 ? 'var(--accent)' : 'var(--line-2)'}`, display: 'grid', placeItems: 'center' }}>
        <div className="mono" style={{ fontSize: 18, fontWeight: 700, color: 'var(--fg-0)' }}>{score}</div>
      </div>

      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span className="mono" style={{ color: 'var(--fg-0)', fontSize: 15, fontWeight: 700 }}>{opportunity.symbol}</span>
          <span className="chip">{opportunity.timeframe.toUpperCase()}</span>
          <span className={`chip ${directionClass}`}>{DIRECTION_LABELS[opportunity.direction] ?? opportunity.direction}</span>
          <EdgeBadge edge={opportunity.edge_stat} />
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
          {signals.length === 0 ? (
            <span className="chip">暂无信号明细</span>
          ) : signals.slice(0, 5).map((signal, index) => (
            <span key={`${signal.name}-${index}`} className="chip info">
              {signalLabel(signal.name)}{signal.tf || signal.timeframe ? ` · ${(signal.tf ?? signal.timeframe)?.toUpperCase()}` : ''}
            </span>
          ))}
        </div>
        <div className="mono" style={{ color: 'var(--fg-3)', fontSize: 10.5, marginTop: 8 }}>
          {new Date(opportunity.created_at).toLocaleString()} · {STATUS_LABELS[opportunity.status] ?? opportunity.status}
        </div>
      </div>

      <div style={{ borderLeft: '1px solid var(--line-1)', paddingLeft: 14 }}>
        <div className="mono" style={{ color: 'var(--fg-1)', fontSize: 11, lineHeight: 1.8 }}>
          入场 {price(plan?.entry)}<br />
          止损 {price(plan?.stop_loss)}<br />
          止盈 {price(plan?.take_profit)} · 盈亏比 {plan?.rr_ratio?.toFixed(2) ?? '-'}
        </div>
        <Link
          href={`/chart/${encodeURIComponent(opportunity.symbol)}?planId=${opportunity.id}`}
          className="btn small"
          style={{ marginTop: 10, textDecoration: 'none' }}
        >
          查看图表
        </Link>
      </div>
    </div>
  )
}
