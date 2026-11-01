'use client'

import { useEffect, useRef } from 'react'
import { MAX_TIMER_MS, REFRESH_GRACE_MS, lastClosedBarMs, nextRefreshDelayMs } from './barClose'

/**
 * 收盘对齐刷新：每跨过一根已收盘 K 线（且过了 grace）回调一次，其余时间不打扰后端。
 *
 * 长周期（4h/1day）不会挂一个几小时的 setTimeout，而是按分钟轮询判断，
 * 页面休眠/唤醒（visibilitychange）后也会立刻重新校准。
 */
export function useBarCloseRefresh(
  timeframe: string,
  onBarClose: () => void,
  options: { enabled?: boolean } = {},
): void {
  const callbackRef = useRef(onBarClose)
  callbackRef.current = onBarClose
  const lastBarRef = useRef<number | null>(null)
  const enabled = options.enabled ?? true

  useEffect(() => {
    if (!enabled) return
    lastBarRef.current = null
    let timer: ReturnType<typeof setTimeout> | null = null
    let cancelled = false

    const tick = () => {
      if (cancelled) return
      const now = Date.now()
      const closed = lastClosedBarMs(timeframe, now)
      if (closed !== null) {
        const gracePassed = now - closed >= REFRESH_GRACE_MS
        if (lastBarRef.current === null) {
          lastBarRef.current = closed
        } else if (closed > lastBarRef.current && gracePassed) {
          lastBarRef.current = closed
          callbackRef.current()
        }
      }
      const delay = nextRefreshDelayMs(timeframe, now)
      const wait = delay === null ? MAX_TIMER_MS : Math.min(Math.max(delay, 1_000), MAX_TIMER_MS)
      timer = setTimeout(tick, wait)
    }

    tick()
    const onVisible = () => {
      if (document.visibilityState === 'visible') tick()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [timeframe, enabled])
}
