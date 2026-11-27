import { ExternalLink, Loader2, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { AiPill, Banner } from '@/components/Labels'
import { Pill, StatusPill } from '@/components/StatusPill'
import { Timeline } from '@/components/Timeline'
import { api } from '@/lib/api'
import { useAction, useApi } from '@/lib/useApi'
import { can, useSession } from '@/lib/session'
import { dateTimeIN, inr, months } from '@/lib/format'
import { statusLabel } from '@/lib/status'
import type { Assessment, Citation } from '@/lib/types'
import type { TabProps } from '@/pages/ApplicationDetail'

const STEP: Record<string, string> = {
  load_application: 'Loaded the application', load_credit_record: 'Read the bank, bureau and KYC record', gather_evidence: 'Gathered income and obligations',
  evaluate_policy: 'Ran the policy rules', retrieve_policy: 'Found the policy passages', write_summary: 'Wrote the explanation',
}
const compact = (o: Record<string, unknown>) =>
  Object.entries(o).filter(([k]) => k !== 'at' && k !== 'step')
    .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${Array.isArray(v) ? v.join(', ') : typeof v === 'object' && v ? JSON.stringify(v) : String(v)}`).join(' · ')

function Cite({ c }: { c: Citation }) {
  return (
    <li className="space-y-1 rounded-lg border border-line bg-white p-3">
      <a href={c.url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs font-medium text-brand hover:underline">
        {c.title}{c.section && ` · ${c.section}`} <ExternalLink className="size-3" />
      </a>
      <p className="text-[0.7rem] leading-relaxed [overflow-wrap:anywhere] text-ink-2">{c.quote.length > 320 ? `${c.quote.slice(0, 320)}…` : c.quote}</p>
      <p className="text-[0.62rem] text-muted-foreground">{c.publisher} · version {c.version} · fetched {dateTimeIN(c.fetched_at.replace(' ', 'T'))}</p>
    </li>
  )
}

export function AssessmentTab({ app, reload }: TabProps) {
  const { me } = useSession()
  const run = useAction()
  const { data: a, error, reload: again } = useApi<Assessment>(`/applications/${app.id}/assessment?v=${app.version}`, { pollMs: 4000, until: (x) => x.state !== 'PENDING' })
  if (error) return <Banner tone="problem">{error.message}</Banner>
  if (!a) return <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Loading the assessment…</div>

  if (a.state === 'PENDING') {
    return (
      <section className="card flex items-center gap-3">
        <Loader2 className="size-5 animate-spin text-brand" />
        <div>
          <p className="text-sm font-medium">Assessment running</p>
        </div>
      </section>
    )
  }
  if (a.state !== 'RECORDED' || !a.run?.output) {
    return (
      <section className="card space-y-3">
        {a.state === 'FAILED'
          ? <Banner tone="problem">The assessment stopped after {a.job?.attempts} attempts: {a.job?.last_error ?? 'no detail recorded'}</Banner>
          : <p className="text-sm text-muted-foreground">The assessment starts when the Loan Officer attests that the documents are complete and verified.</p>}
        {a.state === 'FAILED' && app.status === 'DOCS_VERIFIED' && can(me, 'application.forward') && (
          <Button onClick={() => run(() => api(`/applications/${app.id}/assessment`, { body: { expected_version: app.version } }), { ok: 'Assessment queued again', onDone: () => { again(); reload() } })}>
            <RotateCcw /> Run again
          </Button>
        )}
      </section>
    )
  }

  const o = a.run.output
  const citations = [...Object.entries(o.citations.by_rule), ...Object.entries(o.citations.terms)].filter(([, cs]) => cs.length)
  const income = o.evidence.income as { monthly?: string; months?: number; source?: string } | undefined
  const obligations = o.evidence.obligations as { monthly?: string; source?: string } | undefined

  return (
    <div className="space-y-5">
      <section className="card space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="section-head">Recommendation</h2>
          <AiPill recommendation={o.recommendation} />
          <Pill>{o.segment}</Pill>
          <span className="text-xs text-muted-foreground">{o.summary.source === 'GEMINI' ? `explained by ${o.summary.model}` : 'explained by a fixed template'} · {dateTimeIN(a.run.finished_at)}</span>
        </div>
        <p className="text-sm leading-relaxed">{o.summary.text}</p>
        {o.summary.points?.length > 0 && <ul className="list-disc space-y-1 pl-5 text-sm text-ink-2">{o.summary.points.map((p) => <li key={p}>{p}</li>)}</ul>}
      </section>

      <div className="grid grid-cols-3 gap-4">
        <section className="card space-y-1.5">
          <p className="text-xs text-muted-foreground">Income used</p>
          <p className="text-lg font-semibold tabular">{inr(income?.monthly ?? null)} a month</p>
          <p className="text-xs text-muted-foreground">{income?.source}{income?.months ? ` · ${income.months} months` : ''}</p>
        </section>
        <section className="card space-y-1.5">
          <p className="text-xs text-muted-foreground">Existing obligations</p>
          <p className="text-lg font-semibold tabular">{inr(obligations?.monthly ?? null)} a month</p>
          <p className="text-xs text-muted-foreground">{obligations?.source}</p>
        </section>
        <section className="card space-y-1.5">
          <p className="text-xs text-muted-foreground">Eligible terms</p>
          <p className="text-lg font-semibold tabular">
            {a.eligible_terms ? `${inr(a.eligible_terms.amount)} · ${(a.eligible_terms.rate_bp / 100).toFixed(2)}% · ${months(a.eligible_terms.tenure_months)}` : '—'}
          </p>
        </section>
      </div>

      {(o.credit_profile || o.risk) && (
        <div className="grid grid-cols-2 gap-4">
          {o.credit_profile && (
            <section className="card space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="section-head">Credit profile</h2>
                <Pill tone="watch">DEMO / SIMULATED</Pill>
              </div>
              <p className="text-2xl font-semibold tabular">{o.credit_profile.score ?? 'No score'}</p>
              <p className="text-xs text-muted-foreground">
                {statusLabel(o.credit_profile.history_status)} credit history · {o.credit_profile.source === 'SYNTHETIC_BUREAU' ? 'from the synthetic bureau record' : 'simulated from the PAN'}
                {' '}· valid {o.credit_profile.period_start} to {o.credit_profile.valid_until}
              </p>
            </section>
          )}
          {o.risk && (
            <section className="card space-y-2">
              <div className="flex items-center gap-2">
                <h2 className="section-head">Risk category</h2>
                <Pill tone={o.risk.level === 'LOW' ? 'good' : o.risk.level === 'HIGH' ? 'problem' : 'watch'}>{o.risk.level}</Pill>
              </div>
              <ul className="list-disc space-y-1 pl-5 text-sm text-ink-2">{o.risk.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
              <p className="text-xs text-muted-foreground">Basis: {o.risk.basis}</p>
            </section>
          )}
        </div>
      )}

      <section className="data-table-wrap">
        <table className="data-table">
          <thead><tr><th>Rule</th><th>Result</th><th>What was found</th><th>Figure basis</th></tr></thead>
          <tbody>
            {o.assessment.rules.map((r) => (
              <tr key={r.id}>
                <td className="whitespace-normal!"><span className="font-mono text-[0.65rem] text-muted-foreground">{r.id}</span> {r.title}</td>
                <td><StatusPill status={r.status} /></td>
                <td className="whitespace-normal! text-ink-2">{r.message}</td>
                <td><Pill tone={r.confidence === 'VERIFIED' ? 'good' : 'neutral'}>{r.confidence}</Pill></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <div className="grid grid-cols-2 gap-4">
        <section className="card space-y-3">
          <h2 className="section-head">Policy passages cited</h2>
          {citations.length === 0 ? <p className="text-sm text-muted-foreground">No passages were close enough to cite.</p> : citations.map(([k, cs]) => (
            <div key={k} className="space-y-1.5">
              <p className="text-xs font-medium">{/^R\d+$/.test(k) ? `Rule ${k}` : k}</p>
              <ul className="space-y-2">{cs.map((c) => <Cite key={c.chunk_id} c={c} />)}</ul>
            </div>
          ))}
        </section>
        <section className="card space-y-3">
          <h2 className="section-head">What the agent did</h2>
          <Timeline items={a.run.steps.map((s) => ({ at: s.at, title: STEP[s.step] ?? statusLabel(s.step.toUpperCase()), note: compact(s) }))} />
          <p className="text-xs text-muted-foreground">Credit record: {o.credit_lookup.source}, matched by {o.credit_lookup.matched_by}.</p>
        </section>
      </div>
    </div>
  )
}
