'use client'

import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { StrategyCard } from '@/components/strategies/StrategyCard'
import { listStrategies } from '@/lib/api/strategies'

const CATEGORIES = [
  { value: 'all', label: '全部' },
  { value: 'indicator', label: '指标' },
  { value: 'pattern', label: '形态' },
  { value: 'structure', label: '结构' },
  { value: 'combo', label: '组合' },
]

export default function StrategiesPage() {
  const [category, setCategory] = useState('all')
  const [query, setQuery] = useState('')
  const { data, isLoading, isError } = useQuery({
    queryKey: ['strategies'],
    queryFn: listStrategies,
  })

  const strategies = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (data ?? []).filter(item => {
      const categoryOk = category === 'all' || item.category === category
      const queryOk = !q || item.name.toLowerCase().includes(q) || item.id.toLowerCase().includes(q)
      return categoryOk && queryOk
    })
  }, [data, category, query])

  return (
    <div className="page">
      <div className="page-inner">
        <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12, marginBottom: 14 }}>
          <div>
            <div className="section-title">策略库</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, color: 'var(--fg-0)' }}>策略卡片墙</div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <input className="mr-input" style={{ width: 180 }} placeholder="搜索策略" value={query} onChange={e => setQuery(e.target.value)} />
            <select className="mr-input" style={{ width: 100 }} value={category} onChange={e => setCategory(e.target.value)}>
              {CATEGORIES.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </div>
        </div>

        {isLoading ? (
          <div className="panel" style={{ padding: 18, color: 'var(--fg-2)' }}>加载策略库...</div>
        ) : isError ? (
          <div className="panel" style={{ padding: 18, color: 'var(--down)' }}>策略库接口不可用</div>
        ) : strategies.length === 0 ? (
          <div className="panel" style={{ padding: 18, color: 'var(--fg-2)' }}>暂无策略</div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
            {strategies.map(strategy => <StrategyCard key={strategy.id} strategy={strategy} />)}
          </div>
        )}
      </div>
    </div>
  )
}
