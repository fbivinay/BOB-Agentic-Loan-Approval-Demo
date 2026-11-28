import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { ColumnDef } from '@tanstack/react-table'
import { PageHead } from '@/components/PageHead'
import { Figure, FiguresPanel } from '@/components/Figure'
import { DataTable } from '@/components/DataTable'
import { Banner } from '@/components/Labels'
import { StatusPill } from '@/components/StatusPill'
import { Need } from '@/components/ProtectedRoute'
import { useApi } from '@/lib/useApi'
import { dateTimeIN, inr, months } from '@/lib/format'
import type { AppRow } from '@/lib/types'

const columns: ColumnDef<AppRow>[] = [
  { accessorKey: 'reference', header: 'Reference', cell: ({ row }) => <span className="font-mono text-xs">{row.original.reference}</span> },
  { accessorKey: 'full_name', header: 'Applicant' },
  { id: 'requested', header: 'Requested', accessorFn: (a) => Number(a.requested_amount), cell: ({ row: { original: a } }) => <span className="tabular">{inr(a.requested_amount)} · {months(a.requested_tenure_months)}</span> },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => <StatusPill status={row.original.status} /> },
  { accessorKey: 'updated_at', header: 'Updated', cell: ({ row }) => <span className="text-xs">{dateTimeIN(row.original.updated_at)}</span> },
]

export function Disbursements() {
  return (
    <Need any={['disbursement.view']}>
      <List />
    </Need>
  )
}

function List() {
  const navigate = useNavigate()
  const [filter, setFilter] = useState<'SANCTIONED' | 'DISBURSED'>('SANCTIONED')
  const sanctioned = useApi<{ applications: AppRow[]; counts: Record<string, number> }>('/applications?status=SANCTIONED')
  const disbursed = useApi<{ applications: AppRow[]; counts: Record<string, number> }>('/applications?status=DISBURSED')
  // exact totals from the database, not a count of the rows on screen
  const counts = (sanctioned.data ?? disbursed.data)?.counts
  const n = (s: 'SANCTIONED' | 'DISBURSED') => (counts ? String(counts[s] ?? 0) : sanctioned.error && disbursed.error ? '—' : undefined)
  const current = filter === 'SANCTIONED' ? sanctioned : disbursed
  return (
    <div className="space-y-6">
      <PageHead eyebrow="After sanction" title="Disbursements" />
      <Banner tone="watch">Simulated: no money moves.</Banner>
      <FiguresPanel cols={2}>
        <Figure label="Awaiting acceptance, checklist or release" value={n('SANCTIONED')} onClick={() => setFilter('SANCTIONED')} active={filter === 'SANCTIONED'} />
        <Figure label="Disbursed" value={n('DISBURSED')} tone="good" onClick={() => setFilter('DISBURSED')} active={filter === 'DISBURSED'} />
      </FiguresPanel>
      {current.error ? <Banner tone="problem">{current.error.message}</Banner> : (
        <DataTable rows={current.data?.applications} columns={columns} onRowClick={(a) => navigate(`/applications/${a.id}/disbursement`)}
          empty={filter === 'SANCTIONED' ? 'Nothing waiting for disbursement.' : 'Nothing disbursed yet.'} />
      )}
    </div>
  )
}
