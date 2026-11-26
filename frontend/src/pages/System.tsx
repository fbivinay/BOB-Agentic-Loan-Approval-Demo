import { Link } from 'react-router-dom'
import { ExternalLink, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { PageHead } from '@/components/PageHead'
import { PageSkeleton } from '@/components/AppShell'
import { Figure, FiguresPanel } from '@/components/Figure'
import { Banner } from '@/components/Labels'
import { StatusPill } from '@/components/StatusPill'
import { Need } from '@/components/ProtectedRoute'
import { api } from '@/lib/api'
import { useAction, useApi } from '@/lib/useApi'
import { dateTimeIN } from '@/lib/format'
import { statusLabel } from '@/lib/status'
import type { MonitorRun } from '@/lib/types'

type Health = {
  alerts: string[]
  monitor: { last_success: string | null; hours_since: number | null; stale: boolean; interval_hours: number; last_run: MonitorRun | null }
  jobs: { kind: string; status: string; count: number; oldest_queued: string | null }[]
  failed_jobs: { kind: string; error: string | null; at: string; application_id: string | null }[]
  documents: Record<string, number>
  emails: Record<string, number>
  budgets_today: Record<string, { used: number; cap: number }>
  analytics_outbox: Record<string, number>
  policy: { new_reports: number; open_proposals: number }
}
type Sync = { counts: Record<string, number>; oldest_pending: string | null; failed: { event_id: string; event_type: string; error: string | null; attempts: number; occurred_at: string }[] }

const Counts = ({ c }: { c: Record<string, number> }) =>
  Object.keys(c).length ? <div className="flex flex-wrap gap-1.5">{Object.entries(c).map(([k, n]) => <span key={k} className="inline-flex items-center gap-1 text-xs"><StatusPill status={k} />{n}</span>)}</div>
    : <p className="text-xs text-muted-foreground">None.</p>

export function System() {
  return (
    <Need any={['system.view']}>
      <Body />
    </Need>
  )
}

function Body() {
  const run = useAction()
  const { data: h, error, reload } = useApi<Health>('/system/health')
  const { data: s, reload: reloadSync } = useApi<Sync>('/system/analytics')
  if (error) return <Banner tone="problem">{error.message}</Banner>
  if (!h) return <PageSkeleton />
  const m = h.monitor
  const kinds = [...new Set(h.jobs.map((j) => j.kind))]

  return (
    <div className="space-y-6">
      <PageHead eyebrow="Behind the scenes" title="System status" lede="Background work, budgets and monitoring. Read-only, apart from retrying failed analytics events." />
      {h.alerts.length ? h.alerts.map((a) => <Banner key={a} tone="watch">{a}</Banner>) : <Banner tone="good">No alerts.</Banner>}

      <FiguresPanel cols={4}>
        <Figure label="Policy monitoring" value={m.hours_since === null ? 'Never run' : `${m.hours_since} h ago`} tone={m.stale ? 'problem' : 'good'}
          note={m.last_run ? <>last run {statusLabel(m.last_run.status).toLowerCase()}{m.last_run.actions_run_url && <> · <a className="text-brand hover:underline" href={m.last_run.actions_run_url} target="_blank" rel="noreferrer">log <ExternalLink className="inline size-3" /></a></>}</> : undefined} />
        <Figure label="Reports to review" value={String(h.policy.new_reports)} note={`${h.policy.open_proposals} open proposals`} tone={h.policy.new_reports ? 'watch' : undefined} />
        <Figure label="Failed jobs" value={String(h.failed_jobs.length)} tone={h.failed_jobs.length ? 'problem' : 'good'} note="stopped after retries" />
        <Figure label="Analytics events" value={String(s?.counts.SENT ?? 0)} note={`sent to BigQuery${s?.counts.FAILED ? ` · ${s.counts.FAILED} failed` : ''}`} tone={s?.counts.FAILED ? 'problem' : undefined} />
      </FiguresPanel>

      <div className="grid grid-cols-2 gap-4">
        <section className="card space-y-3">
          <h2 className="section-head">Jobs</h2>
          {kinds.length === 0 ? <p className="text-xs text-muted-foreground">No jobs yet.</p> : (
            <dl className="space-y-2">
              {kinds.map((k) => (
                <div key={k} className="grid grid-cols-[11rem_1fr] items-center gap-2">
                  <dt className="text-xs">{statusLabel(k.toUpperCase())}</dt>
                  <dd><Counts c={Object.fromEntries(h.jobs.filter((j) => j.kind === k).map((j) => [j.status, j.count]))} /></dd>
                </div>
              ))}
            </dl>
          )}
          {h.failed_jobs.map((f, i) => (
            <p key={i} className="text-xs text-problem">
              {statusLabel(f.kind.toUpperCase())} · {dateTimeIN(f.at)} · {f.error}{f.application_id && <> · <Link className="underline" to={`/applications/${f.application_id}`}>open</Link></>}
            </p>
          ))}
        </section>
        <section className="card space-y-3">
          <h2 className="section-head">Today's budgets (IST)</h2>
          {Object.entries(h.budgets_today).map(([k, b]) => (
            <div key={k} className="space-y-1">
              <p className="flex justify-between text-xs"><span>{k === 'gemini' ? 'Gemini calls' : k === 'resend' ? 'Emails' : 'Uploads'}</span><span className="tabular">{b.used} of {b.cap}</span></p>
              <span className="block h-1.5 rounded-full bg-line"><span className={b.used >= b.cap ? 'block h-1.5 rounded-full bg-problem' : 'block h-1.5 rounded-full bg-brand'} style={{ width: `${Math.min(100, (b.used / b.cap) * 100)}%` }} /></span>
            </div>
          ))}
        </section>
        <section className="card space-y-3">
          <h2 className="section-head">Documents waiting</h2>
          <Counts c={h.documents} />
          <h2 className="section-head pt-2">Emails</h2>
          <Counts c={h.emails} />
        </section>
        <section className="card space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="section-head">Analytics to BigQuery</h2>
            {!!s?.failed.length && (
              <Button size="sm" variant="outline" onClick={() => run(() => api<{ requeued: number }>('/system/analytics/retry', { body: {} }), { ok: 'Failed events queued again', onDone: () => { reload(); reloadSync() } })}>
                <RotateCcw /> Retry failed
              </Button>
            )}
          </div>
          {s && <Counts c={s.counts} />}
          {s?.oldest_pending && <p className="text-xs text-muted-foreground">Oldest waiting since {dateTimeIN(s.oldest_pending)}</p>}
          {s?.failed.map((f) => <p key={f.event_id} className="text-xs text-problem">{f.event_type} · {f.attempts} attempts · {f.error}</p>)}
        </section>
      </div>
    </div>
  )
}
