// Response shapes of the backend API, as the routers in backend/app/routers return them. Amounts are decimal strings.

export type DocItem = { code: string; label: string; reason?: string }

export type Indicative = {
  disclaimer: string
  policy: string
  segment_hint: string
  as_of: string
  likely_eligible: boolean
  notes: string[]
  income_used: string
  existing_emis: string
  rate_range_percent: [string, string]
  rate_basis: string
  tenure_options_months: number[]
  max_tenure_months: number | null
  indicative_max_amount: string
  indicative_by_tenure: Record<string, string>
  requested: { amount: string; tenure_months: number }
  indicative_for_request: { amount: string; tenure_months: number | null; emi_range: [string, string] | null }
  documents: { required: DocItem[]; waived: DocItem[]; available: DocItem[]; missing: DocItem[] }
  inputs: {
    declared_net_income: string
    declared_credit_history: string
    other_lender_emi: string
    requested_amount: string
    requested_tenure_months: number
    documents_available: string[]
    bank_facts: Record<string, unknown> | null
  }
  closed_reason?: string
}

export type Enquiry = {
  id: string
  reference: string
  status: 'DRAFT' | 'CONVERTED' | 'CLOSED'
  version: number
  applicant: { customer_id: string | null; relationship: 'ETB' | 'NTB'; full_name: string; mobile: string; employment_type: string }
  requested_amount: string
  requested_tenure_months: number
  indicative: Indicative
  documents: Indicative['documents']
  converted_application_id: string | null
  created_at: string
  updated_at: string
}

export type CustomerHit = {
  customer_id: string
  full_name: string
  mobile: string
  pan: string
  city: string
  home_branch_name: string
  employment_type: string
  customer_since: string
  kyc_status: string
}
