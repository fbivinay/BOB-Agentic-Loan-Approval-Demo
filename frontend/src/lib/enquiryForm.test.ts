import { test } from 'node:test'
import assert from 'node:assert/strict'
import { checkApplicant, checkLoan } from './enquiryForm.ts'

const loan = { amount: '300000', tenure: '36', otherEmi: '0' }
const person = { full_name: 'Asha Verma', date_of_birth: '1992-04-10', mobile: '9876501234', email: '', pan: '', employment_type: 'SALARIED_PRIVATE',
  declared_net_income: '65000' }

test('checkLoan mirrors the API limits', () => {
  assert.equal(checkLoan(loan), null)
  assert.equal(checkLoan({ ...loan, amount: '0' }), 'Enter the loan amount the customer is asking for.')
  assert.equal(checkLoan({ ...loan, amount: '10000001' }), 'The amount can be at most ₹1,00,00,000.')
  assert.equal(checkLoan({ ...loan, tenure: '36.5' }), 'Tenure is a whole number of months from 1 to 360.')
  assert.equal(checkLoan({ ...loan, otherEmi: '-1' }), "Other lenders' EMI cannot be negative.")
})

test('checkApplicant mirrors the API patterns', () => {
  const today = '2026-11-26'
  assert.equal(checkApplicant(person, today), null)
  assert.equal(checkApplicant({ ...person, mobile: '5876501234' }, today), 'Mobile: 10 digits starting with 6, 7, 8 or 9.')
  assert.equal(checkApplicant({ ...person, pan: 'ABCDE1234F' }, today), 'PAN: this demo accepts synthetic PANs only (SYNP followed by 7 digits and a letter).')
  assert.equal(checkApplicant({ ...person, full_name: 'A1' }, today), 'Name: letters, spaces, dots, apostrophes and hyphens only.')
  assert.equal(checkApplicant({ ...person, date_of_birth: '2026-11-26' }, today), 'Date of birth must be in the past.')
  assert.equal(checkApplicant({ ...person, email: 'not-an-email' }, today), 'Email: enter a valid address, or leave it empty.')
  assert.equal(checkApplicant({ ...person, declared_net_income: '' }, today), 'Enter the declared monthly net income.')
})
