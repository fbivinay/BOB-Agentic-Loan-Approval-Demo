import { test } from 'node:test'
import assert from 'node:assert/strict'
import { navFor } from './nav.ts'

// Permissions as seeded in supabase/migrations/0004_security_and_seed.sql and 0006_intake_and_documents.sql.
const LO = ['enquiry.manage', 'application.intake', 'document.verify', 'application.forward', 'assessment.view', 'audit.view', 'disbursement.track',
  'disbursement.view', 'policy.view', 'application.view']
const AM = ['enquiry.view', 'assessment.view', 'audit.view', 'decision.make', 'disbursement.release', 'disbursement.view', 'analytics.view', 'system.view',
  'policy.view', 'application.view']
const BM = [...AM, 'application.takeover', 'application.refer', 'terms.adjust', 'policy.activate']

test('navFor per role', () => {
  assert.deepEqual(navFor(LO).map((n) => n.label), ['Overview', 'Enquiries', 'Applications', 'Policy', 'Disbursements'])
  const mgr = ['Overview', 'Enquiries', 'Applications', 'Review queue', 'Policy', 'Disbursements', 'Analytics', 'System status']
  assert.deepEqual(navFor(AM).map((n) => n.label), mgr)
  assert.deepEqual(navFor(BM).map((n) => n.label), mgr)
})

test('no permissions: only Overview', () => {
  assert.deepEqual(navFor([]).map((n) => n.label), ['Overview'])
})
