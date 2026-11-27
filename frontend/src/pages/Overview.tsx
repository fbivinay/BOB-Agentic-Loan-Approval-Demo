import { useNavigate } from 'react-router-dom'
import type { ColumnDef } from '@tanstack/react-table'
import { PageHead } from '@/components/PageHead'
import { Figure, FiguresPanel } from '@/components/Figure'
import { DataTable } from '@/components/DataTable'
import { Banner } from '@/components/Labels'
import { StatusPill } from '@/components/StatusPill'
import { useApi } from '@/lib/useApi'
import { can, ROLE_LABEL, useSession, type Me } from '@/lib/session'
import { dateIN, inr, months, pct } from '@/lib/format'
import type { AppRow, Enquiry, QueueRow } from '@/lib/types'

function greeting(): string {
  const h = Number(new Intl.DateTimeFormat('en-IN', { hour: 'numeric', hour12: false, timeZone: 'Asia/Kolkata' }).format(new Date()))
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'
}
const n = (rows: unknown[] | undefined) => (rows === undefined ? '—' : String(rows.length))

const NEXT: Record<string, string> = {
  INTAKE: 'Complete the intake details', DOCS_PENDING: 'Upload and verify documents', DISCREPANCY: 'Resolve discrepancies',
  AI_ASSESSED: 'Forward for review', SANCTIONED: 'Record acceptance and complete the checklist',
}
const appColumns: ColumnDef<AppRow>[] = [
  { accessorKey: 'reference', header: 'Reference', cell: ({ row }) => <span className="font-mono text-xs">{row.original.reference}</span> },
  { accessorKey: 'full_name', header: 'Applicant' },
  { id: 'requested', header: 'Requested', cell: ({ row: { original: a } }) => <span className="tabular">{inr(a.requested_amount)} · {months(a.requested_tenure_months)}</span> },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => <StatusPill status={row.original.status} /> },
  { id: 'next', header: 'Next step', cell: ({ row }) => <span className="text-xs">{NEXT[row.original.status]}</span> },
  { accessorKey: 'updated_at', header: 'Updated', cell: ({ row }) => <span className="text-xs">{dateIN(row.original.updated_at)}</span> },
]
const queueColumns: ColumnDef<QueueRow>[] = [
  { accessorKey: 'reference', header: 'Reference', cell: ({ row }) => <span className="font-mono text-xs">{row.original.reference}</span> },
  { accessorKey: 'applicant', header: 'Applicant' },
  { id: 'eligible', header: 'Eligible', cell: ({ row }) => <span className="tabular">{inr(row.original.eligible_amount)}</span> },
  { id: 'ai', header: 'AI recommendation (human decides)', cell: ({ row }) => <StatusPill status={row.original.ai_recommendation} /> },
  { accessorKey: 'forwarded_at', header: 'Forwarded', cell: ({ row }) => <span className="text-xs">{dateIN(row.original.forwarded_at)}</span> },
]

function OfficerView() {
  const navigate = useNavigate()
  const drafts = useApi<{ enquiries: Enquiry[] }>('/enquiries?status=DRAFT')
  const apps = useApi<{ applications: AppRow[] }>('/applications')
  const all = apps.data?.applications
  const by = (...s: string[]) => all?.filter((a) => s.includes(a.status))
  return (
    <>
      <FiguresPanel cols={5}>
        <Figure label="Enquiry drafts" value={n(drafts.data?.enquiries)} onClick={() => navigate('/enquiries')} />
        <Figure label="In intake" value={n(by('INTAKE', 'DOCS_PENDING'))} />
        <Figure label="Discrepancies" value={n(by('DISCREPANCY'))} tone={by('DISCREPANCY')?.length ? 'watch' : undefined} />
        <Figure label="Ready to forward" value={n(by('AI_ASSESSED'))} tone={by('AI_ASSESSED')?.length ? 'watch' : undefined} />
        <Figure label="Awaiting disbursement" value={n(by('SANCTIONED'))} onClick={() => navigate('/disbursements')} />
      </FiguresPanel>
      <section className="space-y-2">
        <h2 className="section-head">Needs you</h2>
        {apps.error ? <Banner tone="problem">{apps.error.message}</Banner> : (
          <DataTable rows={by(...Object.keys(NEXT))} columns={appColumns} onRowClick={(a) => navigate(`/applications/${a.id}`)} empty="Nothing waiting on you." />
        )}
      </section>
    </>
  )
}

function ManagerView({ me }: { me: Me }) {
  const navigate = useNavigate()
  const mine = useApi<{ applications: QueueRow[] }>('/review/queue?scope=mine&limit=200')
  const free = useApi<{ applications: QueueRow[] }>('/review/queue?scope=unassigned&limit=200')
  const health = useApi<{ alerts: string[] }>(can(me, 'system.view') ? '/system/health' : null)
  const kpi = useApi<{ headline: { historical: { approval_rate: number | null } } }>(can(me, 'analytics.view') ? '/analytics/overview' : null)
  return (
    <>
      <FiguresPanel cols={4}>
        <Figure label="My files" value={n(mine.data?.applications)} onClick={() => navigate('/review')} />
        <Figure label="Unassigned" value={n(free.data?.applications)} tone={free.data?.applications.length ? 'watch' : undefined} onClick={() => navigate('/review')} />
        <Figure label="Alerts" value={n(health.data?.alerts)} tone={health.data?.alerts.length ? 'problem' : 'good'} onClick={() => navigate('/system')} />
        <Figure label="Historical approval rate" value={kpi.error ? '—' : pct(kpi.data?.headline.historical.approval_rate)} onClick={() => navigate('/analytics')} />
      </FiguresPanel>
      {health.data?.alerts.map((a) => <Banner key={a} tone="watch">{a}</Banner>)}
      <section className="space-y-2">
        <h2 className="section-head">Waiting to be picked up</h2>
        {free.error ? <Banner tone="problem">{free.error.message}</Banner> : (
          <DataTable rows={free.data?.applications} columns={queueColumns} onRowClick={(q) => navigate(`/applications/${q.id}/decision`)} empty="Nothing waiting." />
        )}
      </section>
    </>
  )
}

export function Overview() {
  const { me } = useSession()
  if (!me) return null
  return (
    <div className="space-y-6">
      <PageHead eyebrow="Today" title={`${greeting()}, ${me.full_name.split(' ')[0]}`} lede={ROLE_LABEL[me.role]} />
      {can(me, 'decision.make') ? <ManagerView me={me} /> : <OfficerView />}
    </div>
  )
}
