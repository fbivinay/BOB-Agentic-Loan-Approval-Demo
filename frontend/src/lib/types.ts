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

export type AppStatus = 'INTAKE' | 'DOCS_PENDING' | 'DISCREPANCY' | 'DOCS_VERIFIED' | 'AI_ASSESSED' | 'FORWARDED' | 'UNDER_REVIEW' | 'APPROVED' | 'DECLINED'
  | 'RECONSIDERATION' | 'SANCTIONED' | 'DISBURSED'

export type AppRow = {
  id: string; reference: string; status: AppStatus; version: number; full_name: string; customer_id: string | null; relationship: 'ETB' | 'NTB'
  requested_amount: string; requested_tenure_months: number; intake_completed: boolean; updated_at: string
}

export type DocRow = {
  id: string; requirement: string; doc_type: string; original_name: string; mime_type: string; size_bytes: number; sha256: string
  status: 'UPLOADED' | 'AWAITING_OCR' | 'NEEDS_MANUAL_REVIEW' | 'EXTRACTED' | 'VERIFIED' | 'REJECTED' | 'WITHDRAWN'; uploaded_at: string; withdrawn_reason: string | null
}

export type Application = {
  id: string; reference: string; status: AppStatus; version: number; enquiry_reference: string | null
  applicant: {
    customer_id: string | null; bank_relationship: 'ETB' | 'NTB'; full_name: string; date_of_birth: string; gender: string | null; mobile: string
    email: string | null; pan: string | null; residency_status: string; is_staff: boolean; employment_type: string; address_line: string | null
    city: string | null; state: string | null; pincode: string | null
  }
  requested: { amount: string; tenure_months: number }
  declared: { monthly_net: string | null; other_lender_emi: string | null; credit_history: string | null }
  terms: { kind: string; amount: string; rate_percent: string; tenure_months: number }[]
  intake: { completed: boolean; completed_at: string | null; missing_details: string[] }
  checklist: {
    frozen: boolean; items: { code: string; label: string; accepts: string[]; uploaded: number }[]; waived: DocItem[]; all_uploaded: boolean
    basis: { bank_relationship: string; kyc_status: string | null; kyc_current: boolean; income_at_bob: boolean } | null; policy: string | null
  }
  documents: DocRow[]
  created_at: string; updated_at: string
}

export type DocReview = {
  document: DocRow & { text_method: string | null; text_method_label: string | null; page_count: number | null; processing_note: string | null; review_note: string | null }
  expected_fields: Record<string, { kind: string; description: string }>
  fields: { field: string; value: string; source: string; source_label: string; text_method: string | null; page_no: number | null; snippet: string | null
    check_note: string | null; model: string | null; updated_at: string }[]
  pages: { page_no: number; text: string; method: string; engine: string | null }[]
  corrections: { field: string; value_before: string | null; value_after: string; corrected_by: string; corrected_at: string }[]
}

export type Discrepancy = {
  id: string; document_id: string | null; rule: string; field: string | null; form_value: string | null; document_value: string | null; detail: string
  status: 'OPEN' | 'RESOLVED'; resolution: string | null; resolution_note: string | null; resolved_by: string | null; resolved_at: string | null; created_at: string
}
