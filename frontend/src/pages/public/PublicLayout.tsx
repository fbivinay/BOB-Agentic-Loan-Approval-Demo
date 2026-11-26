import type { ReactNode } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { LogIn } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Logo } from '@/components/Logo'
import { cn } from '@/lib/utils'
import { PUBLIC_LINKS } from '@/pages/SignIn'

/** The public pages (assignment D2–D4): readable without signing in. */
export function PublicLayout({ eyebrow, title, lede, children }: { eyebrow: string; title: string; lede: ReactNode; children: ReactNode }) {
  return (
    <div className="min-h-screen">
      <header className="masthead">
        <div className="shell masthead__inner justify-between">
          <Link to="/approach" aria-label="Baroda Lending Desk, approach"><Logo /></Link>
          <nav className="flex items-center gap-1" aria-label="About the demo">
            {PUBLIC_LINKS.map((l) => (
              <NavLink key={l.to} to={l.to}
                className={({ isActive }) => cn('rounded-full px-3 py-1.5 text-sm transition-colors', isActive ? 'bg-brand-tint font-medium text-brand' : 'text-ink-2 hover:text-ink')}>
                {l.label}
              </NavLink>
            ))}
            <Button asChild size="sm" className="ml-2"><Link to="/sign-in"><LogIn /> Sign in</Link></Button>
          </nav>
        </div>
      </header>
      <main className="shell space-y-8 py-10">
        <div className="space-y-8">
          <header className="page-head">
            <p className="eyebrow">{eyebrow}</p>
            <h1 className="display mt-1.5">{title}</h1>
            <div className="lede max-w-3xl">{lede}</div>
          </header>
          {children}
        </div>
      </main>
      <footer className="border-t border-line py-6">
        <p className="shell text-xs text-muted-foreground">
          Baroda Lending Desk (Demo). Synthetic data only. Not a Bank of Baroda service, not affiliated with Bank of Baroda, and not a loan offer.
        </p>
      </footer>
    </div>
  )
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="section-head">{title}</h2>
      {children}
    </section>
  )
}
