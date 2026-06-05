import type { TradePlan } from '@/types'

function fmt(v: number): string {
  if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 2 })
  if (Math.abs(v) >= 1) return v.toFixed(4)
  return v.toFixed(6)
}

export function TradePlanOverlay({ tradePlan }: { tradePlan: TradePlan | null | undefined }) {
  if (!tradePlan) return null
  return (
    <div className="panel" style={{ position: 'absolute', top: 10, left: 10, zIndex: 5, padding: '8px 10px', background: 'rgba(15, 19, 24, 0.88)', backdropFilter: 'blur(8px)' }}>
      <div className="section-title" style={{ marginBottom: 6 }}>交易计划</div>
      <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-1)', lineHeight: 1.8 }}>
        <span style={{ color: '#5aa7ff' }}>入场</span> {fmt(tradePlan.entry)}<br />
        <span style={{ color: 'var(--down)' }}>止损</span> {fmt(tradePlan.stop_loss)}<br />
        <span style={{ color: 'var(--up)' }}>止盈</span> {fmt(tradePlan.take_profit)} · {tradePlan.rr_ratio.toFixed(2)}R
      </div>
    </div>
  )
}
