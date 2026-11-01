'use client'

import { useEffect, useState } from 'react'
import { PWA_SERVICE_WORKER_PATH, isStandalone } from '@/lib/pwa'

/** 注册 service worker 并显示安装态/离线状态；失败静默（不影响主流程）。 */
export default function PwaRegister() {
  const [offline, setOffline] = useState(false)
  const [standalone, setStandalone] = useState(false)

  useEffect(() => {
    if (typeof navigator === 'undefined') return
    setOffline(!navigator.onLine)
    setStandalone(
      isStandalone(
        window.matchMedia?.('(display-mode: standalone)').matches ?? false,
        (navigator as unknown as { standalone?: boolean }).standalone,
      ),
    )

    const onOnline = () => setOffline(false)
    const onOffline = () => setOffline(true)
    window.addEventListener('online', onOnline)
    window.addEventListener('offline', onOffline)

    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register(PWA_SERVICE_WORKER_PATH).catch(() => {
        /* 离线壳注册失败不影响在线使用 */
      })
    }
    return () => {
      window.removeEventListener('online', onOnline)
      window.removeEventListener('offline', onOffline)
    }
  }, [])

  if (!offline && !standalone) return null
  return (
    <div className="fixed bottom-2 right-2 z-50 flex gap-1">
      {standalone && <span className="chip info">独立窗口</span>}
      {offline && <span className="chip down">离线</span>}
    </div>
  )
}
