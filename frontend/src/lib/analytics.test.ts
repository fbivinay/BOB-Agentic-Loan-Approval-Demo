import { test } from 'node:test'
import assert from 'node:assert/strict'
import { monthlySeries } from './analytics.ts'

const row = (source: string, month: string, decisions: number, approved: number) =>
  ({ source, month, channel: 'BRANCH', role: 'ASSISTANT_MANAGER', outcome: 'APPROVED', decisions, approved, approved_amount: 0, tat_days_total: 0 })

test('monthlySeries adds up a month across channels and roles, per source', () => {
  const s = monthlySeries([row('HISTORICAL', '2024-01-01', 3, 3), row('HISTORICAL', '2024-01-01', 1, 0), row('HISTORICAL', '2024-02-01', 2, 1),
    row('LIVE', '2026-11-01', 1, 1)])
  assert.deepEqual(s, [
    { month: '2024-01', historical: 4, live: 0, approval_rate: 0.75 },
    { month: '2024-02', historical: 2, live: 0, approval_rate: 0.5 },
    { month: '2026-11', historical: 0, live: 1, approval_rate: 1 },
  ])
})

test('monthlySeries of nothing is empty', () => {
  assert.deepEqual(monthlySeries([]), [])
})
