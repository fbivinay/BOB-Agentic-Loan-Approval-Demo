// Indian formats: rupees in lakh/crore grouping, dates and times in IST.

const whole = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 })
const paise = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const day = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Kolkata' })
const time = new Intl.DateTimeFormat('en-IN', { hour: 'numeric', minute: '2-digit', hour12: true, timeZone: 'Asia/Kolkata' })

const trim = (n: number) => String(Number(n.toFixed(2)))

/** ₹4,00,000; paise only when present; compact: ₹4.5 L, ₹1.48 Cr. API amounts arrive as decimal strings. */
export function inr(v: string | number | null | undefined, opts: { compact?: boolean } = {}): string {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  const sign = n < 0 ? '-' : ''
  const a = Math.abs(n)
  if (opts.compact && a >= 1e7) return `${sign}₹${trim(a / 1e7)} Cr`
  if (opts.compact && a >= 1e5) return `${sign}₹${trim(a / 1e5)} L`
  return `${sign}₹${Number.isInteger(Math.round(a * 100) / 100) ? whole.format(a) : paise.format(a)}`
}

/** A 0–1 ratio as a percentage: 0.709 → 70.9%. */
export function pct(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined || !Number.isFinite(v) ? '—' : `${(v * 100).toFixed(digits)}%`
}

export function dateIN(iso: string | null | undefined): string {
  return iso ? day.format(new Date(iso)) : '—'
}

export function dateTimeIN(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${day.format(d)}, ${time.format(d).toLowerCase()}`
}

export function months(n: number): string {
  return `${n} month${n === 1 ? '' : 's'}`
}
