import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { api, ApiError } from './api'
import { cached, cacheGeneration, remember } from './cache'

/** Reads `path`; `reload()` reads it again without blanking what is on screen. With `pollMs`, re-reads on that
 *  interval until `until(data)` is true (for work the backend finishes in the background). */
export function useApi<T>(path: string | null, opts: { pollMs?: number; until?: (d: T) => boolean } = {}) {
  const [data, setData] = useState<T | undefined>(() => (path === null ? undefined : cached<T>(path)))
  const [error, setError] = useState<ApiError>()
  const [loading, setLoading] = useState(path !== null)
  const [nonce, setNonce] = useState(0)
  const until = useRef(opts.until)
  until.current = opts.until
  const reload = useCallback(() => setNonce((n) => n + 1), [])
  const polled = useRef(false)

  // A different resource (the part before '?') must never show the previous one's data, or its buttons would act on
  // it: clear on change. A changed query (filters, ?v=version) keeps what is on screen until the new answer arrives.
  const resource = path?.split('?')[0] ?? null
  const shown = useRef(resource)
  if (shown.current !== resource) {
    shown.current = resource
    const saved = path === null ? undefined : cached<T>(path)
    if (data !== saved) setData(saved)
    if (error !== undefined) setError(undefined)
  }

  useEffect(() => {
    if (path === null) return
    const ctl = new AbortController()
    setLoading(true)
    const gen = cacheGeneration()
    const quiet = polled.current // a poll re-reads in the background: no progress bar every few seconds
    polled.current = false
    api<T>(path, { signal: ctl.signal, quiet })
      .then((d) => { remember(path, d, gen); if (!ctl.signal.aborted) { setData(d); setError(undefined) } })
      .catch((e) => { if (!ctl.signal.aborted) setError(e instanceof ApiError ? e : new ApiError(0, String(e))) })
      .finally(() => { if (!ctl.signal.aborted) setLoading(false) })
    return () => ctl.abort()
  }, [path, nonce])

  const done = data !== undefined && (!until.current || until.current(data))
  useEffect(() => {
    if (path === null || !opts.pollMs || done || data === undefined) return
    const id = window.setTimeout(() => { polled.current = true; reload() }, opts.pollMs)
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
