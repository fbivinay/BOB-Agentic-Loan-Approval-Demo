import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { api, apiCache, ApiError } from './api'

/** Reads `path`; `reload()` reads it again without blanking what is on screen. With `pollMs`, re-reads on that
 *  interval until `until(data)` is true (for work the backend finishes in the background). */
export function useApi<T>(path: string | null, opts: { pollMs?: number; until?: (d: T) => boolean } = {}) {
  const [data, setData] = useState<T | undefined>(() => (path === null ? undefined : (apiCache.get(path) as T | undefined)))
  const [error, setError] = useState<ApiError>()
  const [loading, setLoading] = useState(path !== null)
  const [nonce, setNonce] = useState(0)
  const until = useRef(opts.until)
  until.current = opts.until
  const reload = useCallback(() => setNonce((n) => n + 1), [])

  // A different resource (the part before '?') must never show the previous one's data, or its buttons would act on
  // it: clear on change. A changed query (filters, ?v=version) keeps what is on screen until the new answer arrives.
  const resource = path?.split('?')[0] ?? null
  const shown = useRef(resource)
  if (shown.current !== resource) {
    shown.current = resource
    const cached = path === null ? undefined : (apiCache.get(path) as T | undefined)
    if (data !== cached) setData(cached)
    if (error !== undefined) setError(undefined)
  }

  useEffect(() => {
    if (path === null) return
    const ctl = new AbortController()
    setLoading(true)
    api<T>(path, { signal: ctl.signal })
      .then((d) => { apiCache.set(path, d); if (!ctl.signal.aborted) { setData(d); setError(undefined) } })
      .catch((e) => { if (!ctl.signal.aborted) setError(e instanceof ApiError ? e : new ApiError(0, String(e))) })
      .finally(() => { if (!ctl.signal.aborted) setLoading(false) })
    return () => ctl.abort()
  }, [path, nonce])

  const done = data !== undefined && (!until.current || until.current(data))
  useEffect(() => {
    if (path === null || !opts.pollMs || done || data === undefined) return
    const id = window.setTimeout(reload, opts.pollMs)
    return () => window.clearTimeout(id)
  }, [path, opts.pollMs, done, data, reload])

  return { data, error, loading, reload }
}

/** Runs a write: toasts the outcome; on a 409 (someone changed it meanwhile) re-reads at once and offers Reload. */
export function useAction() {
  return useCallback(async <T,>(fn: () => Promise<T>, opts: { ok?: string; onDone?: () => void } = {}): Promise<T | undefined> => {
    try {
      const r = await fn()
      if (opts.ok) toast.success(opts.ok)
      opts.onDone?.()
      return r
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : 'Something went wrong. Try again.'
      if (e instanceof ApiError && e.status === 409) {
        toast.error(msg, opts.onDone ? { action: { label: 'Reload', onClick: opts.onDone } } : undefined)
        opts.onDone?.()
      } else toast.error(msg)
      return undefined
    }
  }, [])
}
