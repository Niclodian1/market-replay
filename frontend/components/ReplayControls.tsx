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
  const isClose = currentCandle
    ? currentCandle.close >= currentCandle.open
    : true

  return (
    <div className="shrink-0 bg-gray-950/80 border-t border-gray-800 px-4 py-2.5">
      {/* 控制行 */}
      <div className="flex items-center gap-3 flex-wrap">

        {/* 日期 + 开始/重置 */}
        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-600 whitespace-nowrap shrink-0">起始日期</label>
          <input
            type="date"
            value={replayDate}
            onChange={e => onDateChange(e.target.value)}
            disabled={isReplaying && isPlaying}
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-200 focus:outline-none focus:border-blue-500 disabled:opacity-50 w-32 transition-colors"
          />
          {!isReplaying ? (
            <button
              onClick={onStartReplay}
              disabled={!replayDate}
              className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white px-3 py-1 rounded transition-colors font-medium"
            >
              开始复盘
            </button>
          ) : (
            <button
              onClick={onReset}
              title="重置到起始位置"
              className="text-xs bg-gray-800 hover:bg-gray-700 text-gray-300 px-2.5 py-1 rounded transition-colors"
            >
              ↺ 重置
            </button>
          )}
        </div>

        <div className="w-px h-5 bg-gray-800 shrink-0" />

        {/* 播放控制 */}
        <div className="flex items-center gap-1">
          {/* 上一根 */}
          <button
            onClick={onPrev}
            disabled={!isReplaying || !canGoPrev || isPlaying}
            title="上一根 (←)"
            className="w-7 h-7 flex items-center justify-center rounded-md bg-gray-800 hover:bg-gray-700 disabled:opacity-25 text-gray-300 transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M6 6h2v12H6zm3.5 6 8.5 6V6z"/>
            </svg>
          </button>

          {/* 播放/暂停 */}
          <button
            onClick={isPlaying ? onPause : onPlay}
            disabled={!isReplaying || (!isPlaying && !canGoNext)}
            title={isPlaying ? '暂停 (Space)' : '播放 (Space)'}
            className={clsx(
              'w-9 h-7 flex items-center justify-center rounded-md transition-colors font-medium',
              isPlaying
                ? 'bg-amber-500 hover:bg-amber-400 text-white'
                : 'bg-green-600 hover:bg-green-500 disabled:opacity-25 text-white'
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

          {/* 下一根 */}
          <button
            onClick={onNext}
            disabled={!isReplaying || !canGoNext || isPlaying}
            title="下一根 (→)"
            className="w-7 h-7 flex items-center justify-center rounded-md bg-gray-800 hover:bg-gray-700 disabled:opacity-25 text-gray-300 transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M6 18l8.5-6L6 6v12zm2-8.14 5.5 3.64L8 15.14V9.86zM16 6h2v12h-2z"/>
            </svg>
          </button>
        </div>

        {/* 速度 */}
        <div className="flex items-center gap-1">
          <span className="text-xs text-gray-600">速度</span>
          <div className="flex items-center gap-0.5">
            {SPEEDS.map(s => (
              <button
                key={s.ms}
                onClick={() => onSpeedChange(s.ms)}
                className={clsx(
                  'text-xs px-1.5 py-0.5 rounded transition-colors',
                  playSpeedMs === s.ms
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-500 hover:text-gray-300 hover:bg-gray-800'
                )}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        {/* OHLCV（当前K线） */}
        {isReplaying && currentCandle && (
          <div className="ml-auto flex items-center gap-3 text-xs tabular-nums shrink-0">
            <span className="text-gray-600">{fmtTs(currentCandle.time)}</span>
            <span className="text-gray-500">O <span className="text-gray-300">{fmtPrice(currentCandle.open)}</span></span>
            <span className="text-gray-500">H <span className="text-green-400">{fmtPrice(currentCandle.high)}</span></span>
            <span className="text-gray-500">L <span className="text-red-400">{fmtPrice(currentCandle.low)}</span></span>
            <span className="text-gray-500">C <span className={isClose ? 'text-green-400' : 'text-red-400'}>{fmtPrice(currentCandle.close)}</span></span>
          </div>
        )}
      </div>

      {/* 进度条（复盘中显示） */}
      {isReplaying && (
        <div className="flex items-center gap-2 mt-2">
          <div className="flex-1 h-1 bg-gray-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-500 rounded-full transition-all duration-100"
              style={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
            />
          </div>
          <span className="text-xs text-gray-600 tabular-nums shrink-0 w-20 text-right">
            {cursorIndex - startIndex + 1} / {totalCandles - startIndex} 根
          </span>
        </div>
      )}
    </div>
  )
}
