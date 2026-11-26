import { test } from 'node:test'
import assert from 'node:assert/strict'
import { checkTerms, percentToBp } from './terms.ts'

test('percentToBp rounds to whole basis points', () => {
  assert.equal(percentToBp('12'), 1200)
  assert.equal(percentToBp('12.29'), 1229)
  assert.equal(percentToBp('10.5'), 1050)
  assert.equal(percentToBp('abc'), null)
})

const limits = { 'terms.rate_min_bp': '1050', 'terms.rate_max_bp': '1900', 'terms.tenure_min_months': '12', 'terms.tenure_max_months': '60' }

test('checkTerms shows the demo limits before sending', () => {
  assert.equal(checkTerms({ amount: '250000', rate: '12', tenure: '36' }, limits), null)
  assert.equal(checkTerms({ amount: '0', rate: '12', tenure: '36' }, limits), 'Enter an amount above zero.')
  assert.equal(checkTerms({ amount: '250000', rate: '9.99', tenure: '36' }, limits), 'The rate must be between 10.50% and 19.00% (demo limits).')
  assert.equal(checkTerms({ amount: '250000', rate: '12', tenure: '72' }, limits), 'The tenure must be between 12 and 60 months (demo limits).')
})
