'use client'

import type { Candle } from '@/types'

const SPEEDS = [
  { label: '0.5×', ms: 2000 },
  { label: '1×',   ms: 1000 },
  { label: '2×',   ms: 400  },
  { label: '5×',   ms: 200  },
  { label: '10×',  ms: 80   },
]

interface Props {
  isReplaying:   boolean
  isPlaying:     boolean
  cursorIndex:   number
  startIndex:    number
  totalCandles:  number
  currentCandle: Candle | null
  replayDate:    string
  playSpeedMs:   number
  onDateChange:  (date: string) => void
  onStartReplay: () => void
  onPlay:        () => void
  onPause:       () => void
  onNext:        () => void
  onPrev:        () => void
  onReset:       () => void
  onSpeedChange: (ms: number) => void
}

function fmtPrice(v: number): string {
  if (v >= 10000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 })
  if (v >= 100)   return v.toFixed(2)
  if (v >= 1)     return v.toFixed(4)
  return v.toFixed(6)
}

export function ReplayControls({
  isReplaying, isPlaying, cursorIndex, startIndex, totalCandles,
  currentCandle, replayDate, playSpeedMs,
  onDateChange, onStartReplay, onPlay, onPause,
  onNext, onPrev, onReset, onSpeedChange,
}: Props) {
  const canGoNext = cursorIndex < totalCandles - 1
  const canGoPrev = cursorIndex > startIndex
  const isClose = currentCandle ? currentCandle.close >= currentCandle.open : true

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
      {/* Date + start/reset */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <label className="section-title" style={{ whiteSpace: 'nowrap' }}>起始</label>
        <input
          type="date"
          value={replayDate}
          onChange={e => onDateChange(e.target.value)}
          disabled={isReplaying && isPlaying}
          className="mr-input mono"
          style={{ width: 130, fontSize: 11.5 }}
        />
        {!isReplaying ? (
          <button className="btn primary" onClick={onStartReplay} disabled={!replayDate}>
            开始复盘
          </button>
        ) : (
          <button className="btn" onClick={onReset} title="重置到起始位置">
            <svg className="ico-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 4v5h.582m15.356 2A8.001 8.001 0 0 0 4.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 0 1-15.357-2m15.357 2H15" />
            </svg>
            重置
          </button>
        )}
      </div>

      <div className="mr-topbar-divider" />

      {/* Playback controls */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <button className="btn icon" onClick={onPrev} disabled={!isReplaying || !canGoPrev || isPlaying} title="上一根">
          <svg className="ico-sm" viewBox="0 0 24 24" fill="currentColor">
            <path d="M18 5L8 12l10 7V5z" /><path d="M6 5v14" stroke="currentColor" strokeWidth="2" fill="none" />
          </svg>
        </button>

        <button
          className={`btn${isPlaying ? '' : ' primary'}`}
          onClick={isPlaying ? onPause : onPlay}
          disabled={!isReplaying || (!isPlaying && !canGoNext)}
          style={{ width: 88, justifyContent: 'center', background: isPlaying ? 'var(--accent)' : undefined, color: isPlaying ? '#111' : undefined }}
        >
          {isPlaying ? (
            <>
              <svg className="ico-sm" viewBox="0 0 24 24" fill="currentColor"><rect x="7" y="5" width="4" height="14" /><rect x="13" y="5" width="4" height="14" /></svg>
              暂停
            </>
          ) : (
            <>
              <svg className="ico-sm" viewBox="0 0 24 24" fill="currentColor"><path d="M7 5l13 7-13 7V5z" /></svg>
              播放
            </>
          )}
        </button>

        <button className="btn icon" onClick={onNext} disabled={!isReplaying || !canGoNext || isPlaying} title="下一根">
          <svg className="ico-sm" viewBox="0 0 24 24" fill="currentColor">
            <path d="M6 18l8.5-6L6 6v12z" /><path d="M18 5v14" stroke="currentColor" strokeWidth="2" fill="none" />
          </svg>
        </button>
      </div>

      {/* Speed */}
      <div className="tweak-segment">
        {SPEEDS.map(s => (
          <button key={s.ms} className={playSpeedMs === s.ms ? 'on' : ''} onClick={() => onSpeedChange(s.ms)}
            style={{ minWidth: 38, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
            {s.label}
          </button>
        ))}
      </div>

      {/* OHLCV */}
      {isReplaying && currentCandle && (
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 16, fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>
          <span style={{ color: 'var(--fg-2)' }}>O <span style={{ color: 'var(--fg-0)' }}>{fmtPrice(currentCandle.open)}</span></span>
          <span style={{ color: 'var(--fg-2)' }}>H <span style={{ color: 'var(--fg-0)' }}>{fmtPrice(currentCandle.high)}</span></span>
          <span style={{ color: 'var(--fg-2)' }}>L <span style={{ color: 'var(--fg-0)' }}>{fmtPrice(currentCandle.low)}</span></span>
          <span style={{ color: 'var(--fg-2)' }}>C <span style={{ color: isClose ? 'var(--up)' : 'var(--down)' }}>{fmtPrice(currentCandle.close)}</span></span>
          <span style={{ color: 'var(--fg-2)' }}>V <span style={{ color: 'var(--fg-0)' }}>{currentCandle.volume >= 1e6 ? (currentCandle.volume / 1e6).toFixed(2) + 'M' : (currentCandle.volume / 1e3).toFixed(0) + 'K'}</span></span>
          <span className="kbd">Space</span>
          <span className="kbd">←</span>
          <span className="kbd">→</span>
        </div>
      )}
    </div>
  )
}
