'use client'

import Link from 'next/link'
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getStrategyOverview } from '@/lib/api'
import type { MaStrategyStats, StrategyOverviewData, StrategyOverviewSymbol, StrategyOverviewVariant } from '@/types'

type ViewMode = 'symbol' | 'strategy'

export default function StrategyOverviewPage() {
  const [mode, setMode] = useState<ViewMode>('symbol')
  const [symbol, setSymbol] = useState('BTC-USD')
  const [parameter, setParameter] = useState('all')
  const query = useQuery<StrategyOverviewData>({
    queryKey: ['strategy-overview'],
    queryFn: getStrategyOverview,
    staleTime: 60_000,
    retry: false,
  })
  const data = query.data
  const current = data?.symbols.find(item => item.symbol === symbol) ?? data?.symbols[0]

  const ranked = useMemo(() => {
    if (!data) return []
    if (parameter === 'all') return data.ranked_symbols
    return [...data.symbols].sort((a, b) => {
      const av = a.variants.find(item => item.parameter === parameter)?.stats
      const bv = b.variants.find(item => item.parameter === parameter)?.stats
      return (bv?.n_trades ?? 0) - (av?.n_trades ?? 0)
        || (bv?.expectancy ?? -Infinity) - (av?.expectancy ?? -Infinity)
    })
  }, [data, parameter])

  if (query.isLoading) return <StatePanel text="正在汇总四个市场的回测证据…" />
  if (query.error || !data) return <StatePanel text="策略总览加载失败，请检查回测快照与后端连接。" error />

  return (
    <div className="page">
      <div className="page-inner" style={{ maxWidth: 1480 }}>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 20, marginBottom: 16, flexWrap: 'wrap' }}>
          <div>
            <div className="section-title">Strategy Overview</div>
            <h1 style={{ margin: '5px 0 0', fontSize: 23, letterSpacing: '-.02em' }}>策略总览</h1>
            <div style={{ marginTop: 5, color: 'var(--fg-2)', fontSize: 11.5 }}>
              同一份回测证据，两种观察方向。先判断证据是否充分，再比较收益表现。
            </div>
          </div>
          <div style={{ marginLeft: 'auto', display: 'flex', padding: 2, background: 'var(--bg-2)', border: '1px solid var(--line-1)' }}>
            <ModeButton active={mode === 'symbol'} onClick={() => setMode('symbol')} title="通过品种看策略" subtitle="这个品种适合什么策略" />
            <ModeButton active={mode === 'strategy'} onClick={() => setMode('strategy')} title="通过策略看品种" subtitle="这个策略适合什么品种" />
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr 1fr', border: '1px solid var(--line-1)', background: 'var(--bg-1)', marginBottom: 14 }}>
          <MethodCell label="默认排序" value={data.methodology.ranking} />
          <MethodCell label="样本口径" value="参数样本合计，可能重叠" />
          <MethodCell label="最近生成" value={data.generated_at.replace('T', ' ')} last />
        </div>

        {mode === 'symbol' && current ? (
          <SymbolView data={data} current={current} onSymbol={setSymbol} />
        ) : (
          <StrategyView data={data} parameter={parameter} onParameter={setParameter} ranked={ranked} />
        )}

        <div style={{ marginTop: 12, color: 'var(--fg-3)', fontSize: 10.5 }}>
          {data.methodology.evidence_note} · {data.methodology.sample_note}
        </div>
      </div>
    </div>
  )
}

function SymbolView({ data, current, onSymbol }: { data: StrategyOverviewData; current: StrategyOverviewSymbol; onSymbol: (value: string) => void }) {
  return (
    <>
      <div className="panel" style={{ padding: 14, marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
          <div>
            <div className="stat-label">分析品种</div>
            <select className="input" value={current.symbol} onChange={event => onSymbol(event.target.value)} style={{ minWidth: 230, marginTop: 5 }}>
              {data.symbols.map(item => <option key={item.symbol} value={item.symbol}>{item.symbol} — {item.name}</option>)}
            </select>
          </div>
          <div style={{ width: 1, height: 38, background: 'var(--line-1)' }} />
          <SummaryMetric label="已接策略" value="1" note="策略A" />
          <SummaryMetric label="参数样本合计" value={String(current.sample_count)} note="MA参数间未去重" />
          <SummaryMetric label="正期望参数" value={`${current.positive_variants}/${current.variant_count}`} note={`证据：${current.evidence}`} color={current.positive_variants >= 3 ? 'var(--up)' : 'var(--fg-0)'} />
          <SummaryMetric label="加权单笔期望" value={signed(current.weighted_expectancy)} note="按各参数样本数加权" color={metricColor(current.weighted_expectancy)} />
          <SummaryMetric label="优先验证参数" value={current.preferred_parameter ? `MA${current.preferred_parameter}` : '-'} note="正期望中样本优先" />
          <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
            <div className="stat-label">数据覆盖</div>
            <div className="mono" style={{ marginTop: 5, fontSize: 11 }}>{fmtDate(current.data_range.start)} — {fmtDate(current.data_range.end)}</div>
            <div style={{ marginTop: 3, fontSize: 10, color: 'var(--fg-3)' }}>{current.data_source} · {current.bar_count}根日K</div>
          </div>
        </div>
      </div>

      <section className="panel" style={{ overflowX: 'auto' }}>
        <div className="panel-header" style={{ alignItems: 'flex-start' }}>
          <div>
            <div>策略A · {current.strategy_name}</div>
            <div style={{ marginTop: 4, color: 'var(--fg-3)', fontSize: 10, textTransform: 'none', letterSpacing: 0 }}>
              前收盘不高于均线 → 本日收盘突破均线 → 实体涨幅&gt;3% · 支持多笔独立持仓
            </div>
          </div>
          <div style={{ display: 'flex', gap: 7, alignItems: 'center' }}>
            <Evidence value={current.evidence} />
            {current.current_signals.length > 0 && <span className="chip" style={{ color: 'var(--up)', borderColor: 'var(--up)' }}>当前触发 {current.current_signals.map(v => `MA${v}`).join(' / ')}</span>}
            <Link href={`/ma-strategies?symbol=${encodeURIComponent(current.symbol)}&ma=${current.preferred_parameter ?? '120'}`} className="btn small">查看图表与逐笔交易 →</Link>
          </div>
        </div>
        <VariantTable variants={current.variants} />
      </section>
    </>
  )
}

function StrategyView({ data, parameter, onParameter, ranked }: { data: StrategyOverviewData; parameter: string; onParameter: (value: string) => void; ranked: StrategyOverviewSymbol[] }) {
  const strategy = data.strategies[0]
  const breadth = parameter === 'all'
    ? data.symbols.filter(item => item.positive_variants >= 3).length
    : strategy.positive_symbol_breadth[parameter] ?? 0
  return (
    <>
      <div className="panel" style={{ padding: 14, marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 18, flexWrap: 'wrap' }}>
          <div style={{ minWidth: 270 }}>
            <div className="stat-label">分析策略</div>
            <div style={{ marginTop: 5, fontSize: 15, fontWeight: 700 }}>策略A · {strategy.name}</div>
            <div style={{ marginTop: 3, color: 'var(--fg-3)', fontSize: 10.5 }}>{strategy.description}</div>
          </div>
          <div style={{ display: 'flex', gap: 4, padding: 2, background: 'var(--bg-2)', border: '1px solid var(--line-1)' }}>
            {['all', ...strategy.parameters].map(value => (
              <button key={value} className={parameter === value ? 'btn small accent' : 'btn small ghost'} onClick={() => onParameter(value)}>
                {value === 'all' ? '参数综合' : `MA${value}`}
              </button>
            ))}
          </div>
          <SummaryMetric label={parameter === 'all' ? '稳定适配品种' : '正期望品种'} value={`${breadth}/${data.symbols.length}`} note={parameter === 'all' ? '至少3组参数为正期望' : `MA${parameter}跨市场覆盖`} color={breadth >= 3 ? 'var(--up)' : 'var(--fg-0)'} />
          <SummaryMetric label="排序原则" value="样本优先" note="再看期望值与稳定性" />
        </div>
      </div>

      <div className="panel" style={{ overflowX: 'auto' }}>
        <div className="panel-header">
          <span>品种适配排名</span>
          <span style={{ color: 'var(--fg-3)', fontSize: 10, textTransform: 'none', letterSpacing: 0 }}>
            {parameter === 'all' ? '综合四组参数；样本数为参数样本合计' : `固定参数 MA${parameter}，各品种直接可比`}
          </span>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
          <thead><tr>{['排名', '品种', '证据判断', '样本数', '单笔期望', '胜率', '盈利因子', '正期望参数', '最近信号', ''].map(label => <th key={label} style={th}>{label}</th>)}</tr></thead>
          <tbody>
            {ranked.map((item, index) => {
              const variant = parameter === 'all' ? null : item.variants.find(row => row.parameter === parameter)
              const stats = variant?.stats
              const sample = stats?.n_trades ?? item.sample_count
              const expectancy = stats?.expectancy ?? item.weighted_expectancy
              const winRate = stats?.win_rate ?? item.weighted_win_rate
              const pf = stats?.profit_factor
              const evidence = parameter === 'all' ? item.evidence : variantEvidence(stats)
              const signal = parameter === 'all'
                ? item.current_signals.map(v => `MA${v}`).join(' / ')
                : variant?.latest_setup?.triggered ? `MA${parameter} 已触发` : ''
              return (
                <tr key={item.symbol} style={{ borderTop: '1px solid var(--line-0)' }}>
                  <td style={td}><span className="mono" style={{ color: index === 0 ? 'var(--accent)' : 'var(--fg-3)', fontWeight: 700 }}>{String(index + 1).padStart(2, '0')}</span></td>
                  <td style={td}><div className="mono" style={{ fontWeight: 700 }}>{item.symbol}</div><div style={{ color: 'var(--fg-3)', fontSize: 10 }}>{item.name}</div></td>
                  <td style={td}><Evidence value={evidence} /></td>
                  <td style={td}><b className="mono">{sample}</b></td>
                  <td style={{ ...td, color: metricColor(expectancy) }}><b className="mono">{signed(expectancy)}</b></td>
                  <td style={td} className="mono">{winRate.toFixed(1)}%</td>
                  <td style={td} className="mono">{pf == null ? '综合' : pf.toFixed(2)}</td>
                  <td style={td}>{parameter === 'all' ? `${item.positive_variants}/${item.variant_count}` : expectancy > 0 ? '是' : '否'}</td>
                  <td style={{ ...td, color: signal ? 'var(--up)' : 'var(--fg-3)' }}>{signal || '—'}</td>
                  <td style={{ ...td, textAlign: 'right' }}><Link href={`/ma-strategies?symbol=${encodeURIComponent(item.symbol)}&ma=${parameter === 'all' ? item.preferred_parameter ?? '120' : parameter}`} className="btn small ghost">下钻</Link></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}

function VariantTable({ variants }: { variants: StrategyOverviewVariant[] }) {
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
      <thead><tr>{['参数', '当前状态', '样本数', '单笔期望', '盈利因子', '胜率', '累计收益回撤', '最近信号', '证据判断'].map(label => <th key={label} style={th}>{label}</th>)}</tr></thead>
      <tbody>{[...variants].sort((a, b) => b.stats.n_trades - a.stats.n_trades).map(item => (
        <tr key={item.parameter} style={{ borderTop: '1px solid var(--line-0)' }}>
          <td style={td}><b className="mono">MA{item.parameter}</b>{item.stats.is_best && <span style={{ marginLeft: 6, color: '#a16207', fontSize: 9 }}>历史收益最高</span>}</td>
          <td style={{ ...td, color: item.latest_setup?.triggered ? 'var(--up)' : 'var(--fg-3)' }}>{item.latest_setup?.triggered ? '● 已触发' : item.latest_setup?.crossed ? '突破但实体不足' : '未触发'}</td>
          <td style={td}><b className="mono">{item.stats.n_trades}</b></td>
          <td style={{ ...td, color: metricColor(item.stats.expectancy ?? 0) }}><b className="mono">{signed(item.stats.expectancy ?? 0)}</b></td>
          <td style={td} className="mono">{item.stats.profit_factor?.toFixed(2) ?? '—'}</td>
          <td style={td} className="mono">{item.stats.win_rate.toFixed(1)}%</td>
          <td style={td} className="mono">-{item.stats.max_drawdown.toFixed(1)}%</td>
          <td style={td} className="mono">{fmtDate(item.last_signal_time)}</td>
          <td style={td}><Evidence value={variantEvidence(item.stats)} /></td>
        </tr>
      ))}</tbody>
    </table>
  )
}

function ModeButton({ active, onClick, title, subtitle }: { active: boolean; onClick: () => void; title: string; subtitle: string }) {
  return <button onClick={onClick} style={{ border: 0, padding: '8px 14px', minWidth: 190, textAlign: 'left', cursor: 'pointer', background: active ? 'var(--bg-0)' : 'transparent', color: active ? 'var(--fg-0)' : 'var(--fg-2)', boxShadow: active ? '0 0 0 1px var(--line-1)' : 'none' }}><div style={{ fontSize: 12, fontWeight: 700 }}>{title}</div><div style={{ marginTop: 2, fontSize: 9.5, color: 'var(--fg-3)' }}>{subtitle}</div></button>
}
function MethodCell({ label, value, last = false }: { label: string; value: string; last?: boolean }) { return <div style={{ padding: '9px 12px', borderRight: last ? 0 : '1px solid var(--line-1)' }}><span className="stat-label">{label}</span><span style={{ marginLeft: 9, fontSize: 10.5 }}>{value}</span></div> }
function SummaryMetric({ label, value, note, color = 'var(--fg-0)' }: { label: string; value: string; note: string; color?: string }) { return <div><div className="stat-label">{label}</div><div className="mono" style={{ marginTop: 4, fontSize: 16, fontWeight: 700, color }}>{value}</div><div style={{ marginTop: 2, color: 'var(--fg-3)', fontSize: 9.5 }}>{note}</div></div> }
function Evidence({ value }: { value: string }) { const color = value === '稳定' || value === '正期望' ? 'var(--up)' : value === '偏弱' ? 'var(--down)' : 'var(--fg-2)'; return <span className="chip" style={{ color, borderColor: color }}>{value}</span> }
function variantEvidence(stats?: MaStrategyStats): string { if (!stats || stats.n_trades < 8) return '样本不足'; if ((stats.expectancy ?? 0) > 0 && (stats.profit_factor ?? 0) > 1) return stats.n_trades >= 20 ? '正期望' : '候选'; return '偏弱' }
function signed(value: number): string { return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%` }
function metricColor(value: number): string { return value > 0 ? 'var(--up)' : value < 0 ? 'var(--down)' : 'var(--fg-0)' }
function fmtDate(value?: number | null): string { return value ? new Date(value * 1000).toISOString().slice(0, 10) : '—' }
function StatePanel({ text, error = false }: { text: string; error?: boolean }) { return <div className="page"><div className="page-inner"><div className="panel" style={{ padding: 28, textAlign: 'center', color: error ? 'var(--down)' : 'var(--fg-2)' }}>{text}</div></div></div> }

const th: React.CSSProperties = { padding: '8px 10px', textAlign: 'left', color: 'var(--fg-3)', fontSize: 9.5, fontWeight: 600, letterSpacing: '.06em', background: 'var(--bg-2)', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '10px', verticalAlign: 'middle' }
