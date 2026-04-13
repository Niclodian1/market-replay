'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { clsx } from 'clsx'
import { SymbolSearch } from '@/components/SymbolSearch'
import { useAppStore } from '@/store/useAppStore'

const NAV_LINKS = [
  { href: '/notes', label: '复盘笔记' },
]

export function Navbar() {
  const pathname = usePathname()
  const currentSymbol = useAppStore((s) => s.currentSymbol)

  return (
    <header className="h-12 bg-gray-950/90 backdrop-blur-md border-b border-gray-800/80 flex items-center px-4 gap-4 shrink-0 z-10">
      {/* Logo */}
      <Link
        href="/"
        className="flex items-center gap-2 shrink-0 group"
      >
        <span className="flex items-center justify-center w-6 h-6 rounded bg-blue-600/20 border border-blue-500/30 group-hover:bg-blue-600/30 group-hover:border-blue-500/50 transition-all">
          <svg className="w-3 h-3 text-blue-400" fill="currentColor" viewBox="0 0 24 24">
            <path d="M8 5v14l11-7z"/>
          </svg>
        </span>
        <span className="text-sm font-semibold text-gray-200 group-hover:text-gray-100 transition-colors whitespace-nowrap">
          复盘助手
        </span>
      </Link>

      <div className="w-px h-5 bg-gray-800 shrink-0" />

      {/* 搜索框 */}
      <SymbolSearch />

      <div className="flex-1" />

      {/* 当前标的快捷跳转 */}
      {currentSymbol && (
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-xs text-gray-600 mr-0.5 hidden sm:block font-mono">{currentSymbol}</span>
          <Link
            href={`/chart/${encodeURIComponent(currentSymbol)}`}
            className={clsx(
              'relative text-xs px-2.5 py-1 rounded transition-all',
              pathname.startsWith('/chart')
                ? 'text-gray-100 bg-gray-800 border border-gray-700'
                : 'text-gray-500 hover:text-gray-200 hover:bg-gray-800/60 border border-transparent'
            )}
          >
            图表
            {pathname.startsWith('/chart') && (
              <span className="absolute bottom-0 left-1/2 -translate-x-1/2 w-3 h-px bg-gray-400 rounded-full" />
            )}
          </Link>
          <Link
            href={`/replay/${encodeURIComponent(currentSymbol)}`}
            className={clsx(
              'relative text-xs px-2.5 py-1 rounded transition-all',
              pathname.startsWith('/replay')
                ? 'bg-blue-600/90 text-white border border-blue-500/50'
                : 'text-blue-400 hover:text-blue-300 border border-blue-800/50 hover:border-blue-600/50 hover:bg-blue-900/20'
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
      <nav className="flex items-center shrink-0">
        {NAV_LINKS.map(link => (
          <Link
            key={link.href}
            href={link.href}
            className={clsx(
              'text-xs px-3 py-1.5 rounded transition-all border',
              pathname === link.href
                ? 'text-gray-200 bg-gray-800/80 border-gray-700'
                : 'text-gray-500 hover:text-gray-200 hover:bg-gray-800/50 border-transparent'
            )}
          >
            {link.label}
          </Link>
        ))}
      </nav>
    </header>
  )
}
