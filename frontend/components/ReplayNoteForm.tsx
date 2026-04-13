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

const TREND_OPTIONS: { value: string; label: string; color: string; active: string }[] = [
  { value: '强烈看多', label: '强多', color: 'border-green-700 text-green-500',       active: 'border-green-500 bg-green-500/20 text-green-300' },
  { value: '看多',     label: '看多', color: 'border-green-800 text-green-600',       active: 'border-green-600 bg-green-600/15 text-green-400' },
  { value: '中性',     label: '中性', color: 'border-gray-700 text-gray-500',         active: 'border-gray-500 bg-gray-500/20 text-gray-300'   },
  { value: '看空',     label: '看空', color: 'border-red-800 text-red-600',           active: 'border-red-600 bg-red-600/15 text-red-400'       },
  { value: '强烈看空', label: '强空', color: 'border-red-700 text-red-500',           active: 'border-red-500 bg-red-500/20 text-red-300'       },
]

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-gray-500 mb-1">{label}</label>
      {children}
    </div>
  )
}

const inputCls = 'w-full bg-gray-800 border border-gray-700/80 rounded px-2 py-1.5 text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-blue-500/80 focus:bg-gray-800 transition-colors'
const areaCls  = inputCls + ' resize-none leading-relaxed'

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
    <div className="flex flex-col h-full bg-gray-900 overflow-hidden">
      {/* 头部 */}
      <div className="shrink-0 px-4 py-3 border-b border-gray-800">
        <div className="flex items-center justify-between">
          <div className="font-semibold text-sm text-gray-100">复盘笔记</div>
          {currentTime && (
            <div className="text-xs text-gray-600 tabular-nums">
              {currentTime.slice(0, 10)}
            </div>
          )}
        </div>
        <div className="text-xs text-gray-600 mt-0.5">
          {symbol} · {timeframe}
        </div>
      </div>

      {notStarted ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-2 px-4 text-center">
          <div className="w-10 h-10 rounded-full bg-gray-800 flex items-center justify-center mb-1">
            <svg className="w-5 h-5 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M12 6v6l4 2m6-2a10 10 0 1 1-20 0 10 10 0 0 1 20 0z" />
            </svg>
          </div>
          <div className="text-sm text-gray-500">请先开始复盘</div>
          <div className="text-xs text-gray-700">选择起始日期后点击「开始复盘」</div>
        </div>
      ) : (
        <>
          {/* 表单主体 */}
          <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3.5">
            {/* 趋势判断 */}
            <Field label="趋势判断">
              <div className="flex gap-1">
                {TREND_OPTIONS.map(opt => (
                  <button
                    key={opt.value}
                    onClick={() => setTrendView(trendView === opt.value ? '' : opt.value)}
                    title={opt.value}
                    className={`flex-1 text-xs py-1 rounded border transition-colors ${
                      trendView === opt.value ? opt.active : opt.color + ' hover:brightness-125'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </Field>

            {/* 支撑 / 阻力 */}
            <div className="grid grid-cols-2 gap-2">
              <Field label="支撑位">
                <input
                  type="number"
                  value={supportLevel}
                  onChange={e => setSupportLevel(e.target.value)}
                  placeholder="—"
                  className={inputCls}
                />
              </Field>
              <Field label="阻力位">
                <input
                  type="number"
                  value={resistanceLevel}
                  onChange={e => setResistanceLevel(e.target.value)}
                  placeholder="—"
                  className={inputCls}
                />
              </Field>
            </div>

            {/* 止损 / 止盈 */}
            <div className="grid grid-cols-2 gap-2">
              <Field label="止损">
                <input
                  type="number"
                  value={stopLoss}
                  onChange={e => setStopLoss(e.target.value)}
                  placeholder="—"
                  className={inputCls + ' focus:border-red-500/80'}
                />
              </Field>
              <Field label="止盈">
                <input
                  type="number"
                  value={takeProfit}
                  onChange={e => setTakeProfit(e.target.value)}
                  placeholder="—"
                  className={inputCls + ' focus:border-green-500/80'}
                />
              </Field>
            </div>

            {/* 入场逻辑 */}
            <Field label="入场想法">
              <textarea
                rows={3}
                value={entryIdea}
                onChange={e => setEntryIdea(e.target.value)}
                placeholder="形态、信号、入场理由..."
                className={areaCls}
              />
            </Field>

            {/* 总结 */}
            <Field label="综合总结">
              <textarea
                rows={4}
                value={summary}
                onChange={e => setSummary(e.target.value)}
                placeholder="当前整体判断和推演..."
                className={areaCls}
              />
            </Field>

            {errMsg && (
              <div className="flex items-center gap-2 text-xs text-red-400 bg-red-400/10 border border-red-800/50 rounded px-3 py-2">
                <svg className="w-3.5 h-3.5 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16zm-1-9v4h2V9H9zm0-4v2h2V5H9z" clipRule="evenodd"/>
                </svg>
                {errMsg}
              </div>
            )}
          </div>

          {/* 底部按钮 */}
          <div className="shrink-0 px-4 py-3 border-t border-gray-800 flex gap-2">
            <button
              onClick={handleClear}
              className="flex-1 text-sm text-gray-500 hover:text-gray-200 bg-gray-800 hover:bg-gray-700 py-1.5 rounded transition-colors"
            >
              清空
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className={`flex-1 text-sm py-1.5 rounded transition-colors font-medium ${
                saved
                  ? 'bg-green-700 text-green-100 cursor-default'
                  : 'bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white'
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
