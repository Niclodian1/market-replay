'use client'

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getOpportunityNotifications,
  getOpportunityNotificationSummary,
  markOpportunityNotificationRead,
  retryOpportunityNotification,
  scanOpportunitiesNow,
} from '@/lib/api'
import type {
  OpportunityNotification,
  OpportunityNotificationsResponse,
  OpportunityNotificationSummary,
} from '@/types'

const STATUS_META = {
  sent: { label: 'Discord 已送达', color: 'var(--up)' },
  failed: { label: '投递失败·等待重试', color: 'var(--down)' },
  in_app_only: { label: '仅站内', color: '#f59e0b' },
  pending: { label: '等待投递', color: 'var(--accent)' },
} as const

function parseParameters(value: string): string[] {
  try { return JSON.parse(value) as string[] } catch { return [] }
}

function NotificationCard({ item }: { item: OpportunityNotification }) {
  const queryClient = useQueryClient()
  const status = STATUS_META[item.status] ?? STATUS_META.pending
  const parameters = parseParameters(item.parameters)

  const readMutation = useMutation({
    mutationFn: () => markOpportunityNotificationRead(item.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['opportunity-notifications'] }),
  })
  const retryMutation = useMutation({
    mutationFn: () => retryOpportunityNotification(item.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['opportunity-notifications'] }),
  })

  return (
    <article style={{
      border: `1px solid ${item.is_read ? 'var(--line-1)' : 'var(--accent)'}`,
      borderLeft: `3px solid ${status.color}`,
      borderRadius: 'var(--radius-lg)', background: 'var(--bg-1)', padding: '14px 16px', marginBottom: 9,
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
            <span className="mono" style={{ fontSize: 13, fontWeight: 700, color: 'var(--fg-0)' }}>{item.symbol}</span>
            <span className="chip" style={{ fontSize: 9, color: 'var(--up)', borderColor: 'var(--up)' }}>策略 A</span>
            {parameters.map(period => <span key={period} className="chip" style={{ fontSize: 9 }}>MA{period}</span>)}
            {item.simulated && <span className="chip" style={{ fontSize: 9, color: '#a78bfa', borderColor: '#a78bfa' }}>模拟</span>}
            {!item.is_read && <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--accent)' }} />}
          </div>
          <div style={{ marginTop: 5, fontSize: 12.5, fontWeight: 650, color: 'var(--fg-0)' }}>{item.title}</div>
          <div style={{ marginTop: 4, fontSize: 10.5, color: 'var(--fg-3)' }}>
            {new Date(item.created_at).toLocaleString()} · {item.data_source || '实时行情源'}
          </div>
        </div>
        <span style={{ fontSize: 10, color: status.color, whiteSpace: 'nowrap' }}>{status.label}</span>
      </div>

      <div style={{
        whiteSpace: 'pre-wrap', fontSize: 11.5, lineHeight: 1.75, color: 'var(--fg-1)', marginTop: 12,
        padding: '11px 12px', background: 'var(--bg-0)', borderRadius: 'var(--radius)', border: '1px solid var(--line-0)',
      }}>{item.description.replaceAll('**', '')}</div>

      {(item.last_error || item.next_retry_at) && (
        <div style={{ marginTop: 8, fontSize: 10.5, color: item.status === 'failed' ? 'var(--down)' : 'var(--fg-3)' }}>
          {item.last_error}{item.next_retry_at ? ` · 下次重试 ${new Date(item.next_retry_at).toLocaleString()}` : ''}
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginTop: 10 }}>
        {!item.is_read && (
          <button className="btn small ghost" disabled={readMutation.isPending} onClick={() => readMutation.mutate()}>标为已读</button>
        )}
        {item.status === 'failed' && (
          <button className="btn small ghost" disabled={retryMutation.isPending} onClick={() => retryMutation.mutate()}>
            {retryMutation.isPending ? '重试中…' : '立即重试'}
          </button>
        )}
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 9.5, color: 'var(--fg-3)' }}>投递尝试 {item.attempts} 次</span>
      </div>
    </article>
  )
}

export function OpportunityNotificationFeed() {
  const queryClient = useQueryClient()
  const [scanMessage, setScanMessage] = useState('')
  const notifications = useQuery<OpportunityNotificationsResponse>({
    queryKey: ['opportunity-notifications'],
    queryFn: () => getOpportunityNotifications(50),
    refetchInterval: 60_000,
  })
  const summary = useQuery<OpportunityNotificationSummary>({
    queryKey: ['opportunity-notifications-summary'],
    queryFn: getOpportunityNotificationSummary,
    refetchInterval: 60_000,
  })
  const scanMutation = useMutation({
    mutationFn: () => scanOpportunitiesNow(),
    onSuccess: (data: { results: { created?: boolean; error?: string }[] }) => {
      const created = data.results.filter(result => result.created).length
      const errors = data.results.filter(result => result.error).length
      setScanMessage(errors ? `扫描完成：新增 ${created} 条，${errors} 个数据源异常` : `扫描完成：新增 ${created} 条机会通知`)
      queryClient.invalidateQueries({ queryKey: ['opportunity-notifications'] })
      queryClient.invalidateQueries({ queryKey: ['opportunity-notifications-summary'] })
    },
    onError: () => setScanMessage('扫描失败，请检查后端或数据源状态'),
  })

  const info = summary.data
  const items = notifications.data?.items ?? []

  return (
    <section style={{ marginBottom: 28 }}>
      <div style={{
        border: '1px solid var(--line-1)', borderRadius: 'var(--radius-lg)', background: 'var(--bg-1)',
        padding: '15px 16px', marginBottom: 14,
      }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 260 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: info?.enabled ? 'var(--up)' : 'var(--down)' }} />
              <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--fg-0)' }}>日线机会自动扫描</span>
              <span className="chip" style={{ fontSize: 9 }}>4 品种 × 4 均线</span>
            </div>
            <div style={{ fontSize: 11, lineHeight: 1.7, color: 'var(--fg-2)', marginTop: 7 }}>
              BTC、AAPL、XAUUSD、贵州茅台 · 策略 A · MA30 / 60 / 99 / 120
              <br />同一品种同一天触发多条均线时合并为一条通知；后端重启会自动补扫。
            </div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 10.5, color: info?.discord_configured ? 'var(--up)' : '#f59e0b', marginBottom: 7 }}>
              {info?.discord_configured ? 'Discord 通道已配置' : '未配置 Discord · 当前仅站内通知'}
            </div>
            <button className="btn small" disabled={scanMutation.isPending || info?.enabled === false} onClick={() => scanMutation.mutate()}>
              {scanMutation.isPending ? '正在扫描四个市场…' : '立即补扫'}
            </button>
          </div>
        </div>
        {scanMessage && <div style={{ marginTop: 9, fontSize: 10.5, color: 'var(--fg-2)' }}>{scanMessage}</div>}
        {info?.schedules && (
          <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginTop: 12, paddingTop: 11, borderTop: '1px solid var(--line-0)' }}>
            {info.schedules.map(schedule => (
              <span key={schedule.symbol} className="chip" style={{ fontSize: 9.5 }}>
                {schedule.symbol} · {schedule.days} {schedule.time} {schedule.timezone.split('/').pop()}
              </span>
            ))}
          </div>
        )}
      </div>

      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 10 }}>
        <div className="section-title">机会通知记录</div>
        <span style={{ fontSize: 10.5, color: 'var(--fg-3)' }}>
          {notifications.data ? `${notifications.data.total} 条 · ${notifications.data.unread} 条未读` : '加载中…'}
        </span>
      </div>

      {notifications.isError && <div style={{ color: 'var(--down)', fontSize: 12, padding: '20px 0' }}>机会通知加载失败，请检查后端连接</div>}
      {notifications.isLoading && <div style={{ color: 'var(--fg-3)', fontSize: 12, padding: '20px 0' }}>正在加载机会记录…</div>}
      {!notifications.isLoading && !notifications.isError && items.length === 0 && (
        <div style={{ border: '1px dashed var(--line-1)', borderRadius: 'var(--radius-lg)', padding: '26px 18px', textAlign: 'center' }}>
          <div style={{ fontSize: 12.5, color: 'var(--fg-2)' }}>当前没有已触发的日线机会</div>
          <div style={{ fontSize: 10.5, color: 'var(--fg-3)', marginTop: 5 }}>系统仍在监控；只有完整收盘K线满足策略 A 时才会生成记录。</div>
        </div>
      )}
      {items.map(item => <NotificationCard key={item.id} item={item} />)}
    </section>
  )
}
