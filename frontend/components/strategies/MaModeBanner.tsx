'use client'

interface Props {
  leverage: 2 | 3
  accent: string
}

export function MaModeBanner({ leverage, accent }: Props) {
  return (
    <div style={{
      padding: '8px 14px',
      background: `${accent}14`,
      border: `1px solid ${accent}4d`,
      borderRadius: 8, fontSize: 12, color: 'var(--fg-2)', lineHeight: 1.6,
    }}>
      <strong style={{ color: accent }}>浮盈加仓 · {leverage}x 杠杆</strong>
      {' '}·  保本触发阈值：首次入场价 +15%（原策略 +4%）
      {' '}·  每根K线浮盈若增加，则在下一根K线收盘追加 <strong>浮盈增量 × {leverage}</strong> 的仓位
      {' '}·  点击下方交易行可播放 K 线逐步回放动画
    </div>
  )
}
