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
    <header className="h-12 bg-gray-900/95 backdrop-blur border-b border-gray-800 flex items-center px-4 gap-4 shrink-0 z-10">
      {/* Logo */}
      <Link
        href="/"
        className="flex items-center gap-2 text-gray-100 font-bold text-sm whitespace-nowrap shrink-0 hover:text-blue-400 transition-colors"
      >
        <span className="text-blue-400 text-base leading-none">▶</span>
        <span>复盘助手</span>
      </Link>

      <div className="w-px h-5 bg-gray-800 shrink-0" />

      {/* 搜索框 */}
      <SymbolSearch />

      <div className="flex-1" />

      {/* 当前标的快捷跳转 */}
      {currentSymbol && (
        <div className="flex items-center gap-1 shrink-0">
          <span className="text-xs text-gray-600 mr-1 hidden sm:block">{currentSymbol}</span>
          <Link
            href={`/chart/${encodeURIComponent(currentSymbol)}`}
            className={clsx(
              'text-xs px-2.5 py-1 rounded transition-colors',
              pathname.startsWith('/chart')
                ? 'bg-gray-700 text-gray-100'
                : 'text-gray-400 hover:text-gray-100 bg-gray-800 hover:bg-gray-700'
            )}
          >
            图表
          </Link>
          <Link
            href={`/replay/${encodeURIComponent(currentSymbol)}`}
            className={clsx(
              'text-xs px-2.5 py-1 rounded transition-colors',
              pathname.startsWith('/replay')
                ? 'bg-blue-600 text-white'
                : 'text-blue-400 hover:text-blue-300 bg-blue-900/30 hover:bg-blue-900/50'
            )}
          >
            复盘
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
              'text-sm px-3 py-1.5 rounded transition-colors',
              pathname === link.href
                ? 'text-gray-100 bg-gray-800'
                : 'text-gray-400 hover:text-gray-100 hover:bg-gray-800'
            )}
          >
            {link.label}
          </Link>
        ))}
      </nav>
    </header>
  )
}
