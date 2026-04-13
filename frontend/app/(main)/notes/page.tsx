'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getReplayNotes, deleteReplayNote } from '@/lib/api'
import type { ReplayNote } from '@/types'

function fmtDate(iso: string | Date): string {
  return new Date(iso).toISOString().slice(0, 10)
}

const TREND_STYLE: Record<string, { bg: string; text: string; bar: string }> = {
  '强烈看多': { bg: 'bg-green-500/10', text: 'text-green-400', bar: 'bg-green-500/60' },
  '看多':     { bg: 'bg-green-500/8',  text: 'text-green-500', bar: 'bg-green-500/40' },
  '中性':     { bg: 'bg-gray-500/10',  text: 'text-gray-400',  bar: 'bg-gray-500/40'  },
  '看空':     { bg: 'bg-red-500/8',    text: 'text-red-500',   bar: 'bg-red-500/40'   },
  '强烈看空': { bg: 'bg-red-500/10',   text: 'text-red-400',   bar: 'bg-red-500/60'   },
}

function TrendBadge({ trend }: { trend?: string }) {
  if (!trend) return null
  const s = TREND_STYLE[trend] ?? { bg: 'bg-gray-700/50', text: 'text-gray-400', bar: 'bg-gray-500' }
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full ${s.bg} ${s.text} font-medium`}>
      {trend}
    </span>
  )
}

function NoteCard({ note, onDelete }: { note: ReplayNote; onDelete: (id: number) => void }) {
  const [expanded, setExpanded] = useState(false)
  const [confirming, setConfirming] = useState(false)

  const trend = note.trend_view
  const barColor = trend ? (TREND_STYLE[trend]?.bar ?? 'bg-gray-600/40') : 'bg-gray-800'

  const hasDetails = note.support_level != null || note.resistance_level != null ||
    note.stop_loss != null || note.take_profit != null || note.entry_idea || note.summary

  return (
    <div className={`relative bg-gray-900/50 border rounded-lg overflow-hidden transition-all duration-200 ${
      expanded ? 'border-gray-700/80' : 'border-gray-800/80 hover:border-gray-700/60'
    }`}>
      {/* 左侧趋势条 */}
      <div className={`absolute left-0 top-0 bottom-0 w-0.5 ${barColor} rounded-r`} />

      {/* 卡片头部 */}
      <div
        className="flex items-center gap-3 pl-4 pr-4 py-3 cursor-pointer"
        onClick={() => hasDetails && setExpanded(v => !v)}
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono font-bold text-gray-100 text-sm tracking-wide">{note.symbol}</span>
            <span className="text-xs text-gray-600 bg-gray-800/80 px-1.5 py-0.5 rounded font-mono">
              {note.timeframe}
            </span>
            <TrendBadge trend={note.trend_view} />
          </div>
          <div className="text-xs text-gray-600 mt-1.5 flex items-center gap-2 flex-wrap font-mono">
            <span>复盘 {fmtDate(note.replay_start_time)}</span>
            <span className="text-gray-800">·</span>
            <span>记录 {fmtDate(note.note_time)}</span>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <Link
            href={`/replay/${encodeURIComponent(note.symbol)}`}
            onClick={e => e.stopPropagation()}
            className="text-xs text-blue-500 hover:text-blue-300 transition-colors px-2 py-0.5 rounded hover:bg-blue-400/8 border border-transparent hover:border-blue-800/40"
          >
            复盘 →
          </Link>

          {confirming ? (
            <div className="flex items-center gap-1.5" onClick={e => e.stopPropagation()}>
              <button
                onClick={() => onDelete(note.id)}
                className="text-xs text-red-400 hover:text-red-300 transition-colors font-medium"
              >
                确认
              </button>
              <button
                onClick={() => setConfirming(false)}
                className="text-xs text-gray-600 hover:text-gray-400"
              >
                取消
              </button>
            </div>
          ) : (
            <button
              onClick={e => { e.stopPropagation(); setConfirming(true) }}
              className="text-xs text-gray-700 hover:text-red-400 transition-colors px-1"
            >
              删除
            </button>
          )}

          {hasDetails && (
            <span className="text-gray-700 text-xs w-4 text-center">
              {expanded ? '▲' : '▼'}
            </span>
          )}
        </div>
      </div>

      {/* 展开内容 */}
      {expanded && hasDetails && (
        <div className="pl-4 pr-4 pb-4 border-t border-gray-800/60 pt-3 space-y-3">
          {(note.support_level != null || note.resistance_level != null ||
            note.stop_loss != null || note.take_profit != null) && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {note.support_level != null && (
                <div className="bg-gray-800/40 border border-gray-700/40 rounded-md px-3 py-2">
                  <div className="text-xs text-gray-600 mb-1">支撑位</div>
                  <div className="text-sm text-blue-300 font-mono tabular-nums">
                    {note.support_level.toLocaleString()}
                  </div>
                </div>
              )}
              {note.resistance_level != null && (
                <div className="bg-gray-800/40 border border-gray-700/40 rounded-md px-3 py-2">
                  <div className="text-xs text-gray-600 mb-1">阻力位</div>
                  <div className="text-sm text-purple-300 font-mono tabular-nums">
                    {note.resistance_level.toLocaleString()}
                  </div>
                </div>
              )}
              {note.stop_loss != null && (
                <div className="bg-red-950/30 border border-red-900/30 rounded-md px-3 py-2">
                  <div className="text-xs text-gray-600 mb-1">止损</div>
                  <div className="text-sm text-red-300 font-mono tabular-nums">
                    {note.stop_loss.toLocaleString()}
                  </div>
                </div>
              )}
              {note.take_profit != null && (
                <div className="bg-emerald-950/30 border border-emerald-900/30 rounded-md px-3 py-2">
                  <div className="text-xs text-gray-600 mb-1">止盈</div>
                  <div className="text-sm text-emerald-300 font-mono tabular-nums">
                    {note.take_profit.toLocaleString()}
                  </div>
                </div>
              )}
            </div>
          )}

          {note.entry_idea && (
            <div>
              <div className="text-xs text-gray-600 mb-1.5 flex items-center gap-1.5">
                <span className="w-1 h-3 rounded-full bg-blue-500/60 inline-block" />
                入场想法
              </div>
              <p className="text-sm text-gray-400 whitespace-pre-wrap leading-relaxed bg-gray-800/30 border border-gray-700/30 rounded-md px-3 py-2">
                {note.entry_idea}
              </p>
            </div>
          )}

          {note.summary && (
            <div>
              <div className="text-xs text-gray-600 mb-1.5 flex items-center gap-1.5">
                <span className="w-1 h-3 rounded-full bg-amber-500/60 inline-block" />
                综合总结
              </div>
              <p className="text-sm text-gray-400 whitespace-pre-wrap leading-relaxed bg-gray-800/30 border border-gray-700/30 rounded-md px-3 py-2">
                {note.summary}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function NotesPage() {
  const [filterSymbol, setFilterSymbol] = useState('')
  const queryClient = useQueryClient()

  const { data, isLoading, isError } = useQuery({
    queryKey: ['replay-notes', filterSymbol],
    queryFn: () => getReplayNotes(filterSymbol || undefined),
    staleTime: 30_000,
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteReplayNote(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['replay-notes'] }),
  })

  const notes: ReplayNote[] = data?.items ?? []

  return (
    <div className="flex flex-col h-full gap-4">
      {/* 顶部 */}
      <div className="flex items-center gap-4 shrink-0 flex-wrap">
        <div>
          <h1 className="text-base font-bold text-gray-100 tracking-tight">复盘笔记</h1>
          <p className="text-xs text-gray-600 mt-0.5 font-mono">
            {data ? `共 ${data.total} 条` : '加载中...'}
          </p>
        </div>

        <div className="flex-1" />

        {/* 搜索框 */}
        <div className="relative">
          <svg
            className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3 h-3 text-gray-600 pointer-events-none"
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M21 21l-4.35-4.35M17 11A6 6 0 1 1 5 11a6 6 0 0 1 12 0z" />
          </svg>
          <input
            type="text"
            value={filterSymbol}
            onChange={e => setFilterSymbol(e.target.value.toUpperCase())}
            placeholder="按标的筛选"
            className="bg-gray-900/60 border border-gray-700/60 rounded-md pl-7 pr-7 py-1.5 text-xs text-gray-200 placeholder-gray-700 focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/15 w-36 transition-all font-mono"
          />
          {filterSymbol && (
            <button
              onClick={() => setFilterSymbol('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-600 hover:text-gray-400 text-xs"
            >
              ×
            </button>
          )}
        </div>
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-y-auto space-y-2 pr-0.5">
        {isLoading && (
          <div className="flex flex-col items-center justify-center h-40 text-gray-600 gap-3">
            <div className="w-5 h-5 border-2 border-blue-500/60 border-t-blue-400 rounded-full animate-spin" />
            <span className="text-xs">加载笔记...</span>
          </div>
        )}

        {isError && (
          <div className="flex items-center justify-center h-40">
            <div className="text-center">
              <div className="text-red-400/80 text-sm mb-1">加载失败</div>
              <div className="text-xs text-gray-600">请检查后端连接是否正常</div>
            </div>
          </div>
        )}

        {!isLoading && !isError && notes.length === 0 && (
          <div className="flex flex-col items-center justify-center h-40 gap-3">
            <div className="w-12 h-12 rounded-xl bg-gray-800/60 border border-gray-700/40 flex items-center justify-center">
              <svg className="w-5 h-5 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2" />
              </svg>
            </div>
            <div className="text-center">
              <div className="text-sm text-gray-500">
                {filterSymbol ? `没有 ${filterSymbol} 的复盘笔记` : '还没有复盘笔记'}
              </div>
              {!filterSymbol && (
                <Link href="/" className="text-xs text-blue-500 hover:text-blue-300 transition-colors mt-1.5 block">
                  搜索品种，开始复盘 →
                </Link>
              )}
            </div>
          </div>
        )}

        {notes.map(note => (
          <NoteCard
            key={note.id}
            note={note}
            onDelete={id => deleteMutation.mutate(id)}
          />
        ))}
      </div>
    </div>
  )
}
