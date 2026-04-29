'use client'

import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getHealth } from '@/lib/api'

export function Statusbar() {
  const { data, isError } = useQuery({
    queryKey: ['health'],
    queryFn: () => getHealth(),
    refetchInterval: 30_000,
    retry: 1,
  })

  const [today, setToday] = useState('')

  useEffect(() => {
    const fmt = () => new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
    setToday(fmt())
    // 每分钟更新一次，跨零点时日期自动刷新
    const timer = setInterval(() => setToday(fmt()), 60_000)
    return () => clearInterval(timer)
  }, [])

  const ok = !isError && data?.status === 'ok'

  return (
    <div className="mr-status">
      <span className={`dot${ok ? '' : ' offline'}`} />
      <span>{ok ? '服务正常' : '后端离线'}</span>
      <span className="mr-status-sep">·</span>
      <span>Market Replay</span>
      <span className="mr-status-sep">·</span>
      <span>数据来源 Yahoo Finance</span>
      <div style={{ flex: 1 }} />
      <span suppressHydrationWarning>{today}</span>
    </div>
  )
}
