'use client'

// 缠论图表 overlay：
//  - 笔 / 线段 / 中枢 用 lightweight-charts 4.2 的 ISeriesPrimitive（canvas 绘制，
//    随平移缩放自动重绘，与 fenxing 独立展示器同机制）
//  - 分型 + 买卖点 用 series markers（原生支持文本/形状）
import type { IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts'
import type {
  ChanlunLayerKey,
  ChanlunLevel,
  ChanlunPoint,
  ChanlunPivot,
  ChanlunStroke,
} from '@/types'

// ── 颜色（深/浅主题） ────────────────────────────────────────────────────────

type Palette = {
  stroke: string
  segUp: string
  segDown: string
  pivotSegFill: string
  pivotSegEdge: string
  pivotStrokeFill: string
  pivotStrokeEdge: string
  buy: Record<string, string>
  sell: Record<string, string>
}

const DARK_PALETTE: Palette = {
  stroke: 'rgba(163,163,163,0.45)',
  segUp: '#199e70',
  segDown: '#d95926',
  pivotSegFill: 'rgba(96,165,250,0.10)',
  pivotSegEdge: 'rgba(96,165,250,0.40)',
  pivotStrokeFill: 'rgba(45,212,191,0.08)',
  pivotStrokeEdge: 'rgba(45,212,191,0.32)',
  buy: {
    一买: '#d55181',
    二买: '#008300',
    三买: '#c98500',
    背驰买: '#38bdf8',
  },
  sell: {
    一卖: '#0094a6',
    二卖: '#e5484d',
    三卖: '#9c5fff',
    背驰卖: '#fbbf24',
  },
}

const LIGHT_PALETTE: Palette = {
  stroke: 'rgba(107,114,128,0.45)',
  segUp: '#1baf7a',
  segDown: '#eb6834',
  pivotSegFill: 'rgba(37,99,235,0.08)',
  pivotSegEdge: 'rgba(37,99,235,0.32)',
  pivotStrokeFill: 'rgba(13,148,136,0.06)',
  pivotStrokeEdge: 'rgba(13,148,136,0.28)',
  buy: {
    一买: '#e87ba4',
    二买: '#008300',
    三买: '#eda100',
    背驰买: '#0ea5e9',
  },
  sell: {
    一卖: '#007a9e',
    二卖: '#c03028',
    三卖: '#7a3fe0',
    背驰卖: '#f59e0b',
  },
}
export function chanlunPalette(isDark: boolean): Palette {
  return isDark ? DARK_PALETTE : LIGHT_PALETTE
}

export function pointColor(p: ChanlunPoint, isDark: boolean): string {
  const palette = chanlunPalette(isDark)
  const isBuy = p.k.includes('买')
  const map = isBuy ? palette.buy : palette.sell
  return map[p.k] ?? (isBuy ? palette.buy['二买'] : palette.sell['二卖'])
}

// ── canvas overlay（笔 / 线段 / 中枢） ───────────────────────────────────────

export interface ChanlunOverlayLayerData {
  strokes?: ChanlunStroke[] | null
  segments?: ChanlunStroke[] | null
  pivotsStroke?: ChanlunPivot[] | null
  pivotsSegment?: ChanlunPivot[] | null
}

/** 组合交易连线：一笔 = 入场点(绿圆) → 出场点(红/绿方) 的连线 */
export interface ChanlunTradeLink {
  id: number
  entryT: number
  entryPrice: number
  exitT: number
  exitPrice: number
  stop: number
  exitReason: 'sell' | 'stop' | 'macd'
  pnlPct: number
  entryKind?: string | null
  exitKind?: string | null
  /** 高亮（用户点选查看该笔） */
  active?: boolean
}

export interface ChanlunOverlay {
  requestUpdate: () => void
  setData: (data: ChanlunLayerData) => void
  setDark: (dark: boolean) => void
  setTrades: (trades: ChanlunTradeLink[] | null) => void
  destroy: () => void
}

interface ChanlunLayerData {
  strokes: ChanlunStroke[] | null
  segments: ChanlunStroke[] | null
  pivotsStroke: ChanlunPivot[] | null
  pivotsSegment: ChanlunPivot[] | null
  trades: ChanlunTradeLink[] | null
}

/**
 * 创建并挂载到主图 candle series 上的缠论 overlay primitive。
 * 参照 fenxing/index.html 的 ChanOverlayPrimitive（lightweight-charts 4.2
 * useMediaCoordinateSpace 回调式绘制）：
 *  - 中枢矩形画在最底层（填充 + 上下沿虚线）
 *  - 笔：灰色虚线（逐笔独立线段）
 *  - 线段：粗实线，按方向着色（涨绿/跌橙）
 */
export function createChanlunOverlay(
  series: ISeriesApi<'Candlestick'>,
  chart: IChartApi,
): ChanlunOverlay {
  let data: ChanlunLayerData = { strokes: null, segments: null, pivotsStroke: null, pivotsSegment: null, trades: null }
  let dark = true
  let requestUpdate: (() => void) | null = null

  const renderer = {
    draw(target: unknown) {
      const scopeApi = target as {
        useMediaCoordinateSpace?: (fn: (scope: {
          context: CanvasRenderingContext2D
          mediaSize: { width: number; height: number }
        }) => void) => void
      }
      if (!scopeApi.useMediaCoordinateSpace) return
      scopeApi.useMediaCoordinateSpace((scope) => {
        const ctx = scope.context
        const timeScale = chart.timeScale()
        const X = (t: number) => timeScale.timeToCoordinate(t as UTCTimestamp)
        const Y = (p: number) => series.priceToCoordinate(p)
        const palette = chanlunPalette(dark)
        ctx.lineCap = 'round'

        // ── 中枢（底层） ──
        const drawPivots = (pivots: ChanlunPivot[] | null, fill: string, edge: string, isStrokeLevel: boolean) => {
          if (!pivots) return
          ctx.lineWidth = 1
          for (const p of pivots) {
            const x1 = X(p[0])
            const x2 = X(p[1])
            const yz = Y(p[3]) // ZG
            const yd = Y(p[2]) // ZD
            if (x1 == null || x2 == null || yz == null || yd == null) continue
            const x0 = Math.min(x1, x2)
            const w = Math.abs(x2 - x1)
            const top = Math.min(yz, yd)
            const h = Math.abs(yd - yz)
            ctx.fillStyle = fill
            ctx.fillRect(x0, top, w, h)
            ctx.strokeStyle = edge
            ctx.setLineDash([3, 3])
            ctx.beginPath(); ctx.moveTo(x0, yz); ctx.lineTo(x0 + w, yz); ctx.stroke()
            ctx.beginPath(); ctx.moveTo(x0, yd); ctx.lineTo(x0 + w, yd); ctx.stroke()
            ctx.setLineDash([])
          }
        }
        drawPivots(data.pivotsSegment, palette.pivotSegFill, palette.pivotSegEdge, false)
        drawPivots(data.pivotsStroke, palette.pivotStrokeFill, palette.pivotStrokeEdge, true)

        // ── 笔：灰色虚线 ──
        if (data.strokes && data.strokes.length) {
          ctx.lineWidth = 1
          ctx.strokeStyle = palette.stroke
          ctx.setLineDash([2, 3])
          for (const s of data.strokes) {
            const x1 = X(s[0]), y1 = Y(s[1])
            const x2 = X(s[2]), y2 = Y(s[3])
            if (x1 == null || x2 == null || y1 == null || y2 == null) continue
            ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke()
          }
          ctx.setLineDash([])
        }

        // ── 线段：按方向着色 ──
        if (data.segments && data.segments.length) {
          ctx.lineWidth = 2.5
          for (const s of data.segments) {
            ctx.strokeStyle = s[4] === 1 ? palette.segUp : palette.segDown
            const x1 = X(s[0]), y1 = Y(s[1])
            const x2 = X(s[2]), y2 = Y(s[3])
            if (x1 == null || x2 == null || y1 == null || y2 == null) continue
            ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke()
          }
        }

        // ── 组合交易连线：入场(绿圆) → 出场(方点) ──
        if (data.trades && data.trades.length) {
          for (const t of data.trades) {
            const x1 = X(t.entryT), y1 = Y(t.entryPrice)
            const x2 = X(t.exitT), y2 = Y(t.exitPrice)
            if (x1 == null || x2 == null || y1 == null || y2 == null) continue
            const win = t.pnlPct >= 0
            // 连线
            ctx.lineWidth = t.active ? 3 : 1.5
            ctx.strokeStyle = t.active ? '#f59e0b' : (win ? '#22c55e66' : '#ef444466')
            ctx.setLineDash(t.active ? [] : [4, 3])
            ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke()
            ctx.setLineDash([])
            // 入场点：绿圆
            ctx.fillStyle = '#22c55e'
            ctx.beginPath(); ctx.arc(x1, y1, t.active ? 6 : 4, 0, Math.PI * 2); ctx.fill()
            if (t.active) {
              ctx.strokeStyle = '#f59e0b'; ctx.lineWidth = 2
              ctx.beginPath(); ctx.arc(x1, y1, 9, 0, Math.PI * 2); ctx.stroke()
            }
            // 出场点：方点（盈绿/亏红）
            ctx.fillStyle = win ? '#22c55e' : '#ef4444'
            const r = t.active ? 6 : 4
            ctx.fillRect(x2 - r / 2, y2 - r / 2, r, r)
            if (t.active) {
              ctx.strokeStyle = '#f59e0b'; ctx.lineWidth = 2
              ctx.strokeRect(x2 - 9, y2 - 9, 18, 18)
            }
            // 止损位：灰色横线
            if (t.stop != null) {
              const ys = Y(t.stop)
              if (ys != null) {
                ctx.strokeStyle = '#94a3b855'
                ctx.lineWidth = 1
                ctx.setLineDash([2, 2])
                ctx.beginPath(); ctx.moveTo(x1, ys); ctx.lineTo(x2, ys); ctx.stroke()
                ctx.setLineDash([])
              }
            }
          }
        }
      })
    },
  }

  const view = {
    update() {},
    renderer: () => renderer,
  }

  const primitive = {
    attached(param: { requestUpdate: () => void }) {
      requestUpdate = param.requestUpdate
    },
    detached() {
      requestUpdate = null
    },
    updateAllViews() {},
    paneViews: () => [view],
  }

  series.attachPrimitive(primitive as unknown as Parameters<ISeriesApi<'Candlestick'>['attachPrimitive']>[0])

  return {
    requestUpdate: () => requestUpdate?.() ?? series.applyOptions({}),
    setData(next: ChanlunLayerData) {
      data = next
      requestUpdate?.()
    },
    setDark(next: boolean) {
      dark = next
      requestUpdate?.()
    },
    setTrades(next: ChanlunTradeLink[] | null) {
      data = { ...data, trades: next }
      requestUpdate?.()
    },
    destroy: () => {
      series.detachPrimitive(primitive as unknown as Parameters<ISeriesApi<'Candlestick'>['detachPrimitive']>[0])
    },
  }
}

/** 由当前启用的层 + 缠论数据构造 overlay 数据。 */
export function buildChanlunLayerData(
  chanlun: ChanlunLevel | null | undefined,
  layers: Record<ChanlunLayerKey, boolean>,
): ChanlunLayerData {
  return {
    strokes: layers.stroke ? (chanlun?.strokes ?? null) : null,
    segments: layers.segment ? (chanlun?.segments ?? null) : null,
    pivotsStroke: layers.pivot ? (chanlun?.pivots_stroke ?? null) : null,
    pivotsSegment: layers.pivot ? (chanlun?.pivots_segment ?? null) : null,
    trades: null,
  }
}

// ── series markers（分型 + 买卖点） ──────────────────────────────────────────

interface MarkerInput {
  time: number
  position: 'aboveBar' | 'belowBar'
  color: string
  shape: 'arrowUp' | 'arrowDown' | 'circle' | 'square'
  size: 1 | 2
  text: string
}

/**
 * 构建买卖点 markers：
 *  - 买点 circle / 卖点 square，均标在 bar 上方
 *  - 按 一买/二买/… 着色，文本直接标出（一买/一卖…）
 * 注意：lightweight-charts 的 setMarkers 要求 markers 按时间升序
 *（允许同时间重复），构建后必须整体排序，否则会抛
 * "data must be asc ordered by time"（top/bot 分型不展示）。
 */
function pushPointMarkers(
  out: MarkerInput[],
  points: ChanlunPoint[],
  isDark: boolean,
): void {
  for (const p of points) {
    const isBuy = p.k.includes('买')
    out.push({
      time: p.t,
      position: 'aboveBar',
      color: pointColor(p, isDark),
      shape: isBuy ? 'circle' : 'square',
      size: 1,
      text: p.k,
    })
  }
}

/** 一/二/三类买卖点 markers */
export function buildBuySellMarkers(
  chanlun: ChanlunLevel | null | undefined,
  isDark: boolean,
): MarkerInput[] {
  if (!chanlun) return []
  const out: MarkerInput[] = []
  pushPointMarkers(out, chanlun.buy_sell_points, isDark)
  // lightweight-charts 的 setMarkers 要求按时间升序（允许同时间重复）
  out.sort((a, b) => a.time - b.time)
  return out
}

/** 抢先层背驰买/卖 markers（beichi_points） */
export function buildBeichiMarkers(
  chanlun: ChanlunLevel | null | undefined,
  isDark: boolean,
): MarkerInput[] {
  if (!chanlun) return []
  const out: MarkerInput[] = []
  pushPointMarkers(out, chanlun.beichi_points ?? [], isDark)
  out.sort((a, b) => a.time - b.time)
  return out
}

/** 全部买卖点 markers（一/二/三类 + 背驰），供默认全量使用 */
export function buildChanlunMarkers(
  chanlun: ChanlunLevel | null | undefined,
  isDark: boolean,
): MarkerInput[] {
  return [...buildBuySellMarkers(chanlun, isDark), ...buildBeichiMarkers(chanlun, isDark)].sort(
    (a, b) => a.time - b.time,
  )
}

// ── 买卖点 tooltip（鼠标悬浮展示极值 / 可交易确认时间） ─────────────────────────────

export interface ChanlunTooltip {
  /** 更新提示：time 为 crosshair 所在 bar 的 utc 秒；point 为图表内坐标 */
  update: (time: number | undefined, point: { x: number; y: number } | undefined) => void
  destroy: () => void
}

function formatChanlunTime(ts: number | null | undefined): string {
  if (ts == null) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(ts * 1000))
}

function formatChanlunPrice(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  const abs = Math.abs(v)
  if (abs >= 10000) return v.toLocaleString(undefined, { maximumFractionDigits: 2 })
  if (abs >= 100) return v.toFixed(2)
  if (abs >= 1) return v.toFixed(4)
  return v.toFixed(6)
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** 周期对应的单根 K 线秒数（tooltip 容差窗） */
export function chanlunBarSpan(timeframe?: string): number {
  if (timeframe === '1day') return 86400
  if (timeframe === '4h') return 14400
  return 3600 // 1h / 其他
}

/**
 * 在 ±1 bar 容差内匹配离 time 最近的一簇买卖点。
 * markers 文字画在 bar 上方且偏右，鼠标悬停时 crosshair 命中的往往是
 * 相邻那根 bar，因此不能用严格相等匹配（否则恒不命中）。
 */
export function matchChanlunPoints(
  pts: ChanlunPoint[],
  time: number,
  timeframe?: string,
): ChanlunPoint[] {
  const span = chanlunBarSpan(timeframe)
  const within = pts.filter(p => Math.abs((p.t ?? 0) - time) <= span)
  if (!within.length) return []
  const nearest = Math.min(...within.map(p => Math.abs((p.t ?? 0) - time)))
  return within.filter(p => Math.abs((p.t ?? 0) - time) === nearest)
}

/** 构建单个点 tooltip 卡片 HTML */
export function buildChanlunTooltipHtml(p: ChanlunPoint): string {
  const modeLabel = p.mode === 'seg' ? '线段' : p.mode === 'stroke' ? '笔' : '背驰'
  const confirmed =
    p.state === 'forming'
      ? '<span style="color:#eab308">未确认（forming）</span>'
      : formatChanlunTime(p.confirmedT)
  const gray = '<span style="color:#94a3b8">'
  const rows = [
    `<div style="display:flex;align-items:center;gap:6px;font-weight:600;margin-bottom:4px">` +
      `<span>${escapeHtml(p.k)}</span>` +
      (p.tier ? `<span style="padding:0 4px;border-radius:3px;background:rgba(245,158,11,.18);color:#d97706;font-size:10px">${escapeHtml(p.tier)}</span>` : '') +
      `<span style="padding:0 4px;border-radius:3px;background:rgba(148,163,184,.18);color:#94a3b8;font-size:10px">${modeLabel}</span>` +
      `</div>`,
    `<div>${gray}极值</span> ${formatChanlunTime(p.t)}</div>`,
    `<div>${gray}可交易确认</span> ${confirmed}</div>`,
    `<div>${gray}价格</span> ${formatChanlunPrice(p.p)}</div>`,
  ]
  if (p.mode === 'fast') {
    if (p.ratio != null) rows.push(`<div>${gray}比率</span> ${p.ratio}</div>`)
    if (p.trigger) rows.push(`<div>${gray}触发</span> ${escapeHtml(p.trigger)}</div>`)
    if (p.alertT != null) rows.push(`<div>${gray}告警</span> ${formatChanlunTime(p.alertT)}</div>`)
  }
  if (p.why) rows.push(`<div style="color:#94a3b8;margin-top:3px;white-space:normal">${escapeHtml(p.why)}</div>`)
  return rows.join('')
}

/**
 * 在图表容器里追加一个跟随 crosshair 的买卖点 tooltip。
 * getChanlun / isDark / getLayers 均为访问器，随时刻读取最新值，
 * 无需在数据变化时重建监听。图层开关控制显示哪类点。
 */
export function attachChanlunTooltip(
  container: HTMLElement,
  getChanlun: () => ChanlunLevel | null | undefined,
  isDark: () => boolean,
  getLayers?: () => Record<ChanlunLayerKey, boolean>,
): ChanlunTooltip {
  const el = document.createElement('div')
  el.style.cssText = [
    'position:absolute',
    'z-index:20',
    'pointer-events:none',
    'display:none',
    'max-width:340px',
    'min-width:150px',
    'padding:8px 10px',
    'border-radius:6px',
    'border:1px solid rgba(148,163,184,0.35)',
    'font-size:11px',
    'line-height:1.55',
    'box-shadow:0 4px 14px rgba(0,0,0,0.25)',
    'backdrop-filter:blur(3px)',
  ].join(';')
  container.appendChild(el)

  function paint(color: string) {
    el.style.background = isDark() ? 'rgba(17,24,39,0.92)' : 'rgba(255,255,255,0.94)'
    el.style.color = isDark() ? '#e5e7eb' : '#1f2937'
    el.style.borderLeft = `4px solid ${color}`
  }

  function update(time: number | undefined, point: { x: number; y: number } | undefined) {
    const chanlun = getChanlun()
    if (!chanlun || time == null) {
      if ((window as unknown as { __CHANLUN_TIP_DEBUG?: boolean }).__CHANLUN_TIP_DEBUG)
        console.info('[chanlun-tip] skip: no chanlun/time', { hasChanlun: !!chanlun, time })
      el.style.display = 'none'
      return
    }
    const layers = getLayers?.() ?? { point: true, beichi: true }
    const pts: ChanlunPoint[] = []
    if (layers.point) pts.push(...chanlun.buy_sell_points)
    if (layers.beichi) pts.push(...(chanlun.beichi_points ?? []))
    if (!pts.length || !point) {
      if ((window as unknown as { __CHANLUN_TIP_DEBUG?: boolean }).__CHANLUN_TIP_DEBUG)
        console.info('[chanlun-tip] skip: no points/point', { n: pts.length, hasPoint: !!point, layers })
      el.style.display = 'none'
      return
    }
    // markers 文字画在 bar 上方且偏右，鼠标悬停时 crosshair 命中的往往是
    // 相邻那根 bar：用 ±1 bar 容差、再取距离最近的一簇点，避免严格相等漏配。
    const matched = matchChanlunPoints(pts, time as number, chanlun.timeframe)
    if (!matched.length) {
      if ((window as unknown as { __CHANLUN_TIP_DEBUG?: boolean }).__CHANLUN_TIP_DEBUG)
        console.info('[chanlun-tip] miss', { time, n: pts.length, timeframe: chanlun.timeframe })
      el.style.display = 'none'
      return
    }
    console.info('[chanlun-tip] show', {
      time,
      n: matched.length,
      k: matched.map(p => p.k),
      t: matched.map(p => p.t),
      timeframe: chanlun.timeframe,
      display: el.style.display,
      left: el.style.left,
      top: el.style.top,
      zIndex: el.style.zIndex,
    })
    el.innerHTML = matched
      .map(p => {
        pointColor(p, isDark())
        return `<div style="margin-bottom:6px">${buildChanlunTooltipHtml(p)}</div>`
      })
      .join('') + ''
    paint(pointColor(matched[0], isDark()))
    el.style.display = 'block'
    const w = el.offsetWidth
    const h = el.offsetHeight
    const cw = container.clientWidth
    const ch = container.clientHeight
    let left = point.x + 14
    let top = point.y + 12
    if (left + w > cw - 8) left = point.x - w - 14
    if (top + h > ch - 8) top = point.y - h - 12
    if (top < 4) top = 4
    if (left < 4) left = 4
    el.style.left = `${left}px`
    el.style.top = `${top}px`
  }

  return {
    update,
    destroy: () => {
      el.remove()
    },
  }
}