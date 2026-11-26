import type { ReactNode } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { ShieldOff } from 'lucide-react'
import { ShellSkeleton } from '@/components/AppShell'
import { can, useSession } from '@/lib/session'

/** Signed-in staff only; on a reload, hold the app's frame while the session is read. */
export function ProtectedRoute() {
  const { me, loading } = useSession()
  const { pathname } = useLocation()
  if (loading) return <ShellSkeleton />
  if (!me) return <Navigate to="/sign-in" replace state={{ from: pathname }} />
  return <Outlet />
}

/** A page for people holding any of `any`. The API refuses the rest anyway; this says so plainly instead of failing. */
export function Need({ any, children }: { any: string[]; children: ReactNode }) {
  const { me } = useSession()
  if (can(me, ...any)) return <>{children}</>
  return (
    <div className="flex flex-col items-center gap-3 py-24 text-center">
      <ShieldOff className="size-8 text-muted-foreground" />
      <p className="text-base font-medium">Your role does not have access to this page.</p>
    </div>
  )
}
