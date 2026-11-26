import { Link, useParams } from 'react-router-dom'
import { CheckCheck, ExternalLink } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { PageHead } from '@/components/PageHead'
import { PageSkeleton } from '@/components/AppShell'
import { ReasonDialog } from '@/components/Dialogs'
import { Banner } from '@/components/Labels'
import { StatusPill } from '@/components/StatusPill'
import { Need } from '@/components/ProtectedRoute'
import { api } from '@/lib/api'
import { useAction, useApi } from '@/lib/useApi'
import { can, useSession } from '@/lib/session'
import { dateTimeIN } from '@/lib/format'
import type { Fact, ReportDetail as Report } from '@/lib/types'

function FactLine({ f }: { f: Fact }) {
  return (
    <li className="space-y-0.5 rounded-md bg-white px-2.5 py-1.5">
      <p className="text-xs"><span className="font-mono text-[0.65rem] text-muted-foreground">{f.key}</span> = <strong>{f.value}</strong>{f.condition && <span className="text-muted-foreground"> · when {f.condition}</span>}</p>
      <p className="text-[0.68rem] [overflow-wrap:anywhere] text-ink-2">“{f.quote}”</p>
    </li>
  )
}

export function ReportDetail() {
  return (
    <Need any={['policy.view']}>
      <Detail />
    </Need>
  )
}

function Detail() {
  const { id } = useParams()
  const { me } = useSession()
  const run = useAction()
  const { data: r, error, reload } = useApi<Report>(`/policy/reports/${id}`)
  if (error) return <Banner tone="problem">{error.message}</Banner>
  if (!r) return <PageSkeleton />
  const a = r.layer_a
  const b = r.layer_b

  return (
    <div className="space-y-5">
      <PageHead eyebrow="Policy · change report" title={r.title}
        lede={<span className="inline-flex flex-wrap items-center gap-2"><StatusPill status={r.classification} /><StatusPill status={r.status} />
          <span>{r.publisher} · {r.from_version ? `version ${r.from_version} → ${r.to_version}` : `first reading (version ${r.to_version})`} · found {dateTimeIN(r.created_at)}</span></span>}
        actions={
          <>
            <Button variant="outline" asChild><a href={r.url} target="_blank" rel="noreferrer"><ExternalLink /> Official page</a></Button>
            {r.status === 'NEW' && can(me, 'policy.activate') && (
              <ReasonDialog trigger={<Button><CheckCheck /> Mark reviewed</Button>} title="Mark this report reviewed"
                description="The compliance checklist step. Nothing about the rules changes here." label="Review note" min={10} confirmLabel="Mark reviewed"
                onConfirm={(note) => run(() => api(`/policy/reports/${r.id}/review`, { body: { note } }), { ok: 'Report marked reviewed', onDone: reload })} />
            )}
          </>
        } />
      {r.review_note && <Banner tone="good">Reviewed {dateTimeIN(r.reviewed_at)}: {r.review_note}</Banner>}

      <div className="grid grid-cols-3 gap-4">
        <section className="card space-y-2">
          <h2 className="section-head">A. What changed in the text</h2>
          {!a.changed ? <p className="text-xs text-muted-foreground">{a.note ?? 'The text is unchanged.'}</p> : (
            <>
              <p className="text-xs">{a.added_lines} line(s) added, {a.removed_lines} removed</p>
              <ul className="space-y-1">
                {a.samples?.map((s, i) => (
                  <li key={i} className={s.change === 'added' ? 'rounded bg-good-bg px-2 py-1 text-[0.68rem] text-good' : 'rounded bg-problem-bg px-2 py-1 text-[0.68rem] text-problem line-through'}>
                    <span className="font-medium">{s.section}:</span> {s.text}
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
        <section className="card space-y-2">
          <h2 className="section-head">B. Facts read from it</h2>
          {b.note && <p className="text-xs text-muted-foreground">{b.note}</p>}
          {b.baseline && <p className="text-xs font-medium text-ink-2">First reading of this page: these facts are the baseline.</p>}
          <ul className="space-y-1.5">
            {[...(b.added ?? []), ...(b.current ?? [])].map((f, i) => <FactLine key={`${f.key}-${i}`} f={f} />)}
            {b.changed?.map((c) => <FactLine key={c.key} f={{ ...c.after, value: `${c.before.value} → ${c.after.value}` }} />)}
          </ul>
        </section>
        <section className="card space-y-2">
          <h2 className="section-head">C. What it means for the rules</h2>
          {r.layer_c.length === 0 && <p className="text-xs text-muted-foreground">No rule is affected.</p>}
          <ul className="space-y-2">
            {r.layer_c.map((c, i) => (
              <li key={i} className="space-y-1 rounded-md bg-white px-2.5 py-2 text-xs">
                <p className="flex flex-wrap items-center gap-1.5"><StatusPill status={c.kind} />{c.parameter && <span className="font-mono text-[0.65rem]">{c.parameter}</span>}</p>
                {c.parameter && <p>Demo value <strong>{String(c.current_value)}</strong> ({c.current_confidence}) · official <strong>{c.official_value ?? c.value}</strong>{c.condition && ` when ${c.condition}`}</p>}
                {c.note && <p className="text-ink-2">{c.note}</p>}
              </li>
            ))}
          </ul>
          {r.proposals.length > 0 && (
            <p className="text-xs">Proposals: {r.proposals.map((p) => <Link key={p.id} to="/policy/proposals" className="mr-2 text-brand hover:underline">{p.parameter} ({p.status.toLowerCase()})</Link>)}</p>
          )}
        </section>
      </div>
    </div>
  )
}
