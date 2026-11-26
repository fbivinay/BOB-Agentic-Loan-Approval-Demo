// Term adjustment inputs, checked against the demo limits the API returns (terms.limits) before sending. The database checks
// them again, together with the cap on the eligible amount.

export function percentToBp(pct: string): number | null {
  const n = Number(pct)
  return pct.trim() && Number.isFinite(n) ? Math.round(n * 100) : null
}

export function checkTerms(t: { amount: string; rate: string; tenure: string }, limits: Record<string, string>): string | null {
  if (!(Number(t.amount) > 0)) return 'Enter an amount above zero.'
  const bp = percentToBp(t.rate)
  const [lo, hi] = [Number(limits['terms.rate_min_bp']), Number(limits['terms.rate_max_bp'])]
  if (bp === null || (lo && bp < lo) || (hi && bp > hi)) return `The rate must be between ${(lo / 100).toFixed(2)}% and ${(hi / 100).toFixed(2)}% (demo limits).`
  const [tmin, tmax] = [Number(limits['terms.tenure_min_months']), Number(limits['terms.tenure_max_months'])]
  const n = Number(t.tenure)
  if (!Number.isInteger(n) || (tmin && n < tmin) || (tmax && n > tmax)) return `The tenure must be between ${tmin} and ${tmax} months (demo limits).`
  return null
}
