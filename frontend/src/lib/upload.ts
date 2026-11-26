// The same file rules as the API (backend/app/routers/applications.py and storage.py), checked before sending.

export const MAX_BYTES = 10 * 1024 * 1024

// Which physical documents satisfy each checklist requirement (applications.py ACCEPTS).
export const ACCEPTS: Record<string, string[]> = {
  APPLICATION_FORM: ['APPLICATION_FORM'], PHOTOGRAPH: ['PHOTOGRAPH'], PAN_CARD: ['PAN_CARD'], ADDRESS_PROOF: ['AADHAAR_MASKED', 'PASSPORT', 'VOTER_ID'],
  SALARY_SLIPS_3M: ['SALARY_SLIP'], ITR_1Y: ['ITR'], BUSINESS_PROOF: ['BUSINESS_PROOF'], PENSION_PROOF: ['PENSION_ORDER'], BANK_STATEMENT_6M: ['BANK_STATEMENT'],
}

const KIND: Record<string, 'pdf' | 'image'> = { pdf: 'pdf', png: 'image', jpg: 'image', jpeg: 'image' }
const TYPES: Record<string, 'pdf' | 'image'> = { 'application/pdf': 'pdf', 'image/png': 'image', 'image/jpeg': 'image' }

/** The reason to refuse this file for this requirement, or null. The API also checks the file's own bytes. */
export function checkUpload(f: { name: string; type: string; size: number }, requirement: string): string | null {
  if (f.size === 0) return 'The file is empty'
  if (f.size > MAX_BYTES) return 'Files are limited to 10 MB'
  const ext = f.name.includes('.') ? f.name.split('.').pop()!.toLowerCase() : ''
  const kind = KIND[ext]
  if (!kind || (f.type && TYPES[f.type] !== kind)) return 'Only PDF, PNG and JPEG files are accepted'
  if (requirement === 'PHOTOGRAPH' && kind === 'pdf') return 'The photograph must be a PNG or JPEG image'
  return null
}
