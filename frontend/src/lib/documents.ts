// The document checklist codes and labels, as in backend/app/policy/schemes/baroda_personal_loan.yaml (documents.required).
export const DOC_REQUIREMENTS: { code: string; label: string }[] = [
  { code: 'APPLICATION_FORM', label: 'Signed loan application form' },
  { code: 'PHOTOGRAPH', label: 'Recent passport-size photograph' },
  { code: 'PAN_CARD', label: 'PAN card' },
  { code: 'ADDRESS_PROOF', label: 'Identity and address proof (masked Aadhaar, passport or voter ID)' },
  { code: 'SALARY_SLIPS_3M', label: 'Salary slips for the last 3 months' },
  { code: 'ITR_1Y', label: 'Income tax return with computation of income for the last year' },
  { code: 'BUSINESS_PROOF', label: 'Proof of business (registration or GST certificate)' },
  { code: 'PENSION_PROOF', label: 'Pension payment order' },
  { code: 'BANK_STATEMENT_6M', label: 'Bank statement for the last 6 months' },
]

export const EMPLOYMENT: Record<string, string> = {
  SALARIED_PRIVATE: 'Salaried, private sector', SALARIED_GOVT: 'Salaried, government', SALARIED_PSU: 'Salaried, public sector undertaking',
  SELF_EMPLOYED_PROFESSIONAL: 'Self-employed professional', SELF_EMPLOYED_BUSINESS: 'Self-employed, business', PENSIONER: 'Pensioner',
}

export const CREDIT_HISTORY: Record<string, string> = {
  UNKNOWN: 'Not known yet', ESTABLISHED: 'Established credit history', LIMITED: 'Limited (thin file)', NONE: 'None (new to credit)',
}
