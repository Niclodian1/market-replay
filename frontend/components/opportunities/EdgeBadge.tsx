import type { EdgeStat } from '@/types'

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`
}

export function EdgeBadge({ edge }: { edge: EdgeStat | null }) {
  if (!edge) {
    return <span className="chip">暂无历史表现</span>
  }
  const positive = edge.expectancy >= 0
  return (
    <span className={`chip ${positive ? 'up' : 'down'}`}>
      胜率 {pct(edge.win_rate)} · 期望 {edge.expectancy.toFixed(2)}R · 样本 {edge.sample_size}
    </span>
  )
}
