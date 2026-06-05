import type { EarningsCalendar } from '@/lib/api/fundamentals'
import type { CSSProperties } from 'react'

export function EarningsCalendarCard({ earnings }: { earnings: EarningsCalendar | null | undefined }) {
  const days = daysUntil(earnings?.next_earnings_date)

  return (
    <section className="panel" style={{ padding: 16 }}>
      <div className="panel-header" style={{ margin: '-16px -16px 14px' }}>
        <span>财报日历与业绩前瞻</span>
        <span className="mono">{earnings?.history.length ?? 0} quarters</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: 16 }}>
        <div style={{ padding: 16, background: 'var(--accent-dim)', border: '1px solid var(--accent-line)', borderRadius: 6 }}>
          <div style={{ color: 'var(--fg-2)', fontSize: 12, marginBottom: 8 }}>下次财报日</div>
          <div className="mono" style={{ color: 'var(--fg-0)', fontSize: 22, fontWeight: 800 }}>
            {earnings?.next_earnings_date ?? '-'}
          </div>
          <div style={{ color: 'var(--accent)', marginTop: 8, fontWeight: 700 }}>
            {days == null ? '等待披露' : days >= 0 ? `${days} 天后` : `已过 ${Math.abs(days)} 天`}
          </div>
        </div>
        <div style={{ overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ color: 'var(--fg-2)', textAlign: 'left' }}>
                <th style={cellStyle}>日期</th>
                <th style={cellStyle}>EPS 实际</th>
                <th style={cellStyle}>EPS 预期</th>
                <th style={cellStyle}>惊喜</th>
              </tr>
            </thead>
            <tbody>
              {(earnings?.history ?? []).length === 0 ? (
                <tr><td colSpan={4} style={{ ...cellStyle, color: 'var(--fg-3)' }}>暂无 EPS 历史</td></tr>
              ) : earnings?.history.map(item => (
                <tr key={item.date} style={{ borderTop: '1px solid var(--line-1)' }}>
                  <td style={cellStyle} className="mono">{item.date}</td>
                  <td style={cellStyle} className="mono">{formatNumber(item.eps_actual)}</td>
                  <td style={cellStyle} className="mono">{formatNumber(item.eps_estimate)}</td>
                  <td style={{ ...cellStyle, color: (item.surprise_pct ?? 0) >= 0 ? 'var(--up)' : 'var(--down)' }} className="mono">
                    {formatSurprise(item.surprise_pct)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}

const cellStyle: CSSProperties = { padding: '8px 6px' }

function daysUntil(date?: string | null) {
  if (!date) return null
  const target = new Date(`${date}T00:00:00`)
  if (Number.isNaN(target.getTime())) return null
  const now = new Date()
  return Math.ceil((target.getTime() - now.getTime()) / 86_400_000)
}

function formatNumber(value?: number | null) {
  return value == null ? '-' : value.toFixed(2)
}

function formatSurprise(value?: number | null) {
  return value == null ? '-' : `${value.toFixed(1)}%`
}
