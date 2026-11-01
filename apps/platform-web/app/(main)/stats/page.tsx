'use client'

import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { backfillEvents, getStatsDetail, getStatsOverview } from '@/lib/api'
import SimpleChart from '@/components/chart/SimpleChart'

const TIMEFRAMES = ['15min', '1h', '4h', '1day']

type Summary = {
  n: number
  wins: number
  losses: number
  win_rate: number
  expectancy_r: number
  profit_factor: number
  wilson_low: number
  wilson_high: number
  insufficient: boolean
  censored: number
}

type OverviewRow = Summary & { indicator_id: string }

function pct(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : `${(value * 100).toFixed(1)}%`
}
function num(value: number | null | undefined, digits = 3) {
  return value === null || value === undefined ? '—' : value.toFixed(digits)
}

export default function StatsPage() {
  const queryClient = useQueryClient()
  const [symbol, setSymbol] = useState('BTC-USD')
  const [timeframe, setTimeframe] = useState('1day')
  const [selected, setSelected] = useState<string>('')

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('symbol')) setSymbol(params.get('symbol')!.toUpperCase())
    if (params.get('timeframe')) setTimeframe(params.get('timeframe')!)
  }, [])

  const overviewQ = useQuery({
    queryKey: ['stats', 'overview'],
    queryFn: () => getStatsOverview({ min_sample: 30 }),
    retry: false,
  })

  const detailQ = useQuery({
    queryKey: ['stats', 'detail', selected, symbol, timeframe],
    queryFn: () => getStatsDetail(selected, { symbol, timeframe }),
    enabled: !!selected,
    retry: false,
  })

  const backfill = useMutation({
    mutationFn: () => backfillEvents({ symbol, timeframe, label: true }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['stats'] })
      queryClient.invalidateQueries({ queryKey: ['watch'] })
    },
  })

  const rows: OverviewRow[] = overviewQ.data?.indicators ?? []
  const detail = detailQ.data

  const equityLine = useMemo(() => {
    if (!detail?.equity?.length) return []
    return [
      {
        id: 'equity',
        label: '累计 R',
        color: '#22c55e',
        data: detail.equity.map((point: { time: number; equity: number }) => ({
          time: point.time,
          value: point.equity,
        })),
      },
    ]
  }, [detail])

  return (
    <div className="p-4 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold mr-2">指标统计</h1>
        <input className="input mono w-32" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
        <select className="input" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
          {TIMEFRAMES.map((item) => (
            <option key={item}>{item}</option>
          ))}
        </select>
        <button className="btn small accent" onClick={() => backfill.mutate()} disabled={backfill.isPending}>
          {backfill.isPending ? '回填中…' : '回填事件'}
        </button>
        {backfill.data && (
          <span className="text-xs opacity-70">
            {backfill.data.symbol} / {backfill.data.timeframe}：K 线 {backfill.data.bars}，事件{' '}
            {backfill.data.events}（新增 {backfill.data.inserted}），结果 {backfill.data.outcomes}
          </span>
        )}
        {backfill.error && (
          <span className="text-xs" style={{ color: 'var(--down)' }}>
            {String((backfill.error as Error).message)}
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[420px_1fr] gap-3">
        <div className="panel overflow-auto" style={{ maxHeight: 640 }}>
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-[var(--bg-1)]">
              <tr className="text-left opacity-70">
                <th className="p-2">指标</th>
                <th className="p-2">样本</th>
                <th className="p-2">胜率</th>
                <th className="p-2">期望 R</th>
                <th className="p-2">Wilson 下界</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.indicator_id}
                  className={`cursor-pointer hover:bg-[var(--bg-2)] ${selected === row.indicator_id ? 'bg-[var(--bg-2)]' : ''}`}
                  onClick={() => setSelected(row.indicator_id)}
                >
                  <td className="p-2 mono">
                    {row.indicator_id}
                    {row.insufficient && <span className="chip ml-1">样本不足</span>}
                  </td>
                  <td className="p-2 mono">{row.n}</td>
                  <td className="p-2 mono">{pct(row.win_rate)}</td>
                  <td className="p-2 mono" style={{ color: row.expectancy_r >= 0 ? 'var(--up)' : 'var(--down)' }}>
                    {num(row.expectancy_r)}
                  </td>
                  <td className="p-2 mono">{pct(row.wilson_low)}</td>
                </tr>
              ))}
              {!rows.length && (
                <tr>
                  <td colSpan={5} className="p-4 text-xs opacity-60">
                    还没有回填数据。选好品种/周期后点「回填事件」。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="space-y-3">
          {detail && (
            <>
              <div className="panel p-3">
                <div className="flex flex-wrap items-center gap-4 text-sm">
                  <span className="font-semibold">{detail.indicator_id}</span>
                  <span>n = {detail.summary.n}</span>
                  <span>胜率 {pct(detail.summary.win_rate)}</span>
                  <span>期望 {num(detail.summary.expectancy_r)}R</span>
                  <span>盈亏比 {num(detail.summary.profit_factor, 2)}</span>
                  <span>Wilson [{pct(detail.summary.wilson_low)}, {pct(detail.summary.wilson_high)}]</span>
                </div>
                {equityLine.length > 0 && (
                  <SimpleChart showCandles={false} lines={equityLine} height={160} />
                )}
              </div>

              <div className="panel p-3">
                <div className="text-sm font-semibold mb-2">按年份</div>
                <table className="w-full text-xs">
                  <thead className="opacity-70">
                    <tr className="text-left">
                      <th className="p-1">年份</th>
                      <th className="p-1">样本</th>
                      <th className="p-1">胜率</th>
                      <th className="p-1">期望 R</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(detail.by_year ?? []).map((group: { key: string; n: number; win_rate: number; expectancy_r: number }) => (
                      <tr key={group.key}>
                        <td className="p-1 mono">{group.key}</td>
                        <td className="p-1 mono">{group.n}</td>
                        <td className="p-1 mono">{pct(group.win_rate)}</td>
                        <td className="p-1 mono">{num(group.expectancy_r)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="panel p-3 overflow-auto" style={{ maxHeight: 320 }}>
                <div className="text-sm font-semibold mb-2">历史触发（点「跳到当时」回到那根 K 线）</div>
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-[var(--bg-1)] opacity-70">
                    <tr className="text-left">
                      <th className="p-1">时间</th>
                      <th className="p-1">方向</th>
                      <th className="p-1">入场</th>
                      <th className="p-1">结果</th>
                      <th className="p-1">R</th>
                      <th className="p-1" />
                    </tr>
                  </thead>
                  <tbody>
                    {(detail.outcomes ?? []).map((row: {
                      uid: string
                      entry_time: number
                      direction: string
                      entry_price: number
                      exit_reason: string
                      r_multiple: number
                    }) => (
                      <tr key={row.uid} className="border-t border-[var(--line-1)]">
                        <td className="p-1 mono">{new Date(row.entry_time * 1000).toLocaleString('zh-CN', { hour12: false })}</td>
                        <td className="p-1">{row.direction === 'long' ? '多' : '空'}</td>
                        <td className="p-1 mono">{row.entry_price}</td>
                        <td className="p-1">{row.exit_reason}</td>
                        <td className="p-1 mono" style={{ color: row.r_multiple >= 0 ? 'var(--up)' : 'var(--down)' }}>
                          {num(row.r_multiple)}
                        </td>
                        <td className="p-1">
                          <a
                            className="btn small"
                            href={`/watch?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&t=${row.entry_time}`}
                          >
                            跳到当时
                          </a>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          {!detail && <div className="panel p-6 text-sm opacity-60">左侧选一个指标查看历史胜率与每次触发。</div>}
        </div>
      </div>
    </div>
  )
}
