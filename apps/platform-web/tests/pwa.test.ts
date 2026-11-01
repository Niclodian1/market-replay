/**
 * PWA 外壳契约：manifest 可安装、service worker 只缓存 GET、布局确实引用了 manifest。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import {
  PWA_CACHE_NAME,
  PWA_MANIFEST_PATH,
  PWA_OFFLINE_URL,
  PWA_PRECACHE,
  PWA_SERVICE_WORKER_PATH,
  isStandalone,
  offlineFallbackUrl,
  shouldCacheRequest,
} from '../lib/pwa.ts'

const webRoot = new URL('..', import.meta.url)
const manifest = JSON.parse(readFileSync(new URL('public/manifest.webmanifest', webRoot), 'utf8'))
const sw = readFileSync(new URL('public/sw.js', webRoot), 'utf8')
const layout = readFileSync(new URL('app/layout.tsx', webRoot), 'utf8')

test('manifest 具备可安装字段与图标', () => {
  assert.equal(manifest.start_url, PWA_OFFLINE_URL)
  assert.equal(manifest.display, 'standalone')
  assert.equal(manifest.scope, '/')
  assert.ok(manifest.name && manifest.short_name)
  const sizes = manifest.icons.map((icon: { sizes: string }) => icon.sizes)
  assert.ok(sizes.includes('192x192') && sizes.includes('512x512'), '必须有 192/512 图标')
  assert.ok(
    manifest.icons.some((icon: { purpose?: string }) => (icon.purpose ?? '').includes('maskable')),
    '必须有 maskable 图标',
  )
  for (const icon of manifest.icons) {
    assert.ok(existsSync(new URL(`public${icon.src}`, webRoot)), `图标文件缺失: ${icon.src}`)
  }
  assert.ok(manifest.shortcuts.some((item: { url: string }) => item.url === '/practice'))
})

test('service worker 预缓存与 lib/pwa 常量一致，且只缓存 GET', () => {
  assert.ok(sw.includes(PWA_CACHE_NAME))
  assert.ok(sw.includes(`'${PWA_OFFLINE_URL}'`))
  for (const asset of PWA_PRECACHE) {
    assert.ok(sw.includes(`'${asset}'`), `sw.js 预缓存缺少 ${asset}`)
  }
  assert.match(sw, /request\.method !== 'GET'/)
  for (const hook of ['install', 'activate', 'fetch']) {
    assert.ok(sw.includes(`addEventListener('${hook}'`), `缺少 ${hook} 事件`)
  }
  assert.ok(sw.includes('caches.match(OFFLINE_URL)'), '导航断网要回退到离线页')
})

test('lib/pwa 判定函数', () => {
  assert.equal(isStandalone(true, false), true)
  assert.equal(isStandalone(false, true), true)
  assert.equal(isStandalone(false, false), false)
  assert.equal(shouldCacheRequest({ method: 'GET', origin: 'a', selfOrigin: 'a' }), true)
  assert.equal(shouldCacheRequest({ method: 'POST', origin: 'a', selfOrigin: 'a' }), false)
  assert.equal(shouldCacheRequest({ method: 'GET', origin: 'b', selfOrigin: 'a' }), false)
  assert.equal(offlineFallbackUrl(), PWA_OFFLINE_URL)
})

test('根布局注册 manifest 与 service worker', () => {
  assert.ok(layout.includes('manifest: PWA_MANIFEST_PATH'), 'layout 未引用 manifest 常量')
  assert.equal(PWA_MANIFEST_PATH, '/manifest.webmanifest')
  assert.ok(layout.includes('PwaRegister'))
  assert.ok(layout.includes('themeColor'))
  assert.ok(readFileSync(new URL(`public${PWA_SERVICE_WORKER_PATH}`, webRoot), 'utf8').length > 0)
})
