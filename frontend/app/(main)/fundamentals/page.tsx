'use client'

import Link from 'next/link'
import { useMemo, useState } from 'react'
import { useFundamentalsList } from '@/lib/api/fundamentals'

const MARKETS = [
  { key: 'US', label: '美股' },
  { key: 'HK', label: '港股' },
  { key: 'CN', label: 'A 股' },
]

export default function FundamentalsPage() {
  const [market, setMarket] = useState('US')
  const [query, setQuery] = useState('')
  const params = useMemo(() => ({ market, q: query.trim() || undefined }), [market, query])
  const { data, isLoading, error } = useFundamentalsList(params)
  const items = data?.items ?? []

  return (
    <div className="page">
      <div className="page-inner">
        <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12, marginBottom: 14 }}>
          <div>
            <div className="section-title">Stock Fundamentals & Equity Story</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, color: 'var(--fg-0)' }}>商业模式</div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <div style={{ display: 'flex', gap: 4, background: 'var(--bg-1)', border: '1px solid var(--line-1)', padding: 3, borderRadius: 5 }}>
              {MARKETS.map(item => (
                <button
                  key={item.key}
                  className={`btn small ${market === item.key ? 'primary' : 'ghost'}`}
                  onClick={() => setMarket(item.key)}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <input
              className="mr-input"
              value={query}
              onChange={event => setQuery(event.target.value)}
              placeholder="搜索代码 / 公司 / 行业"
              style={{ width: 220 }}
            />
          </div>
        </div>

        {isLoading ? (
          <div className="panel" style={{ padding: 18, color: 'var(--fg-2)' }}>加载商业模式知识库...</div>
        ) : error ? (
          <div className="panel" style={{ padding: 18, color: 'var(--down)' }}>商业模式接口不可用</div>
        ) : items.length === 0 ? (
          <div className="panel" style={{ padding: 18, color: 'var(--fg-2)' }}>当前市场暂无匹配标的</div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
            {items.map(item => (
              <Link
                key={item.symbol}
                href={`/fundamentals/${encodeURIComponent(item.symbol)}`}
                className="panel"
                style={{ padding: 14, textDecoration: 'none', color: 'inherit', display: 'block' }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'flex-start' }}>
                  <div>
                    <div className="mono" style={{ color: 'var(--fg-0)', fontSize: 16, fontWeight: 800 }}>{item.symbol}</div>
                    <div style={{ color: 'var(--fg-1)', marginTop: 4 }}>{item.name_cn || item.name_en || item.symbol}</div>
                  </div>
                  <span className="chip accent">{item.market}</span>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
                  {item.sector && <span className="chip">{item.sector}</span>}
                  {item.industry && <span className="chip info">{item.industry}</span>}
                  {item.reviewed && <span className="chip up">已校对</span>}
                </div>
                <p style={{ color: 'var(--fg-2)', lineHeight: 1.7, margin: '12px 0 0' }}>
                  {item.business_overview || '暂无业务概览。'}
                </p>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
