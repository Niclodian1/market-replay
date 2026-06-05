import type { CompanyProfile, RevenueSegment } from '@/lib/api/fundamentals'

const COLORS = ['#ffb020', '#5aa7ff', '#26d07c', '#b57dff', '#ff5f6d', '#8bd3dd']

export function RevenueModelCard({ profile }: { profile: CompanyProfile | null | undefined }) {
  const segments = profile?.revenue_breakdown ?? []

  return (
    <section className="panel" style={{ padding: 16 }}>
      <div className="panel-header" style={{ margin: '-16px -16px 14px' }}>
        <span>盈利模式与收入结构</span>
        <span className="mono">{segments.length} segments</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 220px', gap: 16, alignItems: 'center' }}>
        <div>
          <div style={{ color: profile?.revenue_model ? 'var(--fg-0)' : 'var(--fg-3)', lineHeight: 1.8 }}>
            {profile?.revenue_model || '暂无盈利模式描述。'}
          </div>
          <div className="hline">核心风险</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {(profile?.key_risks.length ? profile.key_risks : ['暂无风险记录']).map(item => (
              <span key={item} className="chip">{item}</span>
            ))}
          </div>
        </div>
        <RevenuePie segments={segments} />
      </div>
    </section>
  )
}

function RevenuePie({ segments }: { segments: RevenueSegment[] }) {
  if (segments.length === 0) {
    return <div style={{ color: 'var(--fg-3)', fontSize: 12 }}>暂无收入拆分</div>
  }

  let offset = 0
  const gradient = segments.map((segment, index) => {
    const start = offset
    const end = offset + segment.pct
    offset = end
    return `${COLORS[index % COLORS.length]} ${start}% ${end}%`
  }).join(', ')

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '88px 1fr', gap: 12, alignItems: 'center' }}>
      <div
        aria-label="收入构成饼图"
        style={{
          width: 88,
          height: 88,
          borderRadius: '50%',
          background: `conic-gradient(${gradient})`,
          border: '1px solid var(--line-2)',
        }}
      />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
        {segments.map((segment, index) => (
          <div key={segment.segment} style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 11, color: 'var(--fg-1)' }}>
            <span style={{ width: 8, height: 8, background: COLORS[index % COLORS.length], borderRadius: 2 }} />
            <span style={{ flex: 1 }}>{segment.segment}</span>
            <span className="mono">{segment.pct}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}
