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
      <span className="inline-flex items-center gap-1.5 text-xs text-red-500/80 bg-red-500/8 border border-red-900/40 px-2 py-0.5 rounded-full">
        <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
        后端离线
      </span>
    )
  }

  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-green-500/80 bg-green-500/8 border border-green-900/40 px-2 py-0.5 rounded-full">
      <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
      服务正常
    </span>
  )
}

const QUICK_SYMBOLS = [
  { symbol: 'AAPL',    name: 'Apple Inc.',     type: '股票',     accent: 'border-l-blue-500/60',  bg: 'hover:bg-blue-900/10'   },
  { symbol: 'SPY',     name: 'S&P 500 ETF',    type: 'ETF',      accent: 'border-l-purple-500/60', bg: 'hover:bg-purple-900/10' },
  { symbol: 'BTC-USD', name: 'Bitcoin',         type: '加密货币',  accent: 'border-l-orange-500/60', bg: 'hover:bg-orange-900/10' },
  { symbol: 'QQQ',     name: 'Nasdaq 100 ETF', type: 'ETF',      accent: 'border-l-purple-500/60', bg: 'hover:bg-purple-900/10' },
  { symbol: 'TSLA',    name: 'Tesla Inc.',      type: '股票',     accent: 'border-l-blue-500/60',  bg: 'hover:bg-blue-900/10'   },
  { symbol: 'ETH-USD', name: 'Ethereum',        type: '加密货币',  accent: 'border-l-orange-500/60', bg: 'hover:bg-orange-900/10' },
]

const FEATURES = [
  {
    title: '图表分析',
    desc: '蜡烛图、MA20/60、RSI(14)、MACD，支持对数坐标',
    href: '/chart/AAPL',
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
          d="M7 20V8m4 12V4m4 16v-6m4 6v-10" />
      </svg>
    ),
    gradient: 'from-blue-500/8 via-blue-600/4 to-transparent',
    border: 'border-blue-800/40 hover:border-blue-700/60',
    iconBg: 'bg-blue-500/10 text-blue-400',
    accent: 'text-blue-400',
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
    gradient: 'from-green-500/8 via-green-600/4 to-transparent',
    border: 'border-green-800/40 hover:border-green-700/60',
    iconBg: 'bg-green-500/10 text-green-400',
    accent: 'text-green-400',
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
    gradient: 'from-amber-500/8 via-amber-600/4 to-transparent',
    border: 'border-amber-800/40 hover:border-amber-700/60',
    iconBg: 'bg-amber-500/10 text-amber-400',
    accent: 'text-amber-400',
  },
]

export default function HomePage() {
  return (
    <div className="max-w-2xl mx-auto py-4 animate-fade-up">
      {/* ── 标题区 ──────────────────────────────────────────────────────────── */}
      <div className="mb-10">
        <div className="flex items-center gap-3 mb-3">
          <h1 className="text-2xl font-bold tracking-tight text-gray-100">
            行情复盘助手
          </h1>
          <HealthBadge />
        </div>
        <p className="text-sm text-gray-500 leading-relaxed">
          搜索股票、ETF、加密货币，进入图表分析或复盘模式
        </p>
      </div>

      {/* ── 快速入口 ────────────────────────────────────────────────────────── */}
      <div className="mb-10">
        <div className="flex items-center gap-2 mb-4">
          <div className="w-4 h-px bg-gray-700" />
          <h2 className="text-xs font-semibold text-gray-600 uppercase tracking-widest">快速入口</h2>
          <div className="flex-1 h-px bg-gray-800" />
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
          {QUICK_SYMBOLS.map((item) => (
            <Link
              key={item.symbol}
              href={`/chart/${encodeURIComponent(item.symbol)}`}
              className={`group relative bg-gray-900/60 border-l-2 border border-gray-800/60 rounded-lg p-3.5 transition-all duration-200 ${item.accent} ${item.bg} hover:border-gray-700/80`}
            >
              <div className="font-mono font-bold text-gray-200 text-sm tracking-wide group-hover:text-white transition-colors">
                {item.symbol}
              </div>
              <div className="text-xs text-gray-500 mt-0.5 truncate">{item.name}</div>
              <div className="flex items-center justify-between mt-2">
                <span className="text-xs text-gray-700">{item.type}</span>
                <svg className="w-3 h-3 text-gray-700 group-hover:text-gray-500 group-hover:translate-x-0.5 transition-all" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              </div>
            </Link>
          ))}
        </div>
      </div>

      {/* ── 功能模块 ────────────────────────────────────────────────────────── */}
      <div>
        <div className="flex items-center gap-2 mb-4">
          <div className="w-4 h-px bg-gray-700" />
          <h2 className="text-xs font-semibold text-gray-600 uppercase tracking-widest">功能模块</h2>
          <div className="flex-1 h-px bg-gray-800" />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {FEATURES.map((f) => (
            <Link
              key={f.title}
              href={f.href}
              className={`group bg-gradient-to-br ${f.gradient} border ${f.border} rounded-lg p-4 transition-all duration-200 hover:-translate-y-px`}
            >
              <div className={`w-8 h-8 rounded-lg flex items-center justify-center mb-3 transition-transform group-hover:scale-110 ${f.iconBg}`}>
                {f.icon}
              </div>
              <div className="font-semibold text-gray-200 text-sm mb-1.5 group-hover:text-white transition-colors">
                {f.title}
              </div>
              <div className="text-xs text-gray-600 leading-relaxed">{f.desc}</div>
              <div className={`flex items-center gap-1 mt-3 text-xs ${f.accent} opacity-0 group-hover:opacity-100 transition-opacity`}>
                <span>进入</span>
                <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}
