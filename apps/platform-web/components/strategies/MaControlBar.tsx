'use client'

import type { UseMutationResult } from '@tanstack/react-query'
import type { MaStrategyKey, MaStrategyCVariant } from '@/types'

export type MaMode = 'basic' | 'pro' | 'c'

const MA_PERIODS = ['30', '60', '99', '120'] as const

const STRATEGIES: { key: MaStrategyKey; label: string; desc: string }[] = [
  { key: 'breakout', label: 'A · 突破', desc: 'K线收盘上穿MA + 实体涨幅 > 3%' },
  { key: 'pullback', label: 'B · 回调', desc: 'MA120 之上 + 最低点触及 MA 后收回' },
]

const VARIANTS: { key: MaStrategyCVariant; label: string; desc: string }[] = [
  { key: 'x2', label: 'C · x2', desc: '当根恰好同时上穿 2 根均线（30/60/99/120 中）+ 实体涨幅 > 3%' },
  { key: 'x3', label: 'C · x3', desc: '当根恰好同时上穿 3 根均线 + 实体涨幅 > 3%' },
  { key: 'x4', label: 'C · x4', desc: '当根同时上穿全部 4 根均线 + 实体涨幅 > 3%' },
]

const TF_LABEL: Record<string, string> = { '1day': '1D', '4h': '4H', '1h': '1H' }

interface Props {
  mode: MaMode
  symbol: string
  onSymbolChange: (s: string) => void
  symbols: { code: string; name: string }[]
  // basic + pro
  strategy?: MaStrategyKey
  onStrategyChange?: (s: MaStrategyKey) => void
  // pro only
  leverage?: 2 | 3
  onLeverageChange?: (l: 2 | 3) => void
  // C only
  variant?: MaStrategyCVariant
  onVariantChange?: (v: MaStrategyCVariant) => void
  timeframe?: string
  onTimeframeChange?: (tf: string) => void
  timeframes?: string[]
  // common
  maKey: string
  onMaKeyChange: (k: string) => void
  bestMa: string | null
  bestVariant?: MaStrategyCVariant | null
  generatedAt?: string
  refreshMu: UseMutationResult<any, any, any>
  onRefresh: () => void
}

export function MaControlBar({
  mode,
  symbol,
  onSymbolChange,
  symbols,
  strategy,
  onStrategyChange,
  leverage,
  onLeverageChange,
  variant,
  onVariantChange,
  timeframe,
  onTimeframeChange,
  timeframes,
  maKey,
  onMaKeyChange,
  bestMa,
  bestVariant,
  generatedAt,
  refreshMu,
  onRefresh,
}: Props) {
  return (
    <div className="panel" style={{ padding: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        {/* 品种选择 — 所有模式 */}
        <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 11, color: 'var(--fg-2)', letterSpacing: '.1em', textTransform: 'uppercase' }}>品种</span>
          <select
            className="input"
            value={symbol}
            onChange={e => onSymbolChange(e.target.value)}
            style={{ minWidth: 220 }}
          >
            {symbols.map(s => (
              <option key={s.code} value={s.code}>
                {s.code} — {s.name}
              </option>
            ))}
          </select>
        </label>

        {/* 杠杆切换 — 仅加仓模式 */}
        {mode === 'pro' && onLeverageChange && (
          <div style={{ display: 'flex', gap: 4, padding: 2, background: 'var(--bg-2)', borderRadius: 8 }}>
            {([2, 3] as (2 | 3)[]).map(lev => (
              <button
                key={lev}
                onClick={() => onLeverageChange(lev)}
                className={leverage === lev ? 'btn small accent' : 'btn small ghost'}
                style={{ minWidth: 52, fontWeight: 700 }}
              >
                {lev}x 杠杆
              </button>
            ))}
          </div>
        )}

        {/* 策略 A/B 切换 — 基础 / 加仓 */}
        {(mode === 'basic' || mode === 'pro') && onStrategyChange && (
          <div style={{ display: 'flex', gap: 4, padding: 2, background: 'var(--bg-2)', borderRadius: 8 }}>
            {STRATEGIES.map(s => (
              <button
                key={s.key}
                onClick={() => onStrategyChange(s.key)}
                title={s.desc}
                className={strategy === s.key ? 'btn small accent' : 'btn small ghost'}
                style={{ minWidth: 96 }}
              >
                {s.label}
              </button>
            ))}
          </div>
        )}

        {/* 时间周期 — 仅策略C */}
        {mode === 'c' && timeframes && onTimeframeChange && (
          <div style={{ display: 'flex', gap: 4, padding: 2, background: 'var(--bg-2)', borderRadius: 8 }}>
            {timeframes.map(tf => (
              <button
                key={tf}
                onClick={() => onTimeframeChange(tf)}
                className={timeframe === tf ? 'btn small accent' : 'btn small ghost'}
                style={{ minWidth: 48 }}
              >
                {TF_LABEL[tf] ?? tf}
              </button>
            ))}
          </div>
        )}

        {/* 变体切换 — 仅策略C */}
        {mode === 'c' && onVariantChange && (
          <div style={{ display: 'flex', gap: 4, padding: 2, background: 'var(--bg-2)', borderRadius: 8 }}>
            {VARIANTS.map(v => (
              <button
                key={v.key}
                onClick={() => onVariantChange(v.key)}
                title={v.desc}
                className={variant === v.key ? 'btn small accent' : 'btn small ghost'}
                style={{ minWidth: 70 }}
              >
                {v.label}
              </button>
            ))}
          </div>
        )}

        {/* 最优变体快捷 — 仅策略C */}
        {mode === 'c' && bestVariant && bestVariant !== variant && (
          <button
            className="btn small"
            onClick={() => onVariantChange?.(bestVariant)}
            style={{ background: '#fde68a', color: '#78350f' }}
          >
            ★ 跳到最优 C-{bestVariant}
          </button>
        )}

        {/* MA 周期 — 所有模式 */}
        <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 11, color: 'var(--fg-2)', letterSpacing: '.1em', textTransform: 'uppercase' }}>
            {mode === 'c' ? '叠加 MA' : 'MA'}
          </span>
          <div style={{ display: 'flex', gap: 2 }}>
            {MA_PERIODS.map(n => (
              <button
                key={n}
                onClick={() => onMaKeyChange(n)}
                className={maKey === n ? 'btn small accent' : 'btn small ghost'}
                style={{ minWidth: 44, padding: '4px 6px' }}
              >
                {n}
              </button>
            ))}
          </div>
        </label>

        {/* 跳到最优 MA — 基础 / 加仓 */}
        {(mode === 'basic' || mode === 'pro') && bestMa && bestMa !== maKey && (
          <button
            className="btn small"
            onClick={() => onMaKeyChange(bestMa)}
            style={{ background: '#fde68a', color: '#78350f' }}
          >
            ★ 跳到最优 MA{bestMa}
          </button>
        )}

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
          {generatedAt && (
            <span style={{ color: 'var(--fg-3)', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
              数据生成于 {generatedAt.replace('T', ' ')}
            </span>
          )}
          <button
            onClick={onRefresh}
            disabled={!symbol || refreshMu.isPending}
            className={refreshMu.isPending ? 'btn small ghost' : 'btn small'}
            title="重新拉取最新行情并跑回测"
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
  )
}
