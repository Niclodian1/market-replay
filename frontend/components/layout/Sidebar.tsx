'use client'

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { clsx } from 'clsx'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getWatchlist, deleteWatchlist } from '@/lib/api'
import type { WatchlistItem } from '@/types'

const NAV_ITEMS = [
  {
    label: '首页', href: '/',
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
          d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 0 0 1 1h3m10-11l2 2m-2-2v10a1 1 0 0 1-1 1h-3m-6 0a1 1 0 0 0 1-1v-4a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v4a1 1 0 0 0 1 1m-6 0h6" />
      </svg>
    ),
  },
  {
    label: '复盘笔记', href: '/notes',
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
          d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
      </svg>
    ),
  },
]

export function Sidebar() {
  const pathname = usePathname()
  const router = useRouter()
  const queryClient = useQueryClient()

  const { data } = useQuery<{ items: WatchlistItem[] }>({
    queryKey: ['watchlist'],
    queryFn: () => getWatchlist(),
    staleTime: 60_000,
  })

  const removeMutation = useMutation({
    mutationFn: (id: number) => deleteWatchlist(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
  })

  const watchlist: WatchlistItem[] = data?.items ?? []

  return (
    <aside className="w-48 bg-gray-900 border-r border-gray-800 flex flex-col shrink-0 overflow-hidden">
      {/* 导航菜单 */}
      <nav className="p-2 pt-3 space-y-0.5">
        <div className="text-xs font-semibold text-gray-700 uppercase tracking-wider px-2 mb-1.5">
          导航
        </div>
        {NAV_ITEMS.map((item) => {
          const active = item.href === '/' ? pathname === '/' : pathname.startsWith(item.href)
          return (
            <Link
              key={item.href}
              href={item.href}
              className={clsx(
                'flex items-center gap-2.5 px-2.5 py-2 rounded-md text-sm transition-colors',
                active
                  ? 'bg-gray-800 text-gray-100'
                  : 'text-gray-400 hover:bg-gray-800/60 hover:text-gray-200'
              )}
            >
              <span className={active ? 'text-blue-400' : 'text-gray-600'}>{item.icon}</span>
              <span>{item.label}</span>
            </Link>
          )
        })}
      </nav>

      {/* 分割线 */}
      <div className="border-t border-gray-800 mx-3 mt-1" />

      {/* 自选列表 */}
      <div className="flex-1 overflow-y-auto p-2 pt-3">
        <div className="text-xs font-semibold text-gray-700 uppercase tracking-wider px-2 mb-1.5">
          自选列表
        </div>

        {watchlist.length === 0 ? (
          <div className="mx-1 mt-2 rounded-md border border-dashed border-gray-800 px-3 py-4 text-center">
            <div className="text-xs text-gray-600">暂无自选</div>
            <div className="text-xs text-gray-700 mt-1">在图表页点击「+ 自选」</div>
          </div>
        ) : (
          <div className="space-y-0.5">
            {watchlist.map((item) => {
              const active = pathname === `/chart/${encodeURIComponent(item.symbol)}`
              return (
                <div
                  key={item.id}
                  className={clsx(
                    'group flex items-center gap-1 px-2 py-1.5 rounded-md transition-colors',
                    active ? 'bg-gray-800' : 'hover:bg-gray-800/60'
                  )}
                >
                  <button
                    onClick={() => router.push(`/chart/${encodeURIComponent(item.symbol)}`)}
                    className="flex-1 text-left min-w-0"
                  >
                    <div className={clsx(
                      'text-sm font-mono transition-colors truncate',
                      active ? 'text-gray-100' : 'text-gray-300 group-hover:text-gray-100'
                    )}>
                      {item.symbol}
                    </div>
                    <div className="text-xs text-gray-600 capitalize">{item.asset_type}</div>
                  </button>
                  <button
                    onClick={() => removeMutation.mutate(item.id)}
                    className="opacity-0 group-hover:opacity-100 w-5 h-5 flex items-center justify-center rounded text-gray-600 hover:text-red-400 hover:bg-red-400/10 text-xs transition-all shrink-0"
                    title="移除自选"
                  >
                    ×
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </aside>
  )
}
