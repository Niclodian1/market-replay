'use client'

import Link from 'next/link'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { StrategyPerfTable } from '@/components/strategies/StrategyPerfTable'
import { getStrategyBacktestTrades, getStrategyBacktests, getStrategyDetail, getStrategyTrades, refreshStrategy } from '@/lib/api/strategies'
import type { StrategyBacktestSummary, StrategyBacktestTrade, StrategySymbolPerf, StrategyTrade } from '@/types'

const TIMEFRAMES = ['1h', '4h', '1day']
const SORT_FIELDS = [
  { value: 'win_rate', label: '胜率' },
  { value: 'expectancy', label: '期望值' },
  { value: 'avg_r', label: '平均R' },
  { value: 'sample_size', label: '样本数' },
  { value: 'last_fired_at', label: '最近触发' },
]

export default function StrategyDetailPage({ params }: { params: { strategyId: string } }) {
  const strategyId = decodeURIComponent(params.strategyId)
  const [timeframe, setTimeframe] = useState('4h')
  const [direction, setDirection] = useState('all')
  const [minSamples, setMinSamples] = useState(10)
  const [sortBy, setSortBy] = useState('win_rate')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [tradeRow, setTradeRow] = useState<StrategySymbolPerf | null>(null)
  const [backtestRunId, setBacktestRunId] = useState<number | null>(null)
  const detailsRef = useRef<HTMLDivElement | null>(null)
  const queryClient = useQueryClient()

  const { data, isLoading, isError } = useQuery({
    queryKey: ['strategy-detail', strategyId, timeframe, direction, sortBy, sortDir, minSamples],
    queryFn: () => getStrategyDetail(strategyId, {
      timeframe,
      direction,
      sort_by: sortBy,
      sort_dir: sortDir,
      min_samples: minSamples,
    }),
  })

  const refreshMutation = useMutation({
    mutationFn: () => refreshStrategy(strategyId, timeframe),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['strategy-detail'] })
      queryClient.invalidateQueries({ queryKey: ['strategies'] })
      queryClient.invalidateQueries({ queryKey: ['strategy-backtests'] })
    },
  })

  const tradesQuery = useQuery({
    queryKey: ['strategy-trades', strategyId, tradeRow?.symbol, tradeRow?.timeframe, tradeRow?.direction],
    enabled: !!tradeRow,
    queryFn: () => getStrategyTrades(strategyId, {
      symbol: tradeRow!.symbol,
      timeframe: tradeRow!.timeframe,
      direction: tradeRow!.direction,
      limit: 500,
    }),
  })

  const backtestsQuery = useQuery({
    queryKey: ['strategy-backtests', strategyId],
    queryFn: () => getStrategyBacktests(strategyId, 80),
  })

  const backtestTradesQuery = useQuery({
    queryKey: ['strategy-backtest-trades', backtestRunId],
    enabled: backtestRunId != null,
    queryFn: () => getStrategyBacktestTrades(backtestRunId!),
  })

  const onSort = (field: string) => {
    if (field === sortBy) {
      setSortDir(sortDir === 'desc' ? 'asc' : 'desc')
    } else {
      setSortBy(field)
      setSortDir('desc')
    }
  }

  const showTrades = (row: StrategySymbolPerf) => {
    if (row.source === 'backtest' && row.latest_backtest_run_id) {
      setTradeRow(null)
      setBacktestRunId(row.latest_backtest_run_id)
    } else {
      setBacktestRunId(null)
      setTradeRow(row)
    }
    window.setTimeout(() => detailsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0)
  }

  return (
    <div className="page">
      <div className="page-inner">
        <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12, marginBottom: 14 }}>
          <div>
            <Link href="/strategies" className="btn small ghost" style={{ marginBottom: 8, textDecoration: 'none' }}>返回策略库</Link>
            <div className="section-title">策略详情</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, color: 'var(--fg-0)' }}>{data?.card.name ?? strategyId}</div>
            {data?.card.description && <div style={{ color: 'var(--fg-2)', fontSize: 12, marginTop: 5 }}>{data.card.description}</div>}
          </div>
          <button className="btn primary" disabled={refreshMutation.isPending} onClick={() => refreshMutation.mutate()}>
            {refreshMutation.isPending ? '刷新中...' : '刷新统计'}
          </button>
        </div>

        <div className="panel" style={{ padding: 12, display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12 }}>
          <select className="mr-input" style={{ width: 100 }} value={timeframe} onChange={e => setTimeframe(e.target.value)}>
            {TIMEFRAMES.map(tf => <option key={tf} value={tf}>{tf}</option>)}
          </select>
          <select className="mr-input" style={{ width: 100 }} value={direction} onChange={e => setDirection(e.target.value)}>
            <option value="all">全部方向</option>
            <option value="long">做多</option>
            <option value="short">做空</option>
          </select>
          <select className="mr-input" style={{ width: 120 }} value={minSamples} onChange={e => setMinSamples(Number(e.target.value))}>
            <option value={10}>样本 ≥ 10</option>
            <option value={30}>样本 ≥ 30</option>
            <option value={100}>样本 ≥ 100</option>
          </select>
          <select className="mr-input" style={{ width: 130 }} value={sortBy} onChange={e => setSortBy(e.target.value)}>
            {SORT_FIELDS.map(item => <option key={item.value} value={item.value}>按{item.label}</option>)}
          </select>
          <button className="btn ghost" onClick={() => setSortDir(sortDir === 'desc' ? 'asc' : 'desc')}>
            {sortDir === 'desc' ? '降序' : '升序'}
          </button>
          {refreshMutation.data && <span className="mono" style={{ color: 'var(--fg-2)' }}>已更新 {refreshMutation.data.upserts ?? 0} 条统计</span>}
        </div>

        {isLoading ? (
          <div className="panel" style={{ padding: 18, color: 'var(--fg-2)' }}>加载策略表现...</div>
        ) : isError || !data ? (
          <div className="panel" style={{ padding: 18, color: 'var(--down)' }}>策略详情接口不可用</div>
        ) : (
          <StrategyPerfTable
            rows={data.rows}
            strategyId={strategyId}
            sortBy={sortBy}
            sortDir={sortDir}
            onSort={onSort}
            onShowTrades={showTrades}
          />
        )}

        {tradeRow && (
          <div ref={detailsRef}>
            <TradeDetailsPanel
              row={tradeRow}
              trades={tradesQuery.data?.items ?? []}
              isLoading={tradesQuery.isLoading}
              error={tradesQuery.error}
              onClose={() => setTradeRow(null)}
            />
          </div>
        )}

        {backtestRunId != null && (
          <div ref={detailsRef}>
            <BacktestTradesPanel
              run={backtestsQuery.data?.items.find(item => item.id === backtestRunId) ?? null}
              trades={backtestTradesQuery.data?.items ?? []}
              isLoading={backtestTradesQuery.isLoading}
              error={backtestTradesQuery.error}
              onClose={() => setBacktestRunId(null)}
              strategyId={strategyId}
            />
          </div>
        )}

        <BacktestHistoryPanel
          items={backtestsQuery.data?.items ?? []}
          isLoading={backtestsQuery.isLoading}
          error={backtestsQuery.error}
          onShowTrades={(id) => {
            setTradeRow(null)
            setBacktestRunId(id)
            window.setTimeout(() => detailsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0)
          }}
        />
      </div>
    </div>
  )
}

function BacktestHistoryPanel({
  items,
  isLoading,
  error,
  onShowTrades,
}: {
  items: StrategyBacktestSummary[]
  isLoading: boolean
  error: Error | null
  onShowTrades: (id: number) => void
}) {
  return (
    <div className="panel" style={{ marginTop: 12, overflow: 'hidden' }}>
      <div className="panel-header"><span>历史回测</span><span className="mono">{items.length} 条</span></div>
      {isLoading ? (
        <div style={{ padding: 16, color: 'var(--fg-2)' }}>加载历史回测...</div>
      ) : error ? (
        <div style={{ padding: 16, color: 'var(--down)' }}>历史回测接口不可用：{error.message}</div>
      ) : items.length === 0 ? (
        <div style={{ padding: 16, color: 'var(--fg-2)' }}>暂无多级别回测记录。运行一次回测后会自动出现在这里。</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <div style={{ minWidth: 980 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr 72px 140px 90px 90px 90px 82px 80px', gap: 8, padding: '9px 12px', borderBottom: '1px solid var(--line-1)', color: 'var(--fg-2)', fontSize: 11, fontWeight: 700 }}>
              <div>运行时间</div><div>品种</div><div>周期</div><div>区间</div><div>收益</div><div>胜率</div><div>回撤</div><div>交易</div><div>操作</div>
            </div>
            {items.map(item => (
              <div key={item.id} style={{ display: 'grid', gridTemplateColumns: '110px 1fr 72px 140px 90px 90px 90px 82px 80px', gap: 8, alignItems: 'center', padding: '10px 12px', borderBottom: '1px solid var(--line-1)' }}>
                <div className="mono" style={{ color: 'var(--fg-2)' }}>{formatTime(item.created_at)}</div>
                <div className="mono" style={{ color: 'var(--fg-0)', fontWeight: 700 }}>{item.symbol}</div>
                <div className="chip">{item.timeframe.toUpperCase()}</div>
                <div className="mono" style={{ color: 'var(--fg-2)' }}>{item.start_date}~{item.end_date}</div>
                <div className="mono" style={{ color: item.total_return_pct >= 0 ? 'var(--up)' : 'var(--down)' }}>{item.total_return_pct.toFixed(2)}%</div>
                <div className="mono">{item.win_rate_pct.toFixed(1)}%</div>
                <div className="mono">{item.max_drawdown_pct.toFixed(1)}%</div>
                <div className="mono">{item.total_trades} 笔</div>
                <button className="btn small" onClick={() => onShowTrades(item.id)}>明细</button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function BacktestTradesPanel({
  run,
  trades,
  isLoading,
  error,
  onClose,
  strategyId,
}: {
  run: StrategyBacktestSummary | null
  trades: StrategyBacktestTrade[]
  isLoading: boolean
  error: Error | null
  onClose: () => void
  strategyId?: string
}) {
  const filteredTrades = strategyId
    ? trades.filter(t => (t.signal_names ?? []).some(s => s.toLowerCase() === strategyId.toLowerCase()))
    : trades
  const wins = filteredTrades.filter(t => t.pnl_pct != null && t.pnl_pct > 0).length
  const winRate = filteredTrades.length > 0 ? (wins / filteredTrades.length * 100).toFixed(1) : null
  return (
    <div className="panel fade-in" style={{ marginTop: 12, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, padding: 12, borderBottom: '1px solid var(--line-1)' }}>
        <div>
          <div className="section-title">回测交易明细</div>
          <div className="mono" style={{ marginTop: 4, color: 'var(--fg-0)', fontWeight: 700 }}>
            {run ? `${run.symbol} · ${run.timeframe.toUpperCase()} · ${run.start_date}~${run.end_date}` : '回测记录'}
          </div>
          {winRate != null && (
            <div style={{ marginTop: 4, color: 'var(--fg-2)', fontSize: 12 }}>
              {filteredTrades.length} 笔 · 胜率 <span style={{ color: 'var(--up)', fontWeight: 700 }}>{winRate}%</span>
            </div>
          )}
        </div>
        <button className="btn small ghost" onClick={onClose}>关闭</button>
      </div>
      {isLoading ? (
        <div style={{ padding: 16, color: 'var(--fg-2)' }}>加载回测交易...</div>
      ) : error ? (
        <div style={{ padding: 16, color: 'var(--down)' }}>回测交易接口不可用：{error.message}</div>
      ) : filteredTrades.length === 0 ? (
        <div style={{ padding: 16, color: 'var(--fg-2)' }}>此回测没有交易。</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <div style={{ minWidth: 860 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 88px 88px 82px 92px 82px 1fr', gap: 8, padding: '9px 12px', color: 'var(--fg-2)', fontSize: 11, fontWeight: 700, borderBottom: '1px solid var(--line-1)' }}>
              <div>入场</div><div>出场</div><div>入场价</div><div>出场价</div><div>盈亏%</div><div>实际盈亏</div><div>权重</div><div>信号</div>
            </div>
            {filteredTrades.map((trade, index) => (
              <div key={`${trade.entry_time}-${index}`} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 88px 88px 82px 92px 82px 1fr', gap: 8, alignItems: 'center', padding: '10px 12px', borderBottom: '1px solid var(--line-1)' }}>
                <div className="mono" style={{ color: 'var(--fg-2)' }}>{formatUnixTime(trade.entry_time)}</div>
                <div className="mono" style={{ color: 'var(--fg-2)' }}>{formatUnixTime(trade.exit_time)}</div>
                <div className="mono">{formatPrice(trade.entry_price)}</div>
                <div className="mono">{formatPrice(trade.exit_price)}</div>
                <div className="mono" style={{ color: trade.pnl_pct >= 0 ? 'var(--up)' : 'var(--down)' }}>{trade.pnl_pct.toFixed(2)}%</div>
                <div className="mono" style={{ color: (trade.pnl_amount ?? 0) >= 0 ? 'var(--up)' : 'var(--down)' }}>{trade.pnl_amount == null ? '-' : trade.pnl_amount.toFixed(2)}</div>
                <div className="mono">{trade.signal_weight ?? '-'}</div>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>{(trade.signal_names ?? []).map(name => <span key={name} className="chip info">{name}</span>)}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function TradeDetailsPanel({
  row,
  trades,
  isLoading,
  error,
  onClose,
}: {
  row: StrategySymbolPerf
  trades: StrategyTrade[]
  isLoading: boolean
  error: Error | null
  onClose: () => void
}) {
  const directionLabel = row.direction === 'long' ? '做多' : '做空'
  return (
    <div className="panel fade-in" style={{ marginTop: 12, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, padding: 12, borderBottom: '1px solid var(--line-1)' }}>
        <div>
          <div className="section-title">交易明细</div>
          <div className="mono" style={{ marginTop: 4, color: 'var(--fg-0)', fontWeight: 700 }}>
            {row.symbol} · {row.timeframe.toUpperCase()} · {directionLabel}
          </div>
        </div>
        <button className="btn small ghost" onClick={onClose}>关闭</button>
      </div>
      {isLoading ? (
        <div style={{ padding: 16, color: 'var(--fg-2)' }}>加载交易明细...</div>
      ) : error ? (
        <div style={{ padding: 16, color: 'var(--down)' }}>交易明细接口不可用：{error.message}</div>
      ) : trades.length === 0 ? (
        <div style={{ padding: 16, color: 'var(--fg-2)' }}>暂无该品种的入场/出场记录。</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <div style={{ minWidth: 820 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1.15fr 1.15fr 66px 92px 92px 78px 82px 82px', gap: 8, padding: '9px 12px', color: 'var(--fg-2)', fontSize: 11, fontWeight: 700, borderBottom: '1px solid var(--line-1)' }}>
            <div>入场时间</div>
            <div>出场时间</div>
            <div>方向</div>
            <div>入场价</div>
            <div>出场价</div>
            <div>结果</div>
            <div>实际盈亏</div>
            <div>盈亏%</div>
          </div>
          {trades.map((trade, index) => (
            <div key={`${trade.entry_time}-${index}`} style={{ display: 'grid', gridTemplateColumns: '1.15fr 1.15fr 66px 92px 92px 78px 82px 82px', gap: 8, alignItems: 'center', padding: '10px 12px', borderBottom: '1px solid var(--line-1)' }}>
              <div className="mono" style={{ color: 'var(--fg-2)' }}>{formatTime(trade.entry_time)}</div>
              <div className="mono" style={{ color: 'var(--fg-2)' }}>{trade.exit_time ? formatTime(trade.exit_time) : '未出场'}</div>
              <div className={`chip ${trade.direction === 'long' ? 'up' : 'down'}`}>{trade.direction === 'long' ? '做多' : '做空'}</div>
              <div className="mono">{formatPrice(trade.entry_price)}</div>
              <div className="mono">{trade.exit_price == null ? '-' : formatPrice(trade.exit_price)}</div>
              <div>{outcomeLabel(trade.outcome)}</div>
              <div className="mono" style={{ color: (trade.realized_r ?? 0) >= 0 ? 'var(--up)' : 'var(--down)' }}>
                {trade.realized_r == null ? '-' : `${trade.realized_r.toFixed(2)}R`}
              </div>
              <div className="mono" style={{ color: (trade.pnl_pct ?? 0) >= 0 ? 'var(--up)' : 'var(--down)' }}>
                {trade.pnl_pct == null ? '-' : `${trade.pnl_pct.toFixed(2)}%`}
              </div>
            </div>
          ))}
          </div>
        </div>
      )}
    </div>
  )
}

function formatTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function formatUnixTime(value: number) {
  return new Date(value * 1000).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function formatPrice(value: number) {
  if (value >= 100) return value.toFixed(2)
  if (value >= 1) return value.toFixed(4)
  return value.toPrecision(4)
}

function outcomeLabel(value: string | null) {
  const labels: Record<string, string> = {
    win: '盈利',
    loss: '亏损',
    timeout: '超时',
    open: '持仓',
  }
  return value ? (labels[value] ?? value) : '未回填'
}
