'use client'

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createMonitorPlan,
  createNotificationChannel,
  deleteMonitorPlan,
  getMonitorAlertEvents,
  getMonitorPlans,
  getNotificationChannels,
  getWatchlist,
  markMonitorAlertEventRead,
  runMonitoringNow,
  testEnvironmentEmail,
  testNotificationChannel,
  updateMonitorPlan,
  updateNotificationChannel,
} from '@/lib/api'
import type {
  MonitorAlertEvent,
  MonitorPlan,
  MonitorStrategy,
  MonitorTimeframe,
  NotificationChannel,
  WatchlistItem,
} from '@/types'


const TIMEFRAMES = [
  { value: '1h', label: '1H' },
  { value: '4h', label: '4H' },
  { value: '1day', label: '1D' },
] as const

const STRATEGIES: Array<{ id: MonitorStrategy; label: string; description: string }> = [
  { id: 'ma', label: 'MA突破与回踩', description: 'ATR自适应强度、5/10根成交量证据' },
  { id: 'macd', label: 'MACD动量', description: '首次衰减观察＋面积背离确认，分别告警' },
  { id: 'structure', label: '趋势线与收敛结构', description: '至少3次接触、ATR容差、收盘突破' },
]

const STRATEGY_LABEL: Record<MonitorStrategy, string> = {
  ma: 'MA', macd: 'MACD', structure: '结构',
}

const SEVERITY_LABEL: Record<string, string> = {
  watch: '观察', signal: '信号', confirmed: '确认',
}

type ChannelKind = 'discord' | 'feishu' | 'email'

type EmailChannelForm = {
  smtp_host: string
  smtp_port: string
  smtp_username: string
  smtp_password: string
  security: 'starttls' | 'ssl' | 'none'
  from_address: string
  to_addresses: string
}

type EmailProviderPreset = {
  label: string
  smtp_host: string
  smtp_port: string
  security: EmailChannelForm['security']
}

const EMAIL_PROVIDER_PRESETS: Record<string, EmailProviderPreset> = {
  'gmail.com': { label: 'Gmail', smtp_host: 'smtp.gmail.com', smtp_port: '587', security: 'starttls' },
  'googlemail.com': { label: 'Gmail', smtp_host: 'smtp.gmail.com', smtp_port: '587', security: 'starttls' },
  'outlook.com': { label: 'Outlook', smtp_host: 'smtp-mail.outlook.com', smtp_port: '587', security: 'starttls' },
  'hotmail.com': { label: 'Outlook', smtp_host: 'smtp-mail.outlook.com', smtp_port: '587', security: 'starttls' },
  'live.com': { label: 'Outlook', smtp_host: 'smtp-mail.outlook.com', smtp_port: '587', security: 'starttls' },
  'qq.com': { label: 'QQ邮箱', smtp_host: 'smtp.qq.com', smtp_port: '465', security: 'ssl' },
  'foxmail.com': { label: 'Foxmail/QQ邮箱', smtp_host: 'smtp.qq.com', smtp_port: '465', security: 'ssl' },
  '163.com': { label: '163邮箱', smtp_host: 'smtp.163.com', smtp_port: '465', security: 'ssl' },
  '126.com': { label: '126邮箱', smtp_host: 'smtp.126.com', smtp_port: '465', security: 'ssl' },
  'yeah.net': { label: '网易邮箱', smtp_host: 'smtp.yeah.net', smtp_port: '465', security: 'ssl' },
  'yahoo.com': { label: 'Yahoo Mail', smtp_host: 'smtp.mail.yahoo.com', smtp_port: '465', security: 'ssl' },
  'icloud.com': { label: 'iCloud Mail', smtp_host: 'smtp.mail.me.com', smtp_port: '587', security: 'starttls' },
  'me.com': { label: 'iCloud Mail', smtp_host: 'smtp.mail.me.com', smtp_port: '587', security: 'starttls' },
  'zoho.com': { label: 'Zoho Mail', smtp_host: 'smtp.zoho.com', smtp_port: '465', security: 'ssl' },
}

const EMPTY_EMAIL_FORM: EmailChannelForm = {
  smtp_host: '',
  smtp_port: '',
  smtp_username: '',
  smtp_password: '',
  security: 'starttls',
  from_address: '',
  to_addresses: '',
}

function emailProviderPreset(email: string): EmailProviderPreset | null {
  const value = email.trim().toLowerCase()
  if (value.split('@').length !== 2) return null
  return EMAIL_PROVIDER_PRESETS[value.split('@')[1]] || null
}

function errText(error: unknown): string {
  return (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '操作失败'
}

export function MonitorPlans() {
  const queryClient = useQueryClient()
  const [showPlanForm, setShowPlanForm] = useState(false)
  const [showChannelForm, setShowChannelForm] = useState(false)
  const [editingChannelId, setEditingChannelId] = useState<number | null>(null)
  const [channelKind, setChannelKind] = useState<ChannelKind>('discord')
  const [channelName, setChannelName] = useState('默认 Discord')
  const [webhookUrl, setWebhookUrl] = useState('')
  const [emailForm, setEmailForm] = useState<EmailChannelForm>(EMPTY_EMAIL_FORM)
  const [channelError, setChannelError] = useState('')
  const [channelNotice, setChannelNotice] = useState('')
  const [planError, setPlanError] = useState('')
  const [plan, setPlan] = useState<{
    name: string
    timeframes: MonitorTimeframe[]
    strategies: MonitorStrategy[]
    channel_id: number | null
  }>({
    name: '自选列表技术告警',
    timeframes: ['4h'],
    strategies: ['ma'],
    channel_id: null,
  })

  const plansQuery = useQuery<{ items: MonitorPlan[] }>({
    queryKey: ['monitor-plans'], queryFn: getMonitorPlans, refetchInterval: 60_000,
  })
  const channelsQuery = useQuery<{ items: NotificationChannel[] }>({
    queryKey: ['notification-channels'], queryFn: getNotificationChannels,
  })
  const eventsQuery = useQuery<{ items: MonitorAlertEvent[]; total: number; unread: number }>({
    queryKey: ['monitor-events'], queryFn: () => getMonitorAlertEvents(50), refetchInterval: 60_000,
  })
  const watchlistQuery = useQuery<{ items: WatchlistItem[] }>({
    queryKey: ['watchlist'], queryFn: getWatchlist,
  })

  const channels = channelsQuery.data?.items ?? []
  const plans = plansQuery.data?.items ?? []
  const events = eventsQuery.data?.items ?? []
  const watchlist = watchlistQuery.data?.items ?? []
  const emailPreset = emailProviderPreset(emailForm.from_address)

  const handleEmailAddressChange = (email: string) => {
    const preset = emailProviderPreset(email)
    setEmailForm(current => ({
      ...current,
      from_address: email,
      ...(email.includes('@') ? {
        smtp_username: email,
        to_addresses: email,
      } : {}),
      ...(preset ? {
        smtp_host: preset.smtp_host,
        smtp_port: preset.smtp_port,
        security: preset.security,
      } : {}),
    }))
  }

  const resetChannelForm = () => {
    setEditingChannelId(null)
    setChannelKind('discord')
    setChannelName('默认 Discord')
    setWebhookUrl('')
    setEmailForm(EMPTY_EMAIL_FORM)
    setChannelError('')
  }

  const channelMutation = useMutation({
    mutationFn: () => {
      const config = channelKind === 'email'
        ? {
            smtp_host: emailForm.smtp_host,
            smtp_port: Number(emailForm.smtp_port),
            smtp_username: emailForm.smtp_username,
            smtp_password: emailForm.smtp_password,
            smtp_use_tls: emailForm.security === 'starttls',
            smtp_use_ssl: emailForm.security === 'ssl',
            from_address: emailForm.from_address,
            to_addresses: emailForm.to_addresses,
          }
        : { webhook_url: webhookUrl }
      const payload = {
      name: channelName,
      kind: channelKind,
      config,
      is_default: !channels.some(item => item.source !== 'environment'),
      }
      return editingChannelId
        ? updateNotificationChannel(editingChannelId, { name: channelName, config })
        : createNotificationChannel(payload)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-channels'] })
      resetChannelForm()
      setShowChannelForm(false)
      setChannelNotice(editingChannelId ? '渠道已更新' : '渠道已保存')
    },
    onError: e => setChannelError(errText(e)),
  })

  const planMutation = useMutation({
    mutationFn: () => createMonitorPlan({
      ...plan,
      target_scope: 'watchlist',
      repeating: true,
      parameters: {},
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['monitor-plans'] })
      setShowPlanForm(false); setPlanError('')
    },
    onError: e => setPlanError(errText(e)),
  })

  const togglePlan = useMutation({
    mutationFn: ({ id, active }: { id: number; active: boolean }) => updateMonitorPlan(id, { is_active: active }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['monitor-plans'] }),
  })
  const removePlan = useMutation({
    mutationFn: deleteMonitorPlan,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['monitor-plans'] }),
  })
  const testChannel = useMutation({
    mutationFn: testNotificationChannel,
    onSuccess: (_data, id) => {
      const channel = channels.find(item => item.id === id)
      setChannelNotice(`${channel?.name || '通知渠道'}测试发送成功`)
      setChannelError('')
    },
    onError: e => {
      setChannelNotice('')
      setChannelError(errText(e))
    },
  })
  const testFallbackEmail = useMutation({
    mutationFn: testEnvironmentEmail,
    onSuccess: () => {
      setChannelNotice('环境变量 Email 测试发送成功')
      setChannelError('')
    },
    onError: e => {
      setChannelNotice('')
      setChannelError(errText(e))
    },
  })
  const updateChannelState = useMutation({
    mutationFn: ({ id, data }: { id: number; data: object }) => updateNotificationChannel(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-channels'] })
      setChannelNotice('渠道状态已更新')
    },
    onError: e => setChannelError(errText(e)),
  })
  const readEvent = useMutation({
    mutationFn: markMonitorAlertEventRead,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['monitor-events'] }),
  })
  const runNow = useMutation({
    mutationFn: runMonitoringNow,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['monitor-plans'] })
      queryClient.invalidateQueries({ queryKey: ['monitor-events'] })
    },
  })

  const toggleStrategy = (strategy: MonitorStrategy) => {
    setPlan(current => {
      const exists = current.strategies.includes(strategy)
      if (exists && current.strategies.length === 1) return current
      return {
        ...current,
        strategies: exists
          ? current.strategies.filter(item => item !== strategy)
          : [...current.strategies, strategy],
      }
    })
  }

  const toggleTimeframe = (timeframe: MonitorTimeframe) => {
    setPlan(current => {
      const exists = current.timeframes.includes(timeframe)
      if (exists && current.timeframes.length === 1) return current
      return {
        ...current,
        timeframes: exists
          ? current.timeframes.filter(item => item !== timeframe)
          : TIMEFRAMES.map(item => item.value).filter(item => (
              current.timeframes.includes(item) || item === timeframe
            )),
      }
    })
  }

  const selectStyle: React.CSSProperties = {
    width: '100%', background: 'var(--bg-0)', border: '1px solid var(--line-1)',
    color: 'var(--fg-0)', borderRadius: 'var(--radius)', fontSize: 12, padding: '7px 9px',
  }

  const openNewChannelForm = () => {
    resetChannelForm()
    setChannelNotice('')
    setShowChannelForm(true)
  }

  const editChannel = (item: NotificationChannel) => {
    setEditingChannelId(item.id)
    setChannelKind(item.kind === 'email' ? 'email' : item.kind === 'feishu' ? 'feishu' : 'discord')
    setChannelName(item.name)
    setWebhookUrl('')
    if (item.kind === 'email') {
      const settings = item.settings || {}
      const security = settings.smtp_use_ssl ? 'ssl' : settings.smtp_use_tls ? 'starttls' : 'none'
      setEmailForm({
        smtp_host: String(settings.smtp_host || ''),
        smtp_port: String(settings.smtp_port || 587),
        smtp_username: String(settings.smtp_username || ''),
        smtp_password: '',
        security,
        from_address: String(settings.from_address || ''),
        to_addresses: String(settings.to_addresses || ''),
      })
    }
    setChannelError('')
    setChannelNotice('')
    setShowChannelForm(true)
  }

  const canSaveChannel = Boolean(
    channelName.trim()
    && (channelKind === 'email'
      ? emailForm.smtp_host.trim()
        && emailForm.smtp_port.trim()
        && emailForm.from_address.trim()
        && emailForm.to_addresses.trim()
        && (!emailForm.smtp_username.trim()
          || emailForm.smtp_password.trim()
          || Boolean(editingChannelId))
      : webhookUrl.trim()),
  )

  return (
    <section style={{ marginBottom: 22 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(280px,.7fr)', gap: 12, marginBottom: 12 }}>
        <div style={{ background: 'var(--bg-1)', border: '1px solid var(--line-1)', borderRadius: 'var(--radius-lg)', padding: 16 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
            <div>
              <div className="section-title">监控计划</div>
              <div style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 4 }}>
                动态跟随自选列表 · 多策略并行 · 收盘确认 · 持续告警
              </div>
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              <button className="btn small ghost" disabled={runNow.isPending || plans.length === 0}
                onClick={() => runNow.mutate()}>{runNow.isPending ? '检查中…' : '立即检查'}</button>
              <button className="btn small primary" onClick={() => setShowPlanForm(value => !value)}>
                {showPlanForm ? '取消' : '+ 新建计划'}
              </button>
            </div>
          </div>

          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 12 }}>
            <span className="chip">自选 {watchlist.length} 个品种</span>
            <span className="chip">15F / 1H / 4H / 1D</span>
            <span className="chip">最长约1分钟触发</span>
          </div>

          {showPlanForm && (
            <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--line-0)' }}>
              <div>
                <input className="mr-input" value={plan.name}
                  style={{ width: '100%' }}
                  onChange={e => setPlan(value => ({ ...value, name: e.target.value }))}
                  placeholder="计划名称" />
                <div style={{ fontSize: 10.5, color: 'var(--fg-3)', marginTop: 10, marginBottom: 6 }}>
                  监控周期（可多选，各周期独立检测）
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 7 }}>
                  {TIMEFRAMES.map(item => {
                    const selected = plan.timeframes.includes(item.value)
                    return (
                      <button key={item.value} type="button" onClick={() => toggleTimeframe(item.value)}
                        aria-pressed={selected}
                        style={{ padding: '8px 6px', borderRadius: 'var(--radius)', cursor: 'pointer',
                          border: `1px solid ${selected ? 'var(--accent)' : 'var(--line-1)'}`,
                          background: selected ? 'var(--accent-dim)' : 'var(--bg-0)',
                          color: selected ? 'var(--fg-0)' : 'var(--fg-2)', fontSize: 11.5, fontWeight: 700 }}>
                        {selected ? '✓ ' : ''}{item.label}
                      </button>
                    )
                  })}
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 8, marginTop: 10 }}>
                {STRATEGIES.map(item => {
                  const selected = plan.strategies.includes(item.id)
                  return (
                    <button key={item.id} type="button" onClick={() => toggleStrategy(item.id)}
                      style={{ textAlign: 'left', padding: 10, borderRadius: 'var(--radius)', cursor: 'pointer',
                        border: `1px solid ${selected ? 'var(--accent)' : 'var(--line-1)'}`,
                        background: selected ? 'var(--accent-dim)' : 'var(--bg-0)', color: 'var(--fg-0)' }}>
                      <div style={{ fontSize: 11.5, fontWeight: 700 }}>{selected ? '✓ ' : ''}{item.label}</div>
                      <div style={{ fontSize: 9.5, color: 'var(--fg-3)', marginTop: 3, lineHeight: 1.4 }}>{item.description}</div>
                    </button>
                  )
                })}
              </div>
              <select style={{ ...selectStyle, marginTop: 10 }} value={plan.channel_id ?? ''}
                onChange={e => setPlan(value => ({ ...value, channel_id: e.target.value ? Number(e.target.value) : null }))}>
                <option value="">继承服务器默认渠道</option>
                {channels.filter(item => item.is_active && item.source !== 'environment').map(item => (
                  <option key={item.id} value={item.id}>{item.name}{item.is_default ? '（默认）' : ''}</option>
                ))}
              </select>
              {planError && <div style={{ color: 'var(--down)', fontSize: 11, marginTop: 8 }}>{planError}</div>}
              <button className="btn primary" style={{ marginTop: 10 }} disabled={planMutation.isPending || !plan.name.trim()}
                onClick={() => planMutation.mutate()}>
                {planMutation.isPending ? '创建中…' : '创建并持续监控'}
              </button>
            </div>
          )}

          <div style={{ marginTop: 14 }}>
            {plans.length === 0 && <div style={{ color: 'var(--fg-3)', fontSize: 11 }}>尚未创建监控计划。</div>}
            {plans.map(item => (
              <div key={item.id} style={{ borderTop: '1px solid var(--line-0)', padding: '11px 0' }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--fg-0)' }}>{item.name}</span>
                  {(item.timeframes?.length ? item.timeframes : [item.timeframe]).map(timeframe => (
                    <span className="chip" key={timeframe}>
                      {TIMEFRAMES.find(tf => tf.value === timeframe)?.label || timeframe.toUpperCase()}
                    </span>
                  ))}
                  {item.strategies.map(strategy => <span className="chip" key={strategy}>{STRATEGY_LABEL[strategy]}</span>)}
                      <span className="chip">{item.channel_name || '默认渠道'}</span>
                  <span style={{ color: item.is_active ? 'var(--up)' : 'var(--fg-3)', fontSize: 10 }}>
                    {item.is_active ? '● 监控中' : '○ 已暂停'}
                  </span>
                </div>
                {item.last_error && <div style={{ color: 'var(--down)', fontSize: 10.5, marginTop: 5 }}>{item.last_error}</div>}
                <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                  <button className="btn small ghost" onClick={() => togglePlan.mutate({ id: item.id, active: !item.is_active })}>
                    {item.is_active ? '暂停' : '启用'}
                  </button>
                  <button className="btn small ghost" onClick={() => removePlan.mutate(item.id)}>删除</button>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div style={{ background: 'var(--bg-1)', border: '1px solid var(--line-1)', borderRadius: 'var(--radius-lg)', padding: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
            <div>
              <div className="section-title">通知渠道</div>
              <div style={{ fontSize: 10.5, color: 'var(--fg-3)', marginTop: 4 }}>Discord、飞书与 Email 统一管理，计划只引用渠道</div>
            </div>
            <button className="btn small" onClick={() => {
              if (showChannelForm) {
                setShowChannelForm(false)
                resetChannelForm()
              } else {
                openNewChannelForm()
              }
            }}>{showChannelForm ? '取消' : '+ 添加渠道'}</button>
          </div>
          {showChannelForm && (
            <div style={{ marginTop: 12, padding: 10, border: '1px solid var(--line-0)', borderRadius: 'var(--radius)', background: 'var(--bg-0)' }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 7, marginBottom: 7 }}>
                <button type="button" className={`btn small ${channelKind === 'discord' ? 'primary' : 'ghost'}`}
                  disabled={editingChannelId !== null}
                  onClick={() => {
                    setChannelKind('discord')
                    setChannelName('默认 Discord')
                  }}>Discord</button>
                <button type="button" className={`btn small ${channelKind === 'feishu' ? 'primary' : 'ghost'}`}
                  disabled={editingChannelId !== null}
                  onClick={() => {
                    setChannelKind('feishu')
                    setChannelName('默认飞书')
                  }}>飞书</button>
                <button type="button" className={`btn small ${channelKind === 'email' ? 'primary' : 'ghost'}`}
                  disabled={editingChannelId !== null}
                  onClick={() => {
                    setChannelKind('email')
                    setChannelName('默认 Email')
                  }}>Email</button>
              </div>
              <input className="mr-input" style={{ width: '100%', marginBottom: 7 }} value={channelName}
                onChange={e => setChannelName(e.target.value)} placeholder="渠道名称" />
              {channelKind === 'discord' || channelKind === 'feishu' ? (
                <>
                  <input className="mr-input" style={{ width: '100%' }} value={webhookUrl}
                    onChange={e => setWebhookUrl(e.target.value)}
                    placeholder={editingChannelId
                      ? `输入新的${channelKind === 'feishu' ? '飞书' : 'Discord'} Webhook URL`
                      : `${channelKind === 'feishu' ? '飞书' : 'Discord'} Webhook URL`}
                    type="password" autoComplete="new-password" />
                  {editingChannelId && (
                    <div style={{ fontSize: 9.5, color: 'var(--fg-3)', marginTop: 5 }}>Webhook 不会回显；编辑时需重新输入。</div>
                  )}
                </>
              ) : (
                <>
                  <input className="mr-input" style={{ width: '100%' }} value={emailForm.from_address}
                    onChange={e => handleEmailAddressChange(e.target.value)}
                    placeholder="邮箱号，如 your-name@gmail.com" type="email" autoComplete="email" />
                  {emailPreset ? (
                    <div style={{ color: 'var(--up)', fontSize: 9.5, marginTop: 5 }}>
                      已识别 {emailPreset.label}，SMTP 配置已自动填写；请输入邮箱密码或应用专用密码。
                    </div>
                  ) : emailForm.from_address.includes('@') ? (
                    <div style={{ color: 'var(--fg-3)', fontSize: 9.5, marginTop: 5 }}>
                      未识别该邮箱域名，请确认下方 SMTP 配置，或继续手动填写企业邮箱参数。
                    </div>
                  ) : null}
                  <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 82px', gap: 7 }}>
                    <input className="mr-input" value={emailForm.smtp_host}
                      onChange={e => setEmailForm(value => ({ ...value, smtp_host: e.target.value }))}
                      placeholder="SMTP 主机，如 smtp.gmail.com" />
                    <input className="mr-input" value={emailForm.smtp_port} inputMode="numeric"
                      onChange={e => setEmailForm(value => ({ ...value, smtp_port: e.target.value }))}
                      placeholder="端口" />
                  </div>
                  <select style={{ ...selectStyle, marginTop: 7 }} value={emailForm.security}
                    onChange={e => setEmailForm(value => ({ ...value, security: e.target.value as EmailChannelForm['security'] }))}>
                    <option value="starttls">STARTTLS（常用端口 587）</option>
                    <option value="ssl">SSL/TLS（常用端口 465）</option>
                    <option value="none">无加密（仅限可信内网）</option>
                  </select>
                  <input className="mr-input" style={{ width: '100%', marginTop: 7 }} value={emailForm.smtp_username}
                    onChange={e => setEmailForm(value => ({ ...value, smtp_username: e.target.value }))}
                    placeholder="SMTP 用户名（通常就是邮箱号）" autoComplete="username" />
                  <input className="mr-input" style={{ width: '100%', marginTop: 7 }} value={emailForm.smtp_password}
                    onChange={e => setEmailForm(value => ({ ...value, smtp_password: e.target.value }))}
                    placeholder={editingChannelId ? 'SMTP 密码（留空则保留原密码）' : 'SMTP 密码 / Gmail 应用专用密码'}
                    type="password" autoComplete="new-password" />
                  <input className="mr-input" style={{ width: '100%', marginTop: 7 }} value={emailForm.to_addresses}
                    onChange={e => setEmailForm(value => ({ ...value, to_addresses: e.target.value }))}
                    placeholder="收件人邮箱，多个用英文逗号分隔" />
                  <div style={{ fontSize: 9.5, color: 'var(--fg-3)', marginTop: 6, lineHeight: 1.45 }}>
                    常见邮箱只需输入邮箱号和密码即可自动匹配；Gmail、QQ、163 等通常需要应用专用密码。密码只写入服务器，接口和页面均不会回显。
                  </div>
                </>
              )}
              {channelError && <div style={{ color: 'var(--down)', fontSize: 10.5, marginTop: 6 }}>{channelError}</div>}
              <button className="btn small primary" style={{ marginTop: 8 }}
                disabled={!canSaveChannel || channelMutation.isPending}
                onClick={() => channelMutation.mutate()}>
                {channelMutation.isPending ? '保存中…' : editingChannelId ? '更新渠道' : '保存渠道'}
              </button>
            </div>
          )}
          {channelNotice && <div style={{ color: 'var(--up)', fontSize: 10.5, marginTop: 8 }}>{channelNotice}</div>}
          {!showChannelForm && channelError && <div style={{ color: 'var(--down)', fontSize: 10.5, marginTop: 8 }}>{channelError}</div>}
          <div style={{ marginTop: 12 }}>
            {channels.length === 0 && <div style={{ fontSize: 11, color: 'var(--fg-3)' }}>未配置渠道，信号将只保存在站内。</div>}
            {channels.map(item => (
              <div key={item.id} style={{ padding: '9px 0', borderTop: '1px solid var(--line-0)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'flex-start' }}>
                  <div>
                    <div style={{ fontSize: 11.5, fontWeight: 650 }}>
                      <span style={{
                        color: item.kind === 'email' ? '#60a5fa' : item.kind === 'feishu' ? '#4ade80' : '#a78bfa',
                        marginRight: 5,
                      }}>
                        {item.kind === 'email' ? 'Email' : item.kind === 'feishu' ? '飞书' : 'Discord'}
                      </span>
                      {item.name}
                      {item.is_default && <span style={{ color: 'var(--accent)' }}> · 默认</span>}
                      {item.source === 'environment' && <span style={{ color: 'var(--fg-3)' }}> · 只读回退</span>}
                      {!item.is_active && <span style={{ color: 'var(--fg-3)' }}>
                        · {item.source === 'environment' ? '配置不完整' : '已停用'}
                      </span>}
                    </div>
                    <div className="mono" style={{ fontSize: 9.5, color: 'var(--fg-3)', marginTop: 3 }}>
                      {item.masked_endpoint}
                      {item.kind === 'email' && item.settings.smtp_host && (
                        <> · {item.settings.smtp_host}:{item.settings.smtp_port || 587}</>
                      )}
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                    <button className="btn small ghost"
                      disabled={!item.is_active || testChannel.isPending || testFallbackEmail.isPending}
                      onClick={() => item.source === 'environment'
                        ? testFallbackEmail.mutate()
                        : testChannel.mutate(item.id)}>
                      {item.kind === 'email' ? '测试邮件' : '测试'}
                    </button>
                    {item.source !== 'environment' && <button className="btn small ghost" onClick={() => editChannel(item)}>编辑</button>}
                    {item.source !== 'environment' && !item.is_default && (
                      <button className="btn small ghost"
                        onClick={() => updateChannelState.mutate({ id: item.id, data: { is_default: true } })}>设为默认</button>
                    )}
                    {item.source !== 'environment' && <button className="btn small ghost"
                      onClick={() => updateChannelState.mutate({ id: item.id, data: { is_active: !item.is_active } })}>
                      {item.is_active ? '停用' : '启用'}
                    </button>}
                  </div>
                </div>
              </div>
            ))}
          </div>
          <div style={{ fontSize: 9.5, color: 'var(--fg-3)', marginTop: 12, lineHeight: 1.5 }}>
            每日 8 点日报会优先使用已启用的 Email 渠道；未配置时回退到服务器环境变量。
          </div>
        </div>
      </div>

      <div style={{ background: 'var(--bg-1)', border: '1px solid var(--line-1)', borderRadius: 'var(--radius-lg)', padding: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div className="section-title">最新信号事件</div>
          <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>未读 {eventsQuery.data?.unread ?? 0} · 总计 {eventsQuery.data?.total ?? 0}</span>
        </div>
        {events.length === 0 && <div style={{ padding: '22px 0 8px', color: 'var(--fg-3)', fontSize: 11 }}>暂无信号，监控会在K线收盘后检查。</div>}
        {events.slice(0, 12).map(item => (
          <button key={item.id} onClick={() => !item.is_read && readEvent.mutate(item.id)}
            style={{ display: 'grid', gridTemplateColumns: '120px 1fr auto', gap: 10, width: '100%', textAlign: 'left',
              padding: '10px 0', border: 0, borderTop: '1px solid var(--line-0)', cursor: 'pointer',
              background: 'transparent', color: 'var(--fg-1)', opacity: item.is_read ? .65 : 1 }}>
            <div>
              <div className="mono" style={{ fontSize: 11.5, fontWeight: 700 }}>{item.symbol}</div>
              <div style={{ fontSize: 9.5, color: 'var(--fg-3)', marginTop: 2 }}>{item.timeframe.toUpperCase()} · {STRATEGY_LABEL[item.strategy]}</div>
            </div>
            <div>
              <div style={{ fontSize: 11.5 }}>{String(item.payload.summary || item.event_type)}</div>
              <div style={{ fontSize: 9.5, color: 'var(--fg-3)', marginTop: 3 }}>
                {SEVERITY_LABEL[item.severity]} · {item.direction === 'long' ? '偏多' : '偏空'} · {new Date(item.signal_time * 1000).toLocaleString()}
              </div>
            </div>
            <span className="chip">{item.status === 'sent' ? '已发送' : item.status === 'in_app_only' ? '站内' : item.status}</span>
          </button>
        ))}
      </div>
    </section>
  )
}
