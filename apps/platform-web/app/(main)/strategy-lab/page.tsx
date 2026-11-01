'use client'

import { useState, useMemo, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getStrategyLabCatalog, runStrategyLab } from '@/lib/api'
import type {
  StrategyLabCatalog,
  StrategyLabCatalogEntry,
  StrategyLabRun,
  StrategyLabVariant,
} from '@/types'

const KIND_LABEL: Record<string, string> = {
  multi_position: '多仓',
  single_position: '单仓',
}
const QUICK_SYMBOLS = ['BTC-USD', 'ETH-USD', 'AAPL', 'NVDA']

function fmt(n: number | null | undefined, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return n.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
}
function pct(n: number | null | undefined) {
  return n === null || n === undefined ? '—' : `${fmt(n)}%`
}
function fmtCell(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : fmt(v, 4)
  if (Array.isArray(v)) return v.join('/')
  return String(v)
}

export default function StrategyLabPage() {
  const catalogQ = useQuery<StrategyLabCatalog>({
    queryKey: ['strategy-lab', 'catalog'],
    queryFn: getStrategyLabCatalog,
    retry: false,
  })

  const strategies = catalogQ.data?.strategies ?? []

  // ── 筛选 ──────────────────────────────────────────────────────────────
  const [search, setSearch] = useState('')
  const [kindFilter, setKindFilter] = useState<'all' | 'multi_position' | 'single_position'>('all')

  const filtered = useMemo(() => {
    return strategies.filter((s) => {
      if (kindFilter !== 'all' && s.kind !== kindFilter) return false
      if (search) {
        const q = search.toLowerCase()
        const hay = `${s.name} ${s.id} ${s.tags.join(' ')} ${s.category}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [strategies, search, kindFilter])

  // ── 选中策略 + 运行参数 ────────────────────────────────────────────────
  const [selectedId, setSelectedId] = useState<string>('')
  const selected: StrategyLabCatalogEntry | undefined = useMemo(
    () => strategies.find((s) => s.id === selectedId),
    [strategies, selectedId]
  )
  const [symbol, setSymbol] = useState('BTC-USD')
  const [timeframe, setTimeframe] = useState('1day')
  const [submitted, setSubmitted] = useState<{ id: string; symbol: string; tf: string } | null>(null)

  // 选中策略变化时，把周期纠正为该策略支持的第一个
  useEffect(() => {
    if (selected && !selected.timeframes.includes(timeframe)) {
      setTimeframe(selected.timeframes[0])
    }
  }, [selected]) // eslint-disable-line react-hooks/exhaustive-deps

  const runQ = useQuery<StrategyLabRun>({
    queryKey: ['strategy-lab', 'run', submitted?.id, submitted?.symbol, submitted?.tf],
    queryFn: () => runStrategyLab(submitted!.id, submitted!.symbol, submitted!.tf),
    enabled: !!submitted,
    retry: false,
  })

  const [variantId, setVariantId] = useState<string>('')
  useEffect(() => {
    if (runQ.data) setVariantId(runQ.data.best_variant ?? runQ.data.variants[0]?.id ?? '')
  }, [runQ.data])

  const activeVariant: StrategyLabVariant | undefined =
    runQ.data?.variants.find((v) => v.id === variantId) ?? runQ.data?.variants[0]

  const tradeColumns = useMemo(() => {
    const t = activeVariant?.trades?.[0]
    return t ? Object.keys(t) : []
  }, [activeVariant])

  const canRun = !!selected && !!symbol.trim()

  return (
    <div className="p-6 max-w-[1400px] mx-auto">
      <header className="mb-5">
        <h1 className="text-xl font-semibold">策略台</h1>
        <p className="text-sm text-[var(--text-secondary,#888)] mt-1">
          统一的可筛选策略实验台 —— 所有策略共用同一套信号 / 回测 / 结果。一次运行产出若干变体。
        </p>
      </header>

      <div className="grid grid-cols-[300px_1fr] gap-5">
        {/* ── 左：目录 + 筛选 ── */}
        <aside className="space-y-3">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索策略 / 标签…"
            className="w-full px-3 py-2 rounded-md border border-[var(--border,#333)] bg-transparent text-sm"
          />
          <div className="flex gap-1.5 text-xs">
            {(['all', 'multi_position', 'single_position'] as const).map((k) => (
              <button
                key={k}
                onClick={() => setKindFilter(k)}
                className={`px-2.5 py-1 rounded-full border ${
                  kindFilter === k
                    ? 'bg-[var(--accent,#3b82f6)] text-white border-transparent'
                    : 'border-[var(--border,#333)]'
                }`}
              >
                {k === 'all' ? '全部' : KIND_LABEL[k]}
              </button>
            ))}
          </div>

          {catalogQ.isLoading && <p className="text-sm text-gray-500">加载目录…</p>}
          {catalogQ.isError && <p className="text-sm text-red-500">目录加载失败</p>}

          <ul className="space-y-1.5">
            {filtered.map((s) => (
              <li key={s.id}>
                <button
                  onClick={() => setSelectedId(s.id)}
                  className={`w-full text-left px-3 py-2 rounded-md border transition ${
                    selectedId === s.id
                      ? 'border-[var(--accent,#3b82f6)] bg-[var(--accent,#3b82f6)]/10'
                      : 'border-[var(--border,#333)] hover:border-[var(--accent,#3b82f6)]/50'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">{s.name}</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--surface-2,#222)]">
                      {KIND_LABEL[s.kind]}
                    </span>
                  </div>
                  <div className="flex gap-1 mt-1 flex-wrap">
                    {s.tags.map((t) => (
                      <span key={t} className="text-[10px] text-gray-500">
                        #{t}
                      </span>
                    ))}
                  </div>
                </button>
              </li>
            ))}
            {filtered.length === 0 && !catalogQ.isLoading && (
              <li className="text-sm text-gray-500 px-1">无匹配策略</li>
            )}
          </ul>
        </aside>

        {/* ── 右：运行 + 结果 ── */}
        <main className="space-y-5">
          {!selected && (
            <div className="text-sm text-gray-500 py-20 text-center border border-dashed border-[var(--border,#333)] rounded-lg">
              从左侧选择一个策略开始
            </div>
          )}

          {selected && (
            <>
              <section className="p-4 rounded-lg border border-[var(--border,#333)] space-y-3">
                <div>
                  <h2 className="font-medium">{selected.name}</h2>
                  <p className="text-xs text-gray-500 mt-0.5">{selected.description}</p>
                </div>
                <div className="flex flex-wrap items-end gap-3">
                  <label className="text-xs">
                    <span className="block text-gray-500 mb-1">品种</span>
                    <input
                      value={symbol}
                      onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                      className="px-3 py-1.5 rounded-md border border-[var(--border,#333)] bg-transparent text-sm w-36"
                    />
                  </label>
                  <div className="flex gap-1">
                    {QUICK_SYMBOLS.map((s) => (
                      <button
                        key={s}
                        onClick={() => setSymbol(s)}
                        className="text-[11px] px-2 py-1 rounded border border-[var(--border,#333)] hover:border-[var(--accent,#3b82f6)]/50"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                  <label className="text-xs">
                    <span className="block text-gray-500 mb-1">周期</span>
                    <select
                      value={timeframe}
                      onChange={(e) => setTimeframe(e.target.value)}
                      className="px-3 py-1.5 rounded-md border border-[var(--border,#333)] bg-transparent text-sm"
                    >
                      {selected.timeframes.map((tf) => (
                        <option key={tf} value={tf}>
                          {tf}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    disabled={!canRun}
                    onClick={() => setSubmitted({ id: selected.id, symbol: symbol.trim(), tf: timeframe })}
                    className="px-4 py-1.5 rounded-md bg-[var(--accent,#3b82f6)] text-white text-sm disabled:opacity-40"
                  >
                    运行回测
                  </button>
                </div>
              </section>

              {runQ.isFetching && <p className="text-sm text-gray-500">运行中…(首次需同步该品种数据,可能稍慢)</p>}
              {runQ.isError && (
                <p className="text-sm text-red-500">
                  运行失败:{(runQ.error as any)?.response?.data?.detail ?? '请检查品种 / 周期是否有数据'}
                </p>
              )}

              {runQ.data && (
                <>
                  {/* 变体卡片 */}
                  <section className="grid grid-cols-2 md:grid-cols-3 gap-3">
                    {runQ.data.variants.map((v) => (
                      <button
                        key={v.id}
                        onClick={() => setVariantId(v.id)}
                        className={`text-left p-3 rounded-lg border transition ${
                          variantId === v.id
                            ? 'border-[var(--accent,#3b82f6)] bg-[var(--accent,#3b82f6)]/10'
                            : 'border-[var(--border,#333)] hover:border-[var(--accent,#3b82f6)]/50'
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <span className="text-sm font-medium">{v.label}</span>
                          {v.stats.is_best && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-500">最优</span>
                          )}
                        </div>
                        <div className="mt-2 grid grid-cols-2 gap-y-1 text-xs">
                          <span className="text-gray-500">总收益</span>
                          <span className={v.stats.total_return >= 0 ? 'text-green-500' : 'text-red-500'}>
                            {pct(v.stats.total_return)}
                          </span>
                          <span className="text-gray-500">胜率</span>
                          <span>{pct(v.stats.win_rate)}</span>
                          <span className="text-gray-500">回撤</span>
                          <span>{pct(v.stats.max_drawdown)}</span>
                          <span className="text-gray-500">成交数</span>
                          <span>{v.stats.n_trades}</span>
                        </div>
                      </button>
                    ))}
                  </section>

                  {/* 逐笔表（动态列，兼容不同策略） */}
                  {activeVariant && activeVariant.trades.length > 0 && (
                    <section className="rounded-lg border border-[var(--border,#333)] overflow-auto max-h-[480px]">
                      <table className="w-full text-xs">
                        <thead className="sticky top-0 bg-[var(--surface-2,#1a1a1a)]">
                          <tr>
                            {tradeColumns.map((c) => (
                              <th key={c} className="px-3 py-2 text-left font-medium text-gray-400 whitespace-nowrap">
                                {c}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {activeVariant.trades.slice(0, 200).map((t, i) => (
                            <tr key={i} className="border-t border-[var(--border,#262626)]">
                              {tradeColumns.map((c) => (
                                <td key={c} className="px-3 py-1.5 whitespace-nowrap">
                                  {fmtCell((t as Record<string, unknown>)[c])}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {activeVariant.trades.length > 200 && (
                        <p className="text-[11px] text-gray-500 px-3 py-2">
                          仅显示前 200 笔(共 {activeVariant.trades.length} 笔)
                        </p>
                      )}
                    </section>
                  )}
                </>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  )
}
