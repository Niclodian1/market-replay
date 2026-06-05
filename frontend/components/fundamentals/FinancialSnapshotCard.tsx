import type { FinancialSnapshot } from '@/lib/api/fundamentals'

export function FinancialSnapshotCard({ snapshot }: { snapshot: FinancialSnapshot | null | undefined }) {
  const metrics = [
    { label: '季度营收', value: formatMoney(snapshot?.revenue, snapshot?.currency) },
    { label: '净利润', value: formatMoney(snapshot?.net_income, snapshot?.currency) },
    { label: '营收同比', value: formatPct(snapshot?.revenue_yoy), tone: snapshot?.revenue_yoy },
    { label: '营收环比', value: formatPct(snapshot?.revenue_qoq), tone: snapshot?.revenue_qoq },
    { label: '毛利率', value: formatPct(snapshot?.gross_margin), tone: snapshot?.gross_margin },
    { label: 'ROE', value: formatPct(snapshot?.roe), tone: snapshot?.roe },
    { label: 'PE', value: formatNumber(snapshot?.trailing_pe) },
    { label: '市值', value: formatMoney(snapshot?.market_cap, snapshot?.currency) },
  ]

  return (
    <section className="panel" style={{ padding: 16 }}>
      <div className="panel-header" style={{ margin: '-16px -16px 14px' }}>
        <span>经营业绩与财务快照</span>
        <span className="mono">{snapshot?.quarter ?? '实时数据'}</span>
      </div>
      {!snapshot ? (
        <div style={{ color: 'var(--fg-3)' }}>财务快照暂不可用。</div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 10 }}>
          {metrics.map(metric => (
            <div key={metric.label} style={{ padding: 12, background: 'var(--bg-0)', border: '1px solid var(--line-1)', borderRadius: 5 }}>
              <div style={{ color: 'var(--fg-2)', fontSize: 11, marginBottom: 6 }}>{metric.label}</div>
              <div className="mono" style={{ color: metric.tone == null ? 'var(--fg-0)' : metric.tone >= 0 ? 'var(--up)' : 'var(--down)', fontSize: 16, fontWeight: 700 }}>
                {metric.value}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

function formatNumber(value?: number | null) {
  return value == null ? '-' : value.toFixed(2)
}

function formatPct(value?: number | null) {
  return value == null ? '-' : `${(value * 100).toFixed(1)}%`
}

function formatMoney(value?: number | null, currency?: string | null) {
  if (value == null) return '-'
  const abs = Math.abs(value)
  const unit = abs >= 1e12 ? 'T' : abs >= 1e9 ? 'B' : abs >= 1e6 ? 'M' : ''
  const divisor = unit === 'T' ? 1e12 : unit === 'B' ? 1e9 : unit === 'M' ? 1e6 : 1
  return `${currency ?? ''} ${(value / divisor).toFixed(2)}${unit}`.trim()
}
