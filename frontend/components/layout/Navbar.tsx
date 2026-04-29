'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { clsx } from 'clsx'
import { SymbolSearch } from '@/components/SymbolSearch'
import { useAppStore } from '@/store/useAppStore'

const NAV_LINKS = [
  { href: '/notes', label: '复盘笔记' },
]

function ThemeToggle() {
  const theme = useAppStore(s => s.theme)
  const setTheme = useAppStore(s => s.setTheme)
  const isDark = theme === 'dark'
  return (
    <button
      onClick={() => setTheme(isDark ? 'light' : 'dark')}
      title={isDark ? '切换亮色模式' : '切换暗色模式'}
      className="w-7 h-7 flex items-center justify-center rounded-md border border-gray-300 dark:border-gray-700/60 text-gray-500 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800/50 hover:border-gray-400 dark:hover:border-gray-600 transition-all shrink-0"
    >
      {isDark ? (
        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
            d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364-6.364l-.707.707M6.343 17.657l-.707.707M17.657 17.657l-.707-.707M6.343 6.343l-.707-.707M12 7a5 5 0 1 0 0 10A5 5 0 0 0 12 7z" />
        </svg>
      ) : (
        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
            d="M20.354 15.354A9 9 0 0 1 8.646 3.646 9.003 9.003 0 0 0 12 21a9.003 9.003 0 0 0 8.354-5.646z" />
        </svg>
      )}
    </button>
  )
}

export function Navbar() {
  const pathname = usePathname()
  const currentSymbol = useAppStore((s) => s.currentSymbol)

  return (
    <header className="h-12 bg-white/90 dark:bg-gray-950/90 backdrop-blur-md border-b border-gray-200 dark:border-gray-800/80 flex items-center px-4 gap-4 shrink-0 z-10">
      {/* Logo */}
      <Link href="/" className="flex items-center gap-2 shrink-0 group">
        <span className="flex items-center justify-center w-6 h-6 rounded bg-blue-600/20 border border-blue-500/30 group-hover:bg-blue-600/30 group-hover:border-blue-500/50 transition-all">
          <svg className="w-3 h-3 text-blue-500 dark:text-blue-400" fill="currentColor" viewBox="0 0 24 24">
            <path d="M8 5v14l11-7z"/>
          </svg>
        </span>
        <span className="text-sm font-semibold text-gray-800 dark:text-gray-200 group-hover:text-gray-900 dark:group-hover:text-gray-100 transition-colors whitespace-nowrap">
          复盘助手
        </span>
      </Link>

      <div className="w-px h-5 bg-gray-200 dark:bg-gray-800 shrink-0" />

      {/* 搜索框 */}
      <SymbolSearch />

      <div className="flex-1" />

      {/* 当前标的快捷跳转 */}
      {currentSymbol && (
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-xs text-gray-500 dark:text-gray-600 mr-0.5 hidden sm:block font-mono">{currentSymbol}</span>
          <Link
            href={`/chart/${encodeURIComponent(currentSymbol)}`}
            className={clsx(
              'relative text-xs px-2.5 py-1 rounded transition-all',
              pathname.startsWith('/chart')
                ? 'text-gray-900 dark:text-gray-100 bg-gray-100 dark:bg-gray-800 border border-gray-300 dark:border-gray-700'
                : 'text-gray-500 dark:text-gray-500 hover:text-gray-800 dark:hover:text-gray-200 hover:bg-gray-100/80 dark:hover:bg-gray-800/60 border border-transparent'
            )}
          >
            图表
            {pathname.startsWith('/chart') && (
              <span className="absolute bottom-0 left-1/2 -translate-x-1/2 w-3 h-px bg-gray-500 dark:bg-gray-400 rounded-full" />
            )}
          </Link>
          <Link
            href={`/replay/${encodeURIComponent(currentSymbol)}`}
            className={clsx(
              'relative text-xs px-2.5 py-1 rounded transition-all',
              pathname.startsWith('/replay')
                ? 'bg-blue-600/90 text-white border border-blue-500/50'
                : 'text-blue-500 dark:text-blue-400 hover:text-blue-600 dark:hover:text-blue-300 border border-blue-200 dark:border-blue-800/50 hover:border-blue-400 dark:hover:border-blue-600/50 hover:bg-blue-50 dark:hover:bg-blue-900/20'
            )}
          >
            复盘
            {pathname.startsWith('/replay') && (
              <span className="absolute bottom-0 left-1/2 -translate-x-1/2 w-3 h-px bg-blue-300 rounded-full" />
            )}
          </Link>
        </div>
      )}

      {/* 右侧导航 */}
      <nav className="flex items-center gap-1 shrink-0">
        {NAV_LINKS.map(link => (
          <Link
            key={link.href}
            href={link.href}
            className={clsx(
              'text-xs px-3 py-1.5 rounded transition-all border',
              pathname === link.href
                ? 'text-gray-900 dark:text-gray-200 bg-gray-100 dark:bg-gray-800/80 border-gray-300 dark:border-gray-700'
                : 'text-gray-500 dark:text-gray-500 hover:text-gray-800 dark:hover:text-gray-200 hover:bg-gray-100/80 dark:hover:bg-gray-800/50 border-transparent'
            )}
          >
            {link.label}
          </Link>
        ))}
        <ThemeToggle />
      </nav>
    </header>
  )
}
