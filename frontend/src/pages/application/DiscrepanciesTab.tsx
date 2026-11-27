import { useState } from 'react'
import { CheckCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { selectClass } from '@/components/Field'
import { ReasonDialog } from '@/components/Dialogs'
import { Banner } from '@/components/Labels'
import { StatusPill } from '@/components/StatusPill'
import { api } from '@/lib/api'
import { useAction, useApi } from '@/lib/useApi'
import { can, useSession } from '@/lib/session'
import { dateTimeIN } from '@/lib/format'
import { statusLabel } from '@/lib/status'
import type { Discrepancy } from '@/lib/types'
import type { TabProps } from '@/pages/ApplicationDetail'

const RESOLUTIONS: Record<string, string> = {
  ACCEPT: 'Accept: the difference is explained and acceptable', CORRECT: 'Correct: the form or a value was wrong and is now right',
  REQUEST_NEW_DOCUMENT: 'Request a new document from the customer',
}

function Resolve({ app, d, onDone }: { app: TabProps['app']; d: Discrepancy; onDone: () => void }) {
  const run = useAction()
  const [resolution, setResolution] = useState('ACCEPT')
  return (
    <ReasonDialog trigger={<Button size="sm" variant="outline"><CheckCheck /> Resolve</Button>} title={`Resolve: ${statusLabel(d.rule)}`}
      description={d.detail} label="Note for the record" min={10}
      extra={
        <select className={selectClass} value={resolution} onChange={(e) => setResolution(e.target.value)} aria-label="Resolution">
          {Object.entries(RESOLUTIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      }
      confirmLabel="Resolve"
      onConfirm={(note) => run(() => api(`/applications/${app.id}/discrepancies/${d.id}/resolve`, { body: { resolution, note } }), { ok: 'Discrepancy resolved', onDone })} />
  )
}

export function DiscrepanciesTab({ app, reload }: TabProps) {
  const { me } = useSession()
  const { data, error, reload: again } = useApi<{ discrepancies: Discrepancy[] }>(`/applications/${app.id}/discrepancies?v=${app.version}`)
  if (error) return <Banner tone="problem">{error.message}</Banner>
  const items = data?.discrepancies ?? []
  const done = () => { again(); reload() }
  return (
    <section className="card space-y-3">
      <h2 className="section-head">Where the documents and the form disagree</h2>
      {data && items.length === 0 && <p className="text-sm text-muted-foreground">No discrepancies.</p>}
      <ul className="divide-y divide-line">
        {items.map((d) => (
          <li key={d.id} className="flex items-start justify-between gap-4 py-3">
            <div className="space-y-1 text-sm">
              <p className="flex items-center gap-2 font-medium">{statusLabel(d.rule)} <StatusPill status={d.status} /></p>
              <p className="text-xs text-ink-2">{d.detail}</p>
              {(d.form_value || d.document_value) && (
                <p className="text-xs">
                  <span className="text-muted-foreground">Form:</span> {d.form_value ?? '—'} · <span className="text-muted-foreground">Document:</span> {d.document_value ?? '—'}
                </p>
              )}
              {d.resolution && <p className="text-xs text-muted-foreground">{statusLabel(d.resolution)} · {d.resolution_note} · {dateTimeIN(d.resolved_at)}</p>}
            </div>
            {d.status === 'OPEN' && can(me, 'document.verify') && <Resolve app={app} d={d} onDone={done} />}
          </li>
        ))}
      </ul>
    </section>
  )
}
