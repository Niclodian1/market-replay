'use client'

import { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getAlerts, createAlert, updateAlert, deleteAlert, testAlertWebhook } from '@/lib/api'
import { fetchThresholds, updateThresholds } from '@/lib/api/opportunities'
import type { Alert, AlertCreate, AlertCondition } from '@/types'
import { SymbolCombobox } from '@/components/SymbolCombobox'

const CONDITION_OPTIONS: { value: AlertCondition; label: string; needsThreshold: boolean; thresholdHint: string; needsMaPeriod: boolean }[] = [
  { value: 'price_above',      label: '价格突破（上穿阈值）',          needsThreshold: true,  thresholdHint: '目标价格',           needsMaPeriod: false },
  { value: 'price_below',      label: '价格跌破（下穿阈值）',          needsThreshold: true,  thresholdHint: '目标价格',           needsMaPeriod: false },
  { value: 'ma_cross',         label: '均线交叉（MA20/MA60 金叉死叉）', needsThreshold: false, thresholdHint: '',                  needsMaPeriod: false },
  { value: 'rsi',              label: 'RSI 超买超卖',                needsThreshold: true,  thresholdHint: '超买阈值 (如 70)',    needsMaPeriod: false },
  { value: 'macd',             label: 'MACD 交叉',                  needsThreshold: false, thresholdHint: '',                  needsMaPeriod: false },
  { value: 'ma_breakout',      label: '价格突破均线（自定义周期）',      needsThreshold: false, thresholdHint: '',                  needsMaPeriod: true  },
  { value: 'macd_divergence',  label: 'MACD 背离',                  needsThreshold: false, thresholdHint: '',                  needsMaPeriod: false },
  { value: 'bollinger',        label: '布林带触及',                   needsThreshold: false, thresholdHint: '',                  needsMaPeriod: false },
  { value: 'sr_breakout',      label: '支撑阻力突破',                 needsThreshold: false, thresholdHint: '',                  needsMaPeriod: false },
  { value: 'pinbar',            label: 'Pinbar 锤子线',               needsThreshold: false, thresholdHint: '',                  needsMaPeriod: false },
  { value: 'engulfing',         label: '吞没形态',                    needsThreshold: false, thresholdHint: '',                  needsMaPeriod: false },
  { value: 'w_bottom',          label: 'W 底双底',                    needsThreshold: false, thresholdHint: '',                  needsMaPeriod: false },
]

const TIMEFRAME_OPTIONS = ['15min', '1h', '4h', '1day', '1week']

const CONDITION_LABELS: Record<AlertCondition, string> = {
  price_above:      '价格 ↑',
  price_below:      '价格 ↓',
  ma_cross:         '均线交叉',
  rsi:              'RSI',
  macd:             'MACD',
  ma_breakout:      '突破MA',
  macd_divergence:  'MACD背离',
  bollinger:        '布林带',
  sr_breakout:      'S/R突破',
  pinbar:           'Pinbar',
  engulfing:        '吞没',
  w_bottom:         'W底',
}


function conditionColor(cond: AlertCondition): string {
  const map: Record<AlertCondition, string> = {
    price_above:     'var(--up)',
    price_below:     'var(--down)',
    ma_cross:        '#34d399',
    rsi:             '#f59e0b',
    macd:            'var(--accent)',
    ma_breakout:     '#34d399',
    macd_divergence: '#a78bfa',
    bollinger:       '#60a5fa',
    sr_breakout:     '#fb923c',
    pinbar:          '#f472b6',
    engulfing:       '#e879f9',
    w_bottom:        '#5aa7ff',
  }
  return map[cond] ?? 'var(--fg-2)'
}

// ── Symbol Combobox ────────────────────────────────────────────────────────────

// ── Webhook Presets Storage ────────────────────────────────────────────────────

const WEBHOOK_STORAGE_KEY = 'mr-webhook-presets'

function loadWebhookPresets(): { label: string; url: string }[] {
  try {
    return JSON.parse(localStorage.getItem(WEBHOOK_STORAGE_KEY) || '[]')
  } catch { return [] }
}

function saveWebhookPreset(label: string, url: string) {
  const presets = loadWebhookPresets().filter(p => p.url !== url)
  presets.unshift({ label, url })
  localStorage.setItem(WEBHOOK_STORAGE_KEY, JSON.stringify(presets.slice(0, 10)))
}

function deleteWebhookPreset(url: string) {
  const presets = loadWebhookPresets().filter(p => p.url !== url)
  localStorage.setItem(WEBHOOK_STORAGE_KEY, JSON.stringify(presets))
}

// ── Webhook Field with Preset Dropdown ────────────────────────────────────────

function WebhookField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [presets, setPresets] = useState<{ label: string; url: string }[]>([])
  const [open, setOpen] = useState(false)
  const [saveLabel, setSaveLabel] = useState('')
  const [showSaveInput, setShowSaveInput] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => { setPresets(loadWebhookPresets()) }, [])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false); setShowSaveInput(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const handleSave = () => {
    if (!value || !saveLabel.trim()) return
    saveWebhookPreset(saveLabel.trim(), value)
    setPresets(loadWebhookPresets())
    setSaveLabel(''); setShowSaveInput(false)
  }

  const handleDelete = (url: string, e: React.MouseEvent) => {
    e.stopPropagation()
    deleteWebhookPreset(url)
    setPresets(loadWebhookPresets())
  }

  const select = (url: string) => { onChange(url); setOpen(false) }

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <div style={{ display: 'flex', gap: 6 }}>
        <input
          className="mr-input"
          style={{ flex: 1 }}
          placeholder="https://discord.com/api/webhooks/..."
          value={value}
          onChange={e => onChange(e.target.value)}
          onFocus={() => setOpen(true)}
        />
        <button
          type="button"
          className="btn small ghost"
          title="保存为常用"
          onClick={() => { setShowSaveInput(v => !v); setOpen(false) }}
          style={{ flexShrink: 0, padding: '0 8px' }}
        >
          ★
        </button>
        {presets.length > 0 && (
          <button
            type="button"
            className="btn small ghost"
            title="常用列表"
            onClick={() => { setOpen(v => !v); setShowSaveInput(false) }}
            style={{ flexShrink: 0, padding: '0 8px' }}
          >
            ▾
          </button>
        )}
      </div>

      {showSaveInput && (
        <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
          <input
            className="mr-input"
            style={{ flex: 1 }}
            placeholder="给这个 Webhook 起个名字…"
            value={saveLabel}
            onChange={e => setSaveLabel(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSave()}
          />
          <button type="button" className="btn small primary" onClick={handleSave}>保存</button>
        </div>
      )}

      {open && presets.length > 0 && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 4px)', left: 0, right: 0,
          background: 'var(--bg-1)', border: '1px solid var(--line-1)', borderRadius: 'var(--radius-lg)',
          boxShadow: '0 8px 24px rgba(0,0,0,.25)', zIndex: 60, maxHeight: 200, overflowY: 'auto',
        }}>
          {presets.map((p, i) => (
            <div
              key={i}
              style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', cursor: 'pointer', borderBottom: '1px solid var(--line-0)' }}
              onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-2)')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
              onMouseDown={e => { e.preventDefault(); select(p.url) }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--fg-1)' }}>{p.label}</div>
                <div className="mono" style={{ fontSize: 10, color: 'var(--fg-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {p.url.slice(0, 60)}…
                </div>
              </div>
              <button
                type="button"
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--fg-3)', fontSize: 12, padding: 2 }}
                onMouseDown={e => { e.preventDefault(); handleDelete(p.url, e) }}
              >×</button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Alert Form ─────────────────────────────────────────────────────────────────

function AlertForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<Partial<AlertCreate>>({
    condition_type: 'price_above',
    timeframe: '1day',
    repeating: false,
    ma_period: 20,
  })
  const [err, setErr] = useState('')

  const mutation = useMutation({
    mutationFn: (data: AlertCreate) => createAlert(data),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['alerts'] }); onClose() },
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setErr(msg ? `创建失败：${msg}` : '创建失败，请检查后端连接')
    },
  })

  const condOpt = CONDITION_OPTIONS.find(o => o.value === form.condition_type)

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.name || !form.symbol || !form.condition_type || !form.discord_webhook_url) {
      setErr('请填写所有必填项'); return
    }
    if (condOpt?.needsThreshold && !form.threshold) { setErr('请填写阈值'); return }
    if (condOpt?.needsMaPeriod && (!form.ma_period || form.ma_period < 2)) { setErr('请填写有效的均线周期（≥2）'); return }
    setErr('')
    mutation.mutate(form as AlertCreate)
  }

  const labelStyle: React.CSSProperties = { fontSize: 10.5, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--fg-2)', fontWeight: 600, marginBottom: 5, display: 'block' }
  const selectStyle: React.CSSProperties = {
    width: '100%', background: 'var(--bg-0)', border: '1px solid var(--line-1)', color: 'var(--fg-0)',
    borderRadius: 'var(--radius)', fontSize: 12, padding: '6px 8px', outline: 'none', fontFamily: 'var(--font-ui)',
  }

  return (
    <form onSubmit={handleSubmit} style={{ background: 'var(--bg-1)', border: '1px solid var(--line-1)', borderRadius: 'var(--radius-lg)', padding: 20, marginBottom: 20 }}>
      <div className="section-title" style={{ marginBottom: 14 }}>新建告警</div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
        <div>
          <label style={labelStyle}>告警名称 *</label>
          <input className="mr-input" style={{ width: '100%' }} placeholder="例：BTC 突破 10万"
            value={form.name || ''} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
        </div>
        <div>
          <label style={labelStyle}>标的代码 *</label>
          <SymbolCombobox value={form.symbol || ''} onChange={v => setForm(f => ({ ...f, symbol: v }))} />
        </div>
      </div>

      <div style={{ marginBottom: 12 }}>
        <label style={labelStyle}>触发条件 *</label>
        <select style={selectStyle} value={form.condition_type}
          onChange={e => setForm(f => ({ ...f, condition_type: e.target.value as AlertCondition, threshold: undefined, ma_period: 20 }))}>
          {CONDITION_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
        {condOpt?.needsThreshold && (
          <div>
            <label style={labelStyle}>{condOpt.thresholdHint} *</label>
            <input className="mr-input mono" type="number" style={{ width: '100%' }} placeholder="0"
              value={form.threshold ?? ''}
              onChange={e => setForm(f => ({ ...f, threshold: parseFloat(e.target.value) || undefined }))} />
          </div>
        )}
        {condOpt?.needsMaPeriod && (
          <div>
            <label style={labelStyle}>均线周期 * <span style={{ color: 'var(--fg-3)', fontWeight: 400, textTransform: 'none' }}>（如 20 / 60 / 120）</span></label>
            <input className="mr-input mono" type="number" style={{ width: '100%' }} placeholder="20" min={2}
              value={form.ma_period ?? 20}
              onChange={e => setForm(f => ({ ...f, ma_period: parseInt(e.target.value) || 20 }))} />
          </div>
        )}
        <div>
          <label style={labelStyle}>监控周期</label>
          <select style={selectStyle} value={form.timeframe}
            onChange={e => setForm(f => ({ ...f, timeframe: e.target.value }))}>
            {TIMEFRAME_OPTIONS.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
      </div>

      <div style={{ marginBottom: 12 }}>
        <label style={labelStyle}>Discord Webhook URL *</label>
        <WebhookField
          value={form.discord_webhook_url || ''}
          onChange={v => setForm(f => ({ ...f, discord_webhook_url: v }))}
        />
      </div>

      <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 10 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', userSelect: 'none' }}>
          <div
            onClick={() => setForm(f => ({ ...f, repeating: !f.repeating }))}
            style={{
              width: 34, height: 18, borderRadius: 9, position: 'relative', cursor: 'pointer',
              background: form.repeating ? 'var(--accent)' : 'var(--line-1)',
              transition: 'background 0.2s',
              flexShrink: 0,
            }}
          >
            <div style={{
              position: 'absolute', top: 2, left: form.repeating ? 18 : 2, width: 14, height: 14,
              borderRadius: '50%', background: '#fff', transition: 'left 0.2s',
            }} />
          </div>
          <span style={{ fontSize: 11.5, color: 'var(--fg-1)' }}>反复告警</span>
          <span style={{ fontSize: 10.5, color: 'var(--fg-3)' }}>（触发后自动重置，下次满足条件时继续通知）</span>
        </label>
      </div>

      {err && (
        <div style={{ padding: '8px 10px', background: 'var(--down-bg)', border: '1px solid var(--down)', borderRadius: 'var(--radius)', fontSize: 11.5, color: 'var(--down)', marginBottom: 10 }}>
          {err}
        </div>
      )}

      <div style={{ display: 'flex', gap: 8 }}>
        <button type="button" className="btn" onClick={onClose}>取消</button>
        <button type="submit" className="btn primary" disabled={mutation.isPending} style={{ flex: 1, justifyContent: 'center' }}>
          {mutation.isPending ? '创建中…' : '创建告警'}
        </button>
      </div>
    </form>
  )
}

// ── Alert Card ─────────────────────────────────────────────────────────────────

function AlertCard({ alert }: { alert: Alert }) {
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const [testMsg, setTestMsg] = useState('')

  const toggleMutation = useMutation({
    mutationFn: () => updateAlert(alert.id, { is_active: !alert.is_active }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['alerts'] }),
  })

  const resetMutation = useMutation({
    mutationFn: () => updateAlert(alert.id, { is_active: true }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['alerts'] }),
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteAlert(alert.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['alerts'] }),
  })

  const testMutation = useMutation({
    mutationFn: () => testAlertWebhook(alert.id),
    onSuccess: () => { setTestMsg('✓ 已发送'); setTimeout(() => setTestMsg(''), 3000) },
    onError:   () => { setTestMsg('发送失败'); setTimeout(() => setTestMsg(''), 3000) },
  })

  const accentColor = conditionColor(alert.condition_type)

  const cardBg = alert.is_triggered
    ? 'var(--accent-dim)'
    : alert.is_active
    ? 'var(--bg-1)'
    : 'var(--bg-0)'

  const cardBorder = alert.is_triggered ? 'var(--accent)' : 'var(--line-1)'
  const cardOpacity = !alert.is_active && !alert.is_triggered ? 0.55 : 1

  return (
    <div style={{ background: cardBg, border: `1px solid ${cardBorder}`, borderLeft: `3px solid ${accentColor}`, borderRadius: 'var(--radius-lg)', padding: '12px 14px', opacity: cardOpacity, marginBottom: 8 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
            <span className="mono" style={{ fontWeight: 700, color: 'var(--fg-0)', fontSize: 13 }}>{alert.symbol}</span>
            <span className="chip" style={{ fontSize: 9, color: accentColor, borderColor: accentColor }}>
              {CONDITION_LABELS[alert.condition_type]}
            </span>
            <span className="chip" style={{ fontSize: 9 }}>{alert.timeframe}</span>
            {alert.repeating && (
              <span className="chip" style={{ fontSize: 9, color: 'var(--accent)', borderColor: 'var(--accent)' }}>反复</span>
            )}
            {alert.is_triggered && (
              <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, color: 'var(--accent)', fontWeight: 600 }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--accent)', animation: 'pulse 1.5s infinite' }} />
                已触发
              </span>
            )}
          </div>

          <div style={{ fontSize: 12, color: 'var(--fg-1)', fontWeight: 600, marginBottom: 2 }}>{alert.name}</div>

          {['price_above', 'price_below'].includes(alert.condition_type) && (
            <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-3)' }}>
              阈值 <span style={{ color: 'var(--fg-1)' }}>{alert.threshold}</span>
            </div>
          )}

          {alert.condition_type === 'rsi' && alert.threshold && (
            <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-3)' }}>
              超买 <span style={{ color: 'var(--fg-1)' }}>{alert.threshold}</span> / 超卖 <span style={{ color: 'var(--fg-1)' }}>{100 - alert.threshold}</span>
            </div>
          )}

          {alert.condition_type === 'ma_breakout' && (
            <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-3)' }}>
              均线 <span style={{ color: 'var(--fg-1)' }}>MA{alert.ma_period ?? 20}</span>
            </div>
          )}

          {alert.triggered_at && (
            <div className="mono" style={{ fontSize: 10, color: 'var(--accent)', marginTop: 4 }}>
              {alert.repeating ? '最近触发' : '触发于'} {new Date(alert.triggered_at).toLocaleString()}
            </div>
          )}
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--line-0)' }}>
        {alert.is_triggered ? (
          <button className="btn small" onClick={() => resetMutation.mutate()} disabled={resetMutation.isPending}>
            重新激活
          </button>
        ) : (
          <button className="btn small ghost" onClick={() => toggleMutation.mutate()} disabled={toggleMutation.isPending}>
            {alert.is_active ? '暂停' : '启用'}
          </button>
        )}

        <button className="btn small ghost" onClick={() => testMutation.mutate()} disabled={testMutation.isPending}>
          {testMsg || (testMutation.isPending ? '发送中…' : '测试')}
        </button>

        <div style={{ flex: 1 }} />

        {confirming ? (
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn small danger" onClick={() => deleteMutation.mutate()}>确认</button>
            <button className="btn small ghost" onClick={() => setConfirming(false)}>取消</button>
          </div>
        ) : (
          <button className="btn small ghost" style={{ color: 'var(--fg-3)' }} onClick={() => setConfirming(true)}>删除</button>
        )}
      </div>
    </div>
  )
}

function RecommenderSettings() {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: ['recommender-thresholds'],
    queryFn: fetchThresholds,
  })
  const [draft, setDraft] = useState(data)

  useEffect(() => {
    if (data) setDraft(data)
  }, [data])

  const mutation = useMutation({
    mutationFn: updateThresholds,
    onSuccess: saved => {
      setDraft(saved)
      queryClient.invalidateQueries({ queryKey: ['recommender-thresholds'] })
      queryClient.invalidateQueries({ queryKey: ['opportunities'] })
    },
  })

  if (!draft) {
    return <div className="panel" style={{ padding: 14, color: 'var(--fg-2)', marginBottom: 16 }}>加载推荐器阈值...</div>
  }

  const setNumber = (key: keyof typeof draft, value: number) => setDraft({ ...draft, [key]: value })

  return (
    <div className="panel" style={{ padding: 14, marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div>
          <div className="section-title">Recommender Settings</div>
          <div style={{ fontSize: 12, color: 'var(--fg-2)', marginTop: 4 }}>机会管道使用这些阈值过滤 edge 和置信度</div>
        </div>
        <button className="btn small primary" disabled={mutation.isPending} onClick={() => mutation.mutate(draft)}>
          {mutation.isPending ? '保存中...' : '保存阈值'}
        </button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
        <ThresholdSlider label="Min winrate" value={draft.min_winrate} min={0.5} max={0.7} step={0.01} format={v => `${Math.round(v * 100)}%`} onChange={v => setNumber('min_winrate', v)} />
        <ThresholdSlider label="Min sample" value={draft.min_sample} min={10} max={100} step={1} format={v => `${Math.round(v)}`} onChange={v => setNumber('min_sample', Math.round(v))} />
        <ThresholdSlider label="Min score" value={draft.min_score} min={0.4} max={0.9} step={0.01} format={v => `${Math.round(v * 100)}`} onChange={v => setNumber('min_score', v)} />
        <ThresholdSlider label="Max open/symbol" value={draft.max_open_per_symbol} min={1} max={5} step={1} format={v => `${Math.round(v)}`} onChange={v => setNumber('max_open_per_symbol', Math.round(v))} />
      </div>
    </div>
  )
}

function ThresholdSlider({
  label,
  value,
  min,
  max,
  step,
  format,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  format: (v: number) => string
  onChange: (v: number) => void
}) {
  return (
    <label style={{ display: 'block' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--fg-2)', fontSize: 10.5, letterSpacing: '.08em', textTransform: 'uppercase', marginBottom: 8 }}>
        <span>{label}</span>
        <span className="mono" style={{ color: 'var(--fg-0)' }}>{format(value)}</span>
      </div>
      <input style={{ width: '100%' }} type="range" min={min} max={max} step={step} value={value} onChange={e => onChange(Number(e.target.value))} />
    </label>
  )
}

// ── Main Page ──────────────────────────────────────────────────────────────────

export default function AlertsPage() {
  const [showForm, setShowForm] = useState(false)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['alerts'],
    queryFn: getAlerts,
    refetchInterval: 60_000,
  })

  const alerts: Alert[] = data?.items ?? []
  const triggered = alerts.filter(a => a.is_triggered)
  const active    = alerts.filter(a => a.is_active && !a.is_triggered)
  const paused    = alerts.filter(a => !a.is_active)

  const groupLabelStyle: React.CSSProperties = {
    fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase', fontWeight: 600,
    color: 'var(--fg-3)', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6,
  }

  return (
    <div className="page">
      <div className="page-inner">
        <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 16 }}>
          <div>
            <div className="section-title">策略告警</div>
            <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: 'var(--fg-0)' }}>
              {data ? `${alerts.length} 条告警` : '…'}
            </div>
            <div style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 2 }}>设置条件触发时通过 Discord 自动通知</div>
          </div>
          <button className={`btn${showForm ? ' ghost' : ' primary'}`} onClick={() => setShowForm(v => !v)}>
            {showForm ? '取消' : '+ 新建告警'}
          </button>
        </div>

        <RecommenderSettings />

        {showForm && <AlertForm onClose={() => setShowForm(false)} />}

        {isLoading && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, gap: 12, color: 'var(--fg-2)' }}>
            <div style={{ width: 24, height: 24, border: '2px solid var(--accent)', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.7s linear infinite' }} />
            <span className="mono" style={{ fontSize: 12 }}>加载告警…</span>
          </div>
        )}

        {isError && (
          <div style={{ textAlign: 'center', padding: '48px 0', color: 'var(--down)', fontSize: 13 }}>加载失败，请检查后端连接</div>
        )}

        {!isLoading && !isError && alerts.length === 0 && !showForm && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: 200, gap: 12 }}>
            <svg style={{ width: 36, height: 36, color: 'var(--fg-3)' }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.4}
                d="M15 17h5l-1.405-1.405A2.032 2.032 0 0 1 18 14.158V11a6.002 6.002 0 0 0-4-5.659V5a2 2 0 1 0-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 1 1-6 0v-1m6 0H9" />
            </svg>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 13, color: 'var(--fg-2)' }}>还没有告警</div>
              <div style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 4 }}>点击「新建告警」开始设置</div>
            </div>
          </div>
        )}

        {triggered.length > 0 && (
          <div style={{ marginBottom: 20 }}>
            <div style={groupLabelStyle}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--accent)', animation: 'pulse 1.5s infinite', display: 'inline-block' }} />
              已触发 ({triggered.length})
            </div>
            {triggered.map(a => <AlertCard key={a.id} alert={a} />)}
          </div>
        )}

        {active.length > 0 && (
          <div style={{ marginBottom: 20 }}>
            <div style={groupLabelStyle}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--up)', display: 'inline-block' }} />
              监控中 ({active.length})
            </div>
            {active.map(a => <AlertCard key={a.id} alert={a} />)}
          </div>
        )}

        {paused.length > 0 && (
          <div style={{ marginBottom: 20 }}>
            <div style={groupLabelStyle}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--fg-3)', display: 'inline-block' }} />
              已暂停 ({paused.length})
            </div>
            {paused.map(a => <AlertCard key={a.id} alert={a} />)}
          </div>
        )}
      </div>
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:.4 } }
      `}</style>
    </div>
  )
}
