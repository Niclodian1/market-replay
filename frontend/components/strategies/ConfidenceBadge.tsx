export function ConfidenceBadge({ value }: { value: 'low' | 'mid' | 'high' }) {
  const label = value === 'high' ? '高可信' : value === 'mid' ? '中等' : '样本少'
  const cls = value === 'high' ? 'up' : value === 'mid' ? 'accent' : ''
  return <span className={`chip ${cls}`}>{label}</span>
}
