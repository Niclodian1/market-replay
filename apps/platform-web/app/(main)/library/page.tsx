'use client'

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  addIndicatorVersion,
  archiveLibraryIndicator,
  createLibraryIndicator,
  deleteLibraryIndicator,
  demoteIndicatorVersion,
  exportIndicators,
  getLibraryIndicator,
  getLibraryImpact,
  getLibraryIndicators,
  getLibraryPrimitives,
  importIndicators,
  promoteIndicatorVersion,
  pullIndicators,
  pushIndicators,
  restoreLibraryIndicator,
} from '@/lib/api'

export default function LibraryPage() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [name, setName] = useState('我的指标')
  const [primitiveId, setPrimitiveId] = useState('ma.breakout')
  const [paramsText, setParamsText] = useState('{"periods": [20]}')
  const [timeframes, setTimeframes] = useState('1day')
  const [runIds, setRunIds] = useState('')
  const [bundleText, setBundleText] = useState('')
  const [remoteUrl, setRemoteUrl] = useState('http://localhost:8000')
  const [message, setMessage] = useState('')

  const primitivesQ = useQuery({ queryKey: ['library', 'primitives'], queryFn: getLibraryPrimitives, retry: false })
  const indicatorsQ = useQuery({ queryKey: ['library', 'indicators'], queryFn: () => getLibraryIndicators(true), retry: false })
  const detailQ = useQuery({
    queryKey: ['library', 'detail', selectedId],
    queryFn: () => getLibraryIndicator(selectedId!),
    enabled: selectedId !== null,
    retry: false,
  })

  const impactQ = useQuery({
    queryKey: ['library', 'impact', selectedId],
    queryFn: () => getLibraryImpact(selectedId!),
    enabled: selectedId !== null,
    retry: false,
  })

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['library'] })
    queryClient.invalidateQueries({ queryKey: ['stats'] })
  }

  const create = useMutation({
    mutationFn: () =>
      createLibraryIndicator({
        name,
        primitive_id: primitiveId,
        params: JSON.parse(paramsText),
        timeframes: timeframes.split(',').map((item) => item.trim()).filter(Boolean),
      }),
    onSuccess: (data) => {
      setSelectedId(data.id)
      refresh()
    },
    onError: (error: Error) => setMessage(String(error.message)),
  })

  const addVersion = useMutation({
    mutationFn: () => addIndicatorVersion(selectedId!, { params: JSON.parse(paramsText), timeframes: timeframes.split(',').map((s) => s.trim()) }),
    onSuccess: refresh,
    onError: (error: Error) => setMessage(String(error.message)),
  })

  const promote = useMutation({
    mutationFn: (versionId: number) =>
      promoteIndicatorVersion(selectedId!, versionId, {
        experiment_run_ids: runIds.split(',').map((item) => Number(item.trim())).filter(Boolean),
        actor: 'ui',
      }),
    onSuccess: () => {
      setMessage('已推送为正式版本')
      refresh()
    },
    onError: (error: Error) => setMessage(String(error.message)),
  })

  const demote = useMutation({
    mutationFn: (versionId: number) => demoteIndicatorVersion(selectedId!, versionId, { reason: 'manual' }),
    onSuccess: (data: { impact?: { has_references?: boolean; plans?: { count: number }; experiments?: { count: number } } }) => {
      const impact = data?.impact
      if (impact?.has_references) {
        setMessage(`已下线；仍被引用：订阅计划 ${impact.plans?.count ?? 0} · 实验 ${impact.experiments?.count ?? 0}`)
      }
      refresh()
    },
  })

  const archive = useMutation({
    mutationFn: () => archiveLibraryIndicator(selectedId!, '效果不好'),
    onSuccess: refresh,
  })
  const restore = useMutation({ mutationFn: () => restoreLibraryIndicator(selectedId!), onSuccess: refresh })
  const purge = useMutation({
    mutationFn: () => deleteLibraryIndicator(selectedId!, true, '手动删除'),
    onSuccess: () => {
      setSelectedId(null)
      refresh()
    },
  })

  const exportQ = useMutation({
    mutationFn: () => exportIndicators(),
    onSuccess: (data) => setBundleText(JSON.stringify(data, null, 2)),
  })
  const importM = useMutation({
    mutationFn: () => importIndicators(JSON.parse(bundleText)),
    onSuccess: (data) => {
      setMessage(`导入 ${data.created.length} 个，跳过 ${data.skipped.length} 个`)
      refresh()
    },
    onError: (error: Error) => setMessage(String(error.message)),
  })
  const pushM = useMutation({
    mutationFn: () => pushIndicators(remoteUrl, selectedId ? [selectedId] : []),
    onSuccess: (data) => setMessage(`已推送 ${data.pushed} 个指标到 ${data.target}`),
    onError: (error: Error) => setMessage(`推送失败：${String(error.message)}`),
  })
  const pullM = useMutation({
    mutationFn: () => pullIndicators(remoteUrl),
    onSuccess: (data) => {
      setMessage(`从 ${data.source} 拉取 ${data.pulled} 个，新建 ${data.created.length} 个，跳过 ${data.skipped.length} 个`)
      refresh()
    },
    onError: (error: Error) => setMessage(`拉取失败：${String(error.message)}`),
  })

  const primitives = primitivesQ.data?.primitives ?? []
  const indicators = indicatorsQ.data?.indicators ?? []
  const detail = detailQ.data

  return (
    <div className="p-4 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold mr-2">指标库</h1>
        <span className="chip">primitive（代码，不可删） → indicator（配置） → version（快照）</span>
        {message && <span className="text-xs opacity-70">{message}</span>}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_380px] gap-3">
        <div className="space-y-3">
          <div className="panel p-3 overflow-auto" style={{ maxHeight: 220 }}>
            <div className="text-sm font-semibold mb-2">Primitive（{primitives.length}）</div>
            <table className="w-full text-xs">
              <thead className="opacity-70">
                <tr className="text-left">
                  <th className="p-1">id</th>
                  <th className="p-1">说明</th>
                  <th className="p-1">周期</th>
                  <th className="p-1">已用</th>
                  <th className="p-1">可删除</th>
                </tr>
              </thead>
              <tbody>
                {primitives.map((item: { id: string; label: string; timeframes: string[]; indicator_count: number }) => (
                  <tr key={item.id} className="border-t border-[var(--line-1)]">
                    <td className="p-1 mono">{item.id}</td>
                    <td className="p-1">{item.label}</td>
                    <td className="p-1 mono">{item.timeframes.join('/')}</td>
                    <td className="p-1 mono">{item.indicator_count}</td>
                    <td className="p-1">否</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="panel p-3">
            <div className="text-sm font-semibold mb-2">指标（我的配置）</div>
            <table className="w-full text-xs">
              <thead className="opacity-70">
                <tr className="text-left">
                  <th className="p-1">名称</th>
                  <th className="p-1">primitive</th>
                  <th className="p-1">状态</th>
                  <th className="p-1">当前版本</th>
                </tr>
              </thead>
              <tbody>
                {indicators.map((item: { id: number; name: string; primitive_id: string; status: string; current_version_id: number | null; archived_at: string | null }) => (
                  <tr
                    key={item.id}
                    className={`cursor-pointer hover:bg-[var(--bg-2)] ${selectedId === item.id ? 'bg-[var(--bg-2)]' : ''}`}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <td className="p-1">
                      {item.name} {item.archived_at && <span className="chip">已归档</span>}
                    </td>
                    <td className="p-1 mono">{item.primitive_id}</td>
                    <td className="p-1">{item.status}</td>
                    <td className="p-1 mono">{item.current_version_id ?? '—'}</td>
                  </tr>
                ))}
                {!indicators.length && (
                  <tr>
                    <td colSpan={4} className="p-2 text-xs opacity-60">
                      还没有指标。右侧创建第一个，或在下方导入 bundle。
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="panel p-3 space-y-2">
            <div className="text-sm font-semibold">导出 / 导入 bundle（跨环境迁移）</div>
            <div className="flex gap-2">
              <button className="btn small" onClick={() => exportQ.mutate()}>
                导出全部
              </button>
              <button className="btn small accent" onClick={() => importM.mutate()} disabled={!bundleText.trim()}>
                导入下方 JSON
              </button>
            </div>
            <div className="flex gap-2 items-center">
              <input
                className="input flex-1 text-[11px]"
                value={remoteUrl}
                onChange={(event) => setRemoteUrl(event.target.value)}
                placeholder="https://prod.example.com"
              />
              <button className="btn small" onClick={() => pushM.mutate()} disabled={pushM.isPending || !remoteUrl.trim()}>
                推送到正式环境
              </button>
              <button className="btn small" onClick={() => pullM.mutate()} disabled={pullM.isPending || !remoteUrl.trim()}>
                从正式环境拉取
              </button>
            </div>
            <div className="text-[10px] text-[color:var(--fg-3)]">
              推送默认只推「当前选中」指标（未选中则全部）；拉取按同名去重，不会覆盖已有版本。
            </div>
            <textarea
              className="input w-full mono text-[11px]"
              rows={6}
              value={bundleText}
              onChange={(event) => setBundleText(event.target.value)}
              placeholder='{"schema":"indicator-bundle/1", ...}'
            />
          </div>
        </div>

        <div className="space-y-3">
          <div className="panel p-3 space-y-2">
            <div className="text-sm font-semibold">新建指标</div>
            <input className="input w-full" value={name} onChange={(event) => setName(event.target.value)} />
            <select className="input w-full" value={primitiveId} onChange={(event) => setPrimitiveId(event.target.value)}>
              {primitives.map((item: { id: string; label: string }) => (
                <option key={item.id} value={item.id}>
                  {item.id} · {item.label}
                </option>
              ))}
            </select>
            <input className="input w-full mono text-xs" value={paramsText} onChange={(event) => setParamsText(event.target.value)} />
            <input className="input w-full mono text-xs" value={timeframes} onChange={(event) => setTimeframes(event.target.value)} />
            <button className="btn small accent w-full" onClick={() => create.mutate()}>
              创建
            </button>
          </div>

          {detail && (
            <div className="panel p-3 space-y-2">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-sm">{detail.name}</span>
                <span className="chip">{detail.status}</span>
              </div>
              <div className="flex flex-wrap gap-1">
                <button className="btn small" onClick={() => addVersion.mutate()}>
                  用当前参数发新版本
                </button>
                {detail.archived_at ? (
                  <button className="btn small" onClick={() => restore.mutate()}>
                    恢复
                  </button>
                ) : (
                  <button className="btn small" onClick={() => archive.mutate()}>
                    归档
                  </button>
                )}
                <button className="btn small danger" onClick={() => purge.mutate()}>
                  彻底删除
                </button>
              </div>
              {impactQ.data && (
                <div className="text-xs mono opacity-80 border border-[var(--line-1)] rounded p-2 space-y-0.5">
                  <div>影响面 · 事件 {impactQ.data.events?.total ?? 0}（版本 {impactQ.data.events?.by_version ?? 0} / 原始 {impactQ.data.events?.by_primitive ?? 0}）</div>
                  <div>订阅计划 {impactQ.data.plans?.count ?? 0} · 实验 {impactQ.data.experiments?.count ?? 0}</div>
                  {impactQ.data.has_references && <div style={{ color: 'var(--down)' }}>下线/删除前请先处理上述引用</div>}
                </div>
              )}
              <input
                className="input w-full mono text-xs"
                value={runIds}
                onChange={(event) => setRunIds(event.target.value)}
                placeholder="晋升证据：实验运行 id，逗号分隔"
              />
              <div className="text-xs font-semibold mt-1">版本</div>
              <div className="space-y-1">
                {(detail.versions ?? []).map((version: {
                  id: number
                  version_no: number
                  params_hash: string
                  promoted_at: string | null
                  demoted_at: string | null
                  metrics_snapshot: { n?: number; win_rate?: number; expectancy_r?: number }
                }) => (
                  <div key={version.id} className="border border-[var(--line-1)] rounded p-2 text-xs space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="mono">
                        v{version.version_no} #{version.params_hash}
                      </span>
                      <span>{version.promoted_at ? '已晋升' : version.demoted_at ? '已降级' : '候选'}</span>
                    </div>
                    {version.metrics_snapshot?.n ? (
                      <div className="opacity-70 mono">
                        n={version.metrics_snapshot.n} 胜率=
                        {((version.metrics_snapshot.win_rate ?? 0) * 100).toFixed(1)}% 期望=
                        {(version.metrics_snapshot.expectancy_r ?? 0).toFixed(3)}R
                      </div>
                    ) : null}
                    <div className="flex gap-1">
                      <button className="btn small" onClick={() => promote.mutate(version.id)}>
                        推送正式
                      </button>
                      <button className="btn small" onClick={() => demote.mutate(version.id)}>
                        降级
                      </button>
                    </div>
                  </div>
                ))}
              </div>
              <div className="text-xs font-semibold mt-1">操作记录</div>
              <div className="text-[11px] mono opacity-70 space-y-0.5">
                {(detail.audit ?? []).slice(-8).map((row: { id: number; action: string; note: string; created_at: string }) => (
                  <div key={row.id}>
                    {row.created_at.slice(0, 19)} · {row.action} {row.note}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
