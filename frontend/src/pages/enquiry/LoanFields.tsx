import { Field, selectClass } from '@/components/Field'
import { Input } from '@/components/ui/input'
import { CREDIT_HISTORY, DOC_REQUIREMENTS } from '@/lib/documents'

export type Loan = { amount: string; tenure: string; otherEmi: string; history: string; income: string; docs: string[] }

/** The loan the customer is asking about, and which documents they already have with them. */
export function LoanFields({ value, onChange, incomeLabel, disabled }: {
  value: Loan
  onChange: (v: Loan) => void
  incomeLabel?: string // shown for an existing customer, whose income otherwise comes from the bank's records
  disabled?: boolean
}) {
  const set = (k: keyof Loan) => (e: { target: { value: string } }) => onChange({ ...value, [k]: e.target.value })
  const toggle = (code: string) => onChange({ ...value, docs: value.docs.includes(code) ? value.docs.filter((d) => d !== code) : [...value.docs, code] })
  return (
    <fieldset disabled={disabled} className="space-y-4">
      <div className="grid grid-cols-3 gap-4">
        <Field label="Loan amount (₹)" htmlFor="amount">
          <Input id="amount" inputMode="numeric" value={value.amount} onChange={set('amount')} placeholder="300000" />
        </Field>
        <Field label="Tenure (months)" htmlFor="tenure">
          <Input id="tenure" inputMode="numeric" value={value.tenure} onChange={set('tenure')} placeholder="36" />
        </Field>
        <Field label="EMIs to other lenders (₹ a month)" htmlFor="otherEmi">
          <Input id="otherEmi" inputMode="numeric" value={value.otherEmi} onChange={set('otherEmi')} placeholder="0" />
        </Field>
        <Field label="Credit history, as the customer describes it" htmlFor="history">
          <select id="history" className={selectClass} value={value.history} onChange={set('history')}>
            {Object.entries(CREDIT_HISTORY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Field>
        {incomeLabel && (
          <Field label={incomeLabel} htmlFor="income" hint="Leave empty to use the income on the bank's records.">
            <Input id="income" inputMode="numeric" value={value.income} onChange={set('income')} />
          </Field>
        )}
      </div>
      <div className="space-y-2">
        <p className="text-sm font-medium">Documents the customer has with them</p>
        <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
          {DOC_REQUIREMENTS.map((d) => (
            <label key={d.code} className="flex items-start gap-2 text-xs">
              <input type="checkbox" className="mt-0.5 accent-brand" checked={value.docs.includes(d.code)} onChange={() => toggle(d.code)} />
              {d.label}
            </label>
          ))}
        </div>
      </div>
    </fieldset>
  )
}

export const emptyLoan: Loan = { amount: '', tenure: '36', otherEmi: '0', history: 'UNKNOWN', income: '', docs: [] }
