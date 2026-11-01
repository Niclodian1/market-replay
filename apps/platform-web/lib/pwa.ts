/**
 * PWA 外壳的纯逻辑：manifest/缓存策略的判定集中在这里，测试直接引用。
 */
export const PWA_MANIFEST_PATH = '/manifest.webmanifest'
export const PWA_SERVICE_WORKER_PATH = '/sw.js'
export const PWA_OFFLINE_URL = '/practice'
export const PWA_CACHE_NAME = 'market-replay-v1'

/** 已经以独立窗口（安装态）运行。 */
export function isStandalone(displayModeStandalone: boolean, navigatorStandalone?: boolean): boolean {
  return Boolean(displayModeStandalone || navigatorStandalone)
}

/** 只有同源 GET 才进缓存；接口 POST 一律直连。 */
export function shouldCacheRequest(input: { method: string; origin: string; selfOrigin: string }): boolean {
  return input.method === 'GET' && input.origin === input.selfOrigin
}

/** 需要预缓存的资源（与 public/sw.js 的 PRECACHE 保持一致）。 */
export const PWA_PRECACHE: string[] = [
  PWA_OFFLINE_URL,
  PWA_MANIFEST_PATH,
  '/icon.svg',
  '/icon-192.png',
  '/icon-512.png',
]

/** 断网时的回退地址：练习页（localStorage 有存档，可离线继续）。 */
export function offlineFallbackUrl(): string {
  return PWA_OFFLINE_URL
}
