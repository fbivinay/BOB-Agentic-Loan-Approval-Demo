import type { ComponentType } from 'react'
import { NavLink, Navigate, useParams } from 'react-router-dom'
import { PageHead } from '@/components/PageHead'
import { PageSkeleton } from '@/components/AppShell'
import { Figure, FiguresPanel } from '@/components/Figure'
import { AiPill, Banner } from '@/components/Labels'
import { Pill, StatusPill } from '@/components/StatusPill'
import { Need } from '@/components/ProtectedRoute'
import { useApi } from '@/lib/useApi'
import { useSession, type Me } from '@/lib/session'
import { inr, months } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { Application, AppStatus } from '@/lib/types'
import { SummaryTab } from '@/pages/application/SummaryTab'
import { DocumentsTab } from '@/pages/application/DocumentsTab'
import { DiscrepanciesTab } from '@/pages/application/DiscrepanciesTab'
import { AssessmentTab } from '@/pages/application/AssessmentTab'
import { DecisionTab } from '@/pages/application/DecisionTab'
import { HistoryTab } from '@/pages/application/HistoryTab'
import { SanctionTab } from '@/pages/application/SanctionTab'
import { DisbursementTab } from '@/pages/application/DisbursementTab'

export type TabProps = { app: Application; reload: () => void }
type Tab = { key: string; label: string; show: (app: Application, me: Me) => boolean; Component: ComponentType<TabProps> }

const ORDER: AppStatus[] = ['INTAKE', 'DOCS_PENDING', 'DISCREPANCY', 'DOCS_VERIFIED', 'AI_ASSESSED', 'FORWARDED', 'UNDER_REVIEW', 'RECONSIDERATION', 'DECLINED',
  'APPROVED', 'SANCTIONED', 'DISBURSED']
/** True once the application has reached `s` (DECLINED counts as past review, not past approval). */
export const reached = (app: Application, s: AppStatus) => ORDER.indexOf(app.status) >= ORDER.indexOf(s)

// The tabs, in the order the work happens. Later phases add theirs here.
export const TABS: Tab[] = [
  { key: 'summary', label: 'Summary', show: () => true, Component: SummaryTab },
  { key: 'documents', label: 'Documents', show: () => true, Component: DocumentsTab },
  { key: 'discrepancies', label: 'Discrepancies', show: (a) => a.status === 'DISCREPANCY' || reached(a, 'DOCS_VERIFIED'), Component: DiscrepanciesTab },
  { key: 'assessment', label: 'Assessment', show: (a, me) => reached(a, 'DOCS_VERIFIED') && me.permissions.includes('assessment.view'), Component: AssessmentTab },
  { key: 'decision', label: 'Decision', show: (a) => reached(a, 'AI_ASSESSED'), Component: DecisionTab },
  { key: 'sanction', label: 'Sanction pack', show: (a) => reached(a, 'APPROVED'), Component: SanctionTab },
  { key: 'disbursement', label: 'Disbursement', show: (a, me) => reached(a, 'SANCTIONED') && me.permissions.includes('disbursement.view'), Component: DisbursementTab },
  { key: 'history', label: 'History', show: () => true, Component: HistoryTab },
]

const busy = (a: Application) => a.status === 'DOCS_VERIFIED' || a.documents.some((d) => d.status === 'UPLOADED' || d.status === 'AWAITING_OCR')

export function ApplicationDetail() {
  return (
    <Need any={['application.view']}>
      <Detail />
    </Need>
  )
}

function Detail() {
  const { id, tab = 'summary' } = useParams()
  const { me } = useSession()
  // Re-read every 4 s while a document is being processed or the assessment is running, then stop.
  const { data: app, error, reload } = useApi<Application>(`/applications/${id}`, { pollMs: 4000, until: (a) => !busy(a) })
  const assessed = app && reached(app, 'AI_ASSESSED')
  const { data: review } = useApi<{ ai: { recommendation: string | null } }>(assessed ? `/applications/${id}/review` : null)

  if (error) return <Banner tone="problem">{error.message}</Banner>
  if (!app || !me) return <PageSkeleton />
  const tabs = TABS.filter((t) => t.show(app, me))
  const current = tabs.find((t) => t.key === tab)
  if (!current) return <Navigate to={`/applications/${id}`} replace />
  const eligible = [...app.terms].reverse().find((t) => t.kind === 'ELIGIBLE')

  return (
    <div className="space-y-6">
      <PageHead
        eyebrow={`Application · ${app.reference}`}
        title={app.applicant.full_name}
        lede={
          <span className="inline-flex flex-wrap items-center gap-2">
            <StatusPill status={app.status} />
            {app.applicant.bank_relationship === 'NTB' ? <Pill>New to bank</Pill> : <span className="font-mono text-xs">{app.applicant.customer_id}</span>}
            {app.enquiry_reference && <span className="text-xs text-muted-foreground">from {app.enquiry_reference}</span>}
          </span>
        }
      />
      <FiguresPanel cols={3}>
        <Figure label="Requested" value={inr(app.requested.amount)} note={months(app.requested.tenure_months)} />
        <Figure label="Eligible under the policy engine" value={eligible ? inr(eligible.amount) : '—'}
          note={eligible ? `${eligible.rate_percent}% · ${months(eligible.tenure_months)} · demo parameters` : 'worked out at the assessment'} />
        <Figure label="AI recommendation" value={<div className="flex justify-center pt-2 text-sm font-normal tracking-normal"><AiPill recommendation={review?.ai.recommendation} /></div>} />
      </FiguresPanel>
      <nav className="flex gap-1 border-b border-line" aria-label="Application sections">
        {tabs.map((t) => (
          <NavLink key={t.key} to={`/applications/${id}/${t.key}`}
            className={cn('-mb-px border-b-2 px-3 py-2 text-sm transition-colors',
              t.key === current.key ? 'border-brand font-medium text-brand' : 'border-transparent text-ink-2 hover:text-ink')}>
            {t.label}
          </NavLink>
        ))}
      </nav>
      <current.Component app={app} reload={reload} />
    </div>
  )
}
