import assert from 'node:assert/strict'
import test from 'node:test'
import { loadSource, task } from './load-source.mjs'

const h = loadSource('../src/http.ts')
function make(request, extra = {}) {
  return loadSource('../src/tasks.ts', { './http': { ...h, request } }, extra)
}

test('recovering a completed task fetches the durable result when list omitted it', async () => {
  const paths = []
  const t = make(async (method, path) => { paths.push(path); return task({ state: 'succeeded', result: { ok: true } }) })
  const result = await t.waitTask(task({ state: 'succeeded' }))
  assert.equal(result.ok, true)
  assert.deepEqual(paths, ['/api/tasks/' + 'a'.repeat(32)])
})

test('failed or interrupted tasks retain the backend error and never auto retry', async () => {
  let calls = 0
  const t = make(async () => { calls++ })
  for (const state of ['failed', 'interrupted']) {
    await assert.rejects(t.waitTask(task({ state, error: { code: 'task_interrupted', params: {} } })),
      error => error.coded.code === 'task_interrupted')
  }
  assert.equal(calls, 0)
})

test('cancelling task observation stops polling without sending a backend stop request', async () => {
  const paths = []
  const t = make(async (method, path) => { paths.push([method, path]) })
  const controller = new AbortController()
  const watching = t.waitTask(task(), controller.signal)
  const rejected = assert.rejects(watching, h.isCancelled)
  controller.abort()
  await rejected
  assert.deepEqual(paths, [])
})

test('background submissions preserve query parameters, publish a recovery event and validate results', async () => {
  const calls = [], events = []
  const t = make(async (method, path, body) => {
    calls.push({ method, path, body })
    return task({ state: 'succeeded', result: { dir: '/tmp/report', runs: ['a'], files: [], skipped: [] } })
  }, { window: { dispatchEvent: event => events.push(event.type) } })
  assert.equal((await t.taskResult('/api/evaluation/export?custom=1', { ids: ['a'] })).dir, '/tmp/report')
  assert.equal(calls[0].path, '/api/evaluation/export?custom=1&background=true')
  assert.deepEqual(calls[0].body, { ids: ['a'] })
  assert.deepEqual(events, ['console-tasks-changed'])
  const invalid = make(async () => task({ state: 'succeeded', result: { dir: 1 } }))
  await assert.rejects(invalid.taskResult('/api/evaluation/export'), error => error.coded.code === 'response_invalid')
})

test('task polling exposes stages and then returns the completed result', async () => {
  const phases = []
  const t = make(async () => task({ state: 'succeeded', phase: 'done', result: { ok: true } }), {
    setTimeout: fn => setTimeout(fn, 0),
  })
  assert.equal((await t.waitTask(task({ state: 'running', phase: 'evaluating' }), undefined, task => phases.push(task.phase))).ok, true)
  assert.deepEqual(phases, ['evaluating', 'done'])
})
