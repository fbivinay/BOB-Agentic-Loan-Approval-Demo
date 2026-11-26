import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { AlertCircle, Bot, ClipboardCheck, History, Loader2, UserRound } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { PasswordInput } from '@/components/ui/password-input'
import { Mark } from '@/components/Logo'
import { SESSION_ENDED, warmUp } from '@/lib/api'
import { DEMO_ROLES, ROLE_LABEL, useSession } from '@/lib/session'

const FEATURES = [
  { icon: ClipboardCheck, title: 'AI agents read and check documents; people verify' },
  { icon: Bot, title: 'AI recommends; a manager decides' },
  { icon: History, title: 'Every step recorded in an append-only audit trail' },
]
export const PUBLIC_LINKS = [
  { to: '/approach', label: 'Approach' },
  { to: '/architecture', label: 'Architecture' },
  { to: '/about', label: 'About' },
  { to: '/faq', label: 'FAQ' },
  { to: '/contact', label: 'Contact' },
]
const DEMO_PASSWORD = import.meta.env.VITE_DEMO_PASSWORD ?? ''
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
// Staff sign in with a username (loan.officer); Supabase Auth needs the email, so the staff domain is added. A full email also works.
const STAFF_DOMAIN = 'demo.example.com'
export const toEmail = (username: string) => {
  const u = username.trim().toLowerCase()
  return u.includes('@') ? u : `${u}@${STAFF_DOMAIN}`
}

function sessionEnded(): boolean {
  try {
    const v = sessionStorage.getItem(SESSION_ENDED) === '1'
    sessionStorage.removeItem(SESSION_ENDED)
    return v
  } catch {
    return false
  }
}

export function SignIn() {
  const { me, loading, signIn } = useSession()
  const navigate = useNavigate()
  const from = (useLocation().state as { from?: string } | null)?.from
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(() => (sessionEnded() ? 'Your session has ended. Sign in again.' : null))
  const [capsLock, setCapsLock] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [server, setServer] = useState<'checking' | 'waking' | 'ready' | 'down'>('checking')
  const passwordRef = useRef<HTMLInputElement>(null)

  // A free Render instance sleeps after 15 idle minutes: wake it while the person reads the page.
  useEffect(() => {
    const ctl = new AbortController()
    const slow = window.setTimeout(() => setServer((s) => (s === 'checking' ? 'waking' : s)), 1500)
    warmUp(ctl.signal).then((ok) => !ctl.signal.aborted && setServer(ok ? 'ready' : 'down'))
    return () => {
      ctl.abort()
      window.clearTimeout(slow)
    }
  }, [])

  if (!loading && me) return <Navigate to={from ?? '/overview'} replace />

  async function submit(who: string, emailValue: string, passwordValue: string) {
    const clean = toEmail(emailValue)
    if (!EMAIL.test(clean)) return setError('Enter your username.')
    if (!passwordValue) return setError('Enter your password.')
    setError(null)
    setBusy(who)
    try {
      await signIn(clean, passwordValue)
      navigate(from ?? '/overview', { replace: true })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Sign-in failed. Try again.')
      setBusy(null)
      requestAnimationFrame(() => passwordRef.current?.select())
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    void submit('form', email, password)
  }

  function onPasswordKey(e: KeyboardEvent<HTMLInputElement>) {
    setCapsLock(e.getModifierState('CapsLock'))
  }

  return (
    <div className="grid min-h-screen grid-cols-[7fr_5fr] bg-white">
      <div className="sticky top-4 my-4 flex h-[calc(100vh-2rem)] flex-col justify-between overflow-hidden rounded-r-[2.5rem] bg-linear-to-br from-brand to-brand-deep p-12 text-white">
        <div
          className="pointer-events-none absolute inset-0 opacity-[0.07]"
          style={{
            backgroundImage: 'linear-gradient(to right, white 1px, transparent 1px), linear-gradient(to bottom, white 1px, transparent 1px)',
            backgroundSize: '32px 32px',
          }}
        />
        {/* Sized from the panel's own width (cqw) so the title is as big as fits with "Lending Desk" on one line; the logo matches its two lines. */}
        <div className="@container relative space-y-5">
          <div className="flex items-center gap-6">
            <span className="seal-stamp relative shrink-0">
              <Mark animated className="size-[min(26cqw,16rem)] rounded-[22%] bg-white text-brand-deep shadow-xl" />
            </span>
            <h1 className="text-[min(10.5cqw,6.5rem)] leading-[1.05] font-semibold tracking-tight whitespace-nowrap">
              <span className="brand-line block">Baroda</span>
              <span className="brand-line brand-sheen block [animation-delay:120ms]">Lending Desk</span>
            </h1>
          </div>
          <span className="inline-block rounded-full bg-white/15 px-4 py-1.5 font-mono text-sm uppercase tracking-[0.12em]">Demo</span>
        </div>

        <div className="relative space-y-10">
          <div className="space-y-7">
            {FEATURES.map(({ icon: Icon, title }, i) => (
              <div key={title} className="feature-in flex items-center gap-4" style={{ animationDelay: `${600 + i * 120}ms` }}>
                <div className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-white/10">
                  <Icon className="size-6 text-white" />
                </div>
                <p className="text-[clamp(0.875rem,1.15vw,1rem)] font-medium whitespace-nowrap text-white">{title}</p>
              </div>
            ))}
          </div>
        </div>

        <p className="relative text-xs text-white/60">Demo with synthetic data. Not a Bank of Baroda service.</p>
      </div>

      <div className="flex items-center justify-center bg-white px-8 py-12">
        <div className="w-full max-w-md space-y-6">
          {server === 'waking' && (
            <div role="status" className="flex items-center gap-2 rounded-lg border border-border bg-white px-3 py-2 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" />
              Waking the server (free tier, up to a minute)…
            </div>
          )}
          {server === 'down' && (
            <div role="alert" className="flex items-center gap-2 rounded-lg border border-problem/30 bg-problem-bg px-3 py-2 text-xs text-problem">
              <AlertCircle className="size-3.5" />
              The server is not answering. Try again in a minute.
            </div>
          )}

          <div className="space-y-6 rounded-[1.75rem] border-2 border-brand/70 bg-white p-8 shadow-sm">
            <div className="space-y-1.5 text-center">
              <h2 className="text-xl font-semibold tracking-tight">Sign in</h2>
              <p className="text-sm text-muted-foreground">Pick a demo role, or sign in with a staff username and password.</p>
            </div>

            <div className="grid gap-2">
              {DEMO_ROLES.map((r) => (
                <button
                  key={r.role}
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void submit(r.role, r.email, DEMO_PASSWORD)}
                  className="flex items-center gap-3 rounded-lg border border-border px-3 py-2.5 text-left transition-colors hover:border-brand/50 hover:bg-brand-tint disabled:opacity-60"
                >
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-brand-tint text-brand">
                    {busy === r.role ? <Loader2 className="size-4 animate-spin" /> : <UserRound className="size-4" />}
                  </span>
                  <span className="text-sm font-medium">{ROLE_LABEL[r.role]}</span>
                </button>
              ))}
            </div>

            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              <span className="h-px flex-1 bg-border" />
              or
              <span className="h-px flex-1 bg-border" />
            </div>

            <form className="space-y-4" onSubmit={onSubmit} noValidate>
              <div className="space-y-1.5">
                <Label htmlFor="username">Username</Label>
                <Input id="username" type="text" autoComplete="username" autoCapitalize="none" spellCheck={false}
                  disabled={busy !== null} value={email}
                  onChange={(e) => setEmail(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="password">Password</Label>
                <PasswordInput ref={passwordRef} id="password" autoComplete="current-password" disabled={busy !== null} value={password}
                  onChange={(e) => setPassword(e.target.value)} onKeyDown={onPasswordKey} onKeyUp={onPasswordKey} onBlur={() => setCapsLock(false)} />
                {capsLock && <p className="text-xs text-watch">Caps Lock is on.</p>}
              </div>
              {error && (
                <div role="alert" className="flex items-start gap-2 rounded-md border border-problem/30 bg-problem-bg px-3 py-2 text-xs text-problem">
                  <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
                  <span>{error}</span>
                </div>
              )}
              <Button type="submit" className="w-full gap-2 rounded-lg hover:scale-100" disabled={busy !== null}>
                {busy === 'form' && <Loader2 className="size-4 animate-spin" />}
                {busy === 'form' ? 'Signing in…' : 'Sign in'}
              </Button>
            </form>
          </div>

          <div className="text-center text-xs text-muted-foreground">
            <p className="flex flex-wrap justify-center gap-x-3 gap-y-1">
              {PUBLIC_LINKS.map((l) => (
                <Link key={l.to} to={l.to} className="underline-offset-4 hover:text-brand hover:underline">
                  {l.label}
                </Link>
              ))}
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
