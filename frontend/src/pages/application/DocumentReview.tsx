import { useEffect, useState } from 'react'
import { BadgeCheck, Loader2, RotateCcw, Save, XCircle } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ReasonDialog } from '@/components/Dialogs'
import { Banner, MethodChip } from '@/components/Labels'
import { Pill, StatusPill } from '@/components/StatusPill'
import { api } from '@/lib/api'
import { useAction, useApi } from '@/lib/useApi'
import { can, useSession } from '@/lib/session'
import { dateTimeIN } from '@/lib/format'
import type { DocReview } from '@/lib/types'
import { BlockSkeleton } from '@/components/AppShell'

/** The text as read beside the fields taken from it. AI values are suggestions until a person confirms or corrects them. */
export function DocumentReview({ appId, docId, onChanged }: { appId: string; docId: string; onChanged: () => void }) {
  const { me } = useSession()
  const run = useAction()
  const path = `/applications/${appId}/documents/${docId}`
  const { data: r, error, reload } = useApi<DocReview>(`${path}/review`)
  const [values, setValues] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    if (r) setValues(Object.fromEntries(Object.keys(r.expected_fields).map((k) => [k, r.fields.find((f) => f.field === k)?.value ?? ''])))
  }, [r])

  if (error) return <Banner tone="problem" className="mt-3">{error.message}</Banner>
  if (!r) return <div className="mt-3"><BlockSkeleton lines={2} /></div>
  const d = r.document
  const editor = can(me, 'document.verify') && !['VERIFIED', 'REJECTED', 'WITHDRAWN'].includes(d.status)
  const stored = (k: string) => r.fields.find((f) => f.field === k)
  const after = () => { reload(); onChanged() }

  async function save() {
    // A changed value is a correction; re-sending a flagged AI value unchanged confirms it.
    const send = Object.fromEntries(Object.entries(values).filter(([k, v]) => v.trim() && (v.trim() !== (stored(k)?.value ?? '') || (stored(k)?.source === 'AI_EXTRACTED' && stored(k)?.check_note))))
    if (!Object.keys(send).length) return toast.info('Nothing to save.')
    setSaving(true)
    await run(() => api(`${path}/fields`, { method: 'PATCH', body: { values: send } }), { ok: 'Values saved and recorded', onDone: after })
    setSaving(false)
  }

  async function verify() {
    const res = await run(() => api<{ discrepancies_opened: number; application_status: string }>(`${path}/verify`, { body: {} }), { onDone: after })
    if (res) {
      toast.success('Document verified')
      if (res.discrepancies_opened) toast.warning(`${res.discrepancies_opened} discrepanc${res.discrepancies_opened === 1 ? 'y' : 'ies'} found against the application form`)
    }
  }

  return (
    <div className="mt-3 space-y-3 rounded-xl border border-border bg-surface/60 p-4">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <StatusPill status={d.status} />
        <MethodChip method={d.text_method} />
        {d.page_count && <span className="text-muted-foreground">{d.page_count} page{d.page_count === 1 ? '' : 's'}</span>}
        {d.processing_note && <span className="text-muted-foreground">· {d.processing_note}</span>}
        {d.review_note && <Pill tone="problem">Rejected: {d.review_note}</Pill>}
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div className="max-h-[28rem] space-y-3 overflow-auto rounded-lg border border-line bg-white p-3">
          {r.pages.length === 0 ? (
            <p className="text-xs text-muted-foreground">No machine-read text. Open the document with View and key the values in.</p>
          ) : r.pages.map((p) => (
            <div key={p.page_no}>
              <p className="mb-1 text-[0.65rem] font-medium tracking-wide text-muted-foreground uppercase">Page {p.page_no} · <MethodChip method={p.method} />{p.engine && ` · ${p.engine}`}</p>
              <pre className="font-mono text-[0.68rem] leading-relaxed whitespace-pre-wrap">{p.text}</pre>
            </div>
          ))}
        </div>
        <div className="space-y-2.5">
          {Object.entries(r.expected_fields).length === 0 && <p className="text-xs text-muted-foreground">No values are read from this kind of document; check it by eye.</p>}
          {Object.entries(r.expected_fields).map(([k, spec]) => {
            const f = stored(k)
            return (
              <div key={k} className={f?.check_note ? 'rounded-lg border border-watch/40 bg-watch-bg/60 p-2' : ''}>
                <label htmlFor={`f-${k}`} className="flex items-center justify-between text-xs font-medium">
                  <span>{spec.description}</span>
                  {f && <span className="font-normal text-muted-foreground">{f.source_label}{f.page_no ? ` · page ${f.page_no}` : ''}</span>}
                </label>
                <Input id={`f-${k}`} className="mt-1" value={values[k] ?? ''} disabled={!editor} onChange={(e) => setValues({ ...values, [k]: e.target.value })} />
                {f?.snippet && <p className="mt-1 font-mono text-[0.65rem] text-muted-foreground">“{f.snippet}”</p>}
                {f?.check_note && <p className="mt-1 text-[0.7rem] text-watch">{f.check_note}</p>}
              </div>
            )
          })}
        </div>
      </div>
      {r.corrections.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground">{r.corrections.length} correction{r.corrections.length === 1 ? '' : 's'} recorded</summary>
          <ul className="mt-1 space-y-0.5">
            {r.corrections.map((c, i) => <li key={i}>{c.field}: “{c.value_before ?? '—'}” → “{c.value_after}” · {dateTimeIN(c.corrected_at)}</li>)}
          </ul>
        </details>
      )}
      {editor && (
        <div className="flex flex-wrap justify-end gap-2">
          {(d.status === 'AWAITING_OCR' || d.status === 'NEEDS_MANUAL_REVIEW') && !r.fields.some((f) => f.source === 'MANUAL') && (
            <Button size="sm" variant="ghost" onClick={() => run(() => api(`${path}/reprocess`, { body: {} }), { ok: 'Queued for reading again', onDone: after })}>
              <RotateCcw /> Reprocess
            </Button>
          )}
          <ReasonDialog trigger={<Button size="sm" variant="outline"><XCircle /> Reject</Button>} title="Reject this document"
            description="For an unreadable, expired or wrong document. The customer is asked for a new one." label="Why is it rejected?" min={10} max={500}
            confirmLabel="Reject" destructive onConfirm={(reason) => run(() => api(`${path}/reject`, { body: { reason } }), { ok: 'Document rejected', onDone: after })} />
          <Button size="sm" variant="outline" onClick={save} disabled={saving}>{saving ? <Loader2 className="animate-spin" /> : <Save />} Save values</Button>
          <Button size="sm" onClick={verify}><BadgeCheck /> Verify</Button>
        </div>
      )}
    </div>
  )
}
