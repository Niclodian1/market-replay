'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import { apiClient, getQuote } from '@/lib/api'
import { useAppStore } from '@/store/useAppStore'
import type { Quote } from '@/types'

// ── HealthBadge ───────────────────────────────────────────────────────────────

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

// ── 价格格式化 ────────────────────────────────────────────────────────────────

function fmtPrice(v: number): string {
  if (v >= 10000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 })
  if (v >= 100)   return v.toFixed(2)
  if (v >= 1)     return v.toFixed(4)
  return v.toFixed(6)
}

// ── 单个报价卡片 ──────────────────────────────────────────────────────────────

function QuoteCard({
  symbol,
  onRemove,
}: {
  symbol: string
  onRemove?: () => void
}) {
  const { data, isLoading, isError } = useQuery<Quote>({
    queryKey: ['quote', symbol],
    queryFn: () => getQuote(symbol),
    refetchInterval: 30_000,
    retry: 1,
  })

  const up = (data?.change_percent ?? 0) >= 0

  return (
    <Link
      href={`/chart/${encodeURIComponent(symbol)}`}
      className="group relative bg-gray-900/60 border border-gray-800/60 rounded-lg p-3 hover:bg-gray-800/60 hover:border-gray-700/80 transition-all duration-200"
    >
      {/* 删除按钮（仅有 onRemove 时显示） */}
      {onRemove && (
        <button
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); onRemove() }}
          className="absolute top-1.5 right-1.5 w-4 h-4 rounded flex items-center justify-center text-gray-700 hover:text-red-400 hover:bg-red-900/30 transition-colors opacity-0 group-hover:opacity-100"
        >
          ✕
        </button>
      )}

      {/* 加载态 */}
      {isLoading && (
        <div className="space-y-2">
          <div className="h-3 w-16 bg-gray-800 rounded animate-pulse" />
          <div className="h-4 w-20 bg-gray-800/80 rounded animate-pulse" />
          <div className="h-3 w-12 bg-gray-800/60 rounded animate-pulse" />
        </div>
      )}

      {/* 错误态 */}
      {isError && !isLoading && (
        <div>
          <div className="font-mono font-bold text-gray-400 text-sm">{symbol}</div>
          <div className="text-xs text-gray-600 mt-1">数据不可用</div>
        </div>
      )}

      {/* 正常数据 */}
      {data && !isLoading && (
        <>
          <div className="font-mono font-bold text-gray-200 text-sm tracking-wide group-hover:text-white transition-colors">
            {symbol}
          </div>
          {data.name && (
            <div className="text-[11px] text-gray-500 mt-0.5 truncate max-w-[130px]" title={data.name}>
              {data.name}
            </div>
          )}
          <div className="mt-2 flex items-end justify-between gap-2">
            <span className="text-gray-100 font-mono text-sm font-semibold">
              {fmtPrice(data.price)}
            </span>
            <span className={`text-xs font-mono font-medium ${up ? 'text-green-400' : 'text-red-400'}`}>
              {up ? '+' : ''}{data.change_percent.toFixed(2)}%
            </span>
          </div>
        </>
      )}
    </Link>
  )
}

// ── 功能模块 ──────────────────────────────────────────────────────────────────

const FEATURES = [
  {
    title: '图表分析',
    desc: '蜡烛图、自定义MA、RSI(14)、MACD，支持对数坐标',
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

// ── 固定指数 & 加密 ───────────────────────────────────────────────────────────

const INDEX_SYMBOLS  = ['^NDX', '^GSPC', '^VIX']
const CRYPTO_SYMBOLS = ['BTC-USD', 'ETH-USD']

// ── 分区标题 ──────────────────────────────────────────────────────────────────

function SectionHeader({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <div className="w-4 h-px bg-gray-700" />
      <h2 className="text-xs font-semibold text-gray-600 uppercase tracking-widest">{label}</h2>
      <div className="flex-1 h-px bg-gray-800" />
    </div>
  )
}

// ── 主页 ──────────────────────────────────────────────────────────────────────

export default function HomePage() {
  const homepageStocks    = useAppStore(s => s.homepageStocks)
  const addHomepageStock  = useAppStore(s => s.addHomepageStock)
  const removeHomepageStock = useAppStore(s => s.removeHomepageStock)

  const [addInput, setAddInput] = useState('')
  const [addErr,   setAddErr]   = useState('')

  function handleAddStock() {
    const sym = addInput.trim().toUpperCase()
    if (!sym) return
    if (homepageStocks.includes(sym)) {
      setAddErr('已存在')
      setTimeout(() => setAddErr(''), 2000)
      return
    }
    addHomepageStock(sym)
    setAddInput('')
    setAddErr('')
  }

  return (
    <div className="max-w-3xl mx-auto py-4 animate-fade-up">
      {/* ── 标题区 ───────────────────────────────────────────────────────── */}
      <div className="mb-8">
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

      {/* ── 股指 + 加密货币 并排 ─────────────────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
        {/* 股指 */}
        <div>
          <SectionHeader label="指数行情" />
          <div className="grid grid-cols-1 gap-2">
            {INDEX_SYMBOLS.map(sym => (
              <QuoteCard key={sym} symbol={sym} />
            ))}
          </div>
        </div>

        {/* 加密货币 */}
        <div>
          <SectionHeader label="加密货币" />
          <div className="grid grid-cols-1 gap-2">
            {CRYPTO_SYMBOLS.map(sym => (
              <QuoteCard key={sym} symbol={sym} />
            ))}
          </div>
        </div>
      </div>

      {/* ── 自选个股 ─────────────────────────────────────────────────────── */}
      <div className="mb-8">
        <SectionHeader label="自选个股" />
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
          {homepageStocks.map(sym => (
            <QuoteCard
              key={sym}
              symbol={sym}
              onRemove={() => removeHomepageStock(sym)}
            />
          ))}
        </div>

        {/* 添加行 */}
        <div className="mt-3 flex items-center gap-2">
          <input
            type="text"
            value={addInput}
            onChange={e => { setAddInput(e.target.value.toUpperCase()); setAddErr('') }}
            onKeyDown={e => e.key === 'Enter' && handleAddStock()}
            placeholder="输入股票代码"
            className="flex-1 max-w-[160px] bg-gray-900/60 border border-gray-800/60 rounded-md px-2.5 py-1.5 text-xs text-gray-300 placeholder-gray-700 outline-none focus:border-gray-600 transition-colors font-mono"
          />
          <button
            onClick={handleAddStock}
            className="px-3 py-1.5 text-xs rounded-md bg-gray-800/80 border border-gray-700/60 text-gray-400 hover:text-gray-200 hover:bg-gray-700/60 hover:border-gray-600 transition-all"
          >
            + 添加
          </button>
          {addErr && <span className="text-xs text-red-400">{addErr}</span>}
        </div>
      </div>

      {/* ── 功能模块 ─────────────────────────────────────────────────────── */}
      <div>
        <SectionHeader label="功能模块" />
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
