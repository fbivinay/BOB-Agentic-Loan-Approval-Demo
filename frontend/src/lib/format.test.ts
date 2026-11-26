import { test } from 'node:test'
import assert from 'node:assert/strict'
import { inr, pct, dateIN, dateTimeIN, months } from './format.ts'

test('inr groups in lakhs and keeps paise only when present', () => {
  assert.equal(inr('400000.00'), '₹4,00,000')
  assert.equal(inr('395280.5'), '₹3,95,280.50')
  assert.equal(inr(null), '—')
})

test('inr compact uses L and Cr', () => {
  assert.equal(inr(14800000, { compact: true }), '₹1.48 Cr')
  assert.equal(inr(450000, { compact: true }), '₹4.5 L')
  assert.equal(inr(95000, { compact: true }), '₹95,000')
})

test('pct, dates in IST, months', () => {
  assert.equal(pct(0.709), '70.9%')
  assert.equal(pct(null), '—')
  assert.equal(dateIN('2026-11-26T09:37:43Z'), '26 Nov 2026')
  assert.equal(dateTimeIN('2026-11-26T09:37:43Z'), '26 Nov 2026, 3:07 pm')
  assert.equal(months(36), '36 months')
  assert.equal(months(1), '1 month')
})
