// Browser-side checks that mirror the API's own (backend/app/routers/enquiries.py), so a person sees the problem before
// sending. The API still checks everything. Each returns the first problem as a sentence, or null.

export type LoanInput = { amount: string; tenure: string; otherEmi: string }
export type ApplicantInput = {
  full_name: string; date_of_birth: string; mobile: string; email: string; pan: string; employment_type: string; declared_net_income: string
}

export function checkLoan(v: LoanInput): string | null {
  const amount = Number(v.amount)
  if (!(amount > 0)) return 'Enter the loan amount the customer is asking for.'
  if (amount > 10_000_000) return 'The amount can be at most ₹1,00,00,000.'
  if (!/^\d+$/.test(v.tenure) || +v.tenure < 1 || +v.tenure > 360) return 'Tenure is a whole number of months from 1 to 360.'
  if (v.otherEmi !== '' && !(Number(v.otherEmi) >= 0)) return "Other lenders' EMI cannot be negative."
  return null
}

export function checkApplicant(a: ApplicantInput, today: string): string | null {
  const name = a.full_name.trim()
  if (name.length < 2) return 'Name: enter the applicant\'s full name.'
  if (!/^[A-Za-z][A-Za-z .'-]+$/.test(name)) return 'Name: letters, spaces, dots, apostrophes and hyphens only.'
  if (!a.date_of_birth) return 'Enter the date of birth.'
  if (a.date_of_birth >= today) return 'Date of birth must be in the past.'
  if (!/^[6-9]\d{9}$/.test(a.mobile)) return 'Mobile: 10 digits starting with 6, 7, 8 or 9.'
  if (a.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(a.email)) return 'Email: enter a valid address, or leave it empty.'
  if (a.pan && !/^SYNP\d{7}[A-Z]$/.test(a.pan)) return 'PAN: this demo accepts synthetic PANs only (SYNP followed by 7 digits and a letter).'
  if (!a.employment_type) return 'Choose the employment type.'
  if (!(Number(a.declared_net_income) > 0)) return 'Enter the declared monthly net income.'
  return null
}
