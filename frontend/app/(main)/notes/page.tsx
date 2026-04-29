'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getReplayNotes, deleteReplayNote } from '@/lib/api'
import type { ReplayNote } from '@/types'

function fmtDate(iso: string | Date): string {
  return new Date(iso).toISOString().slice(0, 10)
}

function NoteRow({ note, onDelete }: { note: ReplayNote; onDelete: (id: number) => void }) {
  const [expanded, setExpanded] = useState(false)
  const [confirming, setConfirming] = useState(false)

  const up = note.trend_view === '强多' || note.trend_view === '看多' || note.trend_view === '强烈看多'
  const down = note.trend_view === '强空' || note.trend_view === '看空' || note.trend_view === '强烈看空'
  const accentColor = up ? 'var(--up)' : down ? 'var(--down)' : 'var(--fg-3)'

  const hasDetails = note.support_level != null || note.resistance_level != null ||
    note.stop_loss != null || note.take_profit != null || note.entry_idea || note.summary

  return (
    <div style={{ background: 'var(--bg-1)', border: '1px solid var(--line-1)', borderRadius: 'var(--radius-lg)', overflow: 'hidden', marginBottom: 8 }}>
      <div
        style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', cursor: hasDetails ? 'pointer' : 'default', borderLeft: `2px solid ${accentColor}` }}
        onClick={() => hasDetails && setExpanded(v => !v)}
        onMouseEnter={e => hasDetails && (e.currentTarget.style.background = 'var(--bg-2)')}
        onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span className="mono" style={{ fontWeight: 700, color: 'var(--fg-0)', fontSize: 13 }}>{note.symbol}</span>
            <span className="chip" style={{ fontSize: 9 }}>{note.timeframe.toUpperCase()}</span>
            {note.trend_view && (
              <span className="chip" style={{ fontSize: 9, color: accentColor, borderColor: 'transparent', background: 'transparent' }}>
                {note.trend_view}
              </span>
            )}
          </div>
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-2)', marginTop: 4, display: 'flex', gap: 10 }}>
            <span>复盘 {fmtDate(note.replay_start_time)}</span>
            <span>记录 {fmtDate(note.note_time)}</span>
          </div>
          {note.trend_view && !['强多', '看多', '中性', '看空', '强空'].includes(note.trend_view) && (
            <div style={{ fontSize: 11.5, color: 'var(--fg-1)', marginTop: 6, lineHeight: 1.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {note.trend_view}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
          <Link href={`/replay/${encodeURIComponent(note.symbol)}`}
            onClick={e => e.stopPropagation()}
            className="btn small ghost" style={{ color: 'var(--accent)' }}>
            复盘 →
          </Link>
          {confirming ? (
            <div style={{ display: 'flex', gap: 6 }} onClick={e => e.stopPropagation()}>
              <button className="btn small danger" onClick={() => onDelete(note.id)}>确认</button>
              <button className="btn small ghost" onClick={() => setConfirming(false)}>取消</button>
            </div>
          ) : (
            <button className="btn small ghost" style={{ color: 'var(--fg-3)' }}
              onClick={e => { e.stopPropagation(); setConfirming(true) }}>删除</button>
          )}
          {hasDetails && (
            <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>{expanded ? '▲' : '▼'}</span>
          )}
        </div>
      </div>

      {expanded && hasDetails && (
        <div style={{ padding: '12px 14px', borderTop: '1px solid var(--line-1)', display: 'flex', flexDirection: 'column', gap: 10 }}>
          {(note.support_level != null || note.resistance_level != null || note.stop_loss != null || note.take_profit != null) && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
              {note.support_level != null && (
                <div style={{ background: 'var(--up-bg)', padding: '8px 10px', borderRadius: 4 }}>
                  <div className="stat-label">支撑位</div>
                  <div className="mono" style={{ color: 'var(--up)', fontSize: 13, marginTop: 2 }}>{note.support_level.toLocaleString()}</div>
                </div>
              )}
              {note.resistance_level != null && (
                <div style={{ background: 'var(--down-bg)', padding: '8px 10px', borderRadius: 4 }}>
                  <div className="stat-label">阻力位</div>
                  <div className="mono" style={{ color: 'var(--down)', fontSize: 13, marginTop: 2 }}>{note.resistance_level.toLocaleString()}</div>
                </div>
              )}
              {note.stop_loss != null && (
                <div style={{ background: 'var(--down-bg)', padding: '8px 10px', borderRadius: 4 }}>
                  <div className="stat-label">止损</div>
                  <div className="mono" style={{ color: 'var(--down)', fontSize: 13, marginTop: 2 }}>{note.stop_loss.toLocaleString()}</div>
                </div>
              )}
              {note.take_profit != null && (
                <div style={{ background: 'var(--up-bg)', padding: '8px 10px', borderRadius: 4 }}>
                  <div className="stat-label">止盈</div>
                  <div className="mono" style={{ color: 'var(--up)', fontSize: 13, marginTop: 2 }}>{note.take_profit.toLocaleString()}</div>
                </div>
              )}
            </div>
          )}
          {note.entry_idea && (
            <div>
              <div className="section-title" style={{ marginBottom: 4 }}>入场思路</div>
              <div style={{ fontSize: 12, color: 'var(--fg-1)', lineHeight: 1.6, whiteSpace: 'pre-wrap', padding: '8px 10px', background: 'var(--bg-2)', borderRadius: 4 }}>
                {note.entry_idea}
              </div>
            </div>
          )}
          {note.summary && (
            <div>
              <div className="section-title" style={{ marginBottom: 4 }}>总结</div>
              <div style={{ fontSize: 12, color: 'var(--fg-1)', lineHeight: 1.6, whiteSpace: 'pre-wrap', padding: '8px 10px', background: 'var(--bg-2)', borderRadius: 4 }}>
                {note.summary}
              </div>
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
    <div className="page">
      <div className="page-inner">
        <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 16 }}>
          <div>
            <div className="section-title">复盘笔记</div>
            <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: 'var(--fg-0)' }}>
              {data ? `${data.total} 条记录` : '…'}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <div style={{ position: 'relative' }}>
              <svg style={{ position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)', width: 12, height: 12, color: 'var(--fg-3)', pointerEvents: 'none' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" />
              </svg>
              <input
                className="mr-input mono"
                style={{ paddingLeft: 26, width: 140, fontSize: 11.5 }}
                placeholder="筛选标的…"
                value={filterSymbol}
                onChange={e => setFilterSymbol(e.target.value.toUpperCase())}
              />
            </div>
            {filterSymbol && (
              <button className="btn small ghost" onClick={() => setFilterSymbol('')}>清除</button>
            )}
          </div>
        </div>

        {isLoading && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, gap: 12, color: 'var(--fg-2)' }}>
            <div style={{ width: 24, height: 24, border: '2px solid var(--accent)', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.7s linear infinite' }} />
            <span className="mono" style={{ fontSize: 12 }}>加载笔记…</span>
          </div>
        )}

        {isError && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, flexDirection: 'column', gap: 8 }}>
            <div style={{ color: 'var(--down)', fontSize: 13 }}>加载失败</div>
            <div style={{ color: 'var(--fg-3)', fontSize: 11 }}>请检查后端连接</div>
          </div>
        )}

        {!isLoading && !isError && notes.length === 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: 200, gap: 12 }}>
            <svg style={{ width: 36, height: 36, color: 'var(--fg-3)' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 4h12l4 4v12H4z" /><path d="M8 10h8M8 14h8M8 18h5" />
            </svg>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 13, color: 'var(--fg-2)' }}>
                {filterSymbol ? `没有 ${filterSymbol} 的复盘笔记` : '还没有复盘笔记'}
              </div>
              {!filterSymbol && (
                <Link href="/" style={{ fontSize: 11.5, color: 'var(--accent)', marginTop: 6, display: 'block' }}>
                  进入复盘模式开始记录 →
                </Link>
              )}
            </div>
          </div>
        )}

        {notes.map(note => (
          <NoteRow key={note.id} note={note} onDelete={id => deleteMutation.mutate(id)} />
        ))}
      </div>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
