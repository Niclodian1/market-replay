import { Suspense } from 'react'
import { Rail } from '@/components/layout/Rail'
import { Topbar } from '@/components/layout/Topbar'
import { WatchlistSide } from '@/components/layout/WatchlistSide'
import { Statusbar } from '@/components/layout/Statusbar'

export default function MainLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="mr-app">
      <Rail />
      <Suspense fallback={<div className="mr-topbar" />}>
        <Topbar />
      </Suspense>
      <WatchlistSide />
      <main className="mr-main">
        {children}
      </main>
      <Statusbar />
    </div>
  )
}
