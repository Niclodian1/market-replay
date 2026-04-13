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

const TREND_OPTIONS: { value: string; label: string; idle: string; active: string }[] = [
  { value: '强烈看多', label: '强多', idle: 'border-green-900/60 text-green-700 hover:border-green-700 hover:text-green-500',   active: 'border-green-500 bg-green-500/15 text-green-300 shadow-glow-green' },
  { value: '看多',     label: '看多', idle: 'border-green-900/40 text-green-800 hover:border-green-800 hover:text-green-600',   active: 'border-green-600 bg-green-600/10 text-green-400' },
  { value: '中性',     label: '中性', idle: 'border-gray-700/60 text-gray-600 hover:border-gray-600 hover:text-gray-400',       active: 'border-gray-500 bg-gray-500/15 text-gray-300' },
  { value: '看空',     label: '看空', idle: 'border-red-900/40 text-red-800 hover:border-red-800 hover:text-red-600',           active: 'border-red-600 bg-red-600/10 text-red-400' },
  { value: '强烈看空', label: '强空', idle: 'border-red-900/60 text-red-700 hover:border-red-700 hover:text-red-500',           active: 'border-red-500 bg-red-500/15 text-red-300' },
]

const inputCls = 'w-full bg-gray-800/60 border border-gray-700/60 rounded-md px-2.5 py-1.5 text-xs text-gray-200 placeholder-gray-700 focus:outline-none focus:border-blue-500/60 focus:ring-1 focus:ring-blue-500/15 focus:bg-gray-800 transition-all'
const areaCls  = inputCls + ' resize-none leading-relaxed'

function FieldLabel({ children, dot }: { children: React.ReactNode; dot?: string }) {
  return (
    <label className="flex items-center gap-1.5 text-xs text-gray-500 mb-1.5">
      {dot && <span className={`w-1.5 h-1.5 rounded-full ${dot} shrink-0`} />}
      {children}
    </label>
  )
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

  return (
    <div className="flex flex-col h-full bg-gray-950/40 overflow-hidden">
      {/* 头部 */}
      <div className="shrink-0 px-4 py-3 border-b border-gray-800/80">
        <div className="flex items-center justify-between">
          <div className="font-semibold text-xs text-gray-300 tracking-wide">复盘笔记</div>
          {currentTime && (
            <div className="text-xs text-gray-600 tabular-nums font-mono">
              {currentTime.slice(0, 10)}
            </div>
          )}
        </div>
        <div className="text-xs text-gray-700 mt-0.5 font-mono">
          {symbol} · {timeframe}
        </div>
      </div>

      {notStarted ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 px-6 text-center">
          <div className="w-12 h-12 rounded-xl bg-gray-800/60 border border-gray-700/40 flex items-center justify-center">
            <svg className="w-5 h-5 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M12 6v6l4 2m6-2a10 10 0 1 1-20 0 10 10 0 0 1 20 0z" />
            </svg>
          </div>
          <div>
            <div className="text-sm text-gray-500 font-medium">请先开始复盘</div>
            <div className="text-xs text-gray-700 mt-1 leading-relaxed">选择起始日期后<br/>点击「开始复盘」</div>
          </div>
        </div>
      ) : (
        <>
          {/* 表单主体 */}
          <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">

            {/* 趋势判断 */}
            <div>
              <FieldLabel>趋势判断</FieldLabel>
              <div className="flex gap-1">
                {TREND_OPTIONS.map(opt => (
                  <button
                    key={opt.value}
                    onClick={() => setTrendView(trendView === opt.value ? '' : opt.value)}
                    title={opt.value}
                    className={`flex-1 text-xs py-1.5 rounded-md border transition-all ${
                      trendView === opt.value ? opt.active : opt.idle
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* 支撑 / 阻力 */}
            <div className="grid grid-cols-2 gap-2">
              <div>
                <FieldLabel dot="bg-blue-500/60">支撑位</FieldLabel>
                <input
                  type="number"
                  value={supportLevel}
                  onChange={e => setSupportLevel(e.target.value)}
                  placeholder="—"
                  className={inputCls + ' font-mono'}
                />
              </div>
              <div>
                <FieldLabel dot="bg-purple-500/60">阻力位</FieldLabel>
                <input
                  type="number"
                  value={resistanceLevel}
                  onChange={e => setResistanceLevel(e.target.value)}
                  placeholder="—"
                  className={inputCls + ' font-mono'}
                />
              </div>
            </div>

            {/* 止损 / 止盈 */}
            <div className="grid grid-cols-2 gap-2">
              <div>
                <FieldLabel dot="bg-red-500/60">止损</FieldLabel>
                <input
                  type="number"
                  value={stopLoss}
                  onChange={e => setStopLoss(e.target.value)}
                  placeholder="—"
                  className={inputCls + ' font-mono focus:border-red-500/50 focus:ring-red-500/10'}
                />
              </div>
              <div>
                <FieldLabel dot="bg-emerald-500/60">止盈</FieldLabel>
                <input
                  type="number"
                  value={takeProfit}
                  onChange={e => setTakeProfit(e.target.value)}
                  placeholder="—"
                  className={inputCls + ' font-mono focus:border-emerald-500/50 focus:ring-emerald-500/10'}
                />
              </div>
            </div>

            {/* 入场逻辑 */}
            <div>
              <FieldLabel dot="bg-blue-400/60">入场想法</FieldLabel>
              <textarea
                rows={3}
                value={entryIdea}
                onChange={e => setEntryIdea(e.target.value)}
                placeholder="形态、信号、入场理由..."
                className={areaCls}
              />
            </div>

            {/* 总结 */}
            <div>
              <FieldLabel dot="bg-amber-400/60">综合总结</FieldLabel>
              <textarea
                rows={4}
                value={summary}
                onChange={e => setSummary(e.target.value)}
                placeholder="当前整体判断和推演..."
                className={areaCls}
              />
            </div>

            {errMsg && (
              <div className="flex items-center gap-2 text-xs text-red-400 bg-red-400/8 border border-red-900/40 rounded-md px-3 py-2">
                <svg className="w-3.5 h-3.5 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16zm-1-9v4h2V9H9zm0-4v2h2V5H9z" clipRule="evenodd"/>
                </svg>
                {errMsg}
              </div>
            )}
          </div>

          {/* 底部按钮 */}
          <div className="shrink-0 px-4 py-3 border-t border-gray-800/80 flex gap-2">
            <button
              onClick={handleClear}
              className="px-3 text-xs text-gray-600 hover:text-gray-300 bg-gray-800/60 hover:bg-gray-700/60 py-1.5 rounded-md transition-all border border-gray-700/40"
            >
              清空
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className={`flex-1 text-xs py-1.5 rounded-md transition-all font-medium border ${
                saved
                  ? 'bg-emerald-700/80 text-emerald-200 border-emerald-600/40 cursor-default'
                  : 'bg-blue-600/90 hover:bg-blue-500 disabled:opacity-40 text-white border-blue-500/30'
              }`}
            >
              {saving ? '保存中...' : saved ? '✓ 已保存' : '保存笔记'}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
