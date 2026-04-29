'use client'

import { useState } from 'react'
import { createReplayNote } from '@/lib/api'
import type { ReplayNoteCreate } from '@/types'

interface Props {
  symbol:          string
  timeframe:       string
  replayStartTime: string
  currentTime:     string
}

export function ReplayNoteForm({ symbol, timeframe, replayStartTime, currentTime }: Props) {
  const [trendView,       setTrendView]       = useState('')
  const [supportLevel,    setSupportLevel]    = useState('')
  const [resistanceLevel, setResistanceLevel] = useState('')
  const [entryIdea,       setEntryIdea]       = useState('')
  const [stopLoss,        setStopLoss]        = useState('')
  const [takeProfit,      setTakeProfit]      = useState('')
  const [summary,         setSummary]         = useState('')
  const [saving,          setSaving]          = useState(false)
  const [saved,           setSaved]           = useState(false)
  const [errMsg,          setErrMsg]          = useState('')

  const handleSave = async () => {
    setSaving(true)
    setSaved(false)
    setErrMsg('')
    const note: ReplayNoteCreate = {
      symbol,
      timeframe,
      replay_start_time: replayStartTime || new Date().toISOString(),
      note_time:         currentTime     || new Date().toISOString(),
      trend_view:        trendView       || undefined,
      support_level:     supportLevel    ? parseFloat(supportLevel)    : undefined,
      resistance_level:  resistanceLevel ? parseFloat(resistanceLevel) : undefined,
      entry_idea:        entryIdea       || undefined,
      stop_loss:         stopLoss        ? parseFloat(stopLoss)        : undefined,
      take_profit:       takeProfit      ? parseFloat(takeProfit)      : undefined,
      summary:           summary         || undefined,
    }
    try {
      await createReplayNote(note)
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch {
      setErrMsg('保存失败，请重试')
      setTimeout(() => setErrMsg(''), 3000)
    } finally {
      setSaving(false)
    }
  }

  const handleClear = () => {
    setTrendView(''); setSupportLevel(''); setResistanceLevel('')
    setEntryIdea(''); setStopLoss(''); setTakeProfit(''); setSummary('')
    setSaved(false); setErrMsg('')
  }

  const notStarted = !replayStartTime

  const rowStyle: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 5 }
  const labelStyle: React.CSSProperties = { fontSize: 10.5, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--fg-2)', fontWeight: 600 }
  const areaStyle: React.CSSProperties = {
    background: 'var(--bg-0)', border: '1px solid var(--line-1)', color: 'var(--fg-0)',
    borderRadius: 'var(--radius)', fontSize: 12, padding: '6px 8px',
    fontFamily: 'var(--font-ui)', resize: 'vertical', lineHeight: 1.5, outline: 'none', width: '100%',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div className="panel-header" style={{ border: 0, borderBottom: '1px solid var(--line-1)', height: 40 }}>
        <span>复盘笔记</span>
        {currentTime && (
          <span className="mono" style={{ color: 'var(--fg-2)', textTransform: 'none', letterSpacing: 0, fontSize: 11 }}>
            {currentTime.slice(0, 10)}
          </span>
        )}
      </div>

      {notStarted ? (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, padding: 20, textAlign: 'center', color: 'var(--fg-3)' }}>
          <svg style={{ width: 32, height: 32 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="9" />
            <path d="M10 8l6 4-6 4V8z" fill="currentColor" stroke="none" />
          </svg>
          <div>
            <div style={{ fontSize: 12, color: 'var(--fg-2)', fontWeight: 600 }}>请先开始复盘</div>
            <div style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 4, lineHeight: 1.5 }}>选择起始日期后<br />点击「开始复盘」</div>
          </div>
        </div>
      ) : (
        <>
          <div style={{ flex: 1, overflowY: 'auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={rowStyle}>
              <label style={labelStyle}>趋势判断</label>
              <div className="tweak-segment">
                {['强多', '看多', '中性', '看空', '强空'].map(v => (
                  <button key={v} className={trendView === v ? 'on' : ''} onClick={() => setTrendView(trendView === v ? '' : v)}
                    style={{ fontSize: 11 }}>{v}</button>
                ))}
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <div style={rowStyle}>
                <label style={{ ...labelStyle, color: 'var(--up)' }}>支撑位</label>
                <input className="mr-input mono" type="number" value={supportLevel} onChange={e => setSupportLevel(e.target.value)} placeholder="0.00" />
              </div>
              <div style={rowStyle}>
                <label style={{ ...labelStyle, color: 'var(--down)' }}>阻力位</label>
                <input className="mr-input mono" type="number" value={resistanceLevel} onChange={e => setResistanceLevel(e.target.value)} placeholder="0.00" />
              </div>
            </div>

            <div style={rowStyle}>
              <label style={labelStyle}>入场思路</label>
              <textarea
                rows={2}
                value={entryIdea}
                onChange={e => setEntryIdea(e.target.value)}
                placeholder="条件 / 位置 / 仓位..."
                style={areaStyle}
                onFocus={e => { e.currentTarget.style.borderColor = 'var(--accent-line)'; e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-dim)' }}
                onBlur={e => { e.currentTarget.style.borderColor = 'var(--line-1)'; e.currentTarget.style.boxShadow = 'none' }}
              />
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <div style={rowStyle}>
                <label style={{ ...labelStyle, color: 'var(--down)' }}>止损</label>
                <input className="mr-input mono" type="number" value={stopLoss} onChange={e => setStopLoss(e.target.value)} placeholder="0.00" />
              </div>
              <div style={rowStyle}>
                <label style={{ ...labelStyle, color: 'var(--up)' }}>止盈</label>
                <input className="mr-input mono" type="number" value={takeProfit} onChange={e => setTakeProfit(e.target.value)} placeholder="0.00" />
              </div>
            </div>

            <div style={rowStyle}>
              <label style={labelStyle}>总结</label>
              <textarea
                rows={3}
                value={summary}
                onChange={e => setSummary(e.target.value)}
                placeholder="关键结论，下一步计划..."
                style={areaStyle}
                onFocus={e => { e.currentTarget.style.borderColor = 'var(--accent-line)'; e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-dim)' }}
                onBlur={e => { e.currentTarget.style.borderColor = 'var(--line-1)'; e.currentTarget.style.boxShadow = 'none' }}
              />
            </div>

            <div style={rowStyle}>
              <label style={labelStyle}>趋势分析</label>
              <textarea
                rows={2}
                value={trendView}
                onChange={e => setTrendView(e.target.value)}
                placeholder="当前结构如何？方向判断？"
                style={areaStyle}
                onFocus={e => { e.currentTarget.style.borderColor = 'var(--accent-line)'; e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-dim)' }}
                onBlur={e => { e.currentTarget.style.borderColor = 'var(--line-1)'; e.currentTarget.style.boxShadow = 'none' }}
              />
            </div>

            {errMsg && (
              <div style={{ padding: '8px 10px', background: 'var(--down-bg)', border: '1px solid var(--down)', borderRadius: 'var(--radius)', fontSize: 11.5, color: 'var(--down)' }}>
                {errMsg}
              </div>
            )}
          </div>

          <div style={{ padding: '10px 12px', borderTop: '1px solid var(--line-1)', display: 'flex', gap: 6, flexShrink: 0 }}>
            <button className="btn" onClick={handleClear}>清空</button>
            <button
              className={`btn${saved ? '' : ' primary'}`}
              onClick={handleSave}
              disabled={saving}
              style={{ flex: 1, justifyContent: 'center', background: saved ? 'var(--up)' : undefined, color: saved ? '#111' : undefined }}
            >
              <svg className="ico-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 12l5 5L20 6" />
              </svg>
              {saving ? '保存中…' : saved ? '已保存' : '保存笔记'}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
