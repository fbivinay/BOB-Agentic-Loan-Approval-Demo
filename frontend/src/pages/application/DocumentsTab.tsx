import { useRef, useState } from 'react'
import { CheckCircle2, CircleDashed, ExternalLink, FileSearch, Loader2, MinusCircle, Upload, Undo2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { selectClass } from '@/components/Field'
import { ReasonDialog } from '@/components/Dialogs'
import { Banner } from '@/components/Labels'
import { StatusPill } from '@/components/StatusPill'
import { api, ApiError } from '@/lib/api'
import { useAction, useApi } from '@/lib/useApi'
import { can, useSession } from '@/lib/session'
import { checkUpload } from '@/lib/upload'
import { statusLabel } from '@/lib/status'
import { dateTimeIN } from '@/lib/format'
import { toast } from 'sonner'
import type { Discrepancy, DocRow } from '@/lib/types'
import type { TabProps } from '@/pages/ApplicationDetail'
import { DocumentReview } from '@/pages/application/DocumentReview'

const UPLOAD_STATUSES = ['INTAKE', 'DOCS_PENDING', 'DISCREPANCY']
const ATTESTATION = 'I have checked every document against the originals and the details match.'

/** Opens a 60-second signed URL in a new tab. The tab is opened first, inside the click, so pop-up blockers allow it. */
export async function openSigned(path: string) {
  const w = window.open('', '_blank')
  try {
    const r = await api<{ url: string }>(path)
    if (w) {
      w.opener = null
      w.location.href = r.url
    } else window.location.assign(r.url)
  } catch (e) {
    w?.close()
    toast.error(e instanceof ApiError ? e.message : 'The document could not be opened.')
  }
}

function UploadRow({ appId, code, accepts, onDone }: { appId: string; code: string; accepts: string[]; onDone: () => void }) {
  const [docType, setDocType] = useState(accepts[0])
  const [busy, setBusy] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  async function pick(f: File | undefined) {
    if (!f) return
    const problem = checkUpload(f, code)
    if (problem) {
      toast.error(problem)
      return
    }
    setBusy(true)
    try {
      await api(`/applications/${appId}/documents?requirement=${code}&doc_type=${docType}&file_name=${encodeURIComponent(f.name)}`, { method: 'PUT', file: f })
      toast.success(`${f.name} uploaded; reading it now`)
      onDone()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Upload failed.')
    } finally {
      setBusy(false)
      if (input.current) input.current.value = ''
    }
  }

  return (
    <div className="flex items-center gap-2">
      {accepts.length > 1 && (
        <select className={selectClass + ' w-44'} value={docType} onChange={(e) => setDocType(e.target.value)} aria-label="Document type">
          {accepts.map((t) => <option key={t} value={t}>{statusLabel(t)}</option>)}
        </select>
      )}
      <input ref={input} type="file" accept={code === 'PHOTOGRAPH' ? 'image/png,image/jpeg' : 'application/pdf,image/png,image/jpeg'} hidden
        onChange={(e) => pick(e.target.files?.[0])} data-requirement={code} />
      <Button size="sm" variant="outline" disabled={busy} onClick={() => input.current?.click()}>
        {busy ? <Loader2 className="animate-spin" /> : <Upload />} Upload
      </Button>
    </div>
  )
}

export function DocumentsTab({ app, reload }: TabProps) {
  const { me } = useSession()
  const run = useAction()
  const [open, setOpen] = useState<string | null>(null)
  const { data: disc } = useApi<{ discrepancies: Discrepancy[] }>(`/applications/${app.id}/discrepancies?v=${app.version}`)
  const canUpload = can(me, 'application.intake') && UPLOAD_STATUSES.includes(app.status)
  const live = app.documents.filter((d) => d.status !== 'WITHDRAWN')
  const verified = (code: string) => live.some((d) => d.requirement === code && d.status === 'VERIFIED')
  const openItems = disc?.discrepancies.filter((d) => d.status === 'OPEN').length ?? 0
  const canAttest = can(me, 'document.verify') && app.status === 'DOCS_PENDING' && app.checklist.frozen && app.checklist.items.every((i) => verified(i.code))
    && openItems === 0
  const basis = app.checklist.basis

  return (
    <div className="space-y-5">
      {!app.checklist.frozen && <Banner>Provisional checklist from the enquiry. It is fixed from the bank's current records when intake is completed.</Banner>}
      <section className="card space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="section-head">Checklist</h2>
          {canAttest && (
            <ReasonDialog trigger={<Button><CheckCircle2 /> Attest documents complete</Button>} title="Attest the documents"
              description="The database checks that every required document is verified and nothing is open before the status moves. The AI assessment starts straight after."
              label="Attestation" min={20} initial={ATTESTATION} confirmLabel="Attest"
              onConfirm={(attestation) => run(() => api(`/applications/${app.id}/documents/attest`, { body: { expected_version: app.version, attestation } }),
                { ok: 'Documents verified; the AI assessment has started', onDone: reload })} />
          )}
        </div>
        <ul className="divide-y divide-line">
          {app.checklist.items.map((i) => (
            <li key={i.code} className="flex items-center justify-between gap-4 py-2.5">
              <span className="flex items-start gap-2 text-sm">
                {verified(i.code) ? <CheckCircle2 className="mt-0.5 size-4 text-good" /> : <CircleDashed className="mt-0.5 size-4 text-watch" />}
                <span>
                  {i.label}
                  <span className="block text-xs text-muted-foreground">
                    Accepts {i.accepts.map(statusLabel).join(', ')} · {i.uploaded} uploaded{verified(i.code) ? ', verified' : ''}
                  </span>
                </span>
              </span>
              {canUpload && <UploadRow appId={app.id} code={i.code} accepts={i.accepts} onDone={reload} />}
            </li>
          ))}
          {app.checklist.waived.map((w) => (
            <li key={w.code} className="flex items-start gap-2 py-2.5 text-sm text-muted-foreground">
              <MinusCircle className="mt-0.5 size-4" />
              <span>
                {w.label}
                <span className="block text-xs">
                  Not needed: {basis?.kyc_current && ['PAN_CARD', 'ADDRESS_PROOF'].includes(w.code) ? 'the customer\'s KYC at the bank is current'
                    : basis?.income_at_bob && w.code === 'BANK_STATEMENT_6M' ? 'income is credited to the customer\'s account at the bank' : 'the bank already holds it'}
                </span>
              </span>
            </li>
          ))}
        </ul>
        {openItems > 0 && <Banner tone="watch">{openItems} discrepanc{openItems === 1 ? 'y is' : 'ies are'} open. Resolve them before attesting.</Banner>}
      </section>

      <section className="card space-y-3">
        <h2 className="section-head">Documents</h2>
        {app.documents.length === 0 ? <p className="text-sm text-muted-foreground">Nothing uploaded yet.</p> : (
          <ul className="divide-y divide-line">
            {app.documents.map((d: DocRow) => (
              <li key={d.id} className="py-2.5">
                <div className="flex items-center justify-between gap-3">
                  <span className="min-w-0 text-sm">
                    <span className="font-medium">{d.original_name}</span>
                    <span className="block text-xs text-muted-foreground">
                      {statusLabel(d.doc_type)} for {statusLabel(d.requirement)} · {(d.size_bytes / 1024).toFixed(0)} KB · {dateTimeIN(d.uploaded_at)}
                      {d.withdrawn_reason && <> · withdrawn: {d.withdrawn_reason}</>}
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center gap-2">
                    {(d.status === 'UPLOADED' || d.status === 'AWAITING_OCR') && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}
                    <StatusPill status={d.status} />
                    <Button size="sm" variant="ghost" onClick={() => openSigned(`/applications/${app.id}/documents/${d.id}/url`)}><ExternalLink /> View</Button>
                    {d.status !== 'WITHDRAWN' && (
                      <Button size="sm" variant={open === d.id ? 'default' : 'outline'} onClick={() => setOpen(open === d.id ? null : d.id)}><FileSearch /> Review</Button>
                    )}
                    {canUpload && d.status !== 'WITHDRAWN' && d.status !== 'VERIFIED' && (
                      <ReasonDialog trigger={<Button size="sm" variant="ghost"><Undo2 /> Withdraw</Button>} title={`Withdraw ${d.original_name}`}
                        description="A wrong file is withdrawn, never deleted: it stays on record with the reason." label="Why is it being withdrawn?" min={10} max={500}
                        confirmLabel="Withdraw"
                        onConfirm={(reason) => run(() => api(`/applications/${app.id}/documents/${d.id}/withdraw`, { body: { reason } }), { ok: 'Document withdrawn', onDone: reload })} />
                    )}
                  </span>
                </div>
                {open === d.id && <DocumentReview appId={app.id} docId={d.id} onChanged={reload} />}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
