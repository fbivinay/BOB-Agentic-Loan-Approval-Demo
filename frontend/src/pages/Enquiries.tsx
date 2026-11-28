import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, Search } from 'lucide-react'
import type { ColumnDef } from '@tanstack/react-table'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { PageHead } from '@/components/PageHead'
import { Figure, FiguresPanel } from '@/components/Figure'
import { DataTable } from '@/components/DataTable'
import { Pill, StatusPill } from '@/components/StatusPill'
import { Need } from '@/components/ProtectedRoute'
import { useApi } from '@/lib/useApi'
import { can, useSession } from '@/lib/session'
import { dateTimeIN, inr, months } from '@/lib/format'
import type { Enquiry } from '@/lib/types'

const STATUSES = ['DRAFT', 'CONVERTED', 'CLOSED'] as const
type Status = (typeof STATUSES)[number]

const columns: ColumnDef<Enquiry>[] = [
  { accessorKey: 'reference', header: 'Reference', cell: ({ row }) => <span className="font-mono text-xs">{row.original.reference}</span> },
  {
    id: 'applicant', header: 'Applicant', accessorFn: (e) => e.applicant.full_name,
    cell: ({ row: { original: e } }) => (
      <span className="flex flex-col">
        <span>{e.applicant.full_name}</span>
        <span className="text-xs text-muted-foreground">{e.applicant.customer_id ?? <Pill className="mt-0.5">New to bank</Pill>}</span>
      </span>
    ),
  },
  {
    id: 'requested', header: 'Requested', accessorFn: (e) => Number(e.requested_amount),
    cell: ({ row: { original: e } }) => <span className="tabular">{inr(e.requested_amount)} · {months(e.requested_tenure_months)}</span>,
  },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => <StatusPill status={row.original.status} /> },
  { accessorKey: 'updated_at', header: 'Updated', cell: ({ row }) => <span className="text-xs">{dateTimeIN(row.original.updated_at)}</span> },
]

export function Enquiries() {
  return (
    <Need any={['enquiry.manage', 'enquiry.view']}>
      <EnquiryList />
    </Need>
  )
}

function EnquiryList() {
  const { me } = useSession()
  const navigate = useNavigate()
  const [filter, setFilter] = useState<Status | null>('DRAFT')
  const [typed, setTyped] = useState('')
  const [q, setQ] = useState('')
  const qs = q ? `&q=${encodeURIComponent(q)}` : ''
  const lists = {
    DRAFT: useApi<{ enquiries: Enquiry[]; counts: Record<string, number> }>(`/enquiries?status=DRAFT${qs}`),
    CONVERTED: useApi<{ enquiries: Enquiry[]; counts: Record<string, number> }>(`/enquiries?status=CONVERTED${qs}`),
    CLOSED: useApi<{ enquiries: Enquiry[]; counts: Record<string, number> }>(`/enquiries?status=CLOSED${qs}`),
  }
  // exact totals from the database (every list carries them), not a count of the rows on screen
  const counts = (lists.DRAFT.data ?? lists.CONVERTED.data ?? lists.CLOSED.data)?.counts
  const count = (s: Status) => (counts ? String(counts[s] ?? 0) : STATUSES.every((x) => lists[x].error) ? '—' : undefined)
  const rows = filter ? lists[filter].data?.enquiries
    : STATUSES.some((s) => !lists[s].data) ? undefined
    : STATUSES.flatMap((s) => lists[s].data!.enquiries).sort((a, b) => b.updated_at.localeCompare(a.updated_at))
  const error = STATUSES.map((s) => lists[s].error).find(Boolean)

  return (
    <div className="space-y-6">
      <PageHead
        eyebrow="Before applying"
        title="Enquiries"
        actions={can(me, 'enquiry.manage') && <Button onClick={() => navigate('/enquiries/new')}><Plus /> New enquiry</Button>}
      />
      <FiguresPanel cols={3}>
        <Figure label="Drafts" value={count('DRAFT')} onClick={() => setFilter(filter === 'DRAFT' ? null : 'DRAFT')}
          active={filter === 'DRAFT'} />
        <Figure label="Converted" value={count('CONVERTED')} tone="good"
          onClick={() => setFilter(filter === 'CONVERTED' ? null : 'CONVERTED')} active={filter === 'CONVERTED'} />
        <Figure label="Closed" value={count('CLOSED')}
          onClick={() => setFilter(filter === 'CLOSED' ? null : 'CLOSED')} active={filter === 'CLOSED'} />
      </FiguresPanel>
      <div className="flex items-center justify-between gap-3">
        <h2 className="section-head">{filter ? `${filter[0]}${filter.slice(1).toLowerCase()} enquiries` : 'All enquiries'}</h2>
        <form className="relative w-96" onSubmit={(e) => { e.preventDefault(); setQ(typed.trim()) }}>
          <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-8" placeholder="Reference, mobile, name or customer ID" value={typed}
            onChange={(e) => { setTyped(e.target.value); if (!e.target.value) setQ('') }} />
        </form>
      </div>
      {error ? <p className="text-sm text-problem">{error.message}</p> : (
        <DataTable rows={rows} columns={columns} onRowClick={(e) => navigate(`/enquiries/${e.id}`)}
          empty={q ? 'No enquiry matches that search.' : 'No enquiries here yet.'} />
      )}
    </div>
  )
}
