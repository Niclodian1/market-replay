'use client'

import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getUnifiedMaStrategiesManifest,
  getUnifiedMaStrategiesSymbol,
  refreshUnifiedMaStrategiesSymbol,
} from '@/lib/api'
import type {
  MaExitReason,
  UnifiedMaCombo,
  UnifiedMaLeverage,
  UnifiedMaManifest,
  UnifiedMaStrategy,
  UnifiedMaSymbolData,
  UnifiedMaTimeframe,
  UnifiedMaTrade,
} from '@/types'
import { MaStrategyChart } from '@/components/chart/MaStrategyChart'
import { TradeReplayPlayer } from '@/components/chart/TradeReplayPlayer'
import { MaStatsBar } from '@/components/strategies/MaStatsBar'
import { MaTradeTable } from '@/components/strategies/MaTradeTable'
import type { MaMode } from '@/components/strategies/MaControlBar'

const EXIT_REASON_COLOR: Record<MaExitReason, string> = {
  '止损': '#ef4444',
  '保本': '#f59e0b',
  '均线': '#3b82f6',
  '强制平仓': '#9ca3af',
}

const STRATEGY_LABEL: Record<UnifiedMaStrategy, string> = {
  A: '策略A · 突破',
  B: '策略B · 回调',
  C: '策略C · 多均线',
}

const STRATEGY_DESC: Record<UnifiedMaStrategy, string> = {
  A: '收盘上穿单条均线 + 实体涨幅过滤',
  B: 'MA120 上方回调触及均线后收回',
  C: '同根 K 线同时上穿多条均线',
}

const TF_LABEL: Record<UnifiedMaTimeframe, string> = { '4h': '4H', '1day': '1D' }
const LEV_LABEL: Record<UnifiedMaLeverage, string> = { none: '无杠杆', '2': '2x 杠杆', '3': '3x 杠杆' }
const PREFERRED: UnifiedMaCombo = { strategy: 'A', timeframe: '1day', leverage: 'none' }

export default function MaStrategiesPage() {
  const queryClient = useQueryClient()
  const [symbol, setSymbol] = useState('')
  const [strategy, setStrategy] = useState<UnifiedMaStrategy>('A')
  const [timeframe, setTimeframe] = useState<UnifiedMaTimeframe>('1day')
  const [leverage, setLeverage] = useState<UnifiedMaLeverage>('none')
  const [optionKey, setOptionKey] = useState('120')
  const [selectedTrade, setSelectedTrade] = useState<UnifiedMaTrade | null>(null)
  const [replayTrade, setReplayTrade] = useState<UnifiedMaTrade | null>(null)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const requestedSymbol = params.get('symbol')
    const requestedMa = params.get('ma')
    if (requestedSymbol) setSymbol(requestedSymbol)
    if (requestedMa && ['30', '60', '99', '120'].includes(requestedMa)) setOptionKey(requestedMa)
  }, [])

  const manifestQ = useQuery<UnifiedMaManifest>({
    queryKey: ['ma-strategies-unified', 'manifest'],
    queryFn: getUnifiedMaStrategiesManifest,
    retry: false,
  })

  const currentSymbol = useMemo(
    () => manifestQ.data?.symbols.find(s => s.code === symbol) ?? null,
    [manifestQ.data, symbol],
  )
  const available = currentSymbol?.available ?? []

  useEffect(() => {
    const symbols = manifestQ.data?.symbols ?? []
    if (symbols.length === 0) return
    if (!symbol || !symbols.some(s => s.code === symbol)) {
      setSymbol(symbols[0].code)
    }
  }, [manifestQ.data, symbol])

  useEffect(() => {
    if (!currentSymbol || available.length === 0) return
    const currentOk = available.some(c =>
      c.strategy === strategy && c.timeframe === timeframe && c.leverage === leverage,
    )
    if (currentOk) return

    const preferred = available.find(c =>
      c.strategy === PREFERRED.strategy && c.timeframe === PREFERRED.timeframe && c.leverage === PREFERRED.leverage,
    ) ?? available[0]
    setStrategy(preferred.strategy)
    setTimeframe(preferred.timeframe)
    setLeverage(preferred.leverage)
  }, [available, currentSymbol, strategy, timeframe, leverage])

  const dataQ = useQuery<UnifiedMaSymbolData>({
    queryKey: ['ma-strategies-unified', symbol, strategy, timeframe, leverage],
    queryFn: () => getUnifiedMaStrategiesSymbol(symbol, strategy, timeframe, leverage),
    enabled: !!symbol && available.some(c => c.strategy === strategy && c.timeframe === timeframe && c.leverage === leverage),
    retry: false,
  })

  const refreshMu = useMutation({
    mutationFn: () => refreshUnifiedMaStrategiesSymbol(symbol, strategy, timeframe, leverage),
    onSuccess: (fresh: UnifiedMaSymbolData) => {
      queryClient.setQueryData(['ma-strategies-unified', symbol, strategy, timeframe, leverage], fresh)
      setSelectedTrade(null)
      setReplayTrade(null)
    },
  })

  const data = dataQ.data
  const options = data?.options ?? []

  useEffect(() => {
    if (options.length === 0) return
    if (!options.includes(optionKey)) {
      const best = options.find(k => data?.results[k]?.stats?.is_best)
      setOptionKey(best ?? options[options.length - 1])
    }
  }, [data, options, optionKey])

  useEffect(() => {
    setSelectedTrade(null)
    setReplayTrade(null)
  }, [symbol, strategy, timeframe, leverage, optionKey])

  const currentBlock = data?.results?.[optionKey] ?? null
  const currentStats = currentBlock?.stats ?? null
  const currentTrades = currentBlock?.trades ?? []
  const candles = data?.candles ?? []
  const maLines = data?.ma_lines ?? {}
  const trendMa = data?.strategy === 'C' ? [] : (data?.trend_ma ?? [])
  const chartMaKey = data?.strategy === 'C'
    ? String(((currentTrades[0]?.crossed ?? [30])[0]) ?? 30)
    : optionKey
  const chartMaLine = maLines[chartMaKey] ?? maLines[optionKey] ?? []
  const chartMaLabel = data?.strategy === 'C' ? `MA${chartMaKey}` : `MA${optionKey}`
  const tableMode: MaMode = data?.strategy === 'C' ? 'c' : leverage === 'none' ? 'basic' : 'pro'
  const latestSetup = useMemo(() => {
    if (candles.length < 2 || !optionKey) return null
    const line = maLines[optionKey] ?? []
    const byTime = new Map(line.map(point => [point.time, point.value]))
    const previous = candles[candles.length - 2]
    const latest = candles[candles.length - 1]
    const previousMa = byTime.get(previous.time)
    const currentMa = byTime.get(latest.time)
    if (previousMa == null || currentMa == null) return null
    const bodyRise = latest.open > 0 ? (latest.close / latest.open - 1) * 100 : 0
    const crossed = previous.close <= previousMa && latest.close > currentMa
    return {
      time: latest.time,
      close: latest.close,
      ma: currentMa,
      bodyRise,
      crossed,
      triggered: crossed && bodyRise > 3,
    }
  }, [candles, maLines, optionKey])

  const availableStrategies = unique(available.map(c => c.strategy))
  const availableTimeframes = unique(available.filter(c => c.strategy === strategy).map(c => c.timeframe))
  const availableLeverages = unique(available
    .filter(c => c.strategy === strategy && c.timeframe === timeframe)
    .map(c => c.leverage))

  const noCurrentCombo = !!symbol && available.length > 0 && !available.some(c =>
    c.strategy === strategy && c.timeframe === timeframe && c.leverage === leverage,
  )

  if (manifestQ.error) {
    return (
      <div style={{ padding: 24 }}>
        <div className="panel" style={{ padding: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>暂无统一均线策略数据</div>
          <div style={{ color: 'var(--fg-2)', fontSize: 12 }}>
            请先运行对应回测脚本生成 JSON，或检查后端 `/api/ma-strategies-unified/manifest`。
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="page">
      <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div className="panel" style={{ padding: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <ControlLabel>品种</ControlLabel>
              <select className="input" value={symbol} onChange={e => setSymbol(e.target.value)} style={{ minWidth: 220 }}>
                {(manifestQ.data?.symbols ?? []).map(s => (
                  <option key={s.code} value={s.code}>{s.code} — {s.name}</option>
                ))}
              </select>
            </label>

            <span className="badge" title={STRATEGY_DESC.A}>策略A · 均线突破</span>
            <span className="badge">日线收盘</span>
            <span className="badge">多笔独立持仓</span>

            {data && (
              <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <ControlLabel>{data.parameter_kind === 'variant' ? '变体' : 'MA'}</ControlLabel>
                <Segment>
                  {options.map(k => (
                    <button
                      key={k}
                      onClick={() => setOptionKey(k)}
                      className={optionKey === k ? 'btn small accent' : 'btn small ghost'}
                      style={{ minWidth: data.parameter_kind === 'variant' ? 58 : 44, padding: '4px 6px' }}
                    >
                      {data.parameter_kind === 'variant' ? `C · ${k}` : k}
                    </button>
                  ))}
                </Segment>
              </label>
            )}

            {data && options.some(k => data.results[k]?.stats?.is_best) && (
              <button
                className="btn small"
                onClick={() => setOptionKey(options.find(k => data.results[k]?.stats?.is_best) ?? optionKey)}
                style={{ background: '#fde68a', color: '#78350f' }}
              >
                ★ 跳到最优
              </button>
            )}

            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
              {data?.generated_at && (
                <span style={{ color: 'var(--fg-3)', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                  数据生成于 {data.generated_at.replace('T', ' ')}
                </span>
              )}
              <button
                onClick={() => refreshMu.mutate()}
                disabled={!symbol || refreshMu.isPending || noCurrentCombo}
                className={refreshMu.isPending ? 'btn small ghost' : 'btn small'}
                title="重新拉取最新行情并跑回测"
              >
                {refreshMu.isPending ? '更新中...' : '↻ 更新数据'}
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

        {manifestQ.isLoading && (
          <div className="panel" style={{ padding: 24, textAlign: 'center', color: 'var(--fg-2)' }}>载入中...</div>
        )}
        {noCurrentCombo && (
          <div className="panel" style={{ padding: 24, textAlign: 'center', color: 'var(--fg-2)' }}>
            该策略 / 周期 / 杠杆组合暂无回测数据
          </div>
        )}
        {dataQ.isLoading && (
          <div className="panel" style={{ padding: 24, textAlign: 'center', color: 'var(--fg-2)' }}>载入中...</div>
        )}
        {dataQ.error && !noCurrentCombo && (
          <div className="panel" style={{ padding: 24, textAlign: 'center', color: 'var(--down)' }}>
            数据加载失败：{(dataQ.error as any)?.response?.data?.detail ?? (dataQ.error as Error).message}
          </div>
        )}

        {data && currentStats && (
          <>
            {latestSetup && (
              <div className="panel" style={{ padding: 14, borderLeft: `3px solid ${latestSetup.triggered ? 'var(--up)' : 'var(--line-2)'}` }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                  <div>
                    <div className="stat-label">策略A · MA{optionKey} · 最新完成日线</div>
                    <div style={{ marginTop: 4, fontSize: 16, fontWeight: 700, color: latestSetup.triggered ? 'var(--up)' : 'var(--fg-0)' }}>
                      {latestSetup.triggered ? '已触发入场信号' : latestSetup.crossed ? '已突破，但实体涨幅不足3%' : '当前未触发'}
                    </div>
                  </div>
                  <div style={{ marginLeft: 'auto', display: 'flex', gap: 18, flexWrap: 'wrap', fontSize: 11 }}>
                    <SetupMetric label="交易日" value={formatUnixDate(latestSetup.time)} />
                    <SetupMetric label="收盘" value={latestSetup.close.toFixed(2)} />
                    <SetupMetric label={`MA${optionKey}`} value={latestSetup.ma.toFixed(2)} />
                    <SetupMetric label="实体涨幅" value={`${latestSetup.bodyRise >= 0 ? '+' : ''}${latestSetup.bodyRise.toFixed(2)}%`} color={latestSetup.bodyRise > 3 ? 'var(--up)' : 'var(--fg-0)'} />
                  </div>
                </div>
              </div>
            )}
            <div className="panel" style={{ padding: '10px 12px', display: 'flex', gap: 18, flexWrap: 'wrap', fontSize: 11, color: 'var(--fg-2)' }}>
              <span>数据源：<b style={{ color: 'var(--fg-0)' }}>{data.data_source ?? '未知'}</b></span>
              <span>K线：<b className="mono" style={{ color: 'var(--fg-0)' }}>{data.bar_count ?? candles.length}</b> 根</span>
              <span>范围：<b className="mono" style={{ color: 'var(--fg-0)' }}>{formatUnixDate(data.data_range?.start ?? candles[0]?.time)} — {formatUnixDate(data.data_range?.end ?? candles[candles.length - 1]?.time)}</b></span>
              {data.adjusted && <span>股票价格：<b style={{ color: 'var(--fg-0)' }}>已复权</b></span>}
            </div>
            <MaStatsBar stats={currentStats as any} mode={tableMode} accent="#60a5fa" />
            <div style={{ marginTop: -6, padding: '0 4px', fontSize: 10.5, color: 'var(--fg-3)' }}>
              统计口径：每个突破信号独立开仓、独立止损；“逐笔收益合计”不是账户复合收益，累计收益回撤也不包含资金容量约束。
            </div>
          </>
        )}

        {data && (
          <div className="panel">
            <div className="panel-header">
              <span>交易标记图</span>
              <span className="mono" style={{ textTransform: 'none', letterSpacing: 0, fontSize: 10, color: 'var(--fg-2)' }}>
                {STRATEGY_LABEL[data.strategy]} · {TF_LABEL[data.timeframe]} · {LEV_LABEL[data.leverage]} · {candles.length} 根K线 · {currentTrades.length} 笔交易
                {currentStats?.is_best && <span style={{ marginLeft: 8, color: '#a16207' }}>★ 当前为最优参数</span>}
              </span>
            </div>
            <div style={{ padding: '8px 12px' }}>
              <MaStrategyChart
                candles={candles}
                maLine={chartMaLine}
                maLabel={chartMaLabel}
                trendMa={trendMa}
                trades={currentTrades}
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
                点击交易行聚焦图表，点击播放进入逐K线回放
              </span>
            </div>
          </div>
        )}

        {data && currentTrades.length > 0 && (
          <MaTradeTable
            mode={tableMode}
            trades={currentTrades as any}
            onPlay={setReplayTrade}
            onSelect={(t: any) => setSelectedTrade(prev => (prev === t ? null : t))}
            selected={selectedTrade as any}
            timeframe={timeframe}
            addAccent="#60a5fa"
          />
        )}
        {data && currentTrades.length === 0 && (
          <div className="panel" style={{ padding: 24, textAlign: 'center', color: 'var(--fg-2)' }}>
            该参数下无交易记录
          </div>
        )}

        {replayTrade && data && (
          <TradeReplayPlayer
            trade={replayTrade}
            candles={candles}
            maLine={chartMaLine}
            trendMa={trendMa}
            maLabel={chartMaLabel}
            timeframe={timeframe}
            onClose={() => setReplayTrade(null)}
          />
        )}
      </div>
    </div>
  )
}

function unique<T>(items: T[]): T[] {
  return Array.from(new Set(items))
}

function formatUnixDate(value?: number | null): string {
  if (!value) return '-'
  return new Date(value * 1000).toISOString().slice(0, 10)
}

function Segment({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 4, padding: 2, background: 'var(--bg-2)', borderRadius: 8, flexWrap: 'wrap' }}>
      {children}
    </div>
  )
}

function ControlLabel({ children }: { children: React.ReactNode }) {
  return (
    <span style={{ fontSize: 11, color: 'var(--fg-2)', letterSpacing: '.1em', textTransform: 'uppercase' }}>
      {children}
    </span>
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

function SetupMetric({ label, value, color = 'var(--fg-0)' }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div className="stat-label">{label}</div>
      <div className="mono" style={{ marginTop: 3, color, fontWeight: 600 }}>{value}</div>
    </div>
  )
}
