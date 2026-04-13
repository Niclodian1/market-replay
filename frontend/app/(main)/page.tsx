'use client'

import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/lib/api'

function HealthBadge() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['health'],
    queryFn: () => apiClient.get('/api/health').then((r) => r.data),
    refetchInterval: 30_000,
  })

  if (isLoading) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-gray-600">
        <span className="w-1.5 h-1.5 rounded-full bg-gray-700 animate-pulse" />
        检查中
      </span>
    )
  }

  if (isError || data?.status !== 'ok') {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-red-400">
        <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
        后端离线
      </span>
    )
  }

  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-green-400">
      <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
      服务正常
    </span>
  )
}

const QUICK_SYMBOLS = [
  { symbol: 'AAPL',    name: 'Apple Inc.',     type: '股票',   color: 'border-blue-800/50 hover:border-blue-600' },
  { symbol: 'SPY',     name: 'S&P 500 ETF',    type: 'ETF',    color: 'border-purple-800/50 hover:border-purple-600' },
  { symbol: 'BTC-USD', name: 'Bitcoin',         type: '加密货币', color: 'border-orange-800/50 hover:border-orange-600' },
  { symbol: 'QQQ',     name: 'Nasdaq 100 ETF', type: 'ETF',    color: 'border-purple-800/50 hover:border-purple-600' },
  { symbol: 'TSLA',    name: 'Tesla Inc.',      type: '股票',   color: 'border-blue-800/50 hover:border-blue-600' },
  { symbol: 'ETH-USD', name: 'Ethereum',        type: '加密货币', color: 'border-orange-800/50 hover:border-orange-600' },
]

const FEATURES = [
  {
    title: '图表分析',
    desc: '蜡烛图、MA20/60、RSI(14)、MACD，支持对数坐标和画线工具',
    href: '/chart/AAPL',
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
          d="M7 20V8m4 12V4m4 16v-6m4 6v-10" />
      </svg>
    ),
    accent: 'from-blue-500/10 to-blue-600/5 border-blue-800/50',
    iconColor: 'text-blue-400',
  },
  {
    title: '复盘模式',
    desc: '从历史某天开始，逐根 K 线推进，锻炼预判能力',
    href: '/replay/AAPL',
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
          d="M12 6v6l4 2m6-2a10 10 0 1 1-20 0 10 10 0 0 1 20 0z" />
      </svg>
    ),
    accent: 'from-green-500/10 to-green-600/5 border-green-800/50',
    iconColor: 'text-green-400',
  },
  {
    title: '复盘笔记',
    desc: '记录趋势判断、支撑阻力、入场逻辑，随时回顾',
    href: '/notes',
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
          d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2" />
      </svg>
    ),
    accent: 'from-amber-500/10 to-amber-600/5 border-amber-800/50',
    iconColor: 'text-amber-400',
  },
]

export default function HomePage() {
  return (
    <div className="max-w-3xl mx-auto py-2">
      {/* 标题区 */}
      <div className="mb-8">
        <div className="flex items-center gap-3 mb-2">
          <h1 className="text-2xl font-bold text-gray-100">行情复盘助手</h1>
          <HealthBadge />
        </div>
        <p className="text-sm text-gray-500">
          搜索股票、ETF、加密货币，进入图表分析或复盘模式
        </p>
      </div>

      {/* 快速入口 */}
      <div className="mb-8">
        <h2 className="text-xs font-semibold text-gray-600 uppercase tracking-wider mb-3">
          快速入口
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
          {QUICK_SYMBOLS.map((item) => (
            <Link
              key={item.symbol}
              href={`/chart/${encodeURIComponent(item.symbol)}`}
              className={`group bg-gray-900 border rounded-lg p-3.5 hover:bg-gray-800/80 transition-all ${item.color}`}
            >
              <div className="font-mono font-bold text-gray-100 text-sm group-hover:text-white transition-colors">
                {item.symbol}
              </div>
              <div className="text-xs text-gray-500 mt-0.5 truncate">{item.name}</div>
              <div className="text-xs text-gray-700 mt-1">{item.type}</div>
            </Link>
          ))}
        </div>
      </div>

      {/* 功能说明 */}
      <div>
        <h2 className="text-xs font-semibold text-gray-600 uppercase tracking-wider mb-3">
          功能模块
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {FEATURES.map((f) => (
            <Link
              key={f.title}
              href={f.href}
              className={`group bg-gradient-to-br ${f.accent} border rounded-lg p-4 hover:brightness-110 transition-all`}
            >
              <div className={`${f.iconColor} mb-3`}>{f.icon}</div>
              <div className="font-semibold text-gray-200 text-sm mb-1.5">{f.title}</div>
              <div className="text-xs text-gray-500 leading-relaxed">{f.desc}</div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}
