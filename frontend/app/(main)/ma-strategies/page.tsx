'use client'

import { useState, useMemo, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getMaStrategiesManifest, getMaStrategiesSymbol, refreshMaStrategiesSymbol } from '@/lib/api'
import type {
  MaStrategyKey,
  MaStrategyManifest,
  MaStrategySymbolData,
  MaStrategyTrade,
  MaExitReason,
} from '@/types'
import { MaStrategyChart } from '@/components/chart/MaStrategyChart'

const MA_PERIODS = ['10', '20', '30', '55', '60', '99', '120'] as const
const STRATEGIES: { key: MaStrategyKey; label: string; desc: string }[] = [
  { key: 'breakout', label: 'A · 突破', desc: 'K线收盘上穿MA + 实体涨幅 > 3%' },
  { key: 'pullback', label: 'B · 回调', desc: 'MA120 之上 + 最低点触及 MA 后收回' },
]
const EXIT_REASON_COLOR: Record<MaExitReason, string> = {
  '止损': '#ef4444',
  '保本': '#f59e0b',
  '均线': '#3b82f6',
  '强制平仓': '#9ca3af',
}

function fmtDate(unixSec: number) {
  const d = new Date(unixSec * 1000)
  return d.toISOString().slice(0, 10)
}
function fmt(n: number, digits = 2) {
  return n.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export default function MaStrategiesPage() {
  const manifestQ = useQuery<MaStrategyManifest>({
    queryKey: ['ma-strategies', 'manifest'],
    queryFn: getMaStrategiesManifest,
    retry: false,
  })

  const symbols = manifestQ.data?.symbols ?? []
  const [symbol, setSymbol] = useState<string>('')
  useEffect(() => {
    if (!symbol && symbols.length > 0) setSymbol(symbols[0].code)
  }, [symbols, symbol])

  const dataQ = useQuery<MaStrategySymbolData>({
    queryKey: ['ma-strategies', symbol],
    queryFn: () => getMaStrategiesSymbol(symbol),
    enabled: !!symbol,
    retry: false,
  })

  const [strategy, setStrategy] = useState<MaStrategyKey>('pullback')
  const [maKey, setMaKey] = useState<string>('120')
  const [selectedTrade, setSelectedTrade] = useState<MaStrategyTrade | null>(null)

  const queryClient = useQueryClient()
  const refreshMu = useMutation({
    mutationFn: (sym: string) => refreshMaStrategiesSymbol(sym),
    onSuccess: (fresh: MaStrategySymbolData) => {
      queryClient.setQueryData(['ma-strategies', symbol], fresh)
      setSelectedTrade(null)
    },
  })

  // 切换品种时复位
  useEffect(() => {
    setSelectedTrade(null)
  }, [symbol, strategy, maKey])

  const data = dataQ.data
  const currentBlock = data?.strategies?.[strategy]?.[maKey]
  const stats = currentBlock?.stats
  const trades = currentBlock?.trades ?? []

  // 最优 MA 快捷按钮
  const bestMa = useMemo(() => {
    if (!data) return null
    const block = data.strategies[strategy]
    for (const k of MA_PERIODS) {
      if (block?.[k]?.stats?.is_best) return k
    }
    return null
  }, [data, strategy])

  // 错误：未生成数据
  if (manifestQ.error) {
    return (
      <div style={{ padding: 24 }}>
        <div className="panel" style={{ padding: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>暂无均线策略回测数据</div>
          <div style={{ color: 'var(--fg-2)', fontSize: 12 }}>
            请先在仓库根目录运行：<code>python3 backtest_ma_strategies.py</code>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="page">
    <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* ── 控件栏 ───────────────────────────────────── */}
      <div className="panel" style={{ padding: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 11, color: 'var(--fg-2)', letterSpacing: '.1em', textTransform: 'uppercase' }}>品种</span>
            <select
              className="input"
              value={symbol}
              onChange={e => setSymbol(e.target.value)}
              style={{ minWidth: 220 }}
            >
              {symbols.map(s => (
                <option key={s.code} value={s.code}>
                  {s.code} — {s.name}
                </option>
              ))}
            </select>
          </label>

          <div style={{ display: 'flex', gap: 4, padding: 2, background: 'var(--bg-2)', borderRadius: 8 }}>
            {STRATEGIES.map(s => (
              <button
                key={s.key}
                onClick={() => setStrategy(s.key)}
                title={s.desc}
                className={strategy === s.key ? 'btn small accent' : 'btn small ghost'}
                style={{ minWidth: 96 }}
              >
                {s.label}
              </button>
            ))}
          </div>

          <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 11, color: 'var(--fg-2)', letterSpacing: '.1em', textTransform: 'uppercase' }}>MA</span>
            <div style={{ display: 'flex', gap: 2 }}>
              {MA_PERIODS.map(n => (
                <button
                  key={n}
                  onClick={() => setMaKey(n)}
                  className={maKey === n ? 'btn small accent' : 'btn small ghost'}
                  style={{ minWidth: 44, padding: '4px 6px' }}
                >
                  {n}
                </button>
              ))}
            </div>
          </label>

          {bestMa && bestMa !== maKey && (
            <button className="btn small" onClick={() => setMaKey(bestMa)} style={{ background: '#fde68a', color: '#78350f' }}>
              ★ 跳到最优 MA{bestMa}
            </button>
          )}

          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
            {data && (
              <span style={{ color: 'var(--fg-3)', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                数据生成于 {data.generated_at.replace('T', ' ')}
              </span>
            )}
            <button
              onClick={() => symbol && refreshMu.mutate(symbol)}
              disabled={!symbol || refreshMu.isPending}
              className={refreshMu.isPending ? 'btn small ghost' : 'btn small'}
              title="重新下载该品种最新行情并跑回测（约 5-30 秒）"
            >
              {refreshMu.isPending ? '更新中…' : '↻ 更新数据'}
            </button>
          </div>
        </div>
        {refreshMu.error && (
          <div style={{
            marginTop: 8, padding: '6px 10px',
            background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)',
            borderRadius: 6, color: 'var(--down)', fontSize: 12,
            whiteSpace: 'pre-wrap', fontFamily: 'var(--font-mono)',
          }}>
            更新失败：{(refreshMu.error as any)?.response?.data?.detail ?? (refreshMu.error as Error).message}
          </div>
        )}
      </div>

      {dataQ.isLoading && (
        <div className="panel" style={{ padding: 24, textAlign: 'center', color: 'var(--fg-2)' }}>载入中…</div>
      )}
      {dataQ.error && (
        <div className="panel" style={{ padding: 24, textAlign: 'center', color: 'var(--down)' }}>
          数据加载失败：{(dataQ.error as Error).message}
        </div>
      )}

      {/* ── 统计条 ──────────────────────────────────── */}
      {data && stats && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(7, 1fr)',
            background: 'var(--bg-1)',
            border: '1px solid var(--line-1)',
            borderRadius: 'var(--radius-lg)',
            overflow: 'hidden',
          }}
        >
          {[
            { l: '总收益率', v: (stats.total_return >= 0 ? '+' : '') + fmt(stats.total_return) + '%', c: stats.total_return >= 0 ? 'var(--up)' : 'var(--down)' },
            { l: '交易笔数', v: String(stats.n_trades), c: 'var(--fg-0)' },
            { l: '胜率', v: fmt(stats.win_rate, 1) + '%', c: 'var(--fg-0)' },
            { l: '最大回撤', v: '-' + fmt(stats.max_drawdown) + '%', c: 'var(--down)' },
            { l: '最大单笔', v: '+' + fmt(stats.max_profit) + '%', c: 'var(--up)' },
            { l: '平均盈利', v: '+' + fmt(stats.avg_profit) + '%', c: 'var(--up)' },
            { l: '平均盈亏比', v: stats.avg_rr != null ? fmt(stats.avg_rr) + 'R' : '-', c: 'var(--fg-0)' },
          ].map((m, i, arr) => (
            <div key={i} style={{ borderRight: i < arr.length - 1 ? '1px solid var(--line-1)' : 'none', padding: '12px 10px' }}>
              <div className="stat-label">{m.l}</div>
              <div className="mono" style={{ fontSize: 15, color: m.c, fontWeight: 600, marginTop: 3 }}>{m.v}</div>
            </div>
          ))}
        </div>
      )}

      {/* ── K 线图 ──────────────────────────────────── */}
      {data && (
        <div className="panel">
          <div className="panel-header">
            <span>交易标记图</span>
            <span className="mono" style={{ textTransform: 'none', letterSpacing: 0, fontSize: 10, color: 'var(--fg-2)' }}>
              {data.candles.length} 根K线 · {trades.length} 笔交易
              {stats?.is_best && <span style={{ marginLeft: 8, color: '#a16207' }}>★ 当前为该策略下最优 MA</span>}
            </span>
          </div>
          <div style={{ padding: '8px 12px' }}>
            <MaStrategyChart
              candles={data.candles}
              maLine={data.ma_lines[maKey] ?? []}
              maLabel={`MA${maKey}`}
              trendMa={data.trend_ma}
              trades={trades}
              selectedTrade={selectedTrade}
            />
          </div>
          <div style={{ padding: '4px 12px 10px', display: 'flex', gap: 14, fontSize: 11, color: 'var(--fg-2)', flexWrap: 'wrap' }}>
            <Legend color="#22c55e" shape="▲" label="入场" />
            <Legend color={EXIT_REASON_COLOR['止损']} shape="▼" label="止损出场" />
            <Legend color={EXIT_REASON_COLOR['保本']} shape="▼" label="保本出场" />
            <Legend color={EXIT_REASON_COLOR['均线']} shape="▼" label="跌破均线" />
            <Legend color={EXIT_REASON_COLOR['强制平仓']} shape="▼" label="强制平仓" />
            <span style={{ marginLeft: 'auto', fontStyle: 'italic' }}>
              点击下方明细中任一笔交易，图表会聚焦到该笔的时间区间并显示止损/入场/出场价位线
            </span>
          </div>
        </div>
      )}

      {/* ── 交易明细表 ──────────────────────────────── */}
      {data && trades.length > 0 && (
        <TradeTable
          trades={trades}
          selected={selectedTrade}
          onSelect={t => setSelectedTrade(prev => (prev === t ? null : t))}
        />
      )}
      {data && trades.length === 0 && (
        <div className="panel" style={{ padding: 24, textAlign: 'center', color: 'var(--fg-2)' }}>
          该参数下无交易记录
        </div>
      )}
    </div>{/* end flex column */}
    </div>
  )
}

function Legend({ color, shape, label }: { color: string; shape: string; label: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <span style={{ color, fontSize: 13, lineHeight: 1 }}>{shape}</span>
      {label}
    </span>
  )
}

function TradeTable({
  trades,
  selected,
  onSelect,
}: {
  trades: MaStrategyTrade[]
  selected: MaStrategyTrade | null
  onSelect: (t: MaStrategyTrade) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? trades : trades.slice(0, 20)
  const cols = '36px 1fr 1fr 1fr 1fr 60px 80px 80px 70px 80px 50px'

  return (
    <div className="panel" style={{ overflow: 'hidden' }}>
      <div className="panel-header">
        <span>交易明细</span>
        <span className="mono" style={{ textTransform: 'none', letterSpacing: 0 }}>{trades.length} 笔</span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: cols,
            padding: '8px 12px',
            borderBottom: '1px solid var(--line-2)',
            fontSize: 10,
            letterSpacing: '.14em',
            textTransform: 'uppercase',
            color: 'var(--fg-2)',
            fontWeight: 600,
            minWidth: 1040,
          }}
        >
          <span>#</span><span>入场日期</span><span>入场价</span><span>出场日期</span><span>出场价</span>
          <span style={{ textAlign: 'right' }}>天数</span>
          <span style={{ textAlign: 'right' }}>止损价</span>
          <span style={{ textAlign: 'right' }}>盈亏%</span>
          <span style={{ textAlign: 'right' }}>浮盈%</span>
          <span style={{ textAlign: 'right' }}>盈亏比</span>
          <span style={{ textAlign: 'center' }}>原因</span>
        </div>
        {visible.map((t, i) => {
          const isSel = t === selected
          return (
            <div
              key={i}
              onClick={() => onSelect(t)}
              style={{
                display: 'grid',
                gridTemplateColumns: cols,
                padding: '8px 12px',
                borderBottom: '1px solid var(--line-1)',
                fontFamily: 'var(--font-mono)',
                fontSize: 11.5,
                minWidth: 1040,
                alignItems: 'center',
                cursor: 'pointer',
                background: isSel ? 'var(--bg-2)' : 'transparent',
              }}
              onMouseEnter={e => { if (!isSel) (e.currentTarget.style.background = 'var(--bg-2)') }}
              onMouseLeave={e => { if (!isSel) (e.currentTarget.style.background = 'transparent') }}
            >
              <span style={{ color: 'var(--fg-3)' }}>#{i + 1}</span>
              <span style={{ color: 'var(--fg-1)' }}>{fmtDate(t.entry_time)}</span>
              <span style={{ color: 'var(--fg-0)' }}>{t.entry_price.toLocaleString()}</span>
              <span style={{ color: 'var(--fg-1)' }}>{fmtDate(t.exit_time)}</span>
              <span style={{ color: 'var(--fg-0)' }}>{t.exit_price.toLocaleString()}</span>
              <span style={{ textAlign: 'right', color: 'var(--fg-2)' }}>
                {Math.max(1, Math.round((t.exit_time - t.entry_time) / 86400))}d
              </span>
              <span style={{ textAlign: 'right', color: 'var(--fg-2)' }}>{t.stop_loss.toLocaleString()}</span>
              <span style={{ textAlign: 'right', fontWeight: 600, color: t.pnl_pct >= 0 ? 'var(--up)' : 'var(--down)' }}>
                {t.pnl_pct >= 0 ? '+' : ''}{fmt(t.pnl_pct)}%
              </span>
              <span style={{ textAlign: 'right', color: 'var(--up)' }}>+{fmt(t.max_profit_pct)}%</span>
              <span style={{ textAlign: 'right', color: t.rr != null ? 'var(--up)' : 'var(--fg-3)', fontWeight: 600 }}>
                {t.rr != null ? fmt(t.rr) + 'R' : '-'}
              </span>
              <span style={{ textAlign: 'center', color: EXIT_REASON_COLOR[t.exit_reason], fontWeight: 600 }}>
                {t.exit_reason}
              </span>
            </div>
          )
        })}
      </div>
      {trades.length > 20 && (
        <div style={{ padding: 10, textAlign: 'center', borderTop: '1px solid var(--line-1)' }}>
          <button className="btn small ghost" onClick={() => setExpanded(v => !v)}>
            {expanded ? '收起' : `展开全部 ${trades.length} 笔`}
          </button>
        </div>
      )}
    </div>
  )
}
