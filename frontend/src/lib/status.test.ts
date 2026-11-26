import { test } from 'node:test'
import assert from 'node:assert/strict'
import { METHOD_LABEL, statusLabel, tone } from './status.ts'

test('tones follow PLAN 14A', () => {
  for (const s of ['APPROVED', 'SANCTIONED', 'DISBURSED', 'RECOMMENDED_FOR_APPROVAL', 'VERIFIED']) assert.equal(tone(s), 'good', s)
  for (const s of ['DISCREPANCY', 'FORWARDED', 'UNDER_REVIEW', 'RECONSIDERATION', 'REFER_FOR_FURTHER_REVIEW']) assert.equal(tone(s), 'watch', s)
  for (const s of ['DECLINED', 'POTENTIALLY_INELIGIBLE', 'REJECTED', 'FAILED', 'BOUNCED', 'COMPLAINED']) assert.equal(tone(s), 'problem', s)
  for (const s of ['DRAFT', 'INTAKE', 'DOCS_PENDING', 'DEMO_ASSUMPTION', 'SOMETHING_NEW']) assert.equal(tone(s), 'neutral', s)
  assert.equal(tone(null), 'neutral')
})

test('labels', () => {
  assert.equal(statusLabel('REFER_FOR_FURTHER_REVIEW'), 'Refer for further review')
  assert.equal(statusLabel('DOCS_PENDING'), 'Documents pending')
  assert.equal(statusLabel('AI_ASSESSED'), 'AI assessed')
  assert.equal(statusLabel('NTB'), 'New to bank')
  assert.equal(METHOD_LABEL.PREPARED_DEMO_TEXT, 'Prepared demo data — not OCR')
})
