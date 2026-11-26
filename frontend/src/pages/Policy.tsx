import { NavLink, Navigate, useParams } from 'react-router-dom'
import { PageHead } from '@/components/PageHead'
import { Need } from '@/components/ProtectedRoute'
import { cn } from '@/lib/utils'
import { KnowledgeTab } from '@/pages/policy/KnowledgeTab'
import { MonitorTab } from '@/pages/policy/MonitorTab'
import { ProposalsTab } from '@/pages/policy/ProposalsTab'
import { RuleSetsTab } from '@/pages/policy/RuleSetsTab'

const TABS = [
  { key: 'knowledge', label: 'Knowledge base', Component: KnowledgeTab },
  { key: 'monitor', label: 'Monitoring', Component: MonitorTab },
  { key: 'proposals', label: 'Proposals', Component: ProposalsTab },
  { key: 'rule-sets', label: 'Rule sets', Component: RuleSetsTab },
]

export function Policy() {
  return (
    <Need any={['policy.view']}>
      <Tabs />
    </Need>
  )
}

function Tabs() {
  const { tab = 'knowledge' } = useParams()
  const current = TABS.find((t) => t.key === tab)
  if (!current) return <Navigate to="/policy" replace />
  return (
    <div className="space-y-6">
      <PageHead eyebrow="Rules and sources" title="Policy"
        lede="What the assessment cites, what the daily monitor found on the official pages, and the rule versions a person approved." />
      <nav className="flex gap-1 border-b border-line" aria-label="Policy sections">
        {TABS.map((t) => (
          <NavLink key={t.key} to={`/policy/${t.key}`}
            className={cn('-mb-px border-b-2 px-3 py-2 text-sm transition-colors', t.key === current.key ? 'border-brand font-medium text-brand' : 'border-transparent text-ink-2 hover:text-ink')}>
            {t.label}
          </NavLink>
        ))}
      </nav>
      <current.Component />
    </div>
  )
}
