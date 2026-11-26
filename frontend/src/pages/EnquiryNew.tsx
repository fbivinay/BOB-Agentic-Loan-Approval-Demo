import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2, Search, UserPlus, X } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Field, selectClass } from '@/components/Field'
import { PageHead } from '@/components/PageHead'
import { Banner } from '@/components/Labels'
import { Pill, StatusPill } from '@/components/StatusPill'
import { Need } from '@/components/ProtectedRoute'
import { api, ApiError } from '@/lib/api'
import { checkApplicant, checkLoan, type ApplicantInput } from '@/lib/enquiryForm'
import { EMPLOYMENT } from '@/lib/documents'
import { dateIN } from '@/lib/format'
import type { CustomerHit, Enquiry } from '@/lib/types'
import { emptyLoan, LoanFields, type Loan } from '@/pages/enquiry/LoanFields'

const todayIST = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date())
const blankPerson: ApplicantInput & { gender: string; residency_status: string } = {
  full_name: '', date_of_birth: '', mobile: '', email: '', pan: '', employment_type: 'SALARIED_PRIVATE', declared_net_income: '', gender: '', residency_status: 'RESIDENT',
}
const masked = (m: string) => `•••••• ${m.slice(-4)}`

export function EnquiryNew() {
  return (
    <Need any={['enquiry.manage']}>
      <NewEnquiry />
    </Need>
  )
}

function NewEnquiry() {
  const navigate = useNavigate()
  const [mode, setMode] = useState<'existing' | 'new'>('existing')
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<CustomerHit[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [customer, setCustomer] = useState<CustomerHit | null>(null)
  const [person, setPerson] = useState(blankPerson)
  const [loan, setLoan] = useState<Loan>(emptyLoan)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function search(e: FormEvent) {
    e.preventDefault()
    setSearching(true)
    setSearchError(null)
    try {
      setHits((await api<{ results: CustomerHit[] }>(`/customers/search?q=${encodeURIComponent(q.trim())}`)).results)
    } catch (err) {
      setHits(null)
      setSearchError(err instanceof ApiError ? err.message : 'Search failed.')
    } finally {
      setSearching(false)
    }
  }

  async function submit() {
    const p = (mode === 'new' && checkApplicant(person, todayIST())) || checkLoan(loan) || (mode === 'existing' && !customer && 'Find and pick the customer first.') || null
    setProblem(p)
    if (p) return
    const body: Record<string, unknown> = {
      declared_credit_history: loan.history, other_lender_emi: loan.otherEmi || '0', requested_amount: loan.amount,
      requested_tenure_months: Number(loan.tenure), documents_available: loan.docs,
    }
    if (mode === 'existing') {
      body.customer_id = customer!.customer_id
      if (loan.income) body.declared_net_income = loan.income
    } else {
      body.applicant = {
        full_name: person.full_name.trim(), date_of_birth: person.date_of_birth, mobile: person.mobile, email: person.email || null, pan: person.pan || null,
        gender: person.gender || null, residency_status: person.residency_status, employment_type: person.employment_type, declared_net_income: person.declared_net_income,
      }
    }
    setBusy(true)
    try {
      const e = await api<Enquiry>('/enquiries', { body })
      toast.success(`Enquiry ${e.reference} saved as a draft`)
      navigate(`/enquiries/${e.id}`)
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : 'Saving failed.')
      setBusy(false)
    }
  }

  const setP = (k: keyof typeof person) => (e: { target: { value: string } }) => setPerson({ ...person, [k]: e.target.value })

  return (
    <div className="space-y-6">
      <PageHead eyebrow="Enquiries" title="New enquiry" lede="Find the customer, note what they are asking for, and see an indicative answer. Nothing is decided here." />

      <section className="card space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="section-head">1. Who is asking</h2>
          {mode === 'existing'
            ? <Button variant="outline" size="sm" onClick={() => { setMode('new'); setCustomer(null) }}><UserPlus /> Not a customer? Register a new applicant</Button>
            : <Button variant="outline" size="sm" onClick={() => setMode('existing')}><Search /> Search existing customers instead</Button>}
        </div>

        {mode === 'existing' ? (
          customer ? (
            <div className="flex items-center justify-between rounded-lg border border-border bg-surface px-4 py-3">
              <div>
                <p className="font-medium">{customer.full_name} <span className="font-mono text-xs text-muted-foreground">{customer.customer_id}</span></p>
                <p className="text-xs text-muted-foreground">
                  {masked(customer.mobile)} · {customer.city} · {customer.home_branch_name} · customer since {dateIN(customer.customer_since)} · {EMPLOYMENT[customer.employment_type] ?? customer.employment_type}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <StatusPill status={customer.kyc_status} />
                <Button variant="ghost" size="icon-sm" onClick={() => setCustomer(null)} aria-label="Choose another customer"><X /></Button>
              </div>
            </div>
          ) : (
            <>
              <form className="flex gap-2" onSubmit={search}>
                <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Customer ID (BOB-C0001), mobile, synthetic PAN, account (ACC-000001) or name" />
                <Button type="submit" disabled={q.trim().length < 3 || searching}>{searching ? <Loader2 className="animate-spin" /> : <Search />} Search</Button>
              </form>
              {searchError && <Banner tone="problem">{searchError}</Banner>}
              {hits && hits.length === 0 && <p className="text-sm text-muted-foreground">No existing customer matches. A new-to-bank person is registered as a new applicant.</p>}
              {hits && hits.length > 0 && (
                <ul className="divide-y divide-line rounded-lg border border-border bg-surface">
                  {hits.map((h) => (
                    <li key={h.customer_id}>
                      <button type="button" onClick={() => setCustomer(h)} className="flex w-full items-center justify-between px-4 py-2.5 text-left hover:bg-brand-tint">
                        <span>
                          <span className="font-medium">{h.full_name}</span> <span className="font-mono text-xs text-muted-foreground">{h.customer_id}</span>
                          <span className="block text-xs text-muted-foreground">{masked(h.mobile)} · {h.city} · {h.home_branch_name}</span>
                        </span>
                        <StatusPill status={h.kyc_status} />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )
        ) : (
          <div className="grid grid-cols-3 gap-4">
            <Field label="Full name" htmlFor="full_name"><Input id="full_name" value={person.full_name} onChange={setP('full_name')} /></Field>
            <Field label="Date of birth" htmlFor="dob"><Input id="dob" type="date" max={todayIST()} value={person.date_of_birth} onChange={setP('date_of_birth')} /></Field>
            <Field label="Gender (optional)" htmlFor="gender">
              <select id="gender" className={selectClass} value={person.gender} onChange={setP('gender')}>
                <option value="">Not stated</option><option value="F">Female</option><option value="M">Male</option><option value="O">Other</option>
              </select>
            </Field>
            <Field label="Mobile" htmlFor="mobile"><Input id="mobile" inputMode="numeric" maxLength={10} value={person.mobile} onChange={setP('mobile')} placeholder="98765 01234" /></Field>
            <Field label="Email (optional)" htmlFor="email"><Input id="email" type="email" value={person.email} onChange={setP('email')} /></Field>
            <Field label="Synthetic PAN (optional)" htmlFor="pan" hint="Demo only: SYNP followed by 7 digits and a letter. Real PANs are refused.">
              <Input id="pan" value={person.pan} onChange={(e) => setPerson({ ...person, pan: e.target.value.toUpperCase() })} placeholder="SYNP1234567A" />
            </Field>
            <Field label="Residency" htmlFor="residency">
              <select id="residency" className={selectClass} value={person.residency_status} onChange={setP('residency_status')}>
                <option value="RESIDENT">Resident</option><option value="NRI">Non-resident (NRI)</option>
              </select>
            </Field>
            <Field label="Employment" htmlFor="employment">
              <select id="employment" className={selectClass} value={person.employment_type} onChange={setP('employment_type')}>
                {Object.entries(EMPLOYMENT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </Field>
            <Field label="Declared monthly net income (₹)" htmlFor="ntb_income">
              <Input id="ntb_income" inputMode="numeric" value={person.declared_net_income} onChange={setP('declared_net_income')} />
            </Field>
            <div className="col-span-3"><Pill>New to bank</Pill> <span className="text-xs text-muted-foreground">No bureau or KYC check runs at this stage.</span></div>
          </div>
        )}
      </section>

      <section className="card space-y-4">
        <h2 className="section-head">2. What they are asking for</h2>
        <LoanFields value={loan} onChange={setLoan} incomeLabel={mode === 'existing' ? 'Declared monthly net income (₹, optional)' : undefined} disabled={busy} />
      </section>

      {problem && <Banner tone="problem">{problem}</Banner>}
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={() => navigate('/enquiries')} disabled={busy}>Cancel</Button>
        <Button onClick={submit} disabled={busy}>{busy && <Loader2 className="animate-spin" />} Save draft and check</Button>
      </div>
    </div>
  )
}
