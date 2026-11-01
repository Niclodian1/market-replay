/**
 * 练习数据的离线缓存：练一次就存本地，断网/地铁里还能继续步进。
 * 只缓存「K 线 + 指标序列 + 事件 + 结果」，不缓存会话状态。
 */
type StorageLike = {
  readonly length: number
  key(index: number): string | null
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

const PREFIX = 'practice:'
export const MAX_ENTRIES = 4

function storageOf(storage?: StorageLike | null): StorageLike | null {
  if (storage) return storage
  try {
    return typeof globalThis.localStorage === 'undefined' ? null : (globalThis.localStorage as StorageLike)
  } catch {
    return null
  }
}

export function prunePracticeCache(storage?: StorageLike | null): void {
  const store = storageOf(storage)
  if (!store) return
  const keys: string[] = []
  for (let i = 0; i < store.length; i += 1) {
    const key = store.key(i)
    if (key?.startsWith(PREFIX)) keys.push(key)
  }
  // 后写入的覆盖先写入的：按 key 顺序裁剪最旧的
  for (const key of keys.slice(0, Math.max(0, keys.length - MAX_ENTRIES))) {
    store.removeItem(key)
  }
}

export function savePracticeData<T>(key: string, value: T, storage?: StorageLike | null): void {
  const store = storageOf(storage)
  if (!store) return
  try {
    store.setItem(PREFIX + key, JSON.stringify(value))
    prunePracticeCache(store)
  } catch {
    // 配额满或隐私模式：静默降级为「不缓存」
  }
}

export function loadPracticeData<T>(key: string, storage?: StorageLike | null): T | null {
  const store = storageOf(storage)
  if (!store) return null
  try {
    const raw = store.getItem(PREFIX + key)
    return raw ? (JSON.parse(raw) as T) : null
  } catch {
    return null
  }
}
