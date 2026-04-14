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

  // 均线周期（两条可自定义）
  maPeriods: [number, number]
  setMaPeriods: (periods: [number, number]) => void

  // 首页自选个股
  homepageStocks: string[]
  addHomepageStock: (symbol: string) => void
  removeHomepageStock: (symbol: string) => void
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

      maPeriods: [30, 60],
      setMaPeriods: (periods) => set({ maPeriods: periods }),

      homepageStocks: ['GOOGL', 'NVDA', 'CRCL'],
      addHomepageStock: (symbol) =>
        set((state) => ({
          homepageStocks: state.homepageStocks.includes(symbol)
            ? state.homepageStocks
            : [...state.homepageStocks, symbol],
        })),
      removeHomepageStock: (symbol) =>
        set((state) => ({
          homepageStocks: state.homepageStocks.filter((s) => s !== symbol),
        })),
    }),
    {
      name: 'market-replay-store',
      partialize: (state) => ({
        watchlist: state.watchlist,
        currentTimeframe: state.currentTimeframe,
        maPeriods: state.maPeriods,
        homepageStocks: state.homepageStocks,
      }),
    }
  )
)
