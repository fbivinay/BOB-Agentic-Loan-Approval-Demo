import { useState } from 'react'
import { ExternalLink, FileText, Loader2, RotateCcw, Send } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Field } from '@/components/Field'
import { ReasonDialog } from '@/components/Dialogs'
import { Banner } from '@/components/Labels'
import { StatusPill } from '@/components/StatusPill'
import { Timeline } from '@/components/Timeline'
import { api } from '@/lib/api'
import { useAction, useApi } from '@/lib/useApi'
import { can, useSession } from '@/lib/session'
import { dateTimeIN, inr, months } from '@/lib/format'
import { statusLabel, tone } from '@/lib/status'
import type { EmailView, SanctionPack } from '@/lib/types'
import type { TabProps } from '@/pages/ApplicationDetail'
import { openSigned } from '@/pages/application/DocumentsTab'

// Worth re-reading while the email is on its way: queued or sending, or sent within the last two minutes (delivery
// arrives by the Resend webhook). After that a page reload shows any later status.
export const emailMoving = (e: { status: string; updated_at?: string } | null | undefined) =>
  !!e && (['QUEUED', 'SENDING'].includes(e.status) || (e.status === 'SENT' && !!e.updated_at && Date.now() - Date.parse(e.updated_at) < 120_000))

const ORDER = ['SANCTION_LETTER', 'KFS', 'EMI_SCHEDULE']
const TITLE = { SANCTION_LETTER: 'Sanction letter', KFS: 'Key Facts Statement', EMI_SCHEDULE: 'EMI schedule' }

export function EmailCard({ appId, e, onDone }: { appId: string; e: EmailView; onDone: () => void }) {
  const { me } = useSession()
  const run = useAction()
  const [email, setEmail] = useState('')
  return (
    <section className="card space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="section-head">{statusLabel(e.kind)} email</h2>
          <StatusPill status={e.status} />
        </div>
        {e.can_resend && can(me, 'application.forward') && (
          <ReasonDialog trigger={<Button size="sm" variant="outline"><Send /> Resend</Button>} title="Send the email again"
            description="Correct the applicant's email address if it was wrong. The demo still sends to the test inbox." label="Why is it being resent?" min={10} max={500}
            extra={<Field label="Corrected applicant email (optional)" htmlFor="new-email"><Input id="new-email" type="email" value={email} onChange={(x) => setEmail(x.target.value)} /></Field>}
            confirmLabel="Resend"
            onConfirm={(reason) => run(() => api(`/applications/${appId}/emails/${e.id}/resend`, { body: { reason, email: email.trim() || null } }), { ok: 'Queued to send again', onDone })} />
        )}
      </div>
      <p className="text-sm">{e.subject}</p>
      <p className="text-xs text-muted-foreground">To {e.to_address} · {e.attachments} attachment{e.attachments === 1 ? '' : 's'} · {e.attempts} attempt{e.attempts === 1 ? '' : 's'}</p>
      <Banner>{e.routing}</Banner>
      {e.last_error && <Banner tone="problem">{e.last_error}</Banner>}
      <Timeline items={e.events.map((x) => ({ at: x.at, title: statusLabel(x.event.replace(/^email\.|^poll\./, '').toUpperCase()), tone: tone(x.event.replace(/^email\./, '').toUpperCase()) }))} />
    </section>
  )
}

export function SanctionTab({ app, reload }: TabProps) {
  const { me } = useSession()
  const run = useAction()
  const { data: p, error, reload: again } = useApi<SanctionPack>(`/applications/${app.id}/sanction-pack?v=${app.version}`,
    { pollMs: 4000, until: (x) => x.state !== 'PENDING' && !emailMoving(x.approval_email) })
  if (error) return <Banner tone="problem">{error.message}</Banner>
  if (!p) return <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Loading…</div>
  const done = () => { again(); reload() }

  return (
    <div className="space-y-5">
      <Banner tone="brand">{p.label}</Banner>
      {p.state === 'PENDING' && (
        <section className="card flex items-center gap-3">
          <Loader2 className="size-5 animate-spin text-brand" />
          <p className="text-sm">Preparing the sanction letter, Key Facts Statement and EMI schedule from the approved terms…</p>
        </section>
      )}
      {p.state === 'FAILED' && (
        <section className="card space-y-3">
          <Banner tone="problem">Generation stopped: {p.job?.last_error ?? 'no detail recorded'}</Banner>
          {can(me, 'application.forward') && app.status === 'APPROVED' && (
            <Button onClick={() => run(() => api(`/applications/${app.id}/sanction-pack/retry`, { body: { expected_version: app.version } }), { ok: 'Queued again', onDone: done })}>
              <RotateCcw /> Retry
            </Button>
          )}
        </section>
      )}
      {p.state === 'ISSUED' && (
        <div className="grid grid-cols-3 gap-4">
          {[...p.documents].sort((a, b) => ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind)).map((d) => (
            <section key={d.id} className="card space-y-2">
              <div className="flex items-center gap-2"><FileText className="size-4 text-brand" /><h3 className="text-sm font-medium">{TITLE[d.kind]}</h3></div>
              <p className="text-xs tabular">{inr(d.terms.amount)} · {(d.terms.rate_bp / 100).toFixed(2)}% · {months(d.terms.tenure_months)}</p>
              <p className="text-xs text-muted-foreground">{dateTimeIN(d.created_at)} · <span className="font-mono">sha256 {d.sha256.slice(0, 12)}</span></p>
              <Button size="sm" variant="outline" onClick={() => openSigned(`/applications/${app.id}/sanction-pack/${d.kind}/url`)}><ExternalLink /> Open</Button>
            </section>
          ))}
        </div>
      )}
      {p.approval_email && <EmailCard appId={app.id} e={p.approval_email} onDone={done} />}
    </div>
  )
}
