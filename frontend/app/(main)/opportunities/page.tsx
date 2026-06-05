'use client'

import Link from 'next/link'
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { OpportunityCard } from '@/components/opportunities/OpportunityCard'
import { getWatchlist } from '@/lib/api'
import { backfillSignalOutcomes, runEdgeDiscovery, scanEdgeForSymbol, scanEdgeForWatchlist, useDismissOpportunity, useEdge, useOpportunities } from '@/lib/api/opportunities'
import type { EdgeStat, Opportunity, WatchlistItem } from '@/types'

const TIMEFRAMES = ['15min', '1h', '4h', '1day', '1week']
const ALL_WATCHLIST = '__all_watchlist__'
const SIGNAL_LABELS: Record<string, string> = {
  ema_trend: '均线趋势',
  rsi_extreme: 'RSI 极值',
  pinbar: 'Pinbar 反转',
}
const DIRECTION_LABELS: Record<string, string> = {
  long: '做多',
  short: '做空',
}
const STATUS_LABELS: Record<string, string> = {
  open: '进行中',
  triggered: '已触发',
  closed: '已关闭',
  '': '全部',
}

function signalLabel(name: string): string {
  return SIGNAL_LABELS[name] ?? name
}

export default function OpportunitiesPage() {
  const [scanSymbol, setScanSymbol] = useState(ALL_WATCHLIST)
  const [status, setStatus] = useState('open')
  const [minScore, setMinScore] = useState(0.6)
  const [scanTf, setScanTf] = useState('4h')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const queryClient = useQueryClient()

  const filters = useMemo(() => ({
    status,
    min_score: minScore,
    symbol: scanSymbol === ALL_WATCHLIST ? undefined : scanSymbol,
    limit: 50,
  }), [status, minScore, scanSymbol])

  const { data: watchlistData } = useQuery<{ items: WatchlistItem[] }>({
    queryKey: ['watchlist'],
    queryFn: () => getWatchlist(),
    staleTime: 30_000,
  })
  const watchlist = watchlistData?.items ?? []
  const { data, isLoading, error } = useOpportunities(filters)
  const dismissMutation = useDismissOpportunity()
  const discoveryMutation = useMutation({
    mutationFn: runEdgeDiscovery,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['opportunities'] })
      queryClient.invalidateQueries({ queryKey: ['edge'] })
    },
  })
  const scanMutation = useMutation({
    mutationFn: () => scanSymbol === ALL_WATCHLIST
      ? scanEdgeForWatchlist(scanTf)
      : scanEdgeForSymbol(scanSymbol, scanTf),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['opportunities'] })
      queryClient.invalidateQueries({ queryKey: ['edge'] })
    },
  })
  const outcomeMutation = useMutation({
    mutationFn: () => backfillSignalOutcomes(scanSymbol === ALL_WATCHLIST ? undefined : scanSymbol, scanTf),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['opportunities'] })
      queryClient.invalidateQueries({ queryKey: ['edge'] })
    },
  })
  const scanResult = scanMutation.data ?? discoveryMutation.data
  const outcomeResult = outcomeMutation.data
  const opportunities = data?.items ?? []
  const selected = opportunities.find(item => item.id === selectedId) ?? opportunities[0] ?? null
  const edgeSymbol = selected?.symbol ?? (scanSymbol === ALL_WATCHLIST ? '' : scanSymbol)
  const { data: edgeData, isLoading: edgeLoading } = useEdge(edgeSymbol, scanTf)

  return (
    <div className="page">
      <div className="page-inner">
        <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12, marginBottom: 14 }}>
          <div>
            <div className="section-title">自动机会扫描</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, color: 'var(--fg-0)' }}>交易机会</div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Link href="/strategies" className="btn primary" style={{ textDecoration: 'none' }}>打开策略库</Link>
            <select className="mr-input" style={{ width: 150 }} value={scanSymbol} onChange={e => setScanSymbol(e.target.value)}>
              <option value={ALL_WATCHLIST}>全部自选</option>
              {watchlist.map(item => (
                <option key={item.id} value={item.symbol}>{item.symbol}</option>
              ))}
            </select>
            <select className="mr-input" style={{ width: 86 }} value={scanTf} onChange={e => setScanTf(e.target.value)}>
              {TIMEFRAMES.map(tf => <option key={tf} value={tf}>{tf}</option>)}
            </select>
            <button className="btn" disabled={(scanSymbol === ALL_WATCHLIST && watchlist.length === 0) || scanMutation.isPending} onClick={() => scanMutation.mutate()}>
              {scanMutation.isPending ? '扫描中...' : scanSymbol === ALL_WATCHLIST ? '扫描全部自选' : '扫描选中品种'}
            </button>
            <button className="btn ghost" disabled={outcomeMutation.isPending} onClick={() => outcomeMutation.mutate()}>
              {outcomeMutation.isPending ? '回填中...' : '回填结果'}
            </button>
            <button className="btn ghost" disabled={discoveryMutation.isPending} onClick={() => discoveryMutation.mutate()}>
              {discoveryMutation.isPending ? '刷新中...' : '刷新全部'}
            </button>
            <select className="mr-input" style={{ width: 110 }} value={status} onChange={e => setStatus(e.target.value)}>
              <option value="open">进行中</option>
              <option value="triggered">已触发</option>
              <option value="closed">已关闭</option>
              <option value="">全部</option>
            </select>
            <label className="mono" style={{ color: 'var(--fg-2)', fontSize: 11, display: 'flex', alignItems: 'center', gap: 8 }}>
              最低分 {Math.round(minScore * 100)}
              <input type="range" min="0" max="1" step="0.05" value={minScore} onChange={e => setMinScore(Number(e.target.value))} />
            </label>
          </div>
        </div>

        <div className="panel" style={{ padding: 12, marginBottom: 12, color: 'var(--fg-1)', lineHeight: 1.7 }}>
          <div style={{ fontWeight: 700, color: 'var(--fg-0)', marginBottom: 4 }}>使用案例：扫描自选列表里的 4 小时机会</div>
          <div style={{ fontSize: 12 }}>
            默认选择 <span className="mono" style={{ color: 'var(--accent)' }}>全部自选</span>，周期选 <span className="mono" style={{ color: 'var(--accent)' }}>4h</span>，点击“扫描全部自选”。
            系统会依次同步每个自选品种的 K 线，检查最新 K 线是否出现“均线趋势 + RSI 极值 + Pinbar”等组合信号，并把历史胜率、样本数和期望 R 展示在右侧“历史胜率账本”里。
            如果只想看某一个品种，在下拉框选择该品种后点击“扫描选中品种”。
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 340px', gap: 12 }}>
          {scanResult && (
            <div className="panel" style={{ gridColumn: '1 / -1', padding: 10, color: 'var(--fg-1)' }}>
              扫描结果：{scanResult.symbols} 个标的/周期 · 更新 {scanResult.upserts} 条历史胜率 · 生成 {scanResult.opportunities} 条机会
              {'watchlist_count' in scanResult ? ` · 自选 ${scanResult.watchlist_count} 个` : ''}
              {'signal_events' in scanResult ? ` · 记录 ${scanResult.signal_events} 个信号事件` : ''}
              {'inserted_candles' in scanResult ? ` · 新增 ${scanResult.inserted_candles} 根K线` : ''}
            </div>
          )}
          {outcomeResult && (
            <div className="panel" style={{ gridColumn: '1 / -1', padding: 10, color: 'var(--fg-1)' }}>
              结果回填：处理 {outcomeResult.processed} 个信号 · 完成 {outcomeResult.closed} 个结果 · 更新 {outcomeResult.edge_upserts} 条历史胜率
            </div>
          )}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {isLoading ? (
              <div className="panel" style={{ padding: 18, color: 'var(--fg-2)' }}>加载机会中...</div>
            ) : error ? (
              <div className="panel" style={{ padding: 18, color: 'var(--down)' }}>机会接口不可用</div>
            ) : opportunities.length === 0 ? (
              <div className="panel" style={{ padding: 18, color: 'var(--fg-2)' }}>当前筛选下暂无机会</div>
            ) : opportunities.map((opportunity: Opportunity) => (
              <div
                key={opportunity.id}
                role="button"
                tabIndex={0}
                onClick={() => setSelectedId(opportunity.id)}
                onKeyDown={e => { if (e.key === 'Enter') setSelectedId(opportunity.id) }}
                style={{ cursor: 'pointer' }}
              >
                <OpportunityCard opportunity={opportunity} />
              </div>
            ))}
          </div>

          <aside className="panel" style={{ padding: 14, alignSelf: 'start', position: 'sticky', top: 12 }}>
            <div className="panel-header" style={{ margin: '-14px -14px 12px' }}>
              <span>详情</span>
              <span className="mono">{data?.total ?? 0}</span>
            </div>
            {selected ? (
              <OpportunityDetail opportunity={selected} onDismiss={() => dismissMutation.mutate(selected.id)} />
            ) : (
              <div style={{ color: 'var(--fg-3)', fontSize: 12 }}>选择一条机会查看信号共振与交易计划</div>
            )}
            <EdgeLedgerPanel symbol={edgeSymbol} timeframe={scanTf} edges={edgeData?.items ?? []} isLoading={edgeLoading} />
          </aside>
        </div>
      </div>
    </div>
  )
}

function EdgeLedgerPanel({
  symbol,
  timeframe,
  edges,
  isLoading,
}: {
  symbol: string
  timeframe: string
  edges: EdgeStat[]
  isLoading: boolean
}) {
  return (
    <div style={{ marginTop: 16 }}>
      <div className="hline">历史胜率账本</div>
      {!symbol ? (
        <div style={{ color: 'var(--fg-3)', fontSize: 12 }}>输入标的或选择机会后查看历史胜率排行</div>
      ) : isLoading ? (
        <div style={{ color: 'var(--fg-2)', fontSize: 12 }}>正在加载 {symbol} · {timeframe} 的历史胜率...</div>
      ) : edges.length === 0 ? (
        <div style={{ color: 'var(--fg-3)', fontSize: 12 }}>暂无历史胜率，点击“扫描标的”生成</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {edges.slice(0, 8).map(edge => <EdgeLedgerRow key={edge.id} edge={edge} />)}
        </div>
      )}
    </div>
  )
}

function EdgeLedgerRow({ edge }: { edge: EdgeStat }) {
  const signals = edge.combo_spec.entry ?? []
  return (
    <div style={{ padding: 9, background: 'var(--bg-0)', border: '1px solid var(--line-1)', borderRadius: 5 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
        <span className={`chip ${edge.direction === 'long' ? 'up' : 'down'}`}>{DIRECTION_LABELS[edge.direction] ?? edge.direction}</span>
        <span className="chip">{edge.timeframe.toUpperCase()}</span>
        <span className="mono" style={{ marginLeft: 'auto', color: edge.expectancy >= 0 ? 'var(--up)' : 'var(--down)', fontSize: 11 }}>
          {edge.expectancy.toFixed(2)}R
        </span>
      </div>
      <div className="mono" style={{ color: 'var(--fg-1)', fontSize: 10.5, lineHeight: 1.7 }}>
        胜率 {(edge.win_rate * 100).toFixed(1)}% · 保守胜率 {(edge.wilson_lower * 100).toFixed(1)}% · 样本 {edge.sample_size}
      </div>
      <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginTop: 7 }}>
        {signals.length === 0 ? (
          <span className="chip">信号组合</span>
        ) : signals.map(signal => <span key={signal} className="chip info">{signalLabel(signal)}</span>)}
      </div>
    </div>
  )
}

function OpportunityDetail({ opportunity, onDismiss }: { opportunity: Opportunity; onDismiss: () => void }) {
  const edge = opportunity.edge_stat
  const plan = opportunity.trade_plan
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <span className="mono" style={{ fontSize: 16, color: 'var(--fg-0)', fontWeight: 700 }}>{opportunity.symbol}</span>
        <span className={`chip ${opportunity.direction === 'long' ? 'up' : 'down'}`}>{DIRECTION_LABELS[opportunity.direction] ?? opportunity.direction}</span>
        <span className="chip accent">{Math.round(opportunity.score * 100)}</span>
      </div>

      <div className="hline">历史表现</div>
      <div className="mono" style={{ color: 'var(--fg-1)', fontSize: 11, lineHeight: 1.9 }}>
        样本数：{edge?.sample_size ?? 0}<br />
        胜率：{edge ? `${(edge.win_rate * 100).toFixed(1)}%` : '-'}<br />
        保守胜率：{edge ? `${(edge.wilson_lower * 100).toFixed(1)}%` : '-'}<br />
        期望值：{edge?.expectancy.toFixed(3) ?? '-'}R
      </div>

      <div className="hline">交易计划</div>
      <div className="mono" style={{ color: 'var(--fg-1)', fontSize: 11, lineHeight: 1.9 }}>
        入场：{plan?.entry ?? '-'}<br />
        止损：{plan?.stop_loss ?? '-'}<br />
        止盈：{plan?.take_profit ?? '-'}<br />
        仓位：{plan?.size_r ?? '-'}R
      </div>
      {plan?.rationale && <p style={{ color: 'var(--fg-2)', fontSize: 12, lineHeight: 1.6 }}>{plan.rationale}</p>}

      <div className="hline">触发信号</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {(opportunity.confluence_payload.signals ?? []).map((signal, i) => (
          <span key={`${signal.name}-${i}`} className="chip info">{signalLabel(signal.name)}</span>
        ))}
      </div>

      {opportunity.status === 'open' && (
        <button className="btn danger" style={{ marginTop: 14 }} onClick={onDismiss}>关闭机会</button>
      )}
    </div>
  )
}
