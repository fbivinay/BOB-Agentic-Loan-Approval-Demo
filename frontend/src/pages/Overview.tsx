import { useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import type { ColumnDef } from '@tanstack/react-table'
import { Search } from 'lucide-react'
import { PageHead } from '@/components/PageHead'
import { Figure, FiguresPanel } from '@/components/Figure'
import { DataTable } from '@/components/DataTable'
import { Banner } from '@/components/Labels'
import { StatusPill } from '@/components/StatusPill'
import { Input } from '@/components/ui/input'
import { useApi } from '@/lib/useApi'
import { can, useSession, type Me } from '@/lib/session'
import { dateIN, inr, months, pct } from '@/lib/format'
import type { AppRow, Enquiry, QueueRow } from '@/lib/types'
import { cn } from '@/lib/utils'

function greeting(): string {
  const h = Number(new Intl.DateTimeFormat('en-IN', { hour: 'numeric', hour12: false, timeZone: 'Asia/Kolkata' }).format(new Date()))
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'
}
// A figure's value from a read: undefined (the box shimmers) while it loads, '—' if it failed or this role cannot read it.
const fig = <D,>(q: { data?: D; loading: boolean }, pick: (d: D) => string) => (q.data !== undefined ? pick(q.data) : q.loading ? undefined : '—')
// A typed search matches the name or the reference, ignoring case.
const matches = (q: string, ...fields: (string | null | undefined)[]) => !q || fields.some((f) => f?.toLowerCase().includes(q.toLowerCase()))

const NEXT: Record<string, string> = {
  INTAKE: 'Complete the intake details', DOCS_PENDING: 'Upload and verify documents', DISCREPANCY: 'Resolve discrepancies',
  AI_ASSESSED: 'Forward for review', SANCTIONED: 'Record acceptance and complete the checklist',
}
const appColumns: ColumnDef<AppRow>[] = [
  { accessorKey: 'reference', header: 'Reference', cell: ({ row }) => <span className="font-mono text-xs">{row.original.reference}</span> },
  { accessorKey: 'full_name', header: 'Applicant', cell: ({ row }) => <span className="whitespace-normal">{row.original.full_name}</span> },
  { id: 'requested', header: 'Requested', cell: ({ row: { original: a } }) => <span className="tabular-nums">{inr(a.requested_amount)} · {months(a.requested_tenure_months)}</span> },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => <StatusPill status={row.original.status} /> },
  { id: 'next', header: 'Next step', cell: ({ row }) => <span className="text-xs whitespace-normal">{NEXT[row.original.status]}</span> },
  { accessorKey: 'updated_at', header: 'Updated', cell: ({ row }) => <span className="text-xs">{dateIN(row.original.updated_at)}</span> },
]
const draftColumns: ColumnDef<Enquiry>[] = [
  { accessorKey: 'reference', header: 'Reference', cell: ({ row }) => <span className="font-mono text-xs">{row.original.reference}</span> },
  { id: 'customer', header: 'Customer', cell: ({ row: { original: e } }) => e.applicant.full_name },
  { id: 'requested', header: 'Requested', cell: ({ row: { original: e } }) => <span className="tabular">{inr(e.requested_amount)} · {months(e.requested_tenure_months)}</span> },
  { id: 'next', header: 'Next step', cell: () => <span className="text-xs">Convert or close</span> },
  { accessorKey: 'updated_at', header: 'Updated', cell: ({ row }) => <span className="text-xs">{dateIN(row.original.updated_at)}</span> },
]
const queueColumns: ColumnDef<QueueRow>[] = [
  { accessorKey: 'reference', header: 'Reference', cell: ({ row }) => <span className="font-mono text-xs">{row.original.reference}</span> },
  { accessorKey: 'applicant', header: 'Applicant', cell: ({ row }) => <span className="whitespace-normal">{row.original.applicant}</span> },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => <StatusPill status={row.original.status} /> },
  { id: 'eligible', header: 'Eligible', cell: ({ row }) => <span className="tabular-nums">{inr(row.original.eligible_amount)}</span> },
  { id: 'ai', header: () => <span className="whitespace-normal">AI recommendation (human decides)</span>, cell: ({ row }) => <StatusPill status={row.original.ai_recommendation} /> },
  { accessorKey: 'forwarded_at', header: 'Forwarded', cell: ({ row }) => <span className="text-xs">{dateIN(row.original.forwarded_at)}</span> },
]

/** "Needs you": a heading with a search box, the filters with their counts, then the list. */
function NeedsYou<K extends string>({ filters, show, onShow, q, onQ, children }: {
  filters: { key: K; label: string; count: number | undefined }[]
  show: K
  onShow: (k: K) => void
  q: string
  onQ: (q: string) => void
  children: ReactNode
}) {
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="section-head">Needs you</h2>
        <div className="relative w-80">
          <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-8" placeholder="Name or reference" value={q} onChange={(e) => onQ(e.target.value)} aria-label="Search the list" />
        </div>
      </div>
      {/* The filters as one segmented bar, centred: a white pill holding the options, the chosen one filled with the brand colour. */}
      <div className="flex justify-center">
        <div className="inline-flex w-fit flex-wrap items-center justify-center gap-1 rounded-full border border-zinc-300 bg-surface p-1 shadow-sm" role="group" aria-label="Show">
          {filters.map((f) => (
            <button key={f.key} type="button" onClick={() => onShow(f.key)} aria-pressed={show === f.key}
              className={cn('inline-flex cursor-pointer items-center rounded-full px-[14.5px] py-[8.5px] text-[15.5px] font-medium whitespace-nowrap transition-all',
                show === f.key ? 'bg-brand text-white shadow-sm' : 'text-ink-2 hover:bg-zinc-100 hover:text-foreground')}>
              {f.label}<span className="ml-1.5 tabular-nums opacity-80">{f.count ?? '—'}</span>
            </button>
          ))}
        </div>
      </div>
      {children}
    </section>
  )
}

type OfficerKey = 'all' | 'drafts' | 'intake' | 'discrepancy' | 'forward' | 'disbursement'
const GROUPS: { key: Exclude<OfficerKey, 'all' | 'drafts'>; label: string; statuses: string[]; empty: string }[] = [
  { key: 'intake', label: 'In intake', statuses: ['INTAKE', 'DOCS_PENDING'], empty: 'Nothing in intake.' },
  { key: 'discrepancy', label: 'Discrepancies', statuses: ['DISCREPANCY'], empty: 'No discrepancies to resolve.' },
  { key: 'forward', label: 'Ready to forward', statuses: ['AI_ASSESSED'], empty: 'Nothing ready to forward.' },
  { key: 'disbursement', label: 'Awaiting disbursement', statuses: ['SANCTIONED'], empty: 'Nothing awaiting disbursement.' },
]

function OfficerView() {
  const navigate = useNavigate()
  const [show, setShow] = useState<OfficerKey>('all')
  const [q, setQ] = useState('')
  const toggle = (k: OfficerKey) => setShow(show === k ? 'all' : k)  // a second click on the same box shows everything again
  const drafts = useApi<{ enquiries: Enquiry[]; counts: Record<string, number> }>('/enquiries?status=DRAFT')
  const apps = useApi<{ applications: AppRow[]; counts: Record<string, number> }>('/applications')
  const all = apps.data?.applications
  const by = (...s: string[]) => all?.filter((a) => s.includes(a.status))
  // the figures and filter counts are exact totals from the database, never a count of the rows loaded
  const total = (...s: string[]) => apps.data && s.reduce((t, x) => t + (apps.data!.counts[x] ?? 0), 0)
  const draftTotal = drafts.data && (drafts.data.counts.DRAFT ?? 0)
  const group = GROUPS.find((g) => g.key === show)
  const rows = by(...(group ? group.statuses : Object.keys(NEXT)))?.filter((a) => matches(q, a.full_name, a.reference))
  const draftRows = drafts.data?.enquiries.filter((e) => matches(q, e.applicant.full_name, e.reference))
  const tone = (s: string[]) => (total(...s) ? 'watch' : undefined)
  return (
    <>
      <FiguresPanel cols={5}>
        <Figure label="Enquiry drafts" value={fig(drafts, (d) => String(d.counts.DRAFT ?? 0))} onClick={() => toggle('drafts')} active={show === 'drafts'} />
        {GROUPS.map((g) => (
          <Figure key={g.key} label={g.label} value={fig(apps, () => String(total(...g.statuses)))} tone={g.key === 'intake' || g.key === 'disbursement' ? undefined : tone(g.statuses)}
            onClick={() => toggle(g.key)} active={show === g.key} />
        ))}
      </FiguresPanel>
      <NeedsYou show={show} onShow={setShow} q={q} onQ={setQ} filters={[
        { key: 'all' as OfficerKey, label: 'All', count: total(...Object.keys(NEXT)) },
        { key: 'drafts', label: 'Enquiry drafts', count: draftTotal },
        ...GROUPS.map((g) => ({ key: g.key as OfficerKey, label: g.label, count: total(...g.statuses) })),
      ]}>
        {show === 'drafts'
          ? (drafts.error ? <Banner tone="problem">{drafts.error.message}</Banner> : (
            <DataTable rows={draftRows} columns={draftColumns} onRowClick={(e) => navigate(`/enquiries/${e.id}`)}
              empty={q ? 'Nothing matches that search.' : 'No enquiry drafts.'} />
          ))
          : (apps.error ? <Banner tone="problem">{apps.error.message}</Banner> : (
            <DataTable rows={rows} columns={appColumns} onRowClick={(a) => navigate(`/applications/${a.id}`)}
              empty={q ? 'Nothing matches that search.' : group?.empty ?? 'Nothing waiting on you.'} />
          ))}
      </NeedsYou>
    </>
  )
}

type ManagerKey = 'all' | 'mine' | 'unassigned'

function ManagerView({ me }: { me: Me }) {
  const navigate = useNavigate()
  const [show, setShow] = useState<ManagerKey>('all')
  const [q, setQ] = useState('')
  const toggle = (k: ManagerKey) => setShow(show === k ? 'all' : k)
  const mine = useApi<{ applications: QueueRow[] }>('/review/queue?scope=mine&limit=200')
  const free = useApi<{ applications: QueueRow[] }>('/review/queue?scope=unassigned&limit=200')
  const health = useApi<{ alerts: string[] }>(can(me, 'system.view') ? '/system/health' : null)
  const kpi = useApi<{ headline: { historical: { approval_rate: number | null } } }>(can(me, 'analytics.view') ? '/analytics/overview' : null)
  const both = mine.data && free.data ? [...mine.data.applications, ...free.data.applications.filter((f) => !mine.data!.applications.some((m) => m.id === f.id))] : undefined
  const source = show === 'mine' ? mine.data?.applications : show === 'unassigned' ? free.data?.applications : both
  const rows = source?.filter((r) => matches(q, r.applicant, r.reference))
  const error = mine.error ?? free.error
  return (
    <>
      <FiguresPanel cols={4}>
        <Figure label="My files" value={fig(mine, (d) => String(d.applications.length))} onClick={() => toggle('mine')} active={show === 'mine'} />
        <Figure label="Unassigned" value={fig(free, (d) => String(d.applications.length))} tone={free.data?.applications.length ? 'watch' : undefined}
          onClick={() => toggle('unassigned')} active={show === 'unassigned'} />
        <Figure label="Alerts" value={fig(health, (d) => String(d.alerts.length))} tone={health.data?.alerts.length ? 'problem' : 'good'} onClick={() => navigate('/system')} />
        <Figure label="Historical approval rate" value={fig(kpi, (d) => pct(d.headline.historical.approval_rate))} onClick={() => navigate('/analytics')} />
      </FiguresPanel>
      {health.data?.alerts.map((a) => <Banner key={a} tone="watch">{a}</Banner>)}
      <NeedsYou show={show} onShow={setShow} q={q} onQ={setQ} filters={[
        { key: 'all' as ManagerKey, label: 'All', count: both?.length },
        { key: 'mine', label: 'My files', count: mine.data?.applications.length },
        { key: 'unassigned', label: 'Unassigned', count: free.data?.applications.length },
      ]}>
        {error ? <Banner tone="problem">{error.message}</Banner> : (
          <DataTable rows={rows} columns={queueColumns} onRowClick={(r) => navigate(`/applications/${r.id}/decision`)}
            empty={q ? 'Nothing matches that search.' : show === 'mine' ? 'Nothing assigned to you.' : show === 'unassigned' ? 'Nothing waiting to be picked up.' : 'Nothing waiting on you.'} />
        )}
      </NeedsYou>
    </>
  )
}

export function Overview() {
  const { me } = useSession()
  if (!me) return null
  return (
    <div className="space-y-6">
      <PageHead eyebrow="Today" title={`${greeting()}, ${me.full_name.split(' ')[0]}`} />
      {can(me, 'decision.make') ? <ManagerView me={me} /> : <OfficerView />}
    </div>
  )
}
