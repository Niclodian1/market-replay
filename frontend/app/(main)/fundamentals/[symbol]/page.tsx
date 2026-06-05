'use client'

import Link from 'next/link'
import { BusinessOverviewCard } from '@/components/fundamentals/BusinessOverviewCard'
import { EarningsCalendarCard } from '@/components/fundamentals/EarningsCalendarCard'
import { FinancialSnapshotCard } from '@/components/fundamentals/FinancialSnapshotCard'
import { RevenueModelCard } from '@/components/fundamentals/RevenueModelCard'
import { getQuote } from '@/lib/api'
import { useFundamentals } from '@/lib/api/fundamentals'
import { useQuery } from '@tanstack/react-query'

export default function FundamentalsDetailPage({ params }: { params: { symbol: string } }) {
  const symbol = decodeURIComponent(params.symbol)
  const { data, isLoading, error } = useFundamentals(symbol)
  const { data: quote } = useQuery({
    queryKey: ['quote', symbol],
    queryFn: () => getQuote(symbol),
    enabled: symbol.length > 0,
    staleTime: 30_000,
  })

  const profile = data?.profile
  const displayName = profile?.name_cn || profile?.name_en || quote?.name || symbol

  return (
    <div className="page">
      <div className="page-inner">
        <div style={{ marginBottom: 12 }}>
          <Link href="/fundamentals" className="btn ghost" style={{ textDecoration: 'none' }}>返回列表</Link>
        </div>

        <div className="panel" style={{ padding: 16, marginBottom: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-start' }}>
            <div>
              <div className="section-title">Business Model</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 5, flexWrap: 'wrap' }}>
                <span className="mono" style={{ color: 'var(--fg-0)', fontSize: 22, fontWeight: 800 }}>{symbol}</span>
                {profile?.market && <span className="chip accent">{profile.market}</span>}
                {profile?.sector && <span className="chip">{profile.sector}</span>}
              </div>
              <div style={{ color: 'var(--fg-1)', fontSize: 16, marginTop: 6 }}>{displayName}</div>
            </div>
            <div style={{ minWidth: 180, textAlign: 'right' }}>
              <div style={{ color: 'var(--fg-2)', fontSize: 11 }}>当前价格</div>
              <div className="mono" style={{ fontSize: 22, fontWeight: 800, color: 'var(--fg-0)', marginTop: 4 }}>
                {quote?.price == null ? '-' : quote.price.toFixed(2)}
              </div>
              {quote && (
                <div className="mono" style={{ color: quote.change_percent >= 0 ? 'var(--up)' : 'var(--down)', marginTop: 4 }}>
                  {quote.change_percent >= 0 ? '+' : ''}{quote.change_percent.toFixed(2)}%
                </div>
              )}
            </div>
          </div>
        </div>

        {isLoading ? (
          <div className="panel" style={{ padding: 18, color: 'var(--fg-2)' }}>加载商业模式详情...</div>
        ) : error ? (
          <div className="panel" style={{ padding: 18, color: 'var(--down)' }}>商业模式详情接口不可用</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {!profile && (
              <div className="panel" style={{ padding: 12, color: 'var(--fg-2)' }}>
                本地 Excel 暂无 {symbol} 的定性资料，以下仅展示 yfinance 可用的实时数据。
              </div>
            )}
            <BusinessOverviewCard profile={profile} />
            <RevenueModelCard profile={profile} />
            <FinancialSnapshotCard snapshot={data?.snapshot} />
            <EarningsCalendarCard earnings={data?.earnings} />
          </div>
        )}
      </div>
    </div>
  )
}
