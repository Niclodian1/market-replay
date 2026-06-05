import type { CompanyProfile } from '@/lib/api/fundamentals'

export function BusinessOverviewCard({ profile }: { profile: CompanyProfile | null | undefined }) {
  return (
    <section className="panel" style={{ padding: 16 }}>
      <div className="panel-header" style={{ margin: '-16px -16px 14px' }}>
        <span>业务概览与权益叙事</span>
        <span className="chip">{profile?.reviewed ? '已校对' : '未校对'}</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <TextBlock title="公司在做什么" text={profile?.business_overview} />
        <TextBlock title="为什么值得跟踪" text={profile?.equity_story} />
      </div>
      <div style={{ marginTop: 14 }}>
        <div className="hline">护城河</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {(profile?.moat.length ? profile.moat : ['暂无本地知识库记录']).map(item => (
            <span key={item} className="chip accent">{item}</span>
          ))}
        </div>
      </div>
    </section>
  )
}

function TextBlock({ title, text }: { title: string; text?: string | null }) {
  return (
    <div>
      <div style={{ fontSize: 12, color: 'var(--fg-2)', marginBottom: 8 }}>{title}</div>
      <div style={{ color: text ? 'var(--fg-0)' : 'var(--fg-3)', lineHeight: 1.8 }}>
        {text || '暂无本地知识库内容。'}
      </div>
    </div>
  )
}
