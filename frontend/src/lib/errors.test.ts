import { test } from 'node:test'
import assert from 'node:assert/strict'
import { errorMessage } from './errors.ts'

test('errorMessage', () => {
  assert.equal(errorMessage(0), 'Cannot reach the server. It may be waking up (free tier, up to a minute); try again shortly.')
  assert.equal(errorMessage(401), 'Your session has ended. Sign in again.')
  assert.equal(errorMessage(409, 'The application changed since you opened it. Reload it and try again.'),
    'The application changed since you opened it. Reload it and try again.')
  assert.equal(errorMessage(422, [{ loc: ['body', 'reason'], msg: 'String should have at least 10 characters' }]),
    'reason: String should have at least 10 characters')
  assert.equal(errorMessage(500), 'Something went wrong on the server. Try again.')
  assert.equal(errorMessage(503, 'Bank records are unavailable right now (RuntimeError); try again shortly'),
    'Bank records are unavailable right now (RuntimeError); try again shortly')
})

test('errorMessage joins several validation errors', () => {
  assert.equal(errorMessage(422, [{ loc: ['body', 'mobile'], msg: 'bad' }, { loc: ['body', 'applicant', 'pan'], msg: 'worse' }]), 'mobile: bad; pan: worse')
})
