import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search } from 'lucide-react'
import type { ColumnDef } from '@tanstack/react-table'
import { Input } from '@/components/ui/input'
import { PageHead } from '@/components/PageHead'
import { Figure, FiguresPanel } from '@/components/Figure'
import { DataTable } from '@/components/DataTable'
import { Pill, StatusPill } from '@/components/StatusPill'
import { Banner } from '@/components/Labels'
import { Need } from '@/components/ProtectedRoute'
import { useApi } from '@/lib/useApi'
import { dateTimeIN, inr, months } from '@/lib/format'
import type { AppRow, AppStatus } from '@/lib/types'

const LIMIT = 50 // the API returns the 50 most recently updated
const GROUPS: { key: string; label: string; statuses: AppStatus[]; tone?: 'good' | 'watch' }[] = [
  { key: 'intake', label: 'In intake', statuses: ['INTAKE', 'DOCS_PENDING'] },
  { key: 'discrepancy', label: 'Discrepancies', statuses: ['DISCREPANCY'], tone: 'watch' },
  { key: 'review', label: 'With reviewers', statuses: ['DOCS_VERIFIED', 'AI_ASSESSED', 'FORWARDED', 'UNDER_REVIEW', 'RECONSIDERATION'] },
  { key: 'approved', label: 'Approved or sanctioned', statuses: ['APPROVED', 'SANCTIONED'], tone: 'good' },
  { key: 'disbursed', label: 'Disbursed', statuses: ['DISBURSED'], tone: 'good' },
]

const columns: ColumnDef<AppRow>[] = [
  { accessorKey: 'reference', header: 'Reference', cell: ({ row }) => <span className="font-mono text-xs">{row.original.reference}</span> },
  {
    id: 'applicant', header: 'Applicant', accessorFn: (a) => a.full_name,
    cell: ({ row: { original: a } }) => (
      <span className="flex items-center gap-2">
        {a.full_name}
        {a.relationship === 'NTB' ? <Pill>New to bank</Pill> : <span className="font-mono text-xs text-muted-foreground">{a.customer_id}</span>}
      </span>
    ),
  },
  {
    id: 'requested', header: 'Requested', accessorFn: (a) => Number(a.requested_amount),
    cell: ({ row: { original: a } }) => <span className="tabular">{inr(a.requested_amount)} · {months(a.requested_tenure_months)}</span>,
  },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => <StatusPill status={row.original.status} /> },
  { accessorKey: 'updated_at', header: 'Updated', cell: ({ row }) => <span className="text-xs">{dateTimeIN(row.original.updated_at)}</span> },
]

export function Applications() {
  return (
    <Need any={['application.view']}>
      <List />
    </Need>
  )
}

function List() {
  const navigate = useNavigate()
  const [group, setGroup] = useState<string | null>(null)
  const [typed, setTyped] = useState('')
  const [q, setQ] = useState('')
  const { data, error } = useApi<{ applications: AppRow[] }>(`/applications${q ? `?q=${encodeURIComponent(q)}` : ''}`)
  const all = data?.applications
  const full = (all?.length ?? 0) >= LIMIT
  const inGroup = (key: string) => all?.filter((a) => GROUPS.find((g) => g.key === key)!.statuses.includes(a.status))
  const rows = group ? inGroup(group) : all

  return (
    <div className="space-y-6">
      <PageHead eyebrow="Formal applications" title="Applications" />
      <FiguresPanel cols={5}>
        {GROUPS.map((g) => {
          const n = inGroup(g.key)?.length
          return <Figure key={g.key} label={g.label} value={n === undefined ? '—' : `${n}${full ? '+' : ''}`} tone={n ? g.tone : undefined}
            onClick={() => setGroup(group === g.key ? null : g.key)} active={group === g.key} />
        })}
      </FiguresPanel>
      <div className="flex items-center justify-between gap-3">
        <h2 className="section-head">{group ? GROUPS.find((g) => g.key === group)!.label : 'All applications'}</h2>
        <form className="relative w-96" onSubmit={(e) => { e.preventDefault(); setQ(typed.trim()) }}>
          <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-8" placeholder="Reference, mobile, name or customer ID" value={typed}
            onChange={(e) => { setTyped(e.target.value); if (!e.target.value) setQ('') }} />
        </form>
      </div>
      {error ? <Banner tone="problem">{error.message}</Banner> : (
        <DataTable rows={rows} columns={columns} onRowClick={(a) => navigate(`/applications/${a.id}`)}
          empty={q ? 'No application matches that search.' : 'No applications here yet. They start from a converted enquiry.'} />
      )}
    </div>
  )
}
