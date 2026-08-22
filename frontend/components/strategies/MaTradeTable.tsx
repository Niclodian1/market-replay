'use client'

import { useState, type ReactNode } from 'react'
import type { MaStrategyTrade, MaStrategy3xTrade, MaStrategyCTrade, MaExitReason, UnifiedMaTrade } from '@/types'
import type { MaMode } from './MaControlBar'

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
function fmtDateTime(unixSec: number) {
  return new Date(unixSec * 1000).toISOString().slice(0, 16).replace('T', ' ')
}
function fmt(n: number, digits = 2) {
  return n.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
}
function fmtPrice(n: number) {
  const abs = Math.abs(n)
  const digits = abs >= 1000 ? 2 : abs >= 1 ? 4 : 6
  return n.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

type AnyTrade = MaStrategyTrade | MaStrategy3xTrade | MaStrategyCTrade

interface Props {
  mode: MaMode
  trades: AnyTrade[]
  onPlay?: (t: UnifiedMaTrade) => void
  onSelect?: (t: AnyTrade) => void
  selected?: AnyTrade | null
  timeframe?: string
  addAccent?: string
}

export function MaTradeTable({ mode, trades, onPlay, onSelect, selected, timeframe, addAccent }: Props) {
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? trades : trades.slice(0, 20)

  const isDay = timeframe === '1day'
  const fmtT = (u: number) => (timeframe && !isDay ? fmtDateTime(u) : fmtDate(u))

  if (mode === 'pro') {
    const proTrades = trades as MaStrategy3xTrade[]
    return (
      <ProTable
        trades={proTrades}
        visible={visible as MaStrategy3xTrade[]}
        expanded={expanded}
        onToggle={() => setExpanded(v => !v)}
        total={trades.length}
        onPlay={onPlay!}
        addAccent={addAccent ?? '#60a5fa'}
      />
    )
  }

  if (mode === 'c') {
    const cTrades = trades as MaStrategyCTrade[]
    return (
      <CTable
        trades={cTrades}
        visible={visible as MaStrategyCTrade[]}
        expanded={expanded}
        onToggle={() => setExpanded(v => !v)}
        total={trades.length}
        selected={selected as MaStrategyCTrade | null}
        onSelect={onSelect!}
        onPlay={onPlay}
        fmtT={fmtT}
      />
    )
  }

  // basic mode
  const basicTrades = trades as MaStrategyTrade[]
  return (
    <BasicTable
      trades={basicTrades}
      visible={visible as MaStrategyTrade[]}
      expanded={expanded}
      onToggle={() => setExpanded(v => !v)}
      total={trades.length}
      selected={selected as MaStrategyTrade | null}
      onSelect={onSelect!}
      onPlay={onPlay}
    />
  )
}

// ── Basic 表 ──────────────────────────────────────────

function BasicTable({
  trades, visible, expanded, onToggle, total, selected, onSelect, onPlay,
}: {
  trades: MaStrategyTrade[]
  visible: MaStrategyTrade[]
  expanded: boolean
  onToggle: () => void
  total: number
  selected: MaStrategyTrade | null
  onSelect: (t: MaStrategyTrade) => void
  onPlay?: (t: UnifiedMaTrade) => void
}) {
  const cols = '36px 1fr 1fr 1fr 1fr 60px 80px 80px 70px 80px 50px 76px'
  const minW = 1120

  return (
    <div className="panel" style={{ overflow: 'hidden' }}>
      <div className="panel-header">
        <span>交易明细</span>
        <span className="mono" style={{ textTransform: 'none', letterSpacing: 0 }}>{total} 笔 · 点击播放回放</span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <div style={{ display: 'grid', gridTemplateColumns: cols, padding: '8px 12px', borderBottom: '1px solid var(--line-2)', fontSize: 10, letterSpacing: '.14em', textTransform: 'uppercase', color: 'var(--fg-2)', fontWeight: 600, minWidth: minW }}>
          <span>#</span><span>入场日期</span><span>入场价</span><span>出场日期</span><span>出场价</span>
          <span style={{ textAlign: 'right' }}>天数</span>
          <span style={{ textAlign: 'right' }}>止损价</span>
          <span style={{ textAlign: 'right' }}>盈亏%</span>
          <span style={{ textAlign: 'right' }}>浮盈%</span>
          <span style={{ textAlign: 'right' }}>盈亏比</span>
          <span style={{ textAlign: 'center' }}>原因</span>
          <span style={{ textAlign: 'center' }}>回放</span>
        </div>
        {visible.map((t, i) => {
          const isSel = t === selected
          return (
            <div
              key={i}
              onClick={() => onSelect(t)}
              style={{
                display: 'grid', gridTemplateColumns: cols, padding: '8px 12px',
                borderBottom: '1px solid var(--line-1)', fontFamily: 'var(--font-mono)',
                fontSize: 11.5, minWidth: minW, alignItems: 'center', cursor: 'pointer',
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
              <span style={{ textAlign: 'center' }}>
                <button
                  className="btn small accent"
                  onClick={e => { e.stopPropagation(); onPlay?.(t as UnifiedMaTrade) }}
                  style={{ padding: '3px 8px', fontSize: 11, whiteSpace: 'nowrap' }}
                  title="播放此笔交易K线动画"
                >
                  ▶ 播放
                </button>
              </span>
            </div>
          )
        })}
      </div>
      {total > 20 && (
        <div style={{ padding: 10, textAlign: 'center', borderTop: '1px solid var(--line-1)' }}>
          <button className="btn small ghost" onClick={onToggle}>
            {expanded ? '收起' : `展开全部 ${total} 笔`}
          </button>
        </div>
      )}
    </div>
  )
}

// ── Pro 表 ────────────────────────────────────────────

function ProTable({
  trades, visible, expanded, onToggle, total, onPlay, addAccent,
}: {
  trades: MaStrategy3xTrade[]
  visible: MaStrategy3xTrade[]
  expanded: boolean
  onToggle: () => void
  total: number
  onPlay: (t: UnifiedMaTrade) => void
  addAccent: string
}) {
  const cols = '36px 1fr 1fr 1fr 1fr 60px 80px 90px 70px 80px 60px 50px 76px'
  const minW = 832

  return (
    <div className="panel" style={{ overflow: 'hidden' }}>
      <div className="panel-header">
        <span>逐笔明细</span>
        <span className="mono" style={{ textTransform: 'none', letterSpacing: 0, fontSize: 10, color: 'var(--fg-2)' }}>
          {total} 笔 · 点击 ▶ 播放回放
        </span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <div style={{ display: 'grid', gridTemplateColumns: cols, gap: 0, borderBottom: '1px solid var(--line-1)', minWidth: minW }}>
          {['#', '入场日期', '入场价', '出场日期', '出场价', '持仓天', '止损价', '收益%', '最大%', 'RR', '加仓', '原因', '回放'].map(h => (
            <div key={h} style={{ padding: '6px 8px', fontSize: 10, fontWeight: 600, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--fg-2)', textAlign: 'center', borderRight: '1px solid var(--line-1)' }}>
              {h}
            </div>
          ))}
        </div>
        {visible.map((t, idx) => {
          const isWin = t.pnl_pct >= 0
          const days = Math.round((t.exit_time - t.entry_time) / 86400)
          return (
            <div key={idx} style={{ display: 'grid', gridTemplateColumns: cols, minWidth: minW, background: isWin ? 'rgba(34,197,94,0.04)' : 'rgba(239,68,68,0.04)', borderBottom: '1px solid var(--line-1)' }}>
              <ProCell>{idx + 1}</ProCell>
              <ProCell mono>{fmtDate(t.entry_time)}</ProCell>
              <ProCell mono>{fmtPrice(t.entry_price)}</ProCell>
              <ProCell mono>{fmtDate(t.exit_time)}</ProCell>
              <ProCell mono>{fmtPrice(t.exit_price)}</ProCell>
              <ProCell mono>{days}</ProCell>
              <ProCell mono>{fmtPrice(t.stop_loss)}</ProCell>
              <ProCell mono color={isWin ? 'var(--up)' : 'var(--down)'} bold>
                {(t.pnl_pct >= 0 ? '+' : '') + t.pnl_pct.toFixed(2) + '%'}
              </ProCell>
              <ProCell mono color="var(--up)">{'+' + t.max_profit_pct.toFixed(2) + '%'}</ProCell>
              <ProCell mono>{t.rr != null ? t.rr.toFixed(2) + 'R' : '-'}</ProCell>
              <ProCell mono color={addAccent}>{t.n_adds > 0 ? `+${t.n_adds}` : '-'}</ProCell>
              <ProCell>
                <span style={{ fontSize: 10, padding: '2px 5px', borderRadius: 4, background: EXIT_REASON_COLOR[t.exit_reason] + '22', color: EXIT_REASON_COLOR[t.exit_reason] }}>
                  {t.exit_reason}
                </span>
              </ProCell>
              <ProCell>
                <button className="btn small accent" onClick={() => onPlay(t as UnifiedMaTrade)} style={{ padding: '3px 8px', fontSize: 11, whiteSpace: 'nowrap' }} title="播放此笔交易K线动画">
                  ▶ 播放
                </button>
              </ProCell>
            </div>
          )
        })}
      </div>
      {total > 20 && (
        <div style={{ padding: '8px 12px', textAlign: 'center' }}>
          <button className="btn small ghost" onClick={onToggle}>
            {expanded ? '收起' : `展开全部 ${total} 笔`}
          </button>
        </div>
      )}
    </div>
  )
}

function ProCell({ children, mono, color, bold }: { children: ReactNode; mono?: boolean; color?: string; bold?: boolean }) {
  return (
    <div style={{
      padding: '5px 8px', fontSize: 11, textAlign: 'center',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: mono ? 'var(--font-mono)' : undefined,
      color: color ?? undefined, fontWeight: bold ? 700 : undefined,
      borderRight: '1px solid var(--line-1)',
    }}>
      {children}
    </div>
  )
}

// ── C 表 ──────────────────────────────────────────────

function CTable({
  trades, visible, expanded, onToggle, total, selected, onSelect, onPlay, fmtT,
}: {
  trades: MaStrategyCTrade[]
  visible: MaStrategyCTrade[]
  expanded: boolean
  onToggle: () => void
  total: number
  selected: MaStrategyCTrade | null
  onSelect: (t: MaStrategyCTrade) => void
  onPlay?: (t: UnifiedMaTrade) => void
  fmtT: (u: number) => string
}) {
  const cols = '36px 1.3fr 1fr 1.3fr 1fr 70px 80px 70px 80px 100px 70px 50px 76px'
  const minW = 1220

  return (
    <div className="panel" style={{ overflow: 'hidden' }}>
      <div className="panel-header">
        <span>交易明细</span>
        <span className="mono" style={{ textTransform: 'none', letterSpacing: 0 }}>{total} 笔 · 点击播放回放</span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <div style={{ display: 'grid', gridTemplateColumns: cols, padding: '8px 12px', borderBottom: '1px solid var(--line-2)', fontSize: 10, letterSpacing: '.14em', textTransform: 'uppercase', color: 'var(--fg-2)', fontWeight: 600, minWidth: minW }}>
          <span>#</span><span>入场</span><span>入场价</span><span>出场</span><span>出场价</span>
          <span style={{ textAlign: 'right' }}>止损价</span>
          <span style={{ textAlign: 'right' }}>盈亏%</span>
          <span style={{ textAlign: 'right' }}>浮盈%</span>
          <span style={{ textAlign: 'right' }}>盈亏比</span>
          <span style={{ textAlign: 'center' }}>上穿均线</span>
          <span style={{ textAlign: 'center' }}>出场线</span>
          <span style={{ textAlign: 'center' }}>原因</span>
          <span style={{ textAlign: 'center' }}>回放</span>
        </div>
        {visible.map((t, i) => {
          const isSel = t === selected
          return (
            <div
              key={i}
              onClick={() => onSelect(t)}
              style={{
                display: 'grid', gridTemplateColumns: cols, padding: '8px 12px',
                borderBottom: '1px solid var(--line-1)', fontFamily: 'var(--font-mono)',
                fontSize: 11.5, minWidth: minW, alignItems: 'center', cursor: 'pointer',
                background: isSel ? 'var(--bg-2)' : 'transparent',
              }}
              onMouseEnter={e => { if (!isSel) (e.currentTarget.style.background = 'var(--bg-2)') }}
              onMouseLeave={e => { if (!isSel) (e.currentTarget.style.background = 'transparent') }}
            >
              <span style={{ color: 'var(--fg-3)' }}>#{i + 1}</span>
              <span style={{ color: 'var(--fg-1)' }}>{fmtT(t.entry_time)}</span>
              <span style={{ color: 'var(--fg-0)' }}>{t.entry_price.toLocaleString()}</span>
              <span style={{ color: 'var(--fg-1)' }}>{fmtT(t.exit_time)}</span>
              <span style={{ color: 'var(--fg-0)' }}>{t.exit_price.toLocaleString()}</span>
              <span style={{ textAlign: 'right', color: 'var(--fg-2)' }}>{t.stop_loss.toLocaleString()}</span>
              <span style={{ textAlign: 'right', fontWeight: 600, color: t.pnl_pct >= 0 ? 'var(--up)' : 'var(--down)' }}>
                {t.pnl_pct >= 0 ? '+' : ''}{fmt(t.pnl_pct)}%
              </span>
              <span style={{ textAlign: 'right', color: 'var(--up)' }}>+{fmt(t.max_profit_pct)}%</span>
              <span style={{ textAlign: 'right', color: t.rr != null ? 'var(--up)' : 'var(--fg-3)', fontWeight: 600 }}>
                {t.rr != null ? fmt(t.rr) + 'R' : '-'}
              </span>
              <span style={{ textAlign: 'center', color: 'var(--fg-2)' }}>
                {t.crossed.map(m => `MA${m}`).join('/')}
              </span>
              <span style={{ textAlign: 'center', color: 'var(--fg-1)' }}>MA{t.exit_ma}</span>
              <span style={{ textAlign: 'center', color: EXIT_REASON_COLOR[t.exit_reason], fontWeight: 600 }}>
                {t.exit_reason}
              </span>
              <span style={{ textAlign: 'center' }}>
                <button
                  className="btn small accent"
                  onClick={e => { e.stopPropagation(); onPlay?.(t as UnifiedMaTrade) }}
                  style={{ padding: '3px 8px', fontSize: 11, whiteSpace: 'nowrap' }}
                  title="播放此笔交易K线动画"
                >
                  ▶ 播放
                </button>
              </span>
            </div>
          )
        })}
      </div>
      {total > 20 && (
        <div style={{ padding: 10, textAlign: 'center', borderTop: '1px solid var(--line-1)' }}>
          <button className="btn small ghost" onClick={onToggle}>
            {expanded ? '收起' : `展开全部 ${total} 笔`}
          </button>
        </div>
      )}
    </div>
  )
}
