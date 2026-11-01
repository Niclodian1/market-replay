import assert from 'node:assert/strict'
import test from 'node:test'
import {
  MIN_WARMUP,
  PracticeSession,
  addLeg,
  buildView,
  clampCursor,
  closePosition,
  createPosition,
  defaultStart,
  filterEvents,
  nextReviewTime,
  positionR,
  positionStats,
  randomStart,
  revealedStats,
} from '../lib/practiceCore.ts'

function bars(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    time: 1_700_000_000 + i * 3_600,
    open: 100 + i,
    high: 101 + i,
    low: 99 + i,
    close: 100.5 + i,
  }))
}

function event(index: number, uid: string, indicator = 'chanlun.buy3') {
  return {
    uid,
    indicator_id: indicator,
    direction: 'long',
    bar_time: 1_700_000_000 + index * 3_600,
    confirmed_at: 1_700_000_000 + index * 3_600,
    price: 100 + index,
  }
}

test('clampCursor 不早于 warmup、不晚于最后一根', () => {
  assert.equal(clampCursor(0, 100), MIN_WARMUP)
  assert.equal(clampCursor(-5, 100), MIN_WARMUP)
  assert.equal(clampCursor(500, 100), 99)
  assert.equal(clampCursor(10, 20), 19) // 短序列退化为最后一根
})

test('defaultStart 落在 60% 且合法', () => {
  assert.equal(defaultStart(100), 60)
  assert.equal(defaultStart(1000), 600)
  assert.equal(defaultStart(1), 0)
})

test('buildView 只暴露光标之前的 K 线与已确认事件（无未来函数）', () => {
  const data = bars(120)
  const events = [event(50, 'a'), event(70, 'b'), event(110, 'c')]
  const view = buildView(data, events, 80)
  assert.equal(view.candles.length, 81)
  assert.equal(view.visibleTime, data[80].time)
  assert.deepEqual(view.events.map((item) => item.uid), ['a', 'b'])
  assert.ok(view.progress > 0 && view.progress < 1)
})

test('revealedStats 只统计已揭晓的事件，并按指标聚合', () => {
  const events = [event(10, 'a'), event(20, 'b'), event(30, 'c', 'ma.breakout')]
  const outcomes = {
    a: { won: true, r_multiple: 2, exit_reason: 'target' },
    b: { won: false, r_multiple: -1, exit_reason: 'stop' },
    c: { won: true, r_multiple: 1.5, exit_reason: 'target' },
  }
  const stats = revealedStats(events, outcomes, { a: true, c: true })
  assert.equal(stats.revealed, 2)
  assert.equal(stats.wins, 2)
  assert.equal(stats.hitRate, 1)
  assert.equal(stats.avgR, 1.75)
  assert.equal(stats.byIndicator.length, 2)
  assert.equal(stats.byIndicator[0].indicator_id, 'chanlun.buy3')

  const none = revealedStats(events, outcomes, {})
  assert.equal(none.censored, 0)
  assert.equal(none.revealed, 0)
  assert.equal(none.hitRate, 0)
})

test('PracticeSession 步进/跳转/重置/揭晓可用', () => {
  const session = new PracticeSession(bars(200), [event(50, 'a'), event(120, 'b')], {
    a: { won: true, r_multiple: 2, exit_reason: 'target' },
    b: { won: false, r_multiple: -1, exit_reason: 'stop' },
  })
  assert.equal(session.view.cursor, 120)

  const next = session.step(1)
  assert.equal(next.cursor, 121)
  assert.equal(next.events.length, 2)

  session.step(-1000)
  assert.equal(session.view.cursor, MIN_WARMUP)
  assert.equal(session.view.events.length, 1)

  session.seek(1_700_000_000 + 120 * 3_600)
  assert.equal(session.view.cursor, 120)

  const stats = session.reveal('b')
  assert.equal(stats.revealed, 1)
  assert.equal(stats.losses, 1)
  assert.equal(stats.avgR, -1)

  session.reset()
  assert.equal(session.view.cursor, 120)
  assert.equal(session.revealedMap().b, true)
})

test('randomStart 可复现且在热身后区间内', () => {
  const length = 500
  const a = randomStart(length, 42)
  const b = randomStart(length, 42)
  const c = randomStart(length, 43)
  assert.equal(a, b)
  assert.notEqual(a, c)
  for (const value of [a, b, c]) {
    assert.ok(value >= MIN_WARMUP && value <= length - 1)
  }
  assert.equal(randomStart(1, 42), 0)
})

test('filterEvents 只保留选中指标', () => {
  const events = [event(10, 'a', 'chanlun.buy3'), event(20, 'b', 'ma.breakout'), event(30, 'c')]
  assert.equal(filterEvents(events, ['ma.breakout']).length, 1)
  assert.equal(filterEvents(events, ['chanlun.buy3', 'ma.breakout']).length, 3)
  assert.equal(filterEvents(events, []).length, 0)
})

test('nextReviewTime 找当前时点之后最早的已揭晓亏损', () => {
  const events = [event(10, 'a'), event(50, 'b'), event(90, 'c')]
  const outcomes = {
    a: { won: false, r_multiple: -1, exit_reason: 'stop' },
    b: { won: true, r_multiple: 2, exit_reason: 'target' },
    c: { won: false, r_multiple: -1, exit_reason: 'stop' },
  }
  const revealed = { a: true, b: true, c: true }
  const after = 1_700_000_000 + 20 * 3_600
  assert.equal(nextReviewTime(events, outcomes, revealed, after), events[2].confirmed_at)
  assert.equal(nextReviewTime(events, outcomes, revealed, events[2].confirmed_at), null)
  assert.equal(nextReviewTime(events, outcomes, { a: true }, after), null)
})

test('PracticeSession 随机重开与错题跳转', () => {
  const session = new PracticeSession(
    bars(300),
    [event(50, 'a'), event(120, 'b'), event(200, 'c')],
    {
      a: { won: false, r_multiple: -1, exit_reason: 'stop' },
      b: { won: true, r_multiple: 1, exit_reason: 'target' },
      c: { won: false, r_multiple: -1, exit_reason: 'stop' },
    },
  )
  const view = session.randomReset(7)
  assert.equal(view.cursor, randomStart(300, 7))

  session.seek(1_700_000_000 + 60 * 3_600)
  session.reveal('c')
  const review = session.nextReview()
  assert.ok(review)
  assert.equal(review?.cursor, 200)
  assert.equal(session.nextReview(), null)
})

test('加仓：同向、腿数上限与最小间距', () => {
  const first = { uid: 'e1', indicator_id: 'chanlun.buy3', direction: 'long', bar_time: 1, confirmed_at: 1, price: 100 }
  const second = { uid: 'e2', indicator_id: 'chanlun.buy3', direction: 'long', bar_time: 2, confirmed_at: 2, price: 105 }
  const third = { uid: 'e3', indicator_id: 'chanlun.buy3', direction: 'long', bar_time: 3, confirmed_at: 3, price: 110 }
  const opposite = { uid: 'e4', indicator_id: 'chanlun.sell3', direction: 'short', bar_time: 4, confirmed_at: 4, price: 108 }

  const position = createPosition(first)
  assert.equal(position.avgPrice, 100)
  assert.equal(addLeg(position, opposite), null, '反向事件不能加仓')

  const withSecond = addLeg(position, second, { weight: 0.5 })!
  assert.equal(withSecond.legs.length, 2)
  assert.equal(withSecond.totalWeight, 1.5)
  assert.equal(Math.round(withSecond.avgPrice * 100) / 100, 101.67)

  const capped = addLeg(withSecond, third, { maxLegs: 2 })
  assert.equal(capped, null, '超过腿数上限不再加仓')

  const spaced = addLeg(position, { ...second, price: 100.5 }, { minStepPct: 0.02 })
  assert.equal(spaced, null, '涨幅不足最小间距不加仓')
  assert.ok(addLeg(position, second, { minStepPct: 0.02 }))
})

test('持仓加权 R 与平仓统计', () => {
  const first = { uid: 'e1', indicator_id: 'x', direction: 'long', bar_time: 1, confirmed_at: 1, price: 100 }
  const second = { uid: 'e2', indicator_id: 'x', direction: 'long', bar_time: 2, confirmed_at: 2, price: 105 }
  const position = addLeg(createPosition(first), second, { weight: 1 })!
  const outcomes = {
    e1: { won: true, r_multiple: 2, exit_reason: 'target' },
    e2: { won: false, r_multiple: -1, exit_reason: 'stop' },
  }
  const result = positionR(position, outcomes)!
  assert.equal(result.rMultiple, 0.5)
  assert.deepEqual([result.covered, result.total], [2, 2])
  assert.equal(positionR(createPosition(first), {}), null)

  const closed = closePosition(position, outcomes, 9)!
  assert.equal(closed.won, true)
  assert.equal(closed.closedAt, 9)
  const stats = positionStats([closed, { ...closed, rMultiple: -2, won: false }])
  assert.deepEqual([stats.n, stats.wins, stats.losses], [2, 1, 1])
  assert.equal(stats.avgR, -0.75)
})

test('PracticeSession 支持进场/加仓/平仓', () => {
  const bars = Array.from({ length: 80 }, (_, i) => ({ time: i, open: 1, high: 1, low: 1, close: 1 }))
  const events = [
    { uid: 'e1', indicator_id: 'x', direction: 'long', bar_time: 1, confirmed_at: 1, price: 100 },
    { uid: 'e2', indicator_id: 'x', direction: 'long', bar_time: 2, confirmed_at: 2, price: 105 },
  ]
  const outcomes = { e1: { won: true, r_multiple: 2, exit_reason: 'target' } }
  const session = new PracticeSession(bars, events, outcomes, 10)

  const empty = session.currentPosition
  assert.equal(empty, null)
  assert.equal(session.closePositionNow(), null, '没持仓时平仓返回 null')

  session.openPosition(events[0])
  session.addToPosition(events[1])
  const open = session.currentPosition
  assert.equal(open === null ? 0 : open.legs.length, 2)
  const closed = session.closePositionNow(20)!
  assert.equal(closed.rMultiple, 2, '只有一条腿有结果时按该腿权重结算')
  assert.equal(session.currentPosition, null)
  assert.equal(session.positionStats.n, 1)
  session.reset(10)
  assert.equal(session.closedPositions.length, 1, '重置只清当前持仓，不清历史')
})

test('revealedStats 胜负有盈利口径（r>0）且剔除截尾样本', () => {
  const events = [
    { uid: 'a', indicator_id: 'ma.breakout', direction: 'long', bar_time: 1, confirmed_at: 2, price: 10 },
    { uid: 'b', indicator_id: 'ma.breakout', direction: 'long', bar_time: 3, confirmed_at: 4, price: 11 },
    { uid: 'c', indicator_id: 'ma.breakout', direction: 'long', bar_time: 5, confirmed_at: 6, price: 12 },
    { uid: 'd', indicator_id: 'ma.breakout', direction: 'long', bar_time: 7, confirmed_at: 8, price: 13 },
  ]
  const outcomes = {
    // 未命中 2R 目标但为正收益：按后端口径记胜（旧实现记负）
    a: { won: false, r_multiple: 0.42, exit_reason: 'time' },
    b: { won: true, r_multiple: 2, exit_reason: 'target' },
    c: { won: false, r_multiple: -1, exit_reason: 'stop' },
    // 截尾：既不算胜也不算负，R 也不进均值
    d: { won: false, r_multiple: 1.8, exit_reason: 'censor', censored: true },
  }
  const stats = revealedStats(events, outcomes, { a: true, b: true, c: true, d: true })
  assert.equal(stats.revealed, 3)
  assert.equal(stats.wins, 2)
  assert.equal(stats.losses, 1)
  assert.equal(stats.censored, 1)
  assert.equal(stats.hitRate, 2 / 3)
  assert.equal(Math.round(stats.avgR * 1000) / 1000, Math.round(((0.42 + 2 - 1) / 3) * 1000) / 1000)
})

test('nextReviewTime 只挑真实亏损（排除截尾与盈利）', () => {
  const events = [
    { uid: 'a', indicator_id: 'ma.breakout', direction: 'long', bar_time: 1, confirmed_at: 10, price: 10 },
    { uid: 'b', indicator_id: 'ma.breakout', direction: 'long', bar_time: 2, confirmed_at: 20, price: 10 },
    { uid: 'c', indicator_id: 'ma.breakout', direction: 'long', bar_time: 3, confirmed_at: 30, price: 10 },
  ]
  const outcomes = {
    a: { won: false, r_multiple: 0.5, exit_reason: 'time' },
    b: { won: false, r_multiple: 1.5, exit_reason: 'censor', censored: true },
    c: { won: false, r_multiple: -1, exit_reason: 'stop' },
  }
  const revealed = { a: true, b: true, c: true }
  assert.equal(nextReviewTime(events, outcomes, revealed, 0), 30)
})
