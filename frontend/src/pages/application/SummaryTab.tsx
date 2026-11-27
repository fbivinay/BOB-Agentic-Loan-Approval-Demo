import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, FileText, Loader2 } from 'lucide-react'
import { openSigned } from '@/pages/application/DocumentsTab'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Field, selectClass } from '@/components/Field'
import { Pill, StatusPill } from '@/components/StatusPill'
import { api } from '@/lib/api'
import { useAction } from '@/lib/useApi'
import { can, useSession } from '@/lib/session'
import { CREDIT_HISTORY, EMPLOYMENT } from '@/lib/documents'
import { dateIN, dateTimeIN, inr, months } from '@/lib/format'
import { statusLabel } from '@/lib/status'
import type { TabProps } from '@/pages/ApplicationDetail'

const CONTACT = ['email', 'address_line', 'city', 'state', 'pincode'] as const
const IDENTITY = ['gender', 'pan', 'residency_status'] as const
const LABEL: Record<string, string> = { email: 'Email', address_line: 'Address', city: 'City', state: 'State', pincode: 'PIN code', pan: 'PAN',
  gender: 'Gender', residency_status: 'Residency' }

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <><dt className="text-muted-foreground">{k}</dt><dd>{v ?? '—'}</dd></>
}

export function SummaryTab({ app, reload }: TabProps) {
  const { me } = useSession()
  const run = useAction()
  const a = app.applicant
  const open = app.status === 'INTAKE' && !app.intake.completed && can(me, 'application.intake')
  const ntb = a.bank_relationship === 'NTB'
  const initial = Object.fromEntries([...CONTACT, ...IDENTITY].map((k) => [k, (a[k] as string | null) ?? '']))
  const [form, setForm] = useState<Record<string, string>>(initial)
  useEffect(() => setForm(initial), [app.version])
  const [saving, setSaving] = useState(false)

  async function save() {
    const changes = Object.fromEntries(Object.entries(form).filter(([k, v]) => v.trim() && v.trim() !== (initial[k] ?? '') && (ntb || !(IDENTITY as readonly string[]).includes(k)))
      .map(([k, v]) => [k, k === 'pan' ? v.trim().toUpperCase() : v.trim()]))
    if (!Object.keys(changes).length) return
    setSaving(true)
    await run(() => api(`/applications/${app.id}/applicant`, { method: 'PATCH', body: changes }), { ok: 'Applicant details saved', onDone: reload })
    setSaving(false)
  }

  return (
    <div className="space-y-5">
      {open && (
        <section className="card space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="section-head">Intake</h2>
            {app.intake.missing_details.length
              ? <Pill tone="watch">Still needed: {app.intake.missing_details.map((k) => LABEL[k] ?? k).join(', ')}</Pill>
              : <Pill tone="good">All details on file</Pill>}
          </div>
          <div className="grid grid-cols-3 gap-4">
            {CONTACT.map((k) => (
              <Field key={k} label={LABEL[k]} htmlFor={k}>
                <Input id={k} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
              </Field>
            ))}
            {ntb && (
              <>
                <Field label="PAN" htmlFor="pan">
                  <Input id="pan" value={form.pan} onChange={(e) => setForm({ ...form, pan: e.target.value.toUpperCase() })} />
                </Field>
                <Field label="Gender" htmlFor="gender">
                  <select id="gender" className={selectClass} value={form.gender} onChange={(e) => setForm({ ...form, gender: e.target.value })}>
                    <option value="">Not stated</option><option value="F">Female</option><option value="M">Male</option><option value="O">Other</option>
                  </select>
                </Field>
                <Field label="Residency" htmlFor="residency_status">
                  <select id="residency_status" className={selectClass} value={form.residency_status} onChange={(e) => setForm({ ...form, residency_status: e.target.value })}>
                    <option value="RESIDENT">Resident</option><option value="NRI">Non-resident (NRI)</option>
                  </select>
                </Field>
              </>
            )}
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={save} disabled={saving}>{saving && <Loader2 className="animate-spin" />} Save details</Button>
            <Button disabled={app.intake.missing_details.length > 0}
              onClick={() => run(() => api(`/applications/${app.id}/intake/complete`, { body: { expected_version: app.version } }),
                { ok: 'Intake complete: the document checklist is fixed', onDone: reload })}>
              <CheckCircle2 /> Complete intake
            </Button>
          </div>
        </section>
      )}

      <div className="grid grid-cols-2 gap-4">
        <section className="card space-y-3">
          <h2 className="section-head">Applicant</h2>
          <dl className="grid grid-cols-[10rem_1fr] gap-y-1.5 text-sm">
            <Row k="Relationship" v={statusLabel(a.bank_relationship)} />
            <Row k="Date of birth" v={dateIN(a.date_of_birth)} />
            <Row k="Mobile" v={a.mobile} />
            <Row k="Email" v={a.email} />
            <Row k="PAN" v={a.pan && <span className="font-mono">{a.pan}</span>} />
            <Row k="Employment" v={EMPLOYMENT[a.employment_type] ?? a.employment_type} />
            <Row k="Address" v={[a.address_line, a.city, a.state, a.pincode].filter(Boolean).join(', ') || null} />
            <Row k="Residency" v={a.residency_status === 'NRI' ? 'Non-resident (NRI)' : 'Resident'} />
          </dl>
        </section>
        <section className="card space-y-3">
          <h2 className="section-head">Declared at the enquiry</h2>
          <dl className="grid grid-cols-[10rem_1fr] gap-y-1.5 text-sm">
            <Row k="Monthly net income" v={inr(app.declared.monthly_net)} />
            <Row k="EMIs to other lenders" v={inr(app.declared.other_lender_emi)} />
            <Row k="Credit history" v={app.declared.credit_history ? CREDIT_HISTORY[app.declared.credit_history] : null} />
            <Row k="Intake completed" v={app.intake.completed ? dateTimeIN(app.intake.completed_at) : 'Not yet'} />
            <Row k="Policy version" v={app.checklist.policy && <span className="font-mono text-xs">{app.checklist.policy}</span>} />
          </dl>
          <h3 className="pt-2 text-sm font-medium">Terms</h3>
          <ul className="space-y-1 text-sm">
            {app.terms.map((t, i) => (
              <li key={i} className="flex items-center gap-2">
                <Pill>{statusLabel(t.kind)}</Pill>
                <span className="tabular">{inr(t.amount)} · {t.rate_percent}% · {months(t.tenure_months)}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <section className="card space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <h2 className="section-head">KYC on file</h2>
            {app.kyc && <StatusPill status={app.kyc.kyc_status} />}
            {app.kyc?.reused && <Pill tone="good">Reused: not asked for again</Pill>}
          </div>
          {app.kyc && <Link to={`/kyc/${app.kyc.profile_id}`} className="text-sm text-brand hover:underline">Open the KYC record</Link>}
        </div>
        {!app.kyc ? (
          <p className="text-sm text-muted-foreground">
            {ntb ? 'New to the bank: KYC is completed in this application. Attesting the verified PAN and address proof creates the KYC record.'
              : 'No KYC record on file for this customer.'}
          </p>
        ) : (
          <>
            <p className="text-xs text-muted-foreground">
              Version {app.kyc.version}{app.kyc.verified_on && ` · verified ${dateIN(app.kyc.verified_on)}`} · {app.kyc.reused ? 'reused' : 'not current: PAN and address proof needed'}
            </p>
            <ul className="flex flex-wrap gap-2">
              {app.kyc.documents.map((d) => (
                <li key={d.id}>
                  <Button variant="outline" size="sm" onClick={() => openSigned(`/kyc/${app.kyc!.profile_id}/documents/${d.id}/url`)}>
                    <FileText /> {statusLabel(d.doc_type)} v{d.version}{d.is_sample && <span className="text-[0.62rem] text-watch">SAMPLE</span>}
                  </Button>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </div>
  )
}
