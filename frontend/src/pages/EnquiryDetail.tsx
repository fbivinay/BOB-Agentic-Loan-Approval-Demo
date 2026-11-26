import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowRight, Calculator, FileCheck2, XCircle } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { PageHead } from '@/components/PageHead'
import { PageSkeleton } from '@/components/AppShell'
import { ConfirmDialog, ReasonDialog } from '@/components/Dialogs'
import { Banner } from '@/components/Labels'
import { Pill, StatusPill } from '@/components/StatusPill'
import { Need } from '@/components/ProtectedRoute'
import { api } from '@/lib/api'
import { useAction, useApi } from '@/lib/useApi'
import { can, useSession } from '@/lib/session'
import { checkLoan } from '@/lib/enquiryForm'
import { EMPLOYMENT } from '@/lib/documents'
import { dateTimeIN } from '@/lib/format'
import type { Enquiry } from '@/lib/types'
import { Indicative } from '@/pages/enquiry/Indicative'
import { LoanFields, type Loan } from '@/pages/enquiry/LoanFields'

const loanOf = (e: Enquiry): Loan => {
  const i = e.indicative.inputs
  return { amount: String(Number(i.requested_amount)), tenure: String(i.requested_tenure_months), otherEmi: String(Number(i.other_lender_emi)),
    history: i.declared_credit_history, income: String(Number(i.declared_net_income)), docs: i.documents_available }
}

export function EnquiryDetail() {
  return (
    <Need any={['enquiry.manage', 'enquiry.view']}>
      <Detail />
    </Need>
  )
}

function Detail() {
  const { id } = useParams()
  const { me } = useSession()
  const navigate = useNavigate()
  const run = useAction()
  const { data: e, error, reload } = useApi<Enquiry>(`/enquiries/${id}`)
  const [loan, setLoan] = useState<Loan | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  useEffect(() => { if (e) setLoan(loanOf(e)) }, [e])

  if (error) return <Banner tone="problem">{error.message}</Banner>
  if (!e || !loan) return <PageSkeleton />
  const editable = e.status === 'DRAFT' && can(me, 'enquiry.manage')

  async function recalculate() {
    const p = checkLoan(loan!)
    setProblem(p)
    if (p) return
    const before = loanOf(e!)
    const body: Record<string, unknown> = { expected_version: e!.version }
    if (loan!.amount !== before.amount) body.requested_amount = loan!.amount
    if (loan!.tenure !== before.tenure) body.requested_tenure_months = Number(loan!.tenure)
    if (loan!.otherEmi !== before.otherEmi) body.other_lender_emi = loan!.otherEmi || '0'
    if (loan!.history !== before.history) body.declared_credit_history = loan!.history
    if (loan!.income !== before.income && loan!.income) body.declared_net_income = loan!.income
    if ([...loan!.docs].sort().join() !== [...before.docs].sort().join()) body.documents_available = loan!.docs
    if (Object.keys(body).length === 1) return setProblem('Nothing has changed.')
    await run(() => api(`/enquiries/${e!.id}`, { method: 'PATCH', body }), { ok: 'Indicative figures updated', onDone: reload })
  }

  async function convert() {
    const r = await run(() => api<{ application_id: string; reference: string; warnings: string[] }>(`/enquiries/${e!.id}/convert`,
      { body: { expected_version: e!.version, confirm: true } }), { onDone: reload })
    if (r) {
      toast.success(`Application ${r.reference} created`)
      r.warnings.forEach((w) => toast.warning(w))
      navigate(`/applications/${r.application_id}`)
    }
    return r
  }

  return (
    <div className="space-y-6">
      <PageHead
        eyebrow={`Enquiry · ${e.reference}`}
        title={e.applicant.full_name}
        lede={
          <span className="inline-flex flex-wrap items-center gap-2">
            <StatusPill status={e.status} />
            {e.applicant.customer_id ? <span className="font-mono text-xs">{e.applicant.customer_id}</span> : <Pill>New to bank</Pill>}
            <span>{EMPLOYMENT[e.applicant.employment_type] ?? e.applicant.employment_type}</span>
            <span className="text-xs text-muted-foreground">updated {dateTimeIN(e.updated_at)}</span>
          </span>
        }
        actions={editable && (
          <>
            <ReasonDialog trigger={<Button variant="outline"><XCircle /> Close</Button>} title="Close this enquiry"
              description="The customer has decided not to go ahead for now. The draft stays on record." label="Why is it being closed?" min={5} max={500}
              confirmLabel="Close enquiry"
              onConfirm={(reason) => run(() => api(`/enquiries/${e.id}/close`, { body: { expected_version: e.version, reason } }), { ok: 'Enquiry closed', onDone: reload })} />
            {can(me, 'application.intake') && (
              <ConfirmDialog trigger={<Button><FileCheck2 /> Convert to application</Button>} title="Convert to a formal application"
                body={<p>The customer has chosen to apply formally. This creates the application; documents, checks and assessment start from intake.</p>}
                confirmLabel="Create application" onConfirm={convert} />
            )}
          </>
        )}
      />

      {e.status === 'CONVERTED' && e.converted_application_id && (
        <Banner tone="good">
          This enquiry is now a formal application.{' '}
          <Link className="font-medium underline" to={`/applications/${e.converted_application_id}`}>Open the application <ArrowRight className="inline size-3" /></Link>
        </Banner>
      )}
      {e.status === 'CLOSED' && <Banner>Closed: {e.indicative.closed_reason ?? 'no reason recorded'}</Banner>}

      <Indicative est={e.indicative} />

      {editable && (
        <section className="card space-y-4">
          <h2 className="section-head">Change the request</h2>
          <LoanFields value={loan} onChange={setLoan} incomeLabel={e.applicant.relationship === 'ETB' ? 'Declared monthly net income (₹)' : undefined} />
          {problem && <Banner tone="problem">{problem}</Banner>}
          <div className="flex justify-end">
            <Button variant="outline" onClick={recalculate}><Calculator /> Recalculate</Button>
          </div>
        </section>
      )}
    </div>
  )
}
