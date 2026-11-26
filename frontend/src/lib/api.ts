import { supabase } from './supabase'
import { errorMessage } from './errors'

export { errorMessage }
export const API_URL = String(import.meta.env.VITE_API_URL ?? '').replace(/\/$/, '')
const TIMEOUT_MS = 90_000 // a free Render instance can take about a minute to wake
export const SESSION_ENDED = 'bld-session-ended'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type Init = { method?: string; body?: unknown; file?: File; signal?: AbortSignal }

/** Calls the backend as the signed-in member of staff. Throws ApiError with a sentence fit to show. */
export async function api<T>(path: string, init: Init = {}): Promise<T> {
  const token = (await supabase.auth.getSession()).data.session?.access_token
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`
  let body: BodyInit | undefined
  if (init.file) {
    body = init.file
    headers['Content-Type'] = init.file.type || 'application/octet-stream'
  } else if (init.body !== undefined) {
    body = JSON.stringify(init.body)
    headers['Content-Type'] = 'application/json'
  }
  const timeout = AbortSignal.timeout(TIMEOUT_MS)
  let res: Response
  try {
    res = await fetch(`${API_URL}${path}`, {
      method: init.method ?? (body ? 'POST' : 'GET'), headers, body,
      signal: init.signal ? AbortSignal.any([init.signal, timeout]) : timeout,
    })
  } catch (e) {
    if (init.signal?.aborted) throw e
    throw new ApiError(0, errorMessage(0))
  }
  if (res.status === 204) return undefined as T
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    if (res.status === 401) {
      try { sessionStorage.setItem(SESSION_ENDED, '1') } catch { /* private mode */ }
      void supabase.auth.signOut()
    }
    throw new ApiError(res.status, errorMessage(res.status, data?.detail))
  }
  return data as T
}

/** Resolves once the backend answers /health (it may be waking up). */
export async function warmUp(signal: AbortSignal): Promise<boolean> {
  const until = Date.now() + TIMEOUT_MS
  while (!signal.aborted && Date.now() < until) {
    try {
      const r = await fetch(`${API_URL}/health`, { signal: AbortSignal.any([signal, AbortSignal.timeout(20_000)]) })
      if (r.ok) return true
    } catch { /* still waking */ }
    await new Promise((ok) => setTimeout(ok, 2000))
  }
  return false
}
