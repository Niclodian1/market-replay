'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useRef, useState, useEffect } from 'react'
import { clsx } from 'clsx'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getWatchlist, deleteWatchlist, getQuote, getAlerts } from '@/lib/api'
import type { WatchlistItem, Quote, Alert } from '@/types'

const NAV_ITEMS = [
  {
    label: '首页', href: '/',
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
          d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 0 0 1 1h3m10-11l2 2m-2-2v10a1 1 0 0 1-1 1h-3m-6 0a1 1 0 0 0 1-1v-4a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v4a1 1 0 0 0 1 1m-6 0h6" />
      </svg>
    ),
  },
  {
    label: '复盘笔记', href: '/notes',
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
          d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2" />
      </svg>
    ),
  },
  {
    label: '策略告警', href: '/alerts',
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
          d="M15 17h5l-1.405-1.405A2.032 2.032 0 0 1 18 14.158V11a6.002 6.002 0 0 0-4-5.659V5a2 2 0 1 0-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 1 1-6 0v-1m6 0H9" />
      </svg>
    ),
  },
  {
    label: '交易回测', href: '/backtest',
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
          d="M9 19v-6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2zm0 0V9a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v10m-6 0a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2m0 0V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-2a2 2 0 0 1-2-2z" />
      </svg>
    ),
  },
]

function fmtSidebarPrice(v: number): string {
  if (v >= 10000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 })
  if (v >= 100)   return v.toFixed(2)
  if (v >= 1)     return v.toFixed(4)
  return v.toFixed(6)
}

function SidebarQuoteRow({
  item,
  active,
  hasTriggered,
  onRemove,
}: {
  item: WatchlistItem
  active: boolean
  hasTriggered: boolean
  onRemove: () => void
}) {
  const { data, isLoading } = useQuery<Quote>({
    queryKey: ['quote', item.symbol],
    queryFn: () => getQuote(item.symbol),
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: 2,
  })

  const up = (data?.change_percent ?? 0) >= 0
  const prevPrice = useRef<number | null>(null)
  const [flashClass, setFlashClass] = useState('')

  useEffect(() => {
    if (data?.price == null) return
    if (prevPrice.current !== null && prevPrice.current !== data.price) {
      const cls = data.price > prevPrice.current ? 'flash-up' : 'flash-down'
      setFlashClass('')
      requestAnimationFrame(() => requestAnimationFrame(() => setFlashClass(cls)))
      const t = setTimeout(() => setFlashClass(''), 850)
      prevPrice.current = data.price
      return () => clearTimeout(t)
    }
    prevPrice.current = data.price
  }, [data?.price])

  return (
    <div
      className={clsx(
        'group relative flex flex-col pl-3 pr-2 py-2 rounded-md transition-all cursor-pointer',
        flashClass,
        active
          ? 'bg-gray-100 dark:bg-gray-800/80'
          : hasTriggered
          ? 'bg-orange-100/60 dark:bg-orange-900/25 hover:bg-orange-100/90 dark:hover:bg-orange-900/35'
          : 'hover:bg-gray-100/80 dark:hover:bg-gray-800/40'
      )}
    >
      {active && <span className="absolute left-0 inset-y-1.5 w-0.5 rounded-r-full bg-blue-500/70" />}
      {!active && hasTriggered && <span className="absolute left-0 inset-y-1.5 w-0.5 rounded-r-full bg-orange-500 animate-pulse" />}

      <Link href={`/chart/${encodeURIComponent(item.symbol)}`} className="block min-w-0">
        {/* 行 1：symbol + 涨跌幅/告警铃铛 */}
        <div className="flex items-center justify-between gap-1">
          <span className={clsx(
            'text-xs font-mono font-semibold tracking-wide truncate transition-colors',
            active ? 'text-gray-900 dark:text-gray-100' : 'text-gray-700 dark:text-gray-300 group-hover:text-gray-900 dark:group-hover:text-white'
          )}>
            {item.symbol}
          </span>
          <div className="flex items-center gap-1 shrink-0">
            {hasTriggered && (
              <span className="text-orange-500" title="告警已触发">
                <svg className="w-2.5 h-2.5" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M15 17h5l-1.405-1.405A2.032 2.032 0 0 1 18 14.158V11a6.002 6.002 0 0 0-4-5.659V5a2 2 0 1 0-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 1 1-6 0v-1m6 0H9" />
                </svg>
              </span>
            )}
            {isLoading ? (
              <div className="w-8 h-2.5 bg-gray-200 dark:bg-gray-800 rounded animate-pulse" />
            ) : data ? (
              <span className={`text-[11px] font-mono font-medium ${up ? 'text-green-500' : 'text-red-400'}`}>
                {up ? '+' : ''}{data.change_percent.toFixed(2)}%
              </span>
            ) : null}
          </div>
        </div>
        {/* 行 2：价格 */}
        <div className="mt-0.5">
          {isLoading ? (
            <div className="w-12 h-2.5 bg-gray-200/80 dark:bg-gray-800/80 rounded animate-pulse" />
          ) : data ? (
            <span className="text-[11px] font-mono text-gray-500 dark:text-gray-500">
              {fmtSidebarPrice(data.price)}
            </span>
          ) : (
            <span className="text-[10px] text-gray-400 dark:text-gray-700">—</span>
          )}
        </div>
      </Link>

      {/* 删除按钮（hover 出现，覆盖右侧） */}
      <button
        onClick={e => { e.preventDefault(); e.stopPropagation(); onRemove() }}
        className="absolute top-1.5 right-1 opacity-0 group-hover:opacity-100 w-5 h-5 flex items-center justify-center rounded text-gray-400 dark:text-gray-600 hover:text-red-400 hover:bg-red-400/10 transition-all"
        title="移除自选"
      >
        <svg className="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 px-2 mb-2">
      <span className="text-xs font-semibold text-gray-400 dark:text-gray-700 uppercase tracking-widest">{children}</span>
    </div>
  )
}

export function Sidebar() {
  const pathname = usePathname()
  const queryClient = useQueryClient()

  const { data } = useQuery<{ items: WatchlistItem[] }>({
    queryKey: ['watchlist'],
    queryFn: () => getWatchlist(),
    staleTime: 60_000,
  })

  const { data: alertsData } = useQuery<{ items: Alert[] }>({
    queryKey: ['alerts'],
    queryFn: () => getAlerts(),
    refetchInterval: 60_000,
  })

  const removeMutation = useMutation({
    mutationFn: (id: number) => deleteWatchlist(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
  })

  const watchlist: WatchlistItem[] = data?.items ?? []
  const triggeredSymbols = new Set(
    (alertsData?.items ?? [])
      .filter(a => a.is_triggered && a.is_active)
      .map(a => a.symbol.toUpperCase())
  )

  return (
    <aside className="w-48 bg-white/80 dark:bg-gray-950/60 border-r border-gray-200 dark:border-gray-800/80 flex flex-col shrink-0 overflow-hidden">
      {/* 导航菜单 */}
      <nav className="p-2 pt-3 space-y-0.5">
        <SectionLabel>导航</SectionLabel>
        {NAV_ITEMS.map((item) => {
          const active = item.href === '/' ? pathname === '/' : pathname.startsWith(item.href)
          return (
            <Link
              key={item.href}
              href={item.href}
              className={clsx(
                'relative flex items-center gap-2.5 pl-3 pr-2.5 py-2 rounded-md text-xs transition-all',
                active
                  ? 'bg-gray-100 dark:bg-gray-800/80 text-gray-900 dark:text-gray-100'
                  : 'text-gray-500 dark:text-gray-500 hover:bg-gray-100/80 dark:hover:bg-gray-800/40 hover:text-gray-700 dark:hover:text-gray-300'
              )}
            >
              {active && (
                <span className="absolute left-0 inset-y-1.5 w-0.5 rounded-r-full bg-blue-500" />
              )}
              <span className={active ? 'text-blue-500 dark:text-blue-400' : 'text-gray-400 dark:text-gray-600'}>
                {item.icon}
              </span>
              <span className="font-medium">{item.label}</span>
            </Link>
          )
        })}
      </nav>

      {/* 分割线 */}
      <div className="border-t border-gray-200 dark:border-gray-800/60 mx-3 mt-1" />

      {/* 自选列表 */}
      <div className="flex-1 overflow-y-auto p-2 pt-3">
        <SectionLabel>自选列表</SectionLabel>

        {watchlist.length === 0 ? (
          <div className="mx-1 mt-1.5 rounded-lg border border-dashed border-gray-200 dark:border-gray-800 px-3 py-5 text-center">
            <div className="w-6 h-6 rounded-full bg-gray-100 dark:bg-gray-800 flex items-center justify-center mx-auto mb-2">
              <svg className="w-3 h-3 text-gray-400 dark:text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 4v16m8-8H4" />
              </svg>
            </div>
            <div className="text-xs text-gray-400 dark:text-gray-600">暂无自选</div>
            <div className="text-xs text-gray-300 dark:text-gray-700 mt-0.5">图表页点击「+ 自选」</div>
          </div>
        ) : (
          <div className="space-y-0.5">
            {watchlist.map((item) => {
              const active = pathname === `/chart/${encodeURIComponent(item.symbol)}`
              return (
                <SidebarQuoteRow
                  key={item.id}
                  item={item}
                  active={active}
                  hasTriggered={triggeredSymbols.has(item.symbol.toUpperCase())}
                  onRemove={() => removeMutation.mutate(item.id)}
                />
              )
            })}
          </div>
        )}
      </div>
    </aside>
  )
}
