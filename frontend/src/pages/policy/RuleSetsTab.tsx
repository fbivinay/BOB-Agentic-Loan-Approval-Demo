import { useState } from 'react'
import { FilePlus2, ShieldCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { selectClass } from '@/components/Field'
import { ReasonDialog } from '@/components/Dialogs'
import { Banner } from '@/components/Labels'
import { Pill, StatusPill } from '@/components/StatusPill'
import { api } from '@/lib/api'
import { useAction, useApi } from '@/lib/useApi'
import { can, useSession } from '@/lib/session'
import { dateTimeIN } from '@/lib/format'
import type { Proposal, RuleSet } from '@/lib/types'

const SEGMENTS = ['ETB_EST', 'ETB_THIN', 'ETB_NTC', 'NTB_EST', 'NTB_THIN', 'NTB_NTC']
type Change = { on: boolean; value: string; confidence: 'VERIFIED' | 'DEMO_ASSUMPTION'; segment: string }
const show = (v: unknown) => (v && typeof v === 'object' && 'value' in v ? String((v as { value: unknown }).value) : v === undefined || v === null ? '—' : JSON.stringify(v))

/** A candidate version = the active one plus the chosen parameter changes, each citing its proposal. */
function Draft({ open, onDone }: { open: Proposal[]; onDone: () => void }) {
  const run = useAction()
  const first = new Map<string, Proposal>()
  open.forEach((p) => { if (!first.has(p.parameter)) first.set(p.parameter, p) }) // one change per parameter
  const props = [...first.values()]
  const [ch, setCh] = useState<Record<string, Change>>(() => Object.fromEntries(props.map((p) =>
    [p.id, { on: false, value: p.official_value?.number ?? '', confidence: 'VERIFIED', segment: '' }])))
  const chosen = props.filter((p) => ch[p.id]?.on)
  const set = (id: string, v: Partial<Change>) => setCh({ ...ch, [id]: { ...ch[id], ...v } })

  return (
    <ReasonDialog trigger={<Button><FilePlus2 /> Draft a new version</Button>} title="Draft a candidate rule set"
      description="Checked against the golden cases and saved as a draft."
      label="Note for the record" min={10} confirmLabel="Draft and test"
      extra={
        <div className="max-h-80 space-y-2 overflow-auto">
          {props.length === 0 && <p className="text-xs text-muted-foreground">There are no open proposals to draw on.</p>}
          {props.map((p) => (
            <div key={p.id} className="space-y-1.5 rounded-lg border border-line p-2">
              <label className="flex items-center gap-2 text-xs">
                <input type="checkbox" className="accent-brand" checked={ch[p.id].on} onChange={(e) => set(p.id, { on: e.target.checked })} />
                <span className="font-mono">{p.parameter}</span> <span className="text-muted-foreground">now {String(p.current_value)}, official {p.official_value?.printed ?? '—'}</span>
              </label>
              {ch[p.id].on && (
                <div className="grid grid-cols-3 gap-2">
                  <Input aria-label="New value" value={ch[p.id].value} onChange={(e) => set(p.id, { value: e.target.value })} />
                  <select aria-label="Confidence" className={selectClass} value={ch[p.id].confidence} onChange={(e) => set(p.id, { confidence: e.target.value as Change['confidence'] })}>
                    <option value="VERIFIED">VERIFIED (official figure)</option><option value="DEMO_ASSUMPTION">DEMO_ASSUMPTION</option>
                  </select>
                  <select aria-label="Segment" className={selectClass} value={ch[p.id].segment} onChange={(e) => set(p.id, { segment: e.target.value })}>
                    <option value="">All segments</option>{SEGMENTS.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
              )}
            </div>
          ))}
        </div>
      }
      onConfirm={async (note) => {
        if (!chosen.length) return undefined
        const changes = Object.fromEntries(chosen.map((p) => {
          const c = ch[p.id]
          const value = c.value.trim() !== '' && Number.isFinite(Number(c.value)) ? Number(c.value) : c.value
          return [p.parameter, { value, confidence: c.confidence, segment: c.segment || null, proposal_id: p.id }]
        }))
        return run(() => api('/policy/rule-sets/draft', { body: { changes, note } }), { ok: 'Candidate drafted and tested', onDone })
      }} />
  )
}

export function RuleSetsTab() {
  const { me } = useSession()
  const run = useAction()
  const { data, error, reload } = useApi<{ rule_sets: RuleSet[]; label: string }>('/policy/rule-sets')
  const { data: open, reload: reloadOpen } = useApi<{ proposals: Proposal[] }>(can(me, 'policy.activate') ? '/policy/proposals?status=OPEN' : null)
  const done = () => { reload(); reloadOpen() }
  if (error) return <Banner tone="problem">{error.message}</Banner>

  return (
    <div className="space-y-4">
      {can(me, 'policy.activate') && open && <div className="flex justify-end"><Draft open={open.proposals} onDone={done} /></div>}
      {data?.rule_sets.map((r) => (
        <section key={r.id} className="card space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="section-head">Version {r.version}</h2>
              <StatusPill status={r.status} />
              <span className="font-mono text-xs text-muted-foreground">{r.sha256}</span>
              {r.tests.passed !== null && <Pill tone={r.tests.passed ? 'good' : 'problem'}>Golden cases {r.tests.passed ? 'passed' : 'failed'}{r.tests.summary && `: ${r.tests.summary}`}</Pill>}
            </div>
            {r.status === 'DRAFT' && r.tests.passed && can(me, 'policy.activate') && (
              <ReasonDialog trigger={<Button><ShieldCheck /> Activate</Button>} title={`Activate version ${r.version}`}
                description={<>New applications use this version from their intake; applications already in progress keep theirs. {data.label}</>}
                label="Reason for activating" min={10} confirmLabel="Activate"
                onConfirm={(reason) => run(() => api(`/policy/rule-sets/${r.id}/activate`, { body: { reason } }), { ok: `Version ${r.version} is now active`, onDone: done })} />
            )}
          </div>
          {r.approved_by && <p className="text-xs text-muted-foreground">Activated by {r.approved_by}, {dateTimeIN(r.approved_at)}{r.activation_reason && ` — ${r.activation_reason}`}</p>}
          {r.differences_from_active && r.differences_from_active.length > 0 && (
            <ul className="space-y-1 text-xs">
              {r.differences_from_active.map((d, i) => (
                <li key={i}><span className="font-mono">{d.parameter}</span>{d.segment && ` (${d.segment})`}: {show(d.from)} → <strong>{show(d.to)}</strong></li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  )
}
