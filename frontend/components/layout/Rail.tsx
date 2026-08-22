'use client'

import Link from 'next/link'
import { usePathname, useParams } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'
import { useAppStore } from '@/store/useAppStore'
import { getOpportunityNotifications } from '@/lib/api'
import type { OpportunityNotificationsResponse } from '@/types'

const NAV_ITEMS = [
  {
    key: 'dashboard', href: '/', label: '总览',
    icon: (
      <svg className="ico" viewBox="0 0 24 24">
        <rect x="3" y="3" width="7" height="9" rx="1" />
        <rect x="14" y="3" width="7" height="5" rx="1" />
        <rect x="14" y="12" width="7" height="9" rx="1" />
        <rect x="3" y="16" width="7" height="5" rx="1" />
      </svg>
    ),
  },
  {
    key: 'chart', href: null, label: '行情',
    icon: (
      <svg className="ico" viewBox="0 0 24 24">
        <path d="M3 3v18h18" />
        <path d="M7 15l3-4 3 2 5-7" />
      </svg>
    ),
  },
  {
    key: 'replay', href: null, label: '复盘',
    icon: (
      <svg className="ico" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="9" />
        <path d="M10 8l6 4-6 4V8z" fill="currentColor" stroke="none" />
      </svg>
    ),
  },
  {
    key: 'watchlist', href: '/watchlist', label: '自选',
    icon: (
      <svg className="ico" viewBox="0 0 24 24">
        <path d="M12 3l2.6 5.6 6 .8-4.4 4.3 1 6L12 16.9 6.8 19.7l1-6L3.4 9.4l6-.8L12 3z" />
      </svg>
    ),
  },
  {
    key: 'alerts', href: '/alerts', label: '告警',
    icon: (
      <svg className="ico" viewBox="0 0 24 24">
        <path d="M6 8a6 6 0 1112 0v5l2 3H4l2-3V8z" />
        <path d="M10 19a2 2 0 004 0" />
      </svg>
    ),
  },
  {
    key: 'backtest', href: '/backtest', label: '回测',
    icon: (
      <svg className="ico" viewBox="0 0 24 24">
        <path d="M9 3h6" />
        <path d="M10 3v6l-5 9a2 2 0 002 3h10a2 2 0 002-3l-5-9V3" />
        <path d="M8 15h8" />
      </svg>
    ),
  },
  {
    key: 'strategy-overview', href: '/strategy-overview', label: '策略总览',
    icon: (
      <svg className="ico" viewBox="0 0 24 24">
        <path d="M4 5h16M4 12h16M4 19h16" />
        <path d="M8 3v4M14 10v4M18 17v4" />
      </svg>
    ),
  },
  {
    key: 'strategy-lab', href: '/strategy-lab', label: '策略台',
    icon: (
      <svg className="ico" viewBox="0 0 24 24">
        <path d="M9 3h6" />
        <path d="M10 3v6l-5 8a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-8V3" />
        <path d="M7 15h10" />
      </svg>
    ),
  },
  {
    key: 'ma-strategies', href: '/ma-strategies', label: '均线策略',
    icon: (
      <svg className="ico" viewBox="0 0 24 24">
        <path d="M3 17l4-4 4 3 6-7 4 3" />
        <path d="M3 21h18" />
      </svg>
    ),
  },
  {
    key: 'macro', href: '/macro', label: '宏观',
    icon: (
      <svg className="ico" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 3a9 9 0 000 18" fill="none" />
        <path d="M3 12h18M12 3c2.5 2.5 4 5.5 4 9s-1.5 6.5-4 9c-2.5-2.5-4-5.5-4-9s1.5-6.5 4-9z" />
      </svg>
    ),
  },
  {
    key: 'notes', href: '/notes', label: '笔记',
    icon: (
      <svg className="ico" viewBox="0 0 24 24">
        <path d="M4 4h12l4 4v12H4z" />
        <path d="M8 10h8M8 14h8M8 18h5" />
      </svg>
    ),
  },
]

export function Rail() {
  const pathname = usePathname()
  const params = useParams()
  const currentSymbol = useAppStore(s => s.currentSymbol)
  const { data: notificationData } = useQuery<OpportunityNotificationsResponse>({
    queryKey: ['opportunity-notifications'],
    queryFn: () => getOpportunityNotifications(20),
    refetchInterval: 60_000,
  })

  const getHref = (item: typeof NAV_ITEMS[0]) => {
    if (item.href) return item.href
    const sym = (params?.symbol as string) || currentSymbol || 'AAPL'
    if (item.key === 'chart') return `/chart/${encodeURIComponent(sym)}`
    if (item.key === 'replay') return `/replay/${encodeURIComponent(sym)}`
    return '/'
  }

  const isActive = (item: typeof NAV_ITEMS[0]) => {
    if (item.key === 'dashboard') return pathname === '/'
    if (item.key === 'chart') return pathname.startsWith('/chart')
    if (item.key === 'replay') return pathname.startsWith('/replay')
    if (item.href) return pathname.startsWith(item.href)
    return false
  }

  return (
    <nav className="mr-rail">
      <div className="mr-rail-logo">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 17l5-6 4 3 5-8 4 5" />
          <path d="M3 21h18" />
        </svg>
      </div>

      {NAV_ITEMS.map((item) => (
        <Link
          key={item.key}
          href={getHref(item)}
          className={`mr-rail-btn${isActive(item) ? ' active' : ''}`}
          title={item.label}
        >
          {item.icon}
          {item.key === 'alerts' && (notificationData?.unread ?? 0) > 0 && (
            <span style={{
              position: 'absolute', top: 5, right: 4, minWidth: 14, height: 14, padding: '0 3px',
              display: 'grid', placeItems: 'center', borderRadius: 7, background: 'var(--down)', color: '#fff',
              fontSize: 8, lineHeight: 1, fontFamily: 'var(--font-mono)', border: '1px solid var(--bg-1)',
            }}>{Math.min(notificationData?.unread ?? 0, 99)}</span>
          )}
        </Link>
      ))}

      <div className="mr-rail-spacer" />

      <ThemeToggle />
    </nav>
  )
}

function ThemeToggle() {
  const theme = useAppStore(s => s.theme)
  const setTheme = useAppStore(s => s.setTheme)

  const toggle = () => {
    const next = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    document.documentElement.setAttribute('data-theme', next)
    if (next === 'dark') {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
  }

  return (
    <button className="mr-rail-btn" onClick={toggle} title={theme === 'dark' ? '切换浅色' : '切换深色'}>
      {theme === 'dark' ? (
        <svg className="ico" viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      ) : (
        <svg className="ico" viewBox="0 0 24 24">
          <path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z" />
        </svg>
      )}
    </button>
  )
}
