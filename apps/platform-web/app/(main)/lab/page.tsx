'use client'

import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  cleanupExperiments,
  createExperiment,
  deleteExperiment,
  deleteExperimentRun,
  getExperiment,
  getExperimentChart,
  getExperimentTrades,
  listExperiments,
  cancelExperimentRun,
  runExperiment,
} from '@/lib/api'
import SimpleChart, { type ChartMarker } from '@/components/chart/SimpleChart'

const SAMPLE_SPEC = {
  name: 'ma20 breakout',
  universe: { symbols: ['BTC-USD'], timeframes: ['1day'] },
  entry: { all: [{ indicator: 'ma.breakout', params: { periods: [20] } }] },
  exit: {
    any: [
      { type: 'stop', atr: 1.5 },
      { type: 'target', r: 2.0 },
      { type: 'time', bars: 20 },
    ],
  },
  costs: { fee_bps: 10, slippage_bps: 5 },
  validation: { split: 'holdout', dev_share: 0.7, min_trades: 30 },
}

function num(value: unknown, digits = 2) {
  return typeof value === 'number' ? value.toFixed(digits) : '—'
}

export default function LabPage() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [runId, setRunId] = useState<number | null>(null)
  const [name, setName] = useState('新实验')
  const [specText, setSpecText] = useState(JSON.stringify(SAMPLE_SPEC, null, 2))
  const [error, setError] = useState('')

  const listQ = useQuery({ queryKey: ['lab', 'list'], queryFn: () => listExperiments(false), retry: false })
  const detailQ = useQuery({
    queryKey: ['lab', 'detail', selectedId],
    queryFn: () => getExperiment(selectedId!),
    enabled: selectedId !== null,
    retry: false,
  })
  const tradesQ = useQuery({
    queryKey: ['lab', 'trades', selectedId, runId],
    queryFn: () => getExperimentTrades(selectedId!, runId!),
    enabled: selectedId !== null && runId !== null,
    retry: false,
  })
  const chartQ = useQuery({
    queryKey: ['lab', 'chart', selectedId, runId],
    queryFn: () => getExperimentChart(selectedId!, runId!),
    enabled: selectedId !== null && runId !== null,
    retry: false,
  })

  useEffect(() => {
    if (selectedId === null) return
    const runs = detailQ.data?.runs ?? []
    if (runs.length && runId === null) setRunId(runs[0].id)
  }, [detailQ.data, selectedId, runId])

  const create = useMutation({
    mutationFn: async () => {
      const spec = JSON.parse(specText)
      return createExperiment({ name, spec })
    },
    onSuccess: (data) => {
      setSelectedId(data.id)
      setRunId(null)
      queryClient.invalidateQueries({ queryKey: ['lab', 'list'] })
    },
    onError: (err: Error) => setError(String(err.message)),
  })

  const execute = useMutation({
    mutationFn: () => runExperiment(selectedId!),
    onSuccess: (data) => {
      setRunId(data.id)
      queryClient.invalidateQueries({ queryKey: ['lab', 'detail', selectedId] })
      queryClient.invalidateQueries({ queryKey: ['lab', 'list'] })
    },
    onError: (err: Error) => setError(String(err.message)),
  })

  const cancelRun = useMutation({
    mutationFn: (id: number) => cancelExperimentRun(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['lab', 'detail', selectedId] })
    },
    onError: (err: Error) => setError(String(err.message)),
  })

  const archive = useMutation({
    mutationFn: (purge: boolean) => deleteExperiment(selectedId!, purge),
    onSuccess: () => {
      setSelectedId(null)
      setRunId(null)
      queryClient.invalidateQueries({ queryKey: ['lab', 'list'] })
    },
  })

  const removeRun = useMutation({
    mutationFn: () => deleteExperimentRun(runId!),
    onSuccess: () => {
      setRunId(null)
      queryClient.invalidateQueries({ queryKey: ['lab', 'detail', selectedId] })
    },
  })

  const cleanup = useMutation({ mutationFn: (scope: 'failed' | 'stale' | 'orphan') => cleanupExperiments(scope) })

  const experiments = listQ.data?.experiments ?? []
  const detail = detailQ.data
  const run = detail?.runs?.find((item: { id: number }) => item.id === runId)
  const metrics = run?.metrics ?? {}

  const markers: ChartMarker[] = useMemo(() => {
    if (!chartQ.data) return []
    return chartQ.data.markers.map((marker: {
      time: number
      price: number
      kind: string
      direction: string
      reason?: string
      r_multiple?: number
    }) => ({
      time: marker.time,
      kind: marker.kind === 'entry' ? (marker.direction === 'long' ? 'entry' : 'exit') : 'exit',
      text: marker.kind === 'entry' ? 'B' : marker.reason ?? 'S',
      color: marker.kind === 'entry' ? '#22c55e' : '#f59e0b',
    }))
  }, [chartQ.data])

  return (
    <div className="p-4 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold mr-2">实验台</h1>
        <span className="chip">指标定义 → spec → run → 产物</span>
        <button className="btn small" onClick={() => cleanup.mutate('failed')}>
          清理失败运行
        </button>
        <button className="btn small" onClick={() => cleanup.mutate('orphan')}>
          清理孤儿产物
        </button>
        {cleanup.data && <span className="text-xs opacity-70">已删除 {cleanup.data.deleted}</span>}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[300px_1fr] gap-3">
        <div className="panel p-3 space-y-2">
          <div className="text-sm font-semibold">实验列表</div>
          <div className="overflow-auto space-y-1" style={{ maxHeight: 280 }}>
            {experiments.map((experiment: { id: number; name: string; status: string; last_run?: { status: string; metrics?: { n?: number } } }) => (
              <button
                key={experiment.id}
                className={`w-full text-left p-2 rounded hover:bg-[var(--bg-2)] ${selectedId === experiment.id ? 'bg-[var(--bg-2)]' : ''}`}
                onClick={() => {
                  setSelectedId(experiment.id)
                  setRunId(null)
                }}
              >
                <div className="flex items-center justify-between text-xs">
                  <span>{experiment.name}</span>
                  <span className="chip">{experiment.status}</span>
                </div>
                <div className="text-[11px] opacity-60 mono">
                  {experiment.last_run ? `n=${experiment.last_run.metrics?.n ?? '—'} / ${experiment.last_run.status}` : '未运行'}
                </div>
              </button>
            ))}
            {!experiments.length && <div className="text-xs opacity-60">还没有实验。</div>}
          </div>

          <div className="border-t border-[var(--line-1)] pt-2 space-y-2">
            <input className="input w-full" value={name} onChange={(event) => setName(event.target.value)} />
            <textarea
              className="input w-full mono text-xs"
              rows={12}
              value={specText}
              onChange={(event) => setSpecText(event.target.value)}
            />
            <button className="btn small accent w-full" onClick={() => create.mutate()} disabled={create.isPending}>
              {create.isPending ? '创建中…' : '创建实验'}
            </button>
            {error && (
              <div className="text-xs" style={{ color: 'var(--down)' }}>
                {error}
              </div>
            )}
          </div>
        </div>

        <div className="space-y-3">
          {!detail && <div className="panel p-6 text-sm opacity-60">左侧选择或创建一个实验。</div>}
          {detail && (
            <>
              <div className="panel p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold">{detail.name}</span>
                  <span className="chip">{detail.status}</span>
                  <span className="text-xs opacity-60 mono">spec #{detail.params_hash}</span>
                  <button className="btn small accent" onClick={() => execute.mutate()} disabled={execute.isPending}>
                    {execute.isPending ? '运行中…' : '运行'}
                  </button>
                  <button className="btn small" onClick={() => archive.mutate(false)}>
                    归档
                  </button>
                  <button className="btn small danger" onClick={() => archive.mutate(true)}>
                    彻底删除
                  </button>
                  {runId !== null && (
                    <button className="btn small danger" onClick={() => removeRun.mutate()}>
                      删除本次运行
                    </button>
                  )}
                </div>
                {runId !== null && run && (
                  <div className="mt-2 flex flex-wrap gap-4 text-xs">
                    <span>n = {num(metrics.n, 0)}</span>
                    <span>胜率 {num((metrics.win_rate ?? 0) * 100, 1)}%</span>
                    <span>期望 {num(metrics.expectancy_r)}R</span>
                    <span>总收益 {num(metrics.total_return_pct, 2)}%</span>
                    <span>最大回撤 {num(metrics.max_drawdown_pct, 2)}%</span>
                    <span>盈亏比 {num(metrics.profit_factor, 2)}</span>
                  </div>
                )}
                {run?.error && (
                  <div className="text-xs mt-2" style={{ color: 'var(--down)' }}>
                    {run.error}
                  </div>
                )}
              </div>

              {chartQ.data && (
                <div className="panel p-2">
                  <div className="text-xs opacity-60 px-2 pb-1">
                    {chartQ.data.symbol} / {chartQ.data.timeframe}：绿色 B = 入场，橙色 = 出场
                  </div>
                  <SimpleChart candles={chartQ.data.candles} markers={markers} height={380} />
                </div>
              )}

              {tradesQ.data && (
                <div className="panel p-3 overflow-auto" style={{ maxHeight: 260 }}>
                  <div className="text-sm font-semibold mb-2">交易明细（{tradesQ.data.count}）</div>
                  <table className="w-full text-xs">
                    <thead className="opacity-70">
                      <tr className="text-left">
                        <th className="p-1">入场</th>
                        <th className="p-1">方向</th>
                        <th className="p-1">入场价</th>
                        <th className="p-1">出场</th>
                        <th className="p-1">出场价</th>
                        <th className="p-1">R</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tradesQ.data.trades.map((trade: {
                        id: number
                        entry_time: number
                        direction: string
                        entry_price: number
                        exit_time: number
                        exit_price: number
                        exit_reason: string
                        r_multiple: number
                      }) => (
                        <tr key={trade.id} className="border-t border-[var(--line-1)]">
                          <td className="p-1 mono">{new Date(trade.entry_time * 1000).toLocaleString('zh-CN', { hour12: false })}</td>
                          <td className="p-1">{trade.direction === 'long' ? '多' : '空'}</td>
                          <td className="p-1 mono">{trade.entry_price}</td>
                          <td className="p-1">{trade.exit_reason}</td>
                          <td className="p-1 mono">{trade.exit_price}</td>
                          <td className="p-1 mono" style={{ color: trade.r_multiple >= 0 ? 'var(--up)' : 'var(--down)' }}>
                            {num(trade.r_multiple)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <div className="panel p-3 overflow-auto" style={{ maxHeight: 200 }}>
                <div className="text-sm font-semibold mb-1">运行历史</div>
                <table className="w-full text-xs">
                  <tbody>
                    {(detail.runs ?? []).map((item: { id: number; status: string; metrics: { n?: number; win_rate?: number }; started_at: string }) => (
                      <tr
                        key={item.id}
                        className={`cursor-pointer hover:bg-[var(--bg-2)] ${runId === item.id ? 'bg-[var(--bg-2)]' : ''}`}
                        onClick={() => setRunId(item.id)}
                      >
                        <td className="p-1 mono">#{item.id}</td>
                        <td className="p-1">{item.status}</td>
                        <td className="p-1 mono">n={item.metrics?.n ?? '—'}</td>
                        <td className="p-1 mono">{num((item.metrics?.win_rate ?? 0) * 100, 1)}%</td>
                        <td className="p-1 opacity-60">{item.started_at?.slice(0, 19)}</td>
                        <td className="p-1">
                          {['queued', 'running'].includes(item.status) && (
                            <button
                              className="btn small danger"
                              onClick={(event) => {
                                event.stopPropagation()
                                cancelRun.mutate(item.id)
                              }}
                            >
                              取消
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
