import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPracticeData, MAX_ENTRIES, prunePracticeCache, savePracticeData } from '../lib/practiceCache.ts'

class MemoryStorage {
  private map = new Map<string, string>()
  get length() { return this.map.size }
  key(index: number) { return Array.from(this.map.keys())[index] ?? null }
  getItem(key: string) { return this.map.get(key) ?? null }
  setItem(key: string, value: string) { this.map.set(key, value) }
  removeItem(key: string) { this.map.delete(key) }
}

test('roundtrip 缓存练习数据', () => {
  const storage = new MemoryStorage()
  savePracticeData('BTC-USD:4h', { candles: [1, 2, 3] }, storage)
  assert.deepEqual(loadPracticeData('BTC-USD:4h', storage), { candles: [1, 2, 3] })
  assert.equal(loadPracticeData('missing', storage), null)
})

test('超过上限时裁掉最旧的条目', () => {
  const storage = new MemoryStorage()
  for (let i = 0; i < MAX_ENTRIES + 2; i += 1) {
    savePracticeData(`k${i}`, { i }, storage)
  }
  prunePracticeCache(storage)
  assert.equal(storage.length, MAX_ENTRIES)
  assert.equal(loadPracticeData('k0', storage), null)
  assert.deepEqual(loadPracticeData(`k${MAX_ENTRIES + 1}`, storage), { i: MAX_ENTRIES + 1 })
})

test('没有 localStorage 时静默降级', () => {
  const storage = new MemoryStorage()
  savePracticeData('x', { ok: true }, storage)
  assert.deepEqual(loadPracticeData('x', storage), { ok: true })
  assert.equal(loadPracticeData('x', null), null)
})
