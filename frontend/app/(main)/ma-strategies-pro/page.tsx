'use client'

import { useState, useMemo, useEffect, type ReactNode } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getMaStrategiesProManifest,
  getMaStrategiesProSymbol,
  refreshMaStrategiesProSymbol,
} from '@/lib/api'
import type {
  MaStrategyKey,
  MaStrategyManifest,
  MaStrategy3xSymbolData,
  MaStrategy3xTrade,
  MaExitReason,
} from '@/types'
import { MaStrategyChart } from '@/components/chart/MaStrategyChart'
import { TradeReplayPlayer } from '@/components/chart/TradeReplayPlayer'

const MA_PERIODS = ['10', '20', '30', '55', '60', '99', '120'] as const
const STRATEGIES: { key: MaStrategyKey; label: string; desc: string }[] = [
  { key: 'breakout', label: 'A · 突破', desc: 'K线收盘上穿MA + 实体涨幅 > 3%' },
  { key: 'pullback', label: 'B · 回调', desc: 'MA120 之上 + 最低点触及 MA 后收回' },
]
const ADD_ACCENT = '#60a5fa'
const EXIT_COLOR: Record<MaExitReason, string> = {
  '止损': '#ef4444',
  '保本': '#f59e0b',
  '均线': '#3b82f6',
  '强制平仓': '#9ca3af',
}

function fmtDate(unixSec: number) {
  return new Date(unixSec * 1000).toISOString().slice(0, 10)
}
function fmt(n: number, digits = 2) {
  return n.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
}
function fmtPrice(n: number) {
  const abs = Math.abs(n)
  const digits = abs >= 1000 ? 2 : abs >= 1 ? 4 : 6
  return n.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export default function MaStrategiesProPage() {
  const [leverage, setLeverage]   = useState<2 | 3>(2)
  const [strategy, setStrategy]   = useState<MaStrategyKey>('pullback')
  const [maKey,    setMaKey]      = useState<string>('120')
  const [symbol,   setSymbol]     = useState<string>('')
  const [replayTrade, setReplayTrade] = useState<MaStrategy3xTrade | null>(null)

  const queryClient = useQueryClient()
  const accent = ADD_ACCENT

  const manifestQ = useQuery<MaStrategyManifest>({
    queryKey: ['ma-strategies-pro', 'manifest', leverage],
    queryFn:  () => getMaStrategiesProManifest(leverage),
    retry: false,
  })
  const symbols = manifestQ.data?.symbols ?? []

  useEffect(() => {
    if (symbols.length === 0) return
    if (!symbol || !symbols.find(s => s.code === symbol)) {
      setSymbol(symbols[0].code)
    }
  }, [symbols, symbol])

  const dataQ = useQuery<MaStrategy3xSymbolData>({
    queryKey: ['ma-strategies-pro', symbol, leverage],
    queryFn:  () => getMaStrategiesProSymbol(symbol, leverage),
    enabled: !!symbol,
    retry: false,
  })

  const refreshMu = useMutation({
    mutationFn: (sym: string) => refreshMaStrategiesProSymbol(sym, leverage),
    onSuccess: (fresh: MaStrategy3xSymbolData) => {
      queryClient.setQueryData(['ma-strategies-pro', symbol, leverage], fresh)
    },
  })

  useEffect(() => { setReplayTrade(null) }, [symbol, strategy, maKey, leverage])

  const data         = dataQ.data
  const currentBlock = data?.strategies?.[strategy]?.[maKey]
  const stats        = currentBlock?.stats
  const trades       = (currentBlock?.trades ?? []) as MaStrategy3xTrade[]

  const bestMa = useMemo(() => {
    if (!data) return null
    const block = data.strategies[strategy]
    for (const k of MA_PERIODS) {
      if (block?.[k]?.stats?.is_best) return k
    }
    return null
  }, [data, strategy])

  if (manifestQ.error) {
    return (
      <div style={{ padding: 24 }}>
        <div className="panel" style={{ padding: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>
            暂无浮盈加仓回测数据
          </div>
          <div style={{ color: 'var(--fg-2)', fontSize: 12 }}>
            请先在仓库根目录运行：<code>python3 scripts/backtests/backtest_ma_strategies_3x.py</code>
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

          {/* 杠杆切换 */}
          <div style={{ display: 'flex', gap: 4, padding: 2, background: 'var(--bg-2)', borderRadius: 8 }}>
            {([2, 3] as (2 | 3)[]).map(lev => (
              <button
                key={lev}
                onClick={() => setLeverage(lev)}
                className={leverage === lev ? 'btn small accent' : 'btn small ghost'}
                style={{ minWidth: 52, fontWeight: 700 }}
              >
                {lev}x 杠杆
              </button>
            ))}
          </div>

          {/* 品种选择 */}
          <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 11, color: 'var(--fg-2)', letterSpacing: '.1em', textTransform: 'uppercase' }}>品种</span>
            <select
              className="input"
              value={symbol}
              onChange={e => setSymbol(e.target.value)}
              style={{ minWidth: 220 }}
            >
              {symbols.map(s => (
                <option key={s.code} value={s.code}>{s.code} — {s.name}</option>
              ))}
            </select>
          </label>

          {/* 策略切换 */}
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

          {/* MA 周期 */}
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
                {data.generated_at.replace('T', ' ')}
              </span>
            )}
            <button
              onClick={() => symbol && refreshMu.mutate(symbol)}
              disabled={!symbol || refreshMu.isPending}
              className={refreshMu.isPending ? 'btn small ghost' : 'btn small'}
              title={`重新跑 浮盈-${leverage}x 回测`}
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

      {/* ── 统计条 ────────────── */}
      {data && stats && (
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(8, 1fr)',
          background: 'var(--bg-1)', border: '1px solid var(--line-1)',
          borderRadius: 'var(--radius-lg)', overflow: 'hidden',
        }}>
          {[
            { l: '总收益率', v: (stats.total_return >= 0 ? '+' : '') + fmt(stats.total_return) + '%', c: stats.total_return >= 0 ? 'var(--up)' : 'var(--down)' },
            { l: '交易笔数', v: String(stats.n_trades), c: 'var(--fg-0)' },
            { l: '胜率', v: fmt(stats.win_rate, 1) + '%', c: 'var(--fg-0)' },
            { l: '最大回撤', v: '-' + fmt(stats.max_drawdown) + '%', c: 'var(--down)' },
            { l: '最大单笔', v: '+' + fmt(stats.max_profit) + '%', c: 'var(--up)' },
            { l: '平均盈利', v: '+' + fmt(stats.avg_profit) + '%', c: 'var(--up)' },
            { l: '平均盈亏比', v: stats.avg_rr != null ? fmt(stats.avg_rr) + 'R' : '-', c: 'var(--fg-0)' },
            { l: '杠杆倍数', v: `${stats.leverage ?? leverage}x`, c: accent },
          ].map((m, i, arr) => (
            <div key={i} style={{ borderRight: i < arr.length - 1 ? '1px solid var(--line-1)' : 'none', padding: '12px 10px' }}>
              <div className="stat-label">{m.l}</div>
              <div className="mono" style={{ fontSize: 15, color: m.c, fontWeight: 600, marginTop: 3 }}>{m.v}</div>
            </div>
          ))}
        </div>
      )}

      {/* ── 模式说明 ──────────────────────────────── */}
      {data && stats && (
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
      )}

      {/* ── K 线图 ──────────────────────────────────── */}
      {data && (
        <div className="panel">
          <div className="panel-header">
            <span>交易标记图</span>
            <span className="mono" style={{ textTransform: 'none', letterSpacing: 0, fontSize: 10, color: 'var(--fg-2)' }}>
              {data.candles.length} 根K线 · {trades.length} 笔交易
              {stats?.is_best && <span style={{ marginLeft: 8, color: '#a16207' }}>★ 当前为最优 MA</span>}
            </span>
          </div>
          <div style={{ padding: '8px 12px' }}>
            <MaStrategyChart
              candles={data.candles}
              maLine={data.ma_lines[maKey] ?? []}
              maLabel={`MA${maKey}`}
              trendMa={data.trend_ma}
              trades={trades}
              selectedTrade={null}
            />
          </div>
          <div style={{ padding: '4px 12px 10px', display: 'flex', gap: 14, fontSize: 11, color: 'var(--fg-2)', flexWrap: 'wrap' }}>
            <Legend color="#22c55e" shape="▲" label="入场" />
            <Legend color={EXIT_COLOR['止损']}    shape="▼" label="止损出场" />
            <Legend color={EXIT_COLOR['保本']}    shape="▼" label="保本出场" />
            <Legend color={EXIT_COLOR['均线']}    shape="▼" label="跌破均线" />
            <Legend color={EXIT_COLOR['强制平仓']} shape="▼" label="强制平仓" />
            <span style={{ marginLeft: 'auto', fontStyle: 'italic' }}>
              点击下方交易行 → 播放逐K线动画（含加仓过程）
            </span>
          </div>
        </div>
      )}

      {/* ── 交易明细表 ──────────────────────────────── */}
      {data && trades.length > 0 && (
        <TradeTable
          trades={trades}
          onPlay={setReplayTrade}
          addAccent={accent}
        />
      )}
      {data && trades.length === 0 && (
        <div className="panel" style={{ padding: 24, textAlign: 'center', color: 'var(--fg-2)' }}>
          该参数下无交易记录
        </div>
      )}

      {replayTrade && data && (
        <TradeReplayPlayer
          trade={replayTrade}
          candles={data.candles}
          maLine={data.ma_lines[maKey] ?? []}
          trendMa={data.trend_ma}
          maLabel={`MA${maKey}`}
          onClose={() => setReplayTrade(null)}
        />
      )}
    </div>
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
  onPlay,
  addAccent,
}: {
  trades: MaStrategy3xTrade[]
  onPlay: (t: MaStrategy3xTrade) => void
  addAccent: string
}) {
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? trades : trades.slice(0, 20)
  const cols = '36px 1fr 1fr 1fr 1fr 60px 80px 90px 70px 80px 60px 50px 76px'

  return (
    <div className="panel" style={{ overflow: 'hidden' }}>
      <div className="panel-header">
        <span>逐笔明细</span>
        <span className="mono" style={{ textTransform: 'none', letterSpacing: 0, fontSize: 10, color: 'var(--fg-2)' }}>
          {trades.length} 笔 · 点击 ▶ 播放回放
        </span>
      </div>

      <div style={{ overflowX: 'auto' }}>

      <div style={{ display: 'grid', gridTemplateColumns: cols, gap: 0, borderBottom: '1px solid var(--line-1)', minWidth: 832 }}>
        {['#', '入场日期', '入场价', '出场日期', '出场价', '持仓天', '止损价', '收益%', '最大%', 'RR', '加仓', '原因', '回放'].map(h => (
          <div key={h} style={{
            padding: '6px 8px', fontSize: 10, fontWeight: 600, letterSpacing: '.06em',
            textTransform: 'uppercase', color: 'var(--fg-2)', textAlign: 'center',
            borderRight: '1px solid var(--line-1)',
          }}>{h}</div>
        ))}
      </div>

      {visible.map((t, idx) => {
        const isWin = t.pnl_pct >= 0
        const days  = Math.round((t.exit_time - t.entry_time) / 86400)
        return (
          <div
            key={idx}
            style={{
              display: 'grid', gridTemplateColumns: cols, minWidth: 832,
              background: isWin ? 'rgba(34,197,94,0.04)' : 'rgba(239,68,68,0.04)',
              borderBottom: '1px solid var(--line-1)',
            }}
          >
            <Cell>{idx + 1}</Cell>
            <Cell mono>{fmtDate(t.entry_time)}</Cell>
            <Cell mono>{fmtPrice(t.entry_price)}</Cell>
            <Cell mono>{fmtDate(t.exit_time)}</Cell>
            <Cell mono>{fmtPrice(t.exit_price)}</Cell>
            <Cell mono>{days}</Cell>
            <Cell mono>{fmtPrice(t.stop_loss)}</Cell>
            <Cell mono color={isWin ? 'var(--up)' : 'var(--down)'} bold>
              {(t.pnl_pct >= 0 ? '+' : '') + t.pnl_pct.toFixed(2) + '%'}
            </Cell>
            <Cell mono color="var(--up)">{'+' + t.max_profit_pct.toFixed(2) + '%'}</Cell>
            <Cell mono>{t.rr != null ? t.rr.toFixed(2) + 'R' : '-'}</Cell>
            <Cell mono color={addAccent}>{t.n_adds > 0 ? `+${t.n_adds}` : '-'}</Cell>
            <Cell>
              <span style={{
                fontSize: 10, padding: '2px 5px', borderRadius: 4,
                background: EXIT_COLOR[t.exit_reason] + '22',
                color: EXIT_COLOR[t.exit_reason],
              }}>{t.exit_reason}</span>
            </Cell>
            <Cell>
              <button
                className="btn small accent"
                onClick={() => onPlay(t)}
                style={{ padding: '3px 8px', fontSize: 11, whiteSpace: 'nowrap' }}
                title="播放此笔交易K线动画"
              >▶ 播放</button>
            </Cell>
          </div>
        )
      })}

      </div>

      {trades.length > 20 && (
        <div style={{ padding: '8px 12px', textAlign: 'center' }}>
          <button className="btn small ghost" onClick={() => setExpanded(e => !e)}>
            {expanded ? '收起' : `展开全部 ${trades.length} 笔`}
          </button>
        </div>
      )}
    </div>
  )
}

function Cell({
  children, mono, color, bold,
}: {
  children: ReactNode
  mono?: boolean
  color?: string
  bold?: boolean
}) {
  return (
    <div style={{
      padding: '5px 8px', fontSize: 11, textAlign: 'center',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: mono ? 'var(--font-mono)' : undefined,
      color: color ?? undefined,
      fontWeight: bold ? 700 : undefined,
      borderRight: '1px solid var(--line-1)',
    }}>
      {children}
    </div>
  )
}
