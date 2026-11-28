import { useNavigate } from 'react-router-dom'
import { ExternalLink } from 'lucide-react'
import type { ColumnDef } from '@tanstack/react-table'
import { Figure, FiguresPanel } from '@/components/Figure'
import { DataTable } from '@/components/DataTable'
import { Banner } from '@/components/Labels'
import { StatusPill } from '@/components/StatusPill'
import { useApi } from '@/lib/useApi'
import { dateTimeIN } from '@/lib/format'
import { statusLabel } from '@/lib/status'
import type { MonitorHealth, MonitorRun, ReportRow } from '@/lib/types'

const runColumns: ColumnDef<MonitorRun>[] = [
  { accessorKey: 'started_at', header: 'Started', cell: ({ row }) => <span className="text-xs">{dateTimeIN(row.original.started_at)}</span> },
  { accessorKey: 'trigger', header: 'Trigger', cell: ({ row }) => statusLabel(row.original.trigger) },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => <StatusPill status={row.original.status} /> },
  { accessorKey: 'reports', header: 'Reports' },
  { accessorKey: 'proposals', header: 'Proposals' },
  { id: 'errors', header: 'Errors', accessorFn: (r) => r.errors.length },
  {
    id: 'link', header: 'Run log', cell: ({ row }) => row.original.actions_run_url
      ? <a href={row.original.actions_run_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-brand hover:underline" onClick={(e) => e.stopPropagation()}>GitHub Actions <ExternalLink className="size-3" /></a>
      : <span className="text-muted-foreground">—</span>,
  },
]

const reportColumns: ColumnDef<ReportRow>[] = [
  { accessorKey: 'created_at', header: 'Found', cell: ({ row }) => <span className="text-xs">{dateTimeIN(row.original.created_at)}</span> },
  { accessorKey: 'classification', header: 'Kind', cell: ({ row }) => <StatusPill status={row.original.classification} /> },
  { accessorKey: 'title', header: 'Source', cell: ({ row: { original: r } }) => <span className="whitespace-normal">{r.publisher} · {r.title}</span> },
  { id: 'version', header: 'Version', accessorFn: (r) => (r.from_version ? `${r.from_version} → ${r.to_version}` : `first (${r.to_version})`) },
  {
    id: 'counts', header: 'Items', cell: ({ row: { original: r } }) => (
      <span className="text-xs">{Object.entries(r.counts).filter(([, n]) => n).map(([k, n]) => `${n} ${statusLabel(k).toLowerCase()}`).join(', ') || '—'}</span>
    ),
  },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => <StatusPill status={row.original.status} /> },
]

export function MonitorTab() {
  const navigate = useNavigate()
  const { data: runs, error } = useApi<{ runs: MonitorRun[]; health: MonitorHealth }>('/policy/monitor/runs?limit=20')
  const { data: reports, error: reportsError } = useApi<{ reports: ReportRow[] }>('/policy/reports?limit=100')
  const h = runs?.health
  const open = reports?.reports.filter((r) => r.status === 'NEW').length

  return (
    <div className="space-y-5">
      {error && <Banner tone="problem">{error.message}</Banner>}
      <FiguresPanel cols={3}>
        <Figure label="Last successful run" value={h ? (h.hours_since === null ? 'Never' : `${h.hours_since} h ago`) : error ? '—' : undefined} tone={h?.stale ? 'problem' : 'good'}
          note={h?.stale ? 'stale' : undefined} />
        <Figure label="Reports to review" value={open === undefined ? (reportsError ? '—' : undefined) : String(open)} tone={open ? 'watch' : undefined} />
        <Figure label="Runs shown" value={runs ? String(runs.runs.length) : error ? '—' : undefined} />
      </FiguresPanel>
      <section className="space-y-2">
        <h2 className="section-head">Change reports</h2>
        <DataTable rows={reports?.reports} columns={reportColumns} onRowClick={(r) => navigate(`/policy/reports/${r.id}`)} empty="No change reports yet." />
      </section>
      <section className="space-y-2">
        <h2 className="section-head">Monitor runs</h2>
        <DataTable rows={runs?.runs} columns={runColumns} empty="The monitor has not run yet." />
      </section>
    </div>
  )
}
