import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from './api'
import { clearApiCache } from './cache'
import { supabase } from './supabase'

export type Role = 'LOAN_OFFICER' | 'ASSISTANT_MANAGER' | 'BRANCH_MANAGER'
export type Me = { user_id: string; full_name: string; email: string; role: Role; permissions: string[] }

export const ROLE_LABEL: Record<Role, string> = { LOAN_OFFICER: 'Loan Officer', ASSISTANT_MANAGER: 'Assistant Manager', BRANCH_MANAGER: 'Branch Manager' }

// The synthetic demo staff, as created by scripts/seed_staff.py. Not real people.
export const DEMO_ROLES: { role: Role; email: string; name: string; blurb: string }[] = [
  { role: 'LOAN_OFFICER', email: 'loan.officer@demo.example.com', name: 'Meera Iyer', blurb: 'Enquiries, intake, documents; forwards for review' },
  { role: 'ASSISTANT_MANAGER', email: 'assistant.manager@demo.example.com', name: 'Rohan Deshmukh', blurb: 'Reviews and decides; releases disbursements' },
  { role: 'BRANCH_MANAGER', email: 'branch.manager@demo.example.com', name: 'Sunita Rao', blurb: 'Decides, takes over, adjusts terms; approves policy' },
]

/** True when the person holds any of these permissions. */
export const can = (me: Me | null, ...perms: string[]) => !!me && perms.some((p) => me.permissions.includes(p))

type Session = { me: Me | null; loading: boolean; signIn: (email: string, password: string) => Promise<void>; signOut: () => Promise<void> }
const Ctx = createContext<Session | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)

  // The role and permissions always come from the backend (the database), never from the token's metadata.
  const loadMe = useCallback(async () => {
    try {
      setMe(await api<Me>('/me'))
    } catch {
      setMe(null)
    }
  }, [])

  useEffect(() => {
    let live = true
    supabase.auth.getSession().then(async ({ data }) => {
      if (data.session) await loadMe()
      if (live) setLoading(false)
    })
    // Saved answers belong to one user: drop them whenever the signed-in user changes (sign-out, or another account in any tab).
    let userId: string | undefined
    const { data: sub } = supabase.auth.onAuthStateChange((event, s) => {
      if (s?.user.id !== userId) { clearApiCache(); userId = s?.user.id }
      if (event === 'SIGNED_OUT') setMe(null)
    })
    return () => {
      live = false
      sub.subscription.unsubscribe()
    }
  }, [loadMe])

  const signIn = useCallback(async (email: string, password: string) => {
    const { error } = await supabase.auth.signInWithPassword({ email, password })
    if (error) throw new Error(error.message === 'Invalid login credentials' ? 'That username and password do not match.' : error.message)
    try {
      setMe(await api<Me>('/me'))
    } catch (e) {
      await supabase.auth.signOut()
      throw e
    }
  }, [])

  const signOut = useCallback(async () => {
    await supabase.auth.signOut()
    setMe(null)
  }, [])

  return <Ctx.Provider value={{ me, loading, signIn, signOut }}>{children}</Ctx.Provider>
}

export function useSession(): Session {
  const s = useContext(Ctx)
  if (!s) throw new Error('useSession outside SessionProvider')
  return s
}
