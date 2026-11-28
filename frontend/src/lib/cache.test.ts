import { test } from 'node:test'
import assert from 'node:assert/strict'
import { cached, cacheGeneration, clearApiCache, remember } from './cache.ts'

test('an answer started before a sign-out is not kept for the next user', () => {
  const started = cacheGeneration()
  clearApiCache() // sign-out while the request is in flight
  remember('/applications', ['previous user'], started)
  assert.equal(cached('/applications'), undefined)

  remember('/applications', ['current user'], cacheGeneration())
  assert.deepEqual(cached('/applications'), ['current user'])
  clearApiCache()
  assert.equal(cached('/applications'), undefined)
})
