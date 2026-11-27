import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search } from 'lucide-react'
import type { ColumnDef } from '@tanstack/react-table'
import { Input } from '@/components/ui/input'
import { PageHead } from '@/components/PageHead'
import { DataTable } from '@/components/DataTable'
import { Pill, StatusPill } from '@/components/StatusPill'
import { Banner } from '@/components/Labels'
import { Need } from '@/components/ProtectedRoute'
import { useApi } from '@/lib/useApi'
import { dateIN } from '@/lib/format'
import type { KycRow } from '@/lib/types'

const SOURCE: Record<string, string> = { ACCOUNT_OPENING: 'Account opening', LOAN_ONBOARDING: 'Loan onboarding', KYC_UPDATE: 'KYC update' }

const columns: ColumnDef<KycRow>[] = [
  { accessorKey: 'full_name', header: 'Customer', cell: ({ row: { original: k } }) => (
    <span className="flex items-center gap-2">{k.full_name}{k.customer_id ? <span className="font-mono text-xs text-muted-foreground">{k.customer_id}</span> : <Pill>New to bank</Pill>}</span>
  ) },
  { accessorKey: 'pan', header: 'PAN', cell: ({ row }) => <span className="font-mono text-xs">{row.original.pan}</span> },
  { accessorKey: 'city', header: 'City' },
  { accessorKey: 'kyc_status', header: 'KYC', cell: ({ row }) => <StatusPill status={row.original.kyc_status} /> },
  { accessorKey: 'verified_on', header: 'Verified on', cell: ({ row }) => <span className="text-xs">{dateIN(row.original.verified_on)}</span> },
  { accessorKey: 'source', header: 'Latest change', cell: ({ row }) => <span className="text-xs">{SOURCE[row.original.source] ?? row.original.source} · v{row.original.version}</span> },
]

export function Kyc() {
  return (
    <Need any={['application.view']}>
      <List />
    </Need>
  )
}

function List() {
  const navigate = useNavigate()
  const [typed, setTyped] = useState('')
  const [q, setQ] = useState('')
  const { data, error } = useApi<{ total: number; profiles: KycRow[] }>(`/kyc?limit=200${q ? `&q=${encodeURIComponent(q)}` : ''}`)
  return (
    <div className="space-y-6">
      <PageHead eyebrow="Customers" title="KYC records"
        lede={`Each customer's verified KYC and the documents behind it, with every version kept.${data ? ` ${data.total} records on file.` : ''}`} />
      <Banner>Existing customers' account-opening documents are generated SAMPLE / DEMO documents for synthetic people. New-to-bank customers get a record when their loan documents are verified.</Banner>
      <div className="flex items-center justify-between gap-3">
        <h2 className="section-head">{q ? `Matching "${q}"` : 'Most recently changed'}</h2>
        <form className="relative w-96" onSubmit={(e) => { e.preventDefault(); setQ(typed.trim()) }}>
          <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-8" placeholder="Name, customer ID or PAN" value={typed} onChange={(e) => { setTyped(e.target.value); if (!e.target.value) setQ('') }} />
        </form>
      </div>
      {error ? <Banner tone="problem">{error.message}</Banner> : (
        <DataTable rows={data?.profiles} columns={columns} onRowClick={(k) => navigate(`/kyc/${k.id}`)} empty={q ? 'No KYC record matches that search.' : 'No KYC records yet.'} />
      )}
    </div>
  )
}
