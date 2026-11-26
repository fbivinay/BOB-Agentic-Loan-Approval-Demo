import { useEffect, useState } from 'react'
import { CheckCircle2, CircleDashed, Landmark, Loader2, Save, UserCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ConfirmDialog, ReasonDialog } from '@/components/Dialogs'
import { Figure, FiguresPanel } from '@/components/Figure'
import { Banner } from '@/components/Labels'
import { StatusPill } from '@/components/StatusPill'
import { api } from '@/lib/api'
import { useAction, useApi } from '@/lib/useApi'
import { ROLE_LABEL, type Role } from '@/lib/session'
import { dateIN, dateTimeIN, inr, months } from '@/lib/format'
import type { DisbursementView } from '@/lib/types'
import type { TabProps } from '@/pages/ApplicationDetail'
import { emailMoving } from '@/pages/application/SanctionTab'

export function DisbursementTab({ app, reload }: TabProps) {
  const run = useAction()
  const { data: v, error, loading, reload: again } = useApi<DisbursementView>(`/applications/${app.id}/disbursement?v=${app.version}`,
    { pollMs: 4000, until: (x) => !emailMoving(x.confirmation_email) })
  const [items, setItems] = useState<Record<string, boolean>>({})
  const [note, setNote] = useState('')
  useEffect(() => {
    if (v?.disbursement) {
      setItems(Object.fromEntries(v.disbursement.checklist.map((c) => [c.item, c.done])))
      setNote(v.disbursement.checklist_note ?? '')
    }
  }, [v])
  if (error) return <Banner tone="problem">{error.message}</Banner>
  if (!v) return <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Loading…</div>
  const done = () => { again(); reload() }
  const d = v.disbursement
  const f = v.figures
  const ver = v.application.version
  const A = v.actions
  const checklistDirty = d ? d.checklist.some((c) => items[c.item] !== c.done) || (note.trim() || null) !== (d.checklist_note ?? null) : false

  return (
    <div className="space-y-5">
      <Banner tone="watch" className="text-sm font-medium">{v.banner}</Banner>
      {f && (
        <FiguresPanel cols={4}>
          <Figure label="Approved amount" value={inr(f.approved_amount)} note={`${months(f.tenure_months)} · APR ${f.apr_percent}%`} />
          <Figure label="Upfront charges" value={inr(f.upfront_charges)} note="processing fee with GST" />
          <Figure label="Net to credit" value={inr(f.net_to_credit)} note={`EMI ${inr(f.emi)} from ${dateIN(f.first_emi_date)}`} />
          <Figure label="KFS valid until" value={dateIN(v.kfs_valid_until)} tone={v.kfs_expired ? 'problem' : undefined}
            note={v.kfs_expired ? 'expired: acceptance now needs a reason' : 'three working days from issue'} />
        </FiguresPanel>
      )}

      <div className={loading ? 'pointer-events-none grid grid-cols-3 gap-4 opacity-60' : 'grid grid-cols-3 gap-4'} aria-busy={loading}>
        <section className="card space-y-3">
          <h2 className="section-head">1. Customer acceptance</h2>
          {d?.acceptance.recorded_at ? (
            <p className="flex items-start gap-2 text-sm"><CheckCircle2 className="mt-0.5 size-4 text-good" />
              <span>Recorded by {d.acceptance.by}, {dateTimeIN(d.acceptance.recorded_at)}{d.acceptance.late_reason && <span className="block text-xs text-muted-foreground">After the KFS validity: {d.acceptance.late_reason}</span>}</span>
            </p>
          ) : <p className="text-sm text-muted-foreground">The customer accepts the sanction and the KFS at the branch.</p>}
          {A.record_acceptance && (v.kfs_expired ? (
            <ReasonDialog trigger={<Button size="sm"><UserCheck /> Record acceptance</Button>} title="Record acceptance after the KFS validity"
              description={`The KFS was valid until ${dateIN(v.kfs_valid_until)}. Record how the bank re-confirmed the terms with the customer.`}
              label="How were the terms re-confirmed?" min={10} max={500} confirmLabel="Record acceptance"
              onConfirm={(late_reason) => run(() => api(`/applications/${app.id}/disbursement/acceptance`, { body: { expected_version: ver, late_reason } }), { ok: 'Acceptance recorded', onDone: done })} />
          ) : (
            <ConfirmDialog trigger={<Button size="sm"><UserCheck /> Record acceptance</Button>} title="Record the customer's acceptance"
              body={<p>The customer has accepted the sanction letter and the Key Facts Statement at the branch.</p>} confirmLabel="Record acceptance"
              onConfirm={() => run(() => api(`/applications/${app.id}/disbursement/acceptance`, { body: { expected_version: ver } }), { ok: 'Acceptance recorded', onDone: done })} />
          ))}
        </section>

        <section className="card space-y-3">
          <h2 className="section-head">2. Before disbursement</h2>
          {!d ? <p className="text-sm text-muted-foreground">Opens once the acceptance is recorded.</p> : (
            <>
              <ul className="space-y-2">
                {d.checklist.map((c) => (
                  <li key={c.item}>
                    <label className="flex items-start gap-2 text-sm">
                      {A.update_checklist
                        ? <input type="checkbox" className="mt-1 accent-brand" checked={!!items[c.item]} onChange={(e) => setItems({ ...items, [c.item]: e.target.checked })} />
                        : c.done ? <CheckCircle2 className="mt-0.5 size-4 text-good" /> : <CircleDashed className="mt-0.5 size-4 text-watch" />}
                      {c.label}
                    </label>
                  </li>
                ))}
              </ul>
              {A.update_checklist && (
                <>
                  <Input placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
                  <Button size="sm" variant="outline" disabled={!checklistDirty}
                    onClick={() => run(() => api(`/applications/${app.id}/disbursement/checklist`, { body: { expected_version: ver, items, note: note.trim() || null } }),
                      { ok: 'Checklist saved', onDone: done })}>
                    <Save /> Save checklist
                  </Button>
                </>
              )}
              {d.checklist_completed_by && <p className="text-xs text-muted-foreground">Completed by {d.checklist_completed_by}{d.checklist_note && ` · ${d.checklist_note}`}</p>}
            </>
          )}
        </section>

        <section className="card space-y-3">
          <h2 className="section-head">3. Release (simulated)</h2>
          {d?.released ? (
            <dl className="grid grid-cols-[8rem_1fr] gap-y-1 text-sm">
              <dt className="text-muted-foreground">Reference</dt><dd className="font-mono text-xs">{d.released.simulated_reference}</dd>
              <dt className="text-muted-foreground">Loan account</dt><dd className="font-mono text-xs">{d.released.loan_account_number}</dd>
              <dt className="text-muted-foreground">Net credited</dt><dd className="tabular">{inr(d.released.net_amount)}</dd>
              <dt className="text-muted-foreground">First EMI</dt><dd>{dateIN(d.released.first_emi_date)}</dd>
              <dt className="text-muted-foreground">Released by</dt><dd>{d.released.by} ({ROLE_LABEL[d.released.role as Role] ?? d.released.role}), {dateTimeIN(d.released.at)}</dd>
            </dl>
          ) : A.release ? (
            <ConfirmDialog trigger={<Button><Landmark /> Release</Button>} title="Release the disbursement"
              body={<><p><strong>This is a simulated release. No money moves and no account is credited.</strong></p>
                {f && <p>Net {inr(f.net_to_credit)} to the customer, first EMI {inr(f.emi)}.</p>}</>}
              confirmLabel="Release (simulated)"
              onConfirm={() => run(() => api(`/applications/${app.id}/disbursement/release`, { body: { expected_version: ver, confirm: true } }), { ok: 'Released (simulated)', onDone: done })} />
          ) : d?.status === 'CHECKLIST_COMPLETE' ? (
            <p className="text-sm text-muted-foreground">Released by a different authorised person from the one who recorded acceptance or worked on the checklist.</p>
          ) : <p className="text-sm text-muted-foreground">Available once the checklist is complete.</p>}
          <p className="text-xs text-muted-foreground">{v.permission_label}</p>
        </section>
      </div>

      {v.confirmation_email && (
        <section className="card flex flex-wrap items-center gap-2 text-sm">
          <span className="font-medium">Disbursement confirmation email</span>
          <StatusPill status={v.confirmation_email.status} />
          <span className="text-xs text-muted-foreground">to {v.confirmation_email.to_address}</span>
          {v.confirmation_email.last_error && <span className="text-xs text-problem">{v.confirmation_email.last_error}</span>}
        </section>
      )}
    </div>
  )
}
