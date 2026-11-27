import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ExternalLink, XCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ReasonDialog } from '@/components/Dialogs'
import { Banner } from '@/components/Labels'
import { Pill, StatusPill } from '@/components/StatusPill'
import { api } from '@/lib/api'
import { useAction, useApi } from '@/lib/useApi'
import { can, useSession } from '@/lib/session'
import { dateTimeIN } from '@/lib/format'
import { statusLabel } from '@/lib/status'
import { cn } from '@/lib/utils'
import type { Proposal } from '@/lib/types'

const FILTERS = ['OPEN', 'DRAFTED', 'ACTIVATED', 'REJECTED'] as const

export function ProposalsTab() {
  const { me } = useSession()
  const run = useAction()
  const [status, setStatus] = useState<(typeof FILTERS)[number]>('OPEN')
  const { data, error, reload } = useApi<{ proposals: Proposal[]; label: string }>(`/policy/proposals?status=${status}`)

  return (
    <div className="space-y-4">
      <div className="flex gap-1">
        {FILTERS.map((f) => (
          <button key={f} type="button" onClick={() => setStatus(f)}
            className={cn('rounded-full px-3 py-1 text-sm transition-colors', status === f ? 'bg-brand text-white' : 'text-ink-2 hover:bg-white')}>
            {statusLabel(f)}
          </button>
        ))}
      </div>
      {error && <Banner tone="problem">{error.message}</Banner>}
      {data && data.proposals.length === 0 && <p className="text-sm text-muted-foreground">No {status.toLowerCase()} proposals.</p>}
      <div className="grid grid-cols-2 gap-4">
        {data?.proposals.map((p) => (
          <section key={p.id} className="card space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="flex items-center gap-2"><Pill>{statusLabel(p.kind)}</Pill><span className="font-mono text-xs">{p.parameter}</span></span>
              <StatusPill status={p.status} />
            </div>
            <p className="text-sm">
              Demo value <strong>{String(p.current_value)}</strong> <Pill>{p.current_confidence}</Pill> · official <strong>{p.official_value?.printed ?? p.evidence.value}</strong>
              {p.evidence.condition && <span className="text-muted-foreground"> when {p.evidence.condition}</span>}
            </p>
            <blockquote className="border-l-2 border-brand/40 pl-2 text-xs [overflow-wrap:anywhere] text-ink-2">“{p.evidence.quote}”</blockquote>
            <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <a href={p.evidence.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-brand hover:underline">{p.report.title} <ExternalLink className="size-3" /></a>
              · <Link to={`/policy/reports/${p.report.id}`} className="hover:underline">report</Link> · {dateTimeIN(p.created_at)}
            </p>
            {p.decision_note && <p className="text-xs">Decision: {p.decision_note}</p>}
            {(p.status === 'OPEN' || p.status === 'DRAFTED') && can(me, 'policy.activate') && (
              <ReasonDialog trigger={<Button size="sm" variant="outline"><XCircle /> Reject</Button>} title={`Reject the proposal for ${p.parameter}`}
                description="The rule stays as it is. The proposal stays on record with your note." label="Why is it rejected?" min={10} confirmLabel="Reject"
                onConfirm={(note) => run(() => api(`/policy/proposals/${p.id}/reject`, { body: { note } }), { ok: 'Proposal rejected', onDone: reload })} />
            )}
          </section>
        ))}
      </div>
    </div>
  )
}
