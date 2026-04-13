'use client'

import { clsx } from 'clsx'
import type { Candle } from '@/types'

const SPEEDS = [
  { label: '0.5×', ms: 2000 },
  { label: '1×',   ms: 1000 },
  { label: '2×',   ms: 500  },
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

function fmtTs(ts: number): string {
  return new Date(ts * 1000).toISOString().slice(0, 10)
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
  const progress = totalCandles > 0
    ? ((cursorIndex - startIndex) / Math.max(1, totalCandles - 1 - startIndex)) * 100
    : 0
  const canGoNext = cursorIndex < totalCandles - 1
  const canGoPrev = cursorIndex > startIndex
  const isClose = currentCandle ? currentCandle.close >= currentCandle.open : true

  return (
    <div className="shrink-0 bg-gray-950/90 backdrop-blur border-t border-gray-800/80 px-4 py-2.5">
      {/* 控制行 */}
      <div className="flex items-center gap-3 flex-wrap">

        {/* 日期 + 开始/重置 */}
        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-600 whitespace-nowrap shrink-0">起始</label>
          <input
            type="date"
            value={replayDate}
            onChange={e => onDateChange(e.target.value)}
            disabled={isReplaying && isPlaying}
            className="bg-gray-800/80 border border-gray-700/80 rounded-md px-2 py-1 text-xs text-gray-300 font-mono focus:outline-none focus:border-blue-500/60 focus:ring-1 focus:ring-blue-500/20 disabled:opacity-40 w-32 transition-all"
          />
          {!isReplaying ? (
            <button
              onClick={onStartReplay}
              disabled={!replayDate}
              className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-30 text-white px-3 py-1 rounded-md transition-all font-medium border border-blue-500/30"
            >
              开始复盘
            </button>
          ) : (
            <button
              onClick={onReset}
              title="重置到起始位置"
              className="flex items-center gap-1 text-xs bg-gray-800/80 hover:bg-gray-700 text-gray-400 hover:text-gray-200 px-2.5 py-1 rounded-md transition-all border border-gray-700/60"
            >
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 0 0 4.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 0 1-15.357-2m15.357 2H15" />
              </svg>
              重置
            </button>
          )}
        </div>

        <div className="w-px h-5 bg-gray-800 shrink-0" />

        {/* 播放控制 */}
        <div className="flex items-center gap-1">
          <button
            onClick={onPrev}
            disabled={!isReplaying || !canGoPrev || isPlaying}
            title="上一根 (←)"
            className="w-7 h-7 flex items-center justify-center rounded-md border border-gray-700/60 bg-gray-800/60 hover:bg-gray-700 disabled:opacity-20 text-gray-400 transition-all"
          >
            <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M6 6h2v12H6zm3.5 6 8.5 6V6z"/>
            </svg>
          </button>

          <button
            onClick={isPlaying ? onPause : onPlay}
            disabled={!isReplaying || (!isPlaying && !canGoNext)}
            title={isPlaying ? '暂停 (Space)' : '播放 (Space)'}
            className={clsx(
              'w-9 h-7 flex items-center justify-center rounded-md transition-all border',
              isPlaying
                ? 'bg-amber-500/90 hover:bg-amber-400 text-white border-amber-400/30'
                : 'bg-emerald-600/90 hover:bg-emerald-500 disabled:opacity-20 text-white border-emerald-500/30'
            )}
          >
            {isPlaying ? (
              <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>
              </svg>
            ) : (
              <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M8 5v14l11-7z"/>
              </svg>
            )}
          </button>

          <button
            onClick={onNext}
            disabled={!isReplaying || !canGoNext || isPlaying}
            title="下一根 (→)"
            className="w-7 h-7 flex items-center justify-center rounded-md border border-gray-700/60 bg-gray-800/60 hover:bg-gray-700 disabled:opacity-20 text-gray-400 transition-all"
          >
            <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M6 18l8.5-6L6 6v12zm2-8.14 5.5 3.64L8 15.14V9.86zM16 6h2v12h-2z"/>
            </svg>
          </button>
        </div>

        {/* 速度 */}
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-gray-700 font-medium">速度</span>
          <div className="flex items-center gap-0.5 bg-gray-900/80 border border-gray-800 rounded-md p-0.5">
            {SPEEDS.map(s => (
              <button
                key={s.ms}
                onClick={() => onSpeedChange(s.ms)}
                className={clsx(
                  'text-xs px-1.5 py-0.5 rounded transition-all',
                  playSpeedMs === s.ms
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-600 hover:text-gray-300 hover:bg-gray-800'
                )}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        {/* OHLCV */}
        {isReplaying && currentCandle && (
          <div className="ml-auto flex items-center gap-2.5 text-xs tabular-nums font-mono shrink-0">
            <span className="text-gray-600 text-xs">{fmtTs(currentCandle.time)}</span>
            <span className="text-gray-600">
              O <span className="text-gray-300">{fmtPrice(currentCandle.open)}</span>
            </span>
            <span className="text-gray-600">
              H <span className="text-emerald-400">{fmtPrice(currentCandle.high)}</span>
            </span>
            <span className="text-gray-600">
              L <span className="text-red-400">{fmtPrice(currentCandle.low)}</span>
            </span>
            <span className="text-gray-600">
              C <span className={isClose ? 'text-emerald-400' : 'text-red-400'}>{fmtPrice(currentCandle.close)}</span>
            </span>
          </div>
        )}
      </div>

      {/* 进度条 */}
      {isReplaying && (
        <div className="flex items-center gap-2.5 mt-2.5">
          <div className="flex-1 relative h-1 bg-gray-800/80 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-blue-600 to-blue-500 rounded-full transition-all duration-100"
              style={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
            />
          </div>
          <span className="text-xs text-gray-600 tabular-nums font-mono shrink-0">
            {cursorIndex - startIndex + 1}<span className="text-gray-700 mx-0.5">/</span>{totalCandles - startIndex}
          </span>
        </div>
      )}
    </div>
  )
}
