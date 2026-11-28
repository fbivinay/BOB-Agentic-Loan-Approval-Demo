import { useState } from 'react'
import { ArrowRightLeft, ArrowUpRight, CheckCircle2, Hand, Send, SlidersHorizontal, Undo2, XCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Field } from '@/components/Field'
import { ConfirmDialog, ReasonDialog } from '@/components/Dialogs'
import { AiPill, Banner } from '@/components/Labels'
import { Pill, StatusPill } from '@/components/StatusPill'
import { api } from '@/lib/api'
import { useAction, useApi } from '@/lib/useApi'
import { ROLE_LABEL, type Role } from '@/lib/session'
import { dateTimeIN, inr, months } from '@/lib/format'
import { statusLabel } from '@/lib/status'
import { checkTerms, percentToBp } from '@/lib/terms'
import type { ReviewView, TermsView } from '@/lib/types'
import type { TabProps } from '@/pages/ApplicationDetail'
import { BlockSkeleton } from '@/components/AppShell'

function Terms({ label, t }: { label: string; t: TermsView | null }) {
  return (
    <div className="rounded-lg border border-line bg-white px-3 py-2.5">
      <p className="text-xs text-muted-foreground">{label}</p>
      {t ? (
        <>
          <p className="text-sm font-semibold tabular">{inr(t.amount)} · {t.rate_percent}% · {months(t.tenure_months)}</p>
          <p className="text-xs text-muted-foreground tabular">EMI {inr(t.emi)}</p>
        </>
      ) : <p className="text-sm text-muted-foreground">—</p>}
    </div>
  )
}

function AdjustTerms({ r, onDone }: { r: ReviewView; onDone: () => void }) {
  const run = useAction()
  const c = r.terms.current ?? r.terms.eligible
  const [t, setT] = useState({ amount: c ? String(Number(c.amount)) : '', rate: c?.rate_percent ?? '', tenure: c ? String(c.tenure_months) : '' })
  const problem = checkTerms(t, r.terms.limits)
  return (
    <ReasonDialog trigger={<Button variant="outline"><SlidersHorizontal /> Adjust terms</Button>} title="Adjust the terms"
      description={r.terms.label} label="Why are the terms being changed?" min={10} confirmLabel="Save new terms"
      extra={
        <div className="space-y-2">
          <div className="grid grid-cols-3 gap-3">
            <Field label="Amount (₹)" htmlFor="t-amount"><Input id="t-amount" inputMode="numeric" value={t.amount} onChange={(e) => setT({ ...t, amount: e.target.value })} /></Field>
            <Field label="Rate (% a year)" htmlFor="t-rate"><Input id="t-rate" inputMode="decimal" value={t.rate} onChange={(e) => setT({ ...t, rate: e.target.value })} /></Field>
            <Field label="Tenure (months)" htmlFor="t-tenure"><Input id="t-tenure" inputMode="numeric" value={t.tenure} onChange={(e) => setT({ ...t, tenure: e.target.value })} /></Field>
          </div>
          {problem && <p className="text-xs text-problem">{problem}</p>}
        </div>
      }
      onConfirm={async (reason) => problem ? undefined : run(() => api(`/applications/${r.application.id}/terms`, {
        body: { expected_version: r.application.version, reason, amount: t.amount, rate_bp: percentToBp(t.rate), tenure_months: Number(t.tenure) },
      }), { ok: 'Terms adjusted and recorded', onDone })} />
  )
}

export function DecisionTab({ app, reload }: TabProps) {
  const run = useAction()
  const { data: r, error, loading, reload: again } = useApi<ReviewView>(`/applications/${app.id}/review?v=${app.version}`)
  if (error) return <Banner tone="problem">{error.message}</Banner>
  if (!r) return <BlockSkeleton />
  const done = () => { again(); reload() }
  const v = r.application.version
  const post = (path: string, body: Record<string, unknown>, ok: string) => run(() => api(`/applications/${app.id}/${path}`, { body: { expected_version: v, ...body } }), { ok, onDone: done })
  const cur = r.terms.current
  const approveBody = (reason?: string) => cur && { outcome: 'APPROVED', reason, terms: { amount: cur.amount, rate_bp: cur.rate_bp, tenure_months: cur.tenure_months } }
  const overrideApprove = r.ai.recommendation === 'POTENTIALLY_INELIGIBLE'
  const termsLine = cur ? `${inr(cur.amount)} at ${cur.rate_percent}% for ${months(cur.tenure_months)}, EMI ${inr(cur.emi)}` : ''
  const A = r.actions

  return (
    <div className="space-y-5">
      <section className="card space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="section-head">For decision</h2>
            <StatusPill status={r.application.status} />
            <span className="text-xs text-muted-foreground">
              {r.application.assigned_name ? `Assigned to ${r.application.assigned_name}` : 'Not assigned'}
              {r.application.forwarded_by && ` · forwarded by ${r.application.forwarded_by} ${dateTimeIN(r.application.forwarded_at)}`}
            </span>
          </div>
          {/* While the view re-reads (after any change), the actions wait, so nobody confirms against terms that just changed. */}
          <div className={loading ? 'pointer-events-none flex flex-wrap gap-2 opacity-60 transition-opacity' : 'flex flex-wrap gap-2 transition-opacity'} aria-busy={loading}>
            {A.forward && (
              <ReasonDialog trigger={<Button><Send /> Forward for review</Button>} title="Forward for review"
                description="The application goes to the Assistant and Branch Managers' queue. You cannot approve or decline it." label="Note for the reviewer (optional)"
                min={0} confirmLabel="Forward" onConfirm={(note) => post('forward', { note: note || null }, 'Forwarded for review')} />
            )}
            {A.claim && (
              <ConfirmDialog trigger={<Button><Hand /> Claim</Button>} title="Pick up this application"
                body={<p>It is assigned to you and moves to Under review. Only you can decide it unless a Branch Manager takes it over.</p>}
                confirmLabel="Claim" onConfirm={() => post('claim', {}, 'Assigned to you')} />
            )}
            {A.take_over && (
              <ReasonDialog trigger={<Button variant="outline"><ArrowRightLeft /> Take over</Button>} title={`Take over from ${r.application.assigned_name ?? 'the reviewer'}`}
                label="Why are you taking it over?" min={10} confirmLabel="Take over" onConfirm={(reason) => post('take-over', { reason }, 'You now hold this application')} />
            )}
            {A.adjust_terms && <AdjustTerms r={r} onDone={done} />}
            {A.refer_to_branch_manager && (
              <ReasonDialog trigger={<Button variant="outline"><ArrowUpRight /> Refer to Branch Manager</Button>} title="Refer to the Branch Manager"
                description="The file goes back to the review queue for the Branch Manager only, with your note. The Branch Manager can adjust the terms and decide."
                label="What should the Branch Manager change or check?" min={10} confirmLabel="Refer"
                onConfirm={(reason) => post('refer-to-branch-manager', { reason }, 'Referred to the Branch Manager')} />
            )}
            {A.refer && (
              <ReasonDialog trigger={<Button variant="outline"><Undo2 /> Refer</Button>} title="Refer for reconsideration"
                description="The application goes back to the review queue for any reviewer to pick up." label="Why is it referred?" min={10}
                confirmLabel="Refer" onConfirm={(reason) => post('decision', { outcome: 'REFERRED', reason }, 'Referred for reconsideration')} />
            )}
            {A.decline && (
              <ReasonDialog trigger={<Button variant="outline"><XCircle /> Decline</Button>} title="Decline the application"
                description={r.ai.recommendation === 'RECOMMENDED_FOR_APPROVAL' ? 'The AI recommended approval, so this is recorded as an override of the AI.' : undefined}
                label="Reason for declining" min={10} confirmLabel="Decline" destructive
                onConfirm={(reason) => post('decision', { outcome: 'DECLINED', reason }, 'Declined')} />
            )}
            {A.approve && cur && (overrideApprove ? (
              <ReasonDialog trigger={<Button><CheckCircle2 /> Approve</Button>} title="Approve against the AI recommendation"
                description={`Approving ${termsLine}. The AI found the applicant potentially ineligible, so this is recorded as an override.`}
                label="Why approve?" min={10} confirmLabel="Approve" onConfirm={(reason) => post('decision', approveBody(reason)!, 'Approved; the sanction pack is being prepared')} />
            ) : (
              <ConfirmDialog trigger={<Button><CheckCircle2 /> Approve</Button>} title="Approve the application"
                body={<><p>Approving <strong>{termsLine}</strong>.</p><p>The sanction letter, Key Facts Statement and EMI schedule are prepared next.</p></>}
                confirmLabel="Approve" onConfirm={() => post('decision', approveBody()!, 'Approved; the sanction pack is being prepared')} />
            ))}
          </div>
        </div>
        {r.application.referral && (
          <Banner tone="watch">
            Referred to the {ROLE_LABEL[r.application.referral.to_role as Role] ?? r.application.referral.to_role} by {r.application.referral.by}
            {r.application.referral.at && ` ${dateTimeIN(r.application.referral.at)}`}: “{r.application.referral.note}”
          </Banner>
        )}
        {A.four_eyes && ['FORWARDED', 'UNDER_REVIEW', 'RECONSIDERATION'].includes(r.application.status) && (
          <Banner tone="brand">Four-eyes: another manager decides this one.</Banner>
        )}
        {A.approve && !cur && <Banner tone="watch">No eligible amount, so no terms to approve.</Banner>}
      </section>

      <div className="grid grid-cols-2 gap-4">
        <section className="card space-y-3">
          <h2 className="section-head">AI recommendation</h2>
          <AiPill recommendation={r.ai.recommendation} />
          {r.ai.summary && <p className="text-sm leading-relaxed">{r.ai.summary}</p>}
          {r.ai.reasons && r.ai.reasons.length > 0 && <ul className="list-disc space-y-1 pl-5 text-sm text-ink-2">{r.ai.reasons.map((x) => <li key={x}>{x}</li>)}</ul>}
        </section>
        <section className="card space-y-3">
          <h2 className="section-head">Terms</h2>
          <div className="grid grid-cols-3 gap-2">
            <Terms label="Eligible (policy engine)" t={r.terms.eligible} />
            <Terms label="Current (what approval would grant)" t={r.terms.current} />
            <Terms label="Approved" t={r.terms.approved} />
          </div>
          {r.term_changes.length > 0 && (
            <ul className="space-y-1.5 text-xs">
              {r.term_changes.map((c, i) => (
                <li key={i} className="rounded-md bg-surface px-2 py-1.5">
                  {c.by} ({ROLE_LABEL[c.role as Role] ?? c.role}) changed to {inr(c.after.amount)} · {(c.after.rate_bp / 100).toFixed(2)}% · {months(c.after.tenure_months)} — “{c.reason}” · {dateTimeIN(c.at)}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {r.decisions.length > 0 && (
        <section className="card space-y-2">
          <h2 className="section-head">Decisions</h2>
          {r.decisions.map((d) => (
            <div key={d.id} className="flex flex-wrap items-center gap-2 text-sm">
              <StatusPill status={d.outcome} />
              <span>{d.by} ({ROLE_LABEL[d.role as Role] ?? d.role}) · {dateTimeIN(d.at)}</span>
              {d.is_override && <Pill tone="watch">Override of the AI ({statusLabel(d.ai_recommendation ?? '')})</Pill>}
              {d.reason && <span className="text-ink-2">— {d.reason}</span>}
            </div>
          ))}
        </section>
      )}
    </div>
  )
}
