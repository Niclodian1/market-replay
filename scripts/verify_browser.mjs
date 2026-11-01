#!/usr/bin/env node
/**
 * 浏览器端到端验证（Chrome DevTools Protocol，无第三方依赖）。
 *
 *   node scripts/verify_browser.mjs --base http://127.0.0.1:3100 [--cdp http://127.0.0.1:9222]
 *
 * 前置：
 *   1) 后端：apps/platform-api（uvicorn，默认 8000）
 *   2) 前端：apps/platform-web 已 `npm run build`，`npx next start -p 3100`
 *   3) headless Chrome：`"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
 *        --headless=new --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-cdp about:blank`
 *
 * 检查项：页面 DOM 断言、控制台异常/API 错误为 0、练习页进场/加仓/平仓交互、
 *         揭晓结果口径、PWA 可安装性（manifest + installabilityErrors）、断网回退。
 * 退出码：0 全部通过；1 有断言失败。
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'

const args = process.argv.slice(2)
const flag = (name, fallback) => {
  const index = args.indexOf(`--${name}`)
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback
}
const BASE = flag('base', 'http://127.0.0.1:3100')
const CDP = flag('cdp', 'http://127.0.0.1:9222')
const SHOT_DIR = flag('shots', '')

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const failures = []
const results = []

function check(name, ok, detail) {
  results.push({ name, ok, detail })
  if (!ok) failures.push(`${name}${detail ? ` — ${detail}` : ''}`)
}

async function session(url, { offline = false } = {}) {
  const target = await (
    await fetch(`${CDP}/json/new?${encodeURIComponent('about:blank')}`, { method: 'PUT' })
  ).json()
  const ws = new WebSocket(target.webSocketDebuggerUrl)
  let id = 0
  const pending = new Map()
  const events = []
  ws.addEventListener('message', (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id)
      pending.delete(msg.id)
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
    } else if (msg.method) {
      events.push(msg)
    }
  })
  await new Promise((r) => ws.addEventListener('open', r))
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const n = ++id
      pending.set(n, { resolve, reject })
      ws.send(JSON.stringify({ id: n, method, params }))
    })
  for (const m of ['Runtime.enable', 'Log.enable', 'Page.enable', 'Network.enable']) await send(m)
  await send('Emulation.setDeviceMetricsOverride', {
    width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false,
  })
  if (offline) {
    await send('Network.emulateNetworkConditions', {
      offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1,
    })
  }
  await send('Page.navigate', { url })
  await sleep(offline ? 9000 : 8000)

  const evaluate = async (expr) =>
    (await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })).result?.value
  const errors = () => ({
    exceptions: events
      .filter((e) => e.method === 'Runtime.exceptionThrown')
      .map((e) => e.params.exceptionDetails?.exception?.description || e.params.exceptionDetails?.text),
    apiErrors: events
      .filter((e) => e.method === 'Runtime.consoleAPICalled' && e.params.type === 'error')
      .map((e) => e.params.args.map((a) => a.value ?? a.description ?? '').join(' ')),
  })
  const screenshot = async (file) => {
    mkdirSync(dirname(file), { recursive: true })
    const { data } = await send('Page.captureScreenshot', { format: 'png' })
    writeFileSync(file, Buffer.from(data, 'base64'))
  }
  const close = async () => {
    ws.close()
    await fetch(`${CDP}/json/close/${target.id}`).catch(() => {})
  }
  return { send, evaluate, errors, screenshot, close }
}

// ── 1. 页面 DOM + 控制台干净 ────────────────────────────────────────────────
const pages = [
  ['/watch', `JSON.stringify({ heading: document.body.innerText.includes('盯盘'), canvas: document.querySelectorAll('canvas').length, bodyLen: document.body.innerText.length })`, (v) => v.heading && v.canvas > 0 && v.bodyLen > 500],
  ['/chart/BTC-USD', `JSON.stringify({ symbol: document.body.innerText.includes('BTC-USD'), canvas: document.querySelectorAll('canvas').length })`, (v) => v.symbol && v.canvas > 0],
  ['/stats', `JSON.stringify({ heading: /统计/.test(document.body.innerText), rows: document.querySelectorAll('table tr').length })`, (v) => v.heading && v.rows >= 2],
  ['/library', `JSON.stringify({ heading: /指标/.test(document.body.innerText), rows: document.querySelectorAll('table tr').length })`, (v) => v.heading && v.rows >= 5],
  ['/lab', `JSON.stringify({ heading: /实验/.test(document.body.innerText), inputs: document.querySelectorAll('input,textarea').length })`, (v) => v.heading],
]

for (const [path, expr, ok] of pages) {
  const s = await session(`${BASE}${path}`)
  const dom = JSON.parse((await s.evaluate(expr)) ?? '{}')
  const { exceptions, apiErrors } = s.errors()
  check(`${path} 渲染`, ok(dom), JSON.stringify(dom))
  check(`${path} 无 JS 异常`, exceptions.length === 0, exceptions.join(' | '))
  check(`${path} 无 API 报错`, apiErrors.length === 0, apiErrors.slice(0, 2).join(' | '))
  if (SHOT_DIR) await s.screenshot(`${SHOT_DIR}/${path.replaceAll('/', '_').replace(/^_/, '') || 'root'}.png`)
  await s.close()
}

// ── 2. 练习页交互：进场 → 加仓 → 平仓 ───────────────────────────────────────
{
  const s = await session(`${BASE}/practice`)
  const click = (label) =>
    s.evaluate(`(() => { const b=[...document.querySelectorAll('button')].filter(x=>x.innerText.trim()===${JSON.stringify(label)})[0]; if(!b) return 'MISSING'; b.click(); return 'ok'; })()`)
  const snapshot = () =>
    s.evaluate(`JSON.stringify({
      legs: document.body.innerText.match(/(\\d+)\\s*条腿/)?.[1] ?? null,
      weight: document.body.innerText.match(/权重 (\\d+)/)?.[1] ?? null,
      stats: (document.body.innerText.match(/已揭晓[^\\n]*/) ?? [''])[0],
      results: [...document.body.innerText.matchAll(/结果：[^\\n]+/g)].map(m => m[0]).length,
    })`)

  check('练习页 进场按钮存在', (await click('进场')) === 'ok')
  await sleep(1200)
  let snap = JSON.parse(await snapshot())
  check('练习页 进场后 1 条腿', snap.legs === '1', JSON.stringify(snap))

  check('练习页 加仓按钮存在', (await click('加仓')) === 'ok')
  await sleep(1200)
  snap = JSON.parse(await snapshot())
  check('练习页 加仓后 2 条腿且权重 2', snap.legs === '2' && snap.weight === '2', JSON.stringify(snap))

  check('练习页 平仓结算可用', (await click('平仓结算')) === 'ok')
  await sleep(1200)

  // 揭晓结果：口径 = r>0 记胜、截尾单列
  for (let i = 0; i < 8; i++) {
    const clicked = await click('揭晓结果')
    if (clicked === 'MISSING') break
    await sleep(600)
  }
  await sleep(1200)
  snap = JSON.parse(await snapshot())
  check('练习页 揭晓结果可点击', snap.results > 0, JSON.stringify(snap))
  check(
    '练习页 揭晓统计含截尾口径',
    /已揭晓 \d+\/\d+ · 胜 \d+ 负 \d+/.test(snap.stats),
    snap.stats,
  )
  const { exceptions, apiErrors } = s.errors()
  check('练习页 无 JS 异常', exceptions.length === 0, exceptions.join(' | '))
  check('练习页 无 API 报错', apiErrors.length === 0, apiErrors.slice(0, 2).join(' | '))
  if (SHOT_DIR) await s.screenshot(`${SHOT_DIR}/practice-interaction.png`)
  await s.close()
}

// ── 3. PWA：manifest + 安装性 + Service Worker ─────────────────────────────
{
  const s = await session(`${BASE}/practice`)
  const manifest = await s.send('Page.getAppManifest').catch((e) => ({ errors: [{ message: String(e) }] }))
  const installability = await s.send('Page.getInstallabilityErrors').catch(() => ({ installabilityErrors: [] }))
  const sw = JSON.parse(
    (await s.evaluate(`(async () => {
      const regs = await navigator.serviceWorker.getRegistrations();
      return JSON.stringify({ count: regs.length, active: !!regs[0]?.active, controller: !!navigator.serviceWorker.controller,
        manifestHref: document.querySelector('link[rel=manifest]')?.getAttribute('href') ?? null });
    })()`)) ?? '{}',
  )
  check('PWA manifest 可解析', (manifest.errors ?? []).length === 0, JSON.stringify(manifest.errors ?? []))
  check('PWA 无安装性错误', (installability.installabilityErrors ?? []).length === 0, JSON.stringify(installability.installabilityErrors ?? []))
  check('Service Worker 已注册并激活', sw.count >= 1 && sw.active && sw.controller, JSON.stringify(sw))
  check('页面声明 manifest', sw.manifestHref === '/manifest.webmanifest', String(sw.manifestHref))
  await s.close()
}

// ── 4. 断网回退：/practice 仍应渲染 ────────────────────────────────────────
{
  const s = await session(`${BASE}/practice`, { offline: true })
  const dom = JSON.parse(
    (await s.evaluate(`JSON.stringify({ render: document.body.innerText.includes('练习'), swControlled: !!navigator.serviceWorker.controller })`)) ?? '{}',
  )
  check('断网后 /practice 仍渲染', dom.render === true, JSON.stringify(dom))
  check('断网页面由 Service Worker 接管', dom.swControlled === true, JSON.stringify(dom))
  if (SHOT_DIR) await s.screenshot(`${SHOT_DIR}/offline-practice.png`)
  await s.close()
}

for (const r of results) console.log(`${r.ok ? '✔' : '✘'} ${r.name}${r.ok ? '' : ` — ${r.detail}`}`)
console.log(`\n${results.length - failures.length}/${results.length} 通过`)
if (failures.length) {
  console.error(`\n失败项:\n- ${failures.join('\n- ')}`)
  process.exit(1)
}
