import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { FileText, Gauge, PencilLine } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Field, selectClass } from '@/components/Field'
import { ReasonDialog } from '@/components/Dialogs'
import { PageHead } from '@/components/PageHead'
import { Banner } from '@/components/Labels'
import { Pill, StatusPill } from '@/components/StatusPill'
import { Timeline } from '@/components/Timeline'
import { Need } from '@/components/ProtectedRoute'
import { api } from '@/lib/api'
import { useAction, useApi } from '@/lib/useApi'
import { can, useSession } from '@/lib/session'
import { dateIN, dateTimeIN } from '@/lib/format'
import { statusLabel } from '@/lib/status'
import type { CreditProfile, KycProfile } from '@/lib/types'
import { openSigned } from '@/pages/application/DocumentsTab'
import { PageSkeleton } from '@/components/AppShell'

const SOURCE: Record<string, string> = { ACCOUNT_OPENING: 'Account opening', LOAN_ONBOARDING: 'Loan onboarding', KYC_UPDATE: 'KYC update', LOAN_APPLICATION: 'Loan application' }
const ADDRESS = [['address_line', 'Address'], ['city', 'City'], ['state', 'State'], ['pincode', 'PIN code']] as const

export function KycDetail() {
  return (
    <Need any={['application.view']}>
      <Detail />
    </Need>
  )
}

function Detail() {
  const { id } = useParams()
  const { me } = useSession()
  const run = useAction()
  const { data: k, error, reload } = useApi<KycProfile>(`/kyc/${id}`)
  const [credit, setCredit] = useState<CreditProfile | null>(null)
  if (error) return <Banner tone="problem">{error.message}</Banner>
  if (!k) return <PageSkeleton />
  const cp = credit ?? k.credit_profile
  const current = k.documents.filter((d) => d.status === 'CURRENT')
  const older = k.documents.filter((d) => d.status === 'SUPERSEDED')

  return (
    <div className="space-y-6">
      <PageHead eyebrow={k.customer_id ? `KYC record · ${k.customer_id}` : 'KYC record · new to bank'} title={k.full_name}
        lede={`Version ${k.version} · ${SOURCE[k.source] ?? k.source}${k.verified_on ? ` · verified ${dateIN(k.verified_on)}` : ''}`} />
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill status={k.kyc_status} />
        {k.is_current ? <Pill tone="good">Reused for new loans</Pill> : <Pill tone="watch">Needs updating before a loan</Pill>}
        {can(me, 'application.intake') && <UpdateKyc k={k} onDone={reload} />}
      </div>

      <div className="grid grid-cols-2 gap-4">
        <section className="card space-y-3">
          <h2 className="section-head">Details on record</h2>
          <dl className="grid grid-cols-[9rem_1fr] gap-y-1.5 text-sm">
            <dt className="text-muted-foreground">PAN</dt><dd className="font-mono">{k.pan}</dd>
            <dt className="text-muted-foreground">Date of birth</dt><dd>{dateIN(k.date_of_birth)}</dd>
            <dt className="text-muted-foreground">Gender</dt><dd>{k.gender ? statusLabel(k.gender) : '—'}</dd>
            <dt className="text-muted-foreground">Aadhaar</dt><dd className="font-mono">{k.aadhaar_masked ?? '—'}</dd>
            <dt className="text-muted-foreground">Address</dt><dd>{[k.address_line, k.city, k.state, k.pincode].filter(Boolean).join(', ') || '—'}</dd>
          </dl>
        </section>
        <section className="card space-y-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2"><h2 className="section-head">Credit profile</h2><Pill tone="watch">DEMO / SIMULATED</Pill></div>
            <Button variant="outline" size="sm" onClick={() => run(async () => { const r = await api<CreditProfile>('/credit-profile', { body: { pan: k.pan } }); setCredit(r); return r }, { ok: 'Credit profile checked' })}>
              <Gauge /> {cp ? 'Check again' : 'Check credit profile'}
            </Button>
          </div>
          {cp ? (
            <>
              <p className="text-3xl font-semibold tabular">{cp.score ?? 'No score'}</p>
              <p className="text-xs text-muted-foreground">
                {statusLabel(cp.history_status)} history · {cp.source === 'SYNTHETIC_BUREAU' ? 'from the synthetic bureau record' : 'simulated from the PAN'}
                {' '}· generated {dateTimeIN(cp.generated_at)} · valid until {dateIN(cp.valid_until)}
              </p>
              <p className="text-xs text-muted-foreground">{cp.label}</p>
            </>
          ) : <p className="text-sm text-muted-foreground">Not checked this month yet.</p>}
        </section>
      </div>

      <section className="card space-y-3">
        <h2 className="section-head">Existing KYC documents</h2>
        <DocList docs={current} profileId={k.id} />
        {older.length > 0 && (
          <>
            <h3 className="pt-2 text-sm font-medium">Earlier versions (kept, never deleted)</h3>
            <DocList docs={older} profileId={k.id} />
          </>
        )}
      </section>

      <section className="card space-y-3">
        <h2 className="section-head">Version history</h2>
        <Timeline items={k.history.map((h) => ({ at: h.at, title: `Version ${h.version} · ${SOURCE[h.source] ?? h.source}`,
          note: `${h.reason}${h.changed_fields.length ? ` · changed: ${h.changed_fields.join(', ')}` : ''}${h.by ? ` · by ${h.by}` : ''}` }))} />
      </section>
    </div>
  )
}

function DocList({ docs, profileId }: { docs: KycProfile['documents']; profileId: string }) {
  if (!docs.length) return <p className="text-sm text-muted-foreground">None.</p>
  return (
    <ul className="divide-y divide-line">
      {docs.map((d) => (
        <li key={d.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
          <div className="space-y-0.5">
            <p className="flex items-center gap-2 text-sm font-medium">{statusLabel(d.doc_type)} · v{d.version} {d.is_sample && <Pill tone="watch">SAMPLE / DEMO</Pill>}
              <StatusPill status={d.status} /></p>
            <p className="text-xs text-muted-foreground">{d.label} · {SOURCE[d.source] ?? d.source} · {dateTimeIN(d.created_at)}</p>
          </div>
          <Button variant="outline" size="sm" onClick={() => openSigned(`/kyc/${profileId}/documents/${d.id}/url`)}><FileText /> View or download</Button>
        </li>
      ))}
    </ul>
  )
}

function UpdateKyc({ k, onDone }: { k: KycProfile; onDone: () => void }) {
  const run = useAction()
  const [f, setF] = useState<Record<string, string>>({ address_line: k.address_line ?? '', city: k.city ?? '', state: k.state ?? '', pincode: k.pincode ?? '', aadhaar_last4: '' })
  const [docType, setDocType] = useState('ADDRESS_PROOF')
  const [file, setFile] = useState<File | null>(null)
  return (
    <ReasonDialog trigger={<Button variant="outline" size="sm"><PencilLine /> Update KYC</Button>} title="Update KYC"
      description="Record what changed and add the new document you checked against the original. The previous version and document are kept."
      label="What changed, and what did you check?" min={10} confirmLabel="Save new version"
      extra={
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            {ADDRESS.map(([key, label]) => (
              <Field key={key} label={label} htmlFor={`k-${key}`}><Input id={`k-${key}`} value={f[key]} onChange={(e) => setF({ ...f, [key]: e.target.value })} /></Field>
            ))}
            <Field label="Aadhaar last 4 digits (if changed)" htmlFor="k-aadhaar"><Input id="k-aadhaar" inputMode="numeric" maxLength={4} value={f.aadhaar_last4}
              onChange={(e) => setF({ ...f, aadhaar_last4: e.target.value.replace(/\D/g, '') })} /></Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="New document" htmlFor="k-doctype">
              <select id="k-doctype" className={selectClass} value={docType} onChange={(e) => setDocType(e.target.value)}>
                <option value="ADDRESS_PROOF">Address proof</option><option value="AADHAAR_MASKED">Masked Aadhaar</option><option value="PAN_CARD">PAN card</option>
                <option value="PASSPORT">Passport</option><option value="VOTER_ID">Voter ID</option>
              </select>
            </Field>
            <Field label="File (PDF, PNG or JPEG)" htmlFor="k-file"><Input id="k-file" type="file" accept=".pdf,.png,.jpg,.jpeg" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></Field>
          </div>
        </div>
      }
      onConfirm={(reason) => run(async () => {
        if (file) await api(`/kyc/${k.id}/documents?doc_type=${docType}&file_name=${encodeURIComponent(file.name)}`, { method: 'PUT', file })
        const changes = Object.fromEntries(Object.entries(f).filter(([key, v]) => v.trim() && v.trim() !== ((k as unknown as Record<string, string | null>)[key] ?? '')))
        return api(`/kyc/${k.id}/update`, { body: { expected_version: k.version, reason, verified_original: true, ...changes } })
      }, { ok: 'KYC updated: a new version is on record', onDone })} />
  )
}
