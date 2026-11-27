import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { ColumnDef } from '@tanstack/react-table'
import { PageHead } from '@/components/PageHead'
import { Figure, FiguresPanel } from '@/components/Figure'
import { DataTable } from '@/components/DataTable'
import { Banner } from '@/components/Labels'
import { Pill, StatusPill } from '@/components/StatusPill'
import { Need } from '@/components/ProtectedRoute'
import { useApi } from '@/lib/useApi'
import { dateIN, inr, months } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { QueueRow } from '@/lib/types'

const SCOPES = [{ key: 'all', label: 'All' }, { key: 'mine', label: 'Mine' }, { key: 'unassigned', label: 'Unassigned' }] as const

const columns: ColumnDef<QueueRow>[] = [
  { accessorKey: 'reference', header: 'Reference', cell: ({ row }) => <span className="font-mono text-xs">{row.original.reference}</span> },
  { accessorKey: 'applicant', header: 'Applicant', cell: ({ row: { original: q } }) => <span className="flex items-center gap-2">{q.applicant}{q.relationship === 'NTB' && <Pill>New to bank</Pill>}</span> },
  { id: 'requested', header: 'Requested', accessorFn: (q) => Number(q.requested_amount), cell: ({ row: { original: q } }) => <span className="tabular">{inr(q.requested_amount)} · {months(q.requested_tenure_months)}</span> },
  { id: 'eligible', header: 'Eligible', accessorFn: (q) => Number(q.eligible_amount ?? 0), cell: ({ row }) => <span className="tabular">{inr(row.original.eligible_amount)}</span> },
  { accessorKey: 'ai_recommendation', header: 'AI recommendation (human decides)', cell: ({ row }) => <StatusPill status={row.original.ai_recommendation} /> },
  { accessorKey: 'status', header: 'Status', cell: ({ row: { original: q } }) => (
    <span className="flex flex-wrap items-center gap-1.5">
      <StatusPill status={q.status} />
      {q.referral && q.status === 'FORWARDED' && <span title={q.referral.note ?? undefined}><Pill tone="watch">For Branch Manager</Pill></span>}
      {q.processed_by_me && <span title="Four-eyes: you processed this application, so another manager decides it"><Pill>Processed by you</Pill></span>}
    </span>
  ) },
  { accessorKey: 'forwarded_at', header: 'Forwarded', cell: ({ row }) => <span className="text-xs">{dateIN(row.original.forwarded_at)}</span> },
  { id: 'assigned', header: 'Assigned to', accessorFn: (q) => q.assigned_name ?? '', cell: ({ row: { original: q } }) => q.mine ? <Pill tone="ink">You</Pill> : (q.assigned_name ?? <span className="text-muted-foreground">Nobody</span>) },
]

export function ReviewQueue() {
  return (
    <Need any={['decision.make']}>
      <Queue />
    </Need>
  )
}

function Queue() {
  const navigate = useNavigate()
  const [scope, setScope] = useState<(typeof SCOPES)[number]['key']>('all')
  const [status, setStatus] = useState<string | null>(null)
  const { data, error } = useApi<{ applications: QueueRow[]; label: string }>(`/review/queue?scope=${scope}&limit=200`)
  const rows = data?.applications
  const n = (s: string) => rows?.filter((r) => r.status === s).length
  const shown = status ? rows?.filter((r) => r.status === status) : rows

  return (
    <div className="space-y-6">
      <PageHead eyebrow="For decision" title="Review queue" lede={data?.label ?? 'AI recommendation, human decides.'} />
      <FiguresPanel cols={3}>
        {([['FORWARDED', 'Forwarded', 'waiting to be picked up'], ['UNDER_REVIEW', 'Under review', 'with a reviewer'], ['RECONSIDERATION', 'Reconsideration', 'referred back by a Branch Manager']] as const)
          .map(([s, label, note]) => (
            <Figure key={s} label={label} value={n(s) === undefined ? '—' : String(n(s))} note={note} tone={n(s) ? 'watch' : undefined}
              onClick={() => setStatus(status === s ? null : s)} active={status === s} />
          ))}
      </FiguresPanel>
      <div className="flex gap-1">
        {SCOPES.map((s) => (
          <button key={s.key} type="button" onClick={() => setScope(s.key)}
            className={cn('rounded-full px-3 py-1 text-sm transition-colors', scope === s.key ? 'bg-brand text-white' : 'text-ink-2 hover:bg-white')}>
            {s.label}
          </button>
        ))}
      </div>
      {error ? <Banner tone="problem">{error.message}</Banner> : (
        <DataTable rows={shown} columns={columns} onRowClick={(q) => navigate(`/applications/${q.id}/decision`)}
          empty={scope === 'mine' ? 'Nothing assigned to you.' : 'Nothing waiting for a decision.'} />
      )}
    </div>
  )
}
