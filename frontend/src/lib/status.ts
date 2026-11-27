// The status vocabulary (docs/PLAN.md 14A): every code maps to one of four tones, and colour means only that.

export type Tone = 'good' | 'watch' | 'problem' | 'neutral'

const TONES: Record<Exclude<Tone, 'neutral'>, string[]> = {
  good: ['APPROVED', 'SANCTIONED', 'DISBURSED', 'RECOMMENDED_FOR_APPROVAL', 'VERIFIED', 'DELIVERED', 'SENT', 'SUCCEEDED', 'ACTIVE', 'RELEASED', 'RESOLVED',
    'CHECKLIST_COMPLETE', 'PASS', 'ACTIVATED', 'ISSUED', 'RECORDED', 'CURRENT'],
  watch: ['DISCREPANCY', 'FORWARDED', 'UNDER_REVIEW', 'RECONSIDERATION', 'REFER_FOR_FURTHER_REVIEW', 'REFERRED', 'NEEDS_MANUAL_REVIEW', 'AWAITING_OCR',
    'DELAYED', 'PARTIAL', 'OPEN', 'RULE_IMPACT', 'VERIFICATION_CANDIDATE', 'COMPLIANCE_ITEM', 'NEW', 'REFER', 'PENDING', 'SKIPPED'],
  problem: ['DECLINED', 'POTENTIALLY_INELIGIBLE', 'REJECTED', 'FAILED', 'BOUNCED', 'COMPLAINED', 'FAIL', 'EXPIRED'],
}
const BY_CODE = new Map(Object.entries(TONES).flatMap(([t, codes]) => codes.map((c) => [c, t as Tone])))

export const tone = (s: string | null | undefined): Tone => (s ? BY_CODE.get(s) ?? 'neutral' : 'neutral')

const LABELS: Record<string, string> = {
  DOCS_PENDING: 'Documents pending', DOCS_VERIFIED: 'Documents verified', AI_ASSESSED: 'AI assessed', NTB: 'New to bank', ETB: 'Existing customer',
  KFS: 'Key Facts Statement', EMI_SCHEDULE: 'EMI schedule', AWAITING_OCR: 'Awaiting OCR', ETB_EST: 'Existing customer, established credit',
  RECOMMENDED_FOR_APPROVAL: 'Recommended for approval', SALARY_SLIPS_3M: 'Salary slips (3 months)', BANK_STATEMENT_6M: 'Bank statement (6 months)',
  ITR_1Y: 'Income tax return (1 year)', AADHAAR_MASKED: 'Masked Aadhaar', PAN_CARD: 'PAN card', ADDRESS_PROOF: 'Address proof', EMPLOYMENT_PROOF: 'Employment proof', SUPPORTING: 'Supporting document',
  M: 'Male', F: 'Female', O: 'Other',
}

/** Sentence case of a code: REFER_FOR_FURTHER_REVIEW → "Refer for further review". */
export function statusLabel(s: string): string {
  if (LABELS[s]) return LABELS[s]
  const words = s.toLowerCase().split('_')
  return words.map((w, i) => (i === 0 ? w[0].toUpperCase() + w.slice(1) : w)).join(' ').replace(/\bai\b/g, 'AI').replace(/\bkyc\b/g, 'KYC').replace(/\bpan\b/g, 'PAN')
    .replace(/\bemi\b/g, 'EMI').replace(/\bocr\b/g, 'OCR')
}

// Exactly as backend/app/routers/documents.py METHOD_LABEL.
export const METHOD_LABEL: Record<string, string> = {
  EMBEDDED_TEXT: 'Embedded PDF text (not OCR)', UNLIMITED_OCR: 'Unlimited-OCR', PRECOMPUTED_UNLIMITED_OCR: 'Unlimited-OCR (precomputed batch)',
  GEMINI_VISION: 'Read by Gemini (AI, not OCR)', MANUAL: 'Keyed in by staff', PREPARED_DEMO_TEXT: 'Prepared demo data — not OCR',
}
