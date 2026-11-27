import { PageHead } from '@/components/PageHead'
import { Pill } from '@/components/StatusPill'
import { ROLE_LABEL, useSession } from '@/lib/session'

// As described in supabase/migrations/0004_security_and_seed.sql and 0006 (role_permissions).
const DESCRIPTION: Record<string, string> = {
  'enquiry.manage': 'Create and manage enquiries (drafts)', 'enquiry.view': 'View enquiries',
  'application.intake': 'Convert an enquiry, run intake, upload documents', 'application.view': 'View applications and their documents',
  'document.verify': 'Verify documents, correct extractions, resolve discrepancies, attest', 'application.forward': 'Forward a completed application for review',
  'assessment.view': 'View the AI assessment', 'audit.view': 'View the audit trail', 'decision.make': 'Approve or decline an application',
  'application.takeover': 'Take over an application from another reviewer', 'application.refer': 'Refer an application for reconsideration',
  'terms.adjust': 'Adjust loan terms within demo limits, with a recorded reason', 'policy.view': 'Read the policy knowledge base',
  'policy.activate': 'Activate a policy rule set', 'disbursement.track': 'Track disbursement and record customer acceptance',
  'disbursement.release': 'Release a simulated disbursement', 'disbursement.view': 'View disbursement status', 'analytics.view': 'View analytics',
  'system.view': 'View system status',
}
const CONFIGURABLE = ['disbursement.track', 'disbursement.release', 'policy.activate']

export function Settings() {
  const { me } = useSession()
  if (!me) return null
  const fixed = me.permissions.filter((p) => !CONFIGURABLE.includes(p))
  const demo = me.permissions.filter((p) => CONFIGURABLE.includes(p))
  return (
    <div className="space-y-6">
      <PageHead eyebrow="You" title="Settings" />
      <section className="card space-y-2">
        <h2 className="section-head">Profile</h2>
        <dl className="grid grid-cols-[8rem_1fr] gap-y-1.5 text-sm">
          <dt className="text-muted-foreground">Name</dt><dd>{me.full_name}</dd>
          <dt className="text-muted-foreground">Email</dt><dd>{me.email}</dd>
          <dt className="text-muted-foreground">Role</dt><dd>{ROLE_LABEL[me.role]}</dd>
        </dl>
      </section>
      <div className="grid grid-cols-2 gap-4">
        <section className="card space-y-2">
          <h2 className="section-head">What this role may do</h2>
          <ul className="space-y-1.5 text-sm">{fixed.map((p) => <li key={p} className="flex items-start gap-2"><Pill>{p}</Pill>{DESCRIPTION[p] ?? p}</li>)}</ul>
        </section>
        <section className="card space-y-2">
          <h2 className="section-head">Demo permissions</h2>
          {demo.length === 0 ? <p className="text-sm text-muted-foreground">None for this role.</p>
            : <ul className="space-y-1.5 text-sm">{demo.map((p) => <li key={p} className="flex items-start gap-2"><Pill tone="watch">{p}</Pill>{DESCRIPTION[p] ?? p}</li>)}</ul>}
        </section>
      </div>
    </div>
  )
}
