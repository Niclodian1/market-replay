import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { WatchlistItem, Timeframe } from '@/types'

interface AppState {
  // 自选列表（同步自后端，此处做本地缓存）
  watchlist: WatchlistItem[]
  setWatchlist: (items: WatchlistItem[]) => void
  addToWatchlist: (item: WatchlistItem) => void
  removeFromWatchlist: (id: number) => void

  // 当前查看的标的
  currentSymbol: string | null
  setCurrentSymbol: (symbol: string | null) => void

  // 当前周期
  currentTimeframe: Timeframe
  setCurrentTimeframe: (tf: Timeframe) => void

  // 复盘状态
  replayActive: boolean
  setReplayActive: (active: boolean) => void
  replayCursorIndex: number
  setReplayCursorIndex: (idx: number) => void
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      watchlist: [],
      setWatchlist: (items) => set({ watchlist: items }),
      addToWatchlist: (item) =>
        set((state) => ({
          watchlist: state.watchlist.some((w) => w.symbol === item.symbol)
            ? state.watchlist
            : [...state.watchlist, item],
        })),
      removeFromWatchlist: (id) =>
        set((state) => ({
          watchlist: state.watchlist.filter((item) => item.id !== id),
        })),

      currentSymbol: null,
      setCurrentSymbol: (symbol) => set({ currentSymbol: symbol }),

      currentTimeframe: '1day',
      setCurrentTimeframe: (tf) => set({ currentTimeframe: tf }),

      replayActive: false,
      setReplayActive: (active) => set({ replayActive: active }),
      replayCursorIndex: 0,
      setReplayCursorIndex: (idx) => set({ replayCursorIndex: idx }),
    }),
    {
      name: 'market-replay-store',
      // 只持久化自选列表和周期偏好
      partialize: (state) => ({
        watchlist: state.watchlist,
        currentTimeframe: state.currentTimeframe,
      }),
    }
  )
)
