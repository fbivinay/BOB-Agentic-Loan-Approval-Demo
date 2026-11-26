import { CheckCircle2, CircleDashed, MinusCircle } from 'lucide-react'
import { Figure, FiguresPanel } from '@/components/Figure'
import { Banner } from '@/components/Labels'
import { Pill } from '@/components/StatusPill'
import { inr, months } from '@/lib/format'
import type { Indicative as Est } from '@/lib/types'

/** The indicative check: never a decision. The formal assessment decides. */
export function Indicative({ est }: { est: Est }) {
  const req = est.indicative_for_request
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="section-head">Indicative check</h2>
        <Pill tone={est.likely_eligible ? 'good' : 'problem'}>{est.likely_eligible ? 'Likely eligible' : 'May not be eligible'}</Pill>
        <Pill>{est.policy}</Pill>
      </div>
      <Banner tone="brand">Indicative only. The formal assessment decides. {est.disclaimer}</Banner>
      <FiguresPanel cols={4}>
        <Figure label="Looks possible for this request" value={inr(req.amount)} note={req.tenure_months ? `over ${months(req.tenure_months)}` : 'no tenure fits'} />
        <Figure label="Indicative EMI" value={req.emi_range ? `${inr(req.emi_range[0])} – ${inr(req.emi_range[1])}` : '—'} note="across the rate range" />
        <Figure label="Rate range" value={`${est.rate_range_percent[0]}% – ${est.rate_range_percent[1]}%`} note={est.rate_basis} />
        <Figure label="Highest indicative amount" value={inr(est.indicative_max_amount)}
          note={est.max_tenure_months ? `longest tenure ${months(est.max_tenure_months)}` : undefined} />
      </FiguresPanel>
      <div className="grid grid-cols-2 gap-4">
        <div className="card space-y-2">
          <p className="text-sm font-medium">How it was worked out</p>
          <dl className="grid grid-cols-2 gap-y-1 text-xs">
            <dt className="text-muted-foreground">Monthly income used</dt><dd className="tabular">{inr(est.income_used)}</dd>
            <dt className="text-muted-foreground">Existing EMIs</dt><dd className="tabular">{inr(est.existing_emis)}</dd>
            <dt className="text-muted-foreground">Segment</dt><dd className="font-mono">{est.segment_hint}</dd>
            <dt className="text-muted-foreground">Figures</dt><dd><Pill>DEMO_ASSUMPTION</Pill></dd>
          </dl>
          {est.notes.length > 0 && (
            <ul className="list-disc space-y-1 pl-4 text-xs text-ink-2">
              {est.notes.map((n) => <li key={n}>{n}</li>)}
            </ul>
          )}
        </div>
        <div className="card space-y-2">
          <p className="text-sm font-medium">Documents</p>
          <ul className="space-y-1.5 text-xs">
            {est.documents.required.map((d) => {
              const have = est.documents.available.some((a) => a.code === d.code)
              return (
                <li key={d.code} className="flex items-start gap-2">
                  {have ? <CheckCircle2 className="mt-px size-3.5 shrink-0 text-good" /> : <CircleDashed className="mt-px size-3.5 shrink-0 text-watch" />}
                  <span>{d.label}{!have && <span className="text-watch"> · still needed</span>}</span>
                </li>
              )
            })}
            {est.documents.waived.map((d) => (
              <li key={d.code} className="flex items-start gap-2 text-muted-foreground">
                <MinusCircle className="mt-px size-3.5 shrink-0" />
                <span>{d.label} · not needed (the bank already holds it)</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  )
}
