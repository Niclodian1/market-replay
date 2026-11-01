/*
 * Market Replay service worker：应用外壳 + 练习页离线可用。
 *
 * 规则：
 * - 只缓存 GET（POST /api/series 等一律直连，避免缓存指标结果导致口径漂移）
 * - 导航请求 network-first，断网回退到缓存的 /practice（练习页本身有 localStorage 存档）
 * - 静态资源（/_next/static、图标、manifest）stale-while-revalidate
 */
const CACHE_NAME = 'market-replay-v1'
const OFFLINE_URL = '/practice'
const PRECACHE = [
  '/practice',
  '/manifest.webmanifest',
  '/icon.svg',
  '/icon-192.png',
  '/icon-512.png',
]

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return
  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone()
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy))
          return response
        })
        .catch(() => caches.match(request).then((hit) => hit || caches.match(OFFLINE_URL))),
    )
    return
  }

  if (url.pathname.startsWith('/_next/static') || PRECACHE.includes(url.pathname) || url.pathname.startsWith('/icon')) {
    event.respondWith(
      caches.match(request).then((hit) => {
        const network = fetch(request)
          .then((response) => {
            const copy = response.clone()
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy))
            return response
          })
          .catch(() => hit)
        return hit || network
      }),
    )
  }
})
