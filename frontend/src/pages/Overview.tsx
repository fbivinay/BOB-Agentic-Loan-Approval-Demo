import { ROLE_LABEL, useSession } from '@/lib/session'

// Interim overview; the role-specific figures and queues arrive with the analytics work.
export function Overview() {
  const { me } = useSession()
  if (!me) return null
  return (
    <div>
      <header className="page-head">
        <p className="eyebrow">Today</p>
        <h1 className="display">Welcome, {me.full_name.split(' ')[0]}</h1>
        <p className="lede">Signed in as {ROLE_LABEL[me.role]}.</p>
      </header>
    </div>
  )
}
