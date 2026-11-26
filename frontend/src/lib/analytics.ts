// Shapes the BigQuery gold rows from /analytics/overview for the charts.

export type MonthlyRow = { source: string; month: string; channel: string; role: string; outcome: string; decisions: number; approved: number
  approved_amount: number | null; tat_days_total: number | null }

/** One point per month: decisions split historical/live, and the month's overall approval rate. Months in order. */
export function monthlySeries(rows: MonthlyRow[]): { month: string; historical: number; live: number; approval_rate: number }[] {
  const by = new Map<string, { historical: number; live: number; approved: number }>()
  for (const r of rows) {
    const m = r.month.slice(0, 7)
    const p = by.get(m) ?? { historical: 0, live: 0, approved: 0 }
    if (r.source === 'LIVE') p.live += r.decisions
    else p.historical += r.decisions
    p.approved += r.approved
    by.set(m, p)
  }
  return [...by.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([month, p]) => {
    const total = p.historical + p.live
    return { month, historical: p.historical, live: p.live, approval_rate: total ? p.approved / total : 0 }
  })
}
