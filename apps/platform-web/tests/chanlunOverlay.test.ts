import assert from 'node:assert/strict'
import test from 'node:test'
import { buildChanlunMarkers, buildChanlunTooltipHtml, matchChanlunPoints } from '../components/chart/chanlunOverlay.ts'

// lightweight-charts 的 setMarkers 要求 markers 按时间升序（同时间允许重复），
// 乱序会抛 "data must be asc ordered by time"。买卖点按后端返回顺序拼接后
// 必须整体排序 —— 本测试锁定该行为，防止回归（曾因漏排引发 BUG）。
test('buildChanlunMarkers 输出必须按时间升序且只含买卖点', () => {
  const chanlun = {
    symbol: 'TEST',
    timeframe: '1day',
    strokes: [],
    segments: [],
    pivots_stroke: [],
    pivots_segment: [],
    buy_sell_points: [
      { t: 500, p: 10, k: '一卖', mode: 'seg' as const },
      { t: 100, p: 11, k: '一买', mode: 'seg' as const },
      { t: 300, p: 12, k: '二买', mode: 'stroke' as const },
    ],
    top_fractals: [{ time: 200, price: 15 }],
    bot_fractals: [{ time: 350, price: 9 }],
  }

  const markers = buildChanlunMarkers(chanlun, true)
  assert.equal(markers.length, 3, '只应包含 3 个买卖点标记，不包含顶底分型')

  for (let i = 1; i < markers.length; i++) {
    assert.ok(
      markers[i].time >= markers[i - 1].time,
      `markers 第 ${i} 个时间 ${markers[i].time} 小于前一个 ${markers[i - 1].time}`,
    )
  }
  assert.deepEqual(
    markers.map(m => m.time),
    [100, 300, 500],
  )
})

test('空数据返回空数组且不抛错', () => {
  assert.deepEqual(buildChanlunMarkers(null, true), [])
  assert.deepEqual(
    buildChanlunMarkers({
      symbol: 'X', timeframe: '1h', strokes: [], segments: [],
      pivots_stroke: [], pivots_segment: [], buy_sell_points: [],
      top_fractals: [], bot_fractals: [],
    }, false),
    [],
  )
})

// tooltip 内容：极值/可交易确认时间、forming 状态、背驰字段
const T0 = 1786716000 // 2026-08-14 14:00 UTC
const T1 = 1786719600 // 2026-08-14 15:00 UTC

test('buildChanlunTooltipHtml 展示极值/可交易确认时间，forming 标注未确认', () => {
  const html = buildChanlunTooltipHtml({
    t: T0, p: 62535.24, k: '一买', mode: 'stroke',
    state: 'confirmed', confirmedT: T1, tier: '明显',
  })
  assert.ok(html.includes('一买'))
  assert.ok(html.includes('笔'))
  assert.ok(html.includes('明显'))
  assert.ok(html.includes('极值'))
  assert.ok(html.includes('可交易确认'))
  assert.ok(html.includes('535.24'))

  const forming = buildChanlunTooltipHtml({
    t: T0, p: 1, k: '三买', mode: 'stroke', state: 'forming', confirmedT: null,
  })
  assert.ok(forming.includes('未确认（forming）'))
})

test('buildChanlunTooltipHtml 背驰点含 比率/触发/告警', () => {
  const html = buildChanlunTooltipHtml({
    t: T0, p: 62900, k: '背驰买', mode: 'fast',
    state: 'confirmed', confirmedT: T0, alertT: T0 - 7200, ratio: 0.058, trigger: '底分型',
  })
  assert.ok(html.includes('背驰'))
  assert.ok(html.includes('比率'))
  assert.ok(html.includes('触发'))
  assert.ok(html.includes('告警'))
})

// tooltip 匹配：markers 文字偏右，悬停时会命中相邻 bar，须用容差就近匹配
test('matchChanlunPoints 悬停相邻 bar 也能命中，精确命中优先', () => {
  // 场景：一买文字画在 T0 bar 上方且偏右 → crosshair 吸附到下一根 bar(T1)，
  // 下一根 bar 无点 → ±1 bar 内容差回落到 T0 的一买。
  const pts = [{ t: T0, p: 1, k: '一买', mode: 'stroke' as const }]
  assert.deepEqual(matchChanlunPoints(pts, T1, '1h').map(p => p.t), [T0])
  // 精确落在 T0 → 命中
  assert.deepEqual(matchChanlunPoints(pts, T0, '1h').map(p => p.t), [T0])
  // 远离所有点（>1bar）→ 空
  assert.deepEqual(matchChanlunPoints(pts, T0 + 7200, '1h'), [])
  // 1day 容差窗更大（±12h）
  assert.ok(matchChanlunPoints(pts, T0 + 43200, '1day').length >= 1)
})