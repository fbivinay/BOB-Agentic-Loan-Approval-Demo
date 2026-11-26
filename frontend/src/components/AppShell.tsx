import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { LogOut, Settings } from 'lucide-react'
import { Suspense, useEffect, useRef, useState } from 'react'
import { Logo } from '@/components/Logo'
import { Skeleton } from '@/components/ui/skeleton'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { navFor } from '@/lib/nav'
import { ROLE_LABEL, useSession } from '@/lib/session'
import { cn } from '@/lib/utils'

const link = ({ isActive }: { isActive: boolean }) =>
  cn('flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors',
    isActive ? 'bg-brand-tint font-medium text-brand' : 'text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900')

// A page that is still loading: a title and a couple of blocks, where the real ones will be.
export function PageSkeleton() {
  return (
    <div className="space-y-5" aria-hidden="true">
      <div className="space-y-2 pt-1 pb-3">
        <Skeleton className="h-3 w-32" />
        <Skeleton className="h-9 w-64" />
      </div>
      <Skeleton className="h-36 w-full rounded-2xl" />
      <Skeleton className="h-64 w-full rounded-2xl" />
    </div>
  )
}

// The frame with nothing in it yet, while the session is read on a reload: same sidebar and inset panel, so nothing jumps.
export function ShellSkeleton() {
  return (
    <div className="flex h-dvh overflow-hidden bg-white" aria-hidden="true">
      <aside className="fixed inset-y-0 left-0 flex w-60 flex-col">
        <div className="flex h-16 items-center px-5"><Logo /></div>
        <div className="flex-1 space-y-2 p-3">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-8 w-full rounded-lg" />)}
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col py-4 pr-4 pl-60">
        <div className="min-h-0 flex-1 overflow-hidden rounded-2xl border border-border bg-ground">
          <div className="mx-auto w-full max-w-7xl p-6"><PageSkeleton /></div>
        </div>
      </div>
    </div>
  )
}

// Desktop and laptop only: a fixed sidebar and the page as an inset panel that scrolls on its own.
export function AppShell() {
  const { me, signOut } = useSession()
  const navigate = useNavigate()
  const [confirm, setConfirm] = useState(false)
  const panel = useRef<HTMLDivElement>(null)
  const { pathname } = useLocation()
  useEffect(() => { panel.current?.scrollTo({ top: 0 }) }, [pathname])
  if (!me) return null

  async function leave() {
    setConfirm(false)
    await signOut()
    navigate('/sign-in', { replace: true })
  }

  return (
    <div className="flex h-dvh overflow-hidden bg-white">
      <aside className="fixed inset-y-0 left-0 z-20 flex w-60 flex-col">
        <div className="flex h-16 items-center px-5"><Logo /></div>
        <div className="mx-3 mb-1 rounded-lg border border-border/70 px-3 py-2">
          <p className="truncate text-sm font-medium">{me.full_name}</p>
          <p className="text-xs text-muted-foreground">{ROLE_LABEL[me.role]}</p>
        </div>
        <nav className="min-h-0 flex-1 space-y-0.5 overflow-y-auto p-3" aria-label="Sections">
          {navFor(me.permissions).map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={link}>
              <Icon className="size-4" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="space-y-0.5 p-3">
          <NavLink to="/settings" className={link}>
            <Settings className="size-4" />
            Settings
          </NavLink>
          <button type="button" onClick={() => setConfirm(true)} className={link({ isActive: false }) + ' w-full'}>
            <LogOut className="size-4" />
            Sign out
          </button>
        </div>
      </aside>

      <Dialog open={confirm} onOpenChange={setConfirm}>
        <DialogContent className="sm:max-w-xs">
          <DialogHeader>
            <DialogTitle>Sign out?</DialogTitle>
            <DialogDescription>You'll need to sign in again to continue.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(false)}>Cancel</Button>
            <Button onClick={leave}>Sign out</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <div className="flex min-w-0 flex-1 flex-col py-4 pr-4 pl-60">
        <div className="min-h-0 flex-1 overflow-hidden rounded-2xl border border-border bg-ground">
          <div ref={panel} data-scroll-root className="h-full overflow-y-auto">
            <main className="mx-auto w-full max-w-7xl p-6 pb-16">
              <Suspense fallback={<PageSkeleton />}>
                <Outlet />
              </Suspense>
            </main>
          </div>
        </div>
      </div>
    </div>
  )
}
