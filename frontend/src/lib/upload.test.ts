import { test } from 'node:test'
import assert from 'node:assert/strict'
import { ACCEPTS, checkUpload } from './upload.ts'

test('checkUpload mirrors the API', () => {
  const ok = { name: 'slip.pdf', type: 'application/pdf', size: 200_000 }
  assert.equal(checkUpload(ok, 'SALARY_SLIPS_3M'), null)
  assert.equal(checkUpload({ ...ok, size: 10 * 1024 * 1024 + 1 }, 'SALARY_SLIPS_3M'), 'Files are limited to 10 MB')
  assert.equal(checkUpload({ ...ok, name: 'slip.docx', type: 'application/msword' }, 'SALARY_SLIPS_3M'), 'Only PDF, PNG and JPEG files are accepted')
  assert.equal(checkUpload(ok, 'PHOTOGRAPH'), 'The photograph must be a PNG or JPEG image')
  assert.equal(checkUpload({ ...ok, size: 0 }, 'PAN_CARD'), 'The file is empty')
})

test('checkUpload goes by the extension when the browser gives no type', () => {
  assert.equal(checkUpload({ name: 'photo.JPEG', type: '', size: 1000 }, 'PHOTOGRAPH'), null)
  assert.equal(checkUpload({ name: 'photo', type: '', size: 1000 }, 'PHOTOGRAPH'), 'Only PDF, PNG and JPEG files are accepted')
})

test('ACCEPTS matches the API', () => {
  assert.deepEqual(ACCEPTS.ADDRESS_PROOF, ['AADHAAR_MASKED', 'PASSPORT', 'VOTER_ID'])
  assert.deepEqual(ACCEPTS.BANK_STATEMENT_6M, ['BANK_STATEMENT'])
})
