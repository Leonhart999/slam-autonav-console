import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { loadSource, deferred } from './load-source.mjs'

const vue = createRequire(import.meta.url)('vue')
const http = loadSource('../src/http.ts')
const messages = { errorText: e => e.message }
const { useQuery } = loadSource('../src/composables/useQuery.ts', { vue, '@/http': http, '@/messages': messages })

test('stale errors and finally cannot overwrite the current query loading state', async () => {
  const effect = vue.effectScope()
  const q = effect.run(useQuery)
  const a = deferred(), b = deferred(), applied = []
  const first = q.run(() => a.promise, value => applied.push(value))
  const second = q.run(() => b.promise, value => applied.push(value))
  a.reject(new Error('old failure')); await first
  assert.equal(q.error.value, '')
  assert.equal(q.loading.value, true)
  b.resolve('B'); await second
  assert.deepEqual(applied, ['B'])
  assert.equal(q.loading.value, false)
  effect.stop()
})

test('disposing a page cancels transport and prevents late data commits', async () => {
  const effect = vue.effectScope()
  const q = effect.run(useQuery), a = deferred()
  let signal, committed = false
  const work = q.run(s => { signal = s; return a.promise }, () => { committed = true })
  effect.stop()
  assert.equal(signal.aborted, true)
  a.resolve('late'); await work
  assert.equal(committed, false)
  assert.equal(q.loading.value, false)
})

test('recovering evaluation invalidates a previous run request and replaces the cached result', async () => {
  const effect = vue.effectScope(), old = deferred()
  const result = { id: 'run-a', eval: null, info: {}, type: 'mapping', ref_eval: null, maps: [], figures: [] }
  const { useRunDetails } = loadSource('../src/evaluation/useRunDetails.ts', {
    vue, '@/http': http, '@/messages': messages,
    './api': { evalApi: { detail: () => old.promise } },
    '@/tasks': { tasksApi: { get: async () => ({ kind: 'evaluate', target: 'run-a' }) }, waitTask: async () => result },
  })
  const details = effect.run(() => useRunDetails(vue.ref([])))
  const previous = details.ensure('run-a')
  await details.recover('run-a', 'job')
  old.resolve({ ...result, info: { stale: true } }); await previous
  assert.equal(details.details.value['run-a'].info.stale, undefined)
  assert.equal(details.busy.value.size, 0)
  effect.stop()
})

test('wrong-target task results cannot replace evaluation state', async () => {
  const effect = vue.effectScope()
  let observed = false
  const { useRunDetails } = loadSource('../src/evaluation/useRunDetails.ts', {
    vue, '@/http': http, '@/messages': messages, './api': { evalApi: {} },
    '@/tasks': { tasksApi: { get: async () => ({ kind: 'evaluate', target: 'other-run' }) }, waitTask: async () => { observed = true } },
  })
  const details = effect.run(() => useRunDetails(vue.ref([])))
  await details.recover('run-a', 'job')
  assert.equal(observed, false)
  assert.equal(details.details.value['run-a'], undefined)
  assert.equal(details.errors.value['run-a'], 'response_invalid')
  effect.stop()
})
