import assert from 'node:assert/strict'
import test from 'node:test'
import { loadSource, deferred, task } from './load-source.mjs'

const make = (fetch, extra = {}) => loadSource('../src/http.ts', {}, { fetch, ...extra })
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const code = expected => error => error.coded?.code === expected

test('JSON requests preserve structured errors and validate endpoint responses', async () => {
  const calls = []
  const h = make(async (path, options) => { calls.push(options); return json({ ok: true }) })
  assert.equal((await h.request('POST', '/api/control', { value: 2 })).ok, true)
  assert.equal(calls[0].body, '{"value":2}')
  assert.equal(calls[0].headers.get('Content-Type'), 'application/json')
  await assert.rejects(h.request('GET', '/api/maps'), code('response_invalid'))
  const invalid = make(async () => new Response('<html>server error</html>'))
  await assert.rejects(invalid.request('GET', '/api/control'), code('response_invalid'))
  const denied = make(async () => json({ detail: { code: 'map_operation_failed', params: { reason: 'busy' } } }, 409))
  await assert.rejects(denied.request('POST', '/api/control'), error =>
    error.status === 409 && error.coded.params.reason === 'busy')
  assert.equal(h.validResponse('/api/maps/a', { ok: true }, 'DELETE'), true)
  assert.equal(h.validResponse('/api/tasks', { tasks: [task({ input: null })] }), false)
})

test('network errors, explicit cancellation and timeouts have distinct outcomes', async () => {
  const h = make(async () => { throw new TypeError('offline') })
  await assert.rejects(h.request('GET', '/api/control'), code('request_network'))
  let count = 0
  const waiting = make((path, { signal }) => {
    count++
    return new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true }))
  })
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(waiting.request('GET', '/api/control', undefined, { signal: controller.signal }), waiting.isCancelled)
  assert.equal(count, 0)
  const active = new AbortController()
  const request = waiting.request('GET', '/api/control', undefined, { signal: active.signal })
  active.abort()
  await assert.rejects(request, waiting.isCancelled)
  await assert.rejects(waiting.request('GET', '/api/control', undefined, { timeout: 2 }), code('request_timeout'))
})

test('timeout covers slow response bodies and always cleans listeners and timers', async () => {
  const controller = new AbortController()
  const originalAdd = controller.signal.addEventListener.bind(controller.signal)
  const originalRemove = controller.signal.removeEventListener.bind(controller.signal)
  let adds = 0, removes = 0, cleared = 0
  controller.signal.addEventListener = (...args) => { adds++; return originalAdd(...args) }
  controller.signal.removeEventListener = (...args) => { removes++; return originalRemove(...args) }
  const h = make(async (path, { signal }) => ({ ok: true,
    json: () => new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new Error('body aborted')), { once: true })),
  }), { clearTimeout: timer => { cleared++; clearTimeout(timer) } })
  await assert.rejects(h.request('GET', '/api/control', undefined, { signal: controller.signal, timeout: 2 }), code('request_timeout'))
  assert.equal(adds, 1)
  assert.equal(removes, 1)
  assert.equal(cleared, 1)
})

test('latest request rejects old successes and errors even when transport ignores abort', async () => {
  const h = make(() => {})
  const scope = h.latestRequest(), a = deferred(), b = deferred()
  let oldSignal
  const first = scope.run(signal => { oldSignal = signal; return a.promise })
  const firstRejected = assert.rejects(first, h.isCancelled)
  const second = scope.run(() => b.promise)
  assert.equal(oldSignal.aborted, true)
  b.resolve('B'); assert.equal(await second, 'B')
  a.resolve('A'); await firstRejected
  const c = deferred(), d = deferred()
  const lateError = assert.rejects(scope.run(() => c.promise), h.isCancelled)
  const current = scope.run(() => d.promise)
  c.reject(new Error('stale error')); await lateError
  d.resolve('D'); assert.equal(await current, 'D')
  const e = deferred()
  const cancelled = assert.rejects(scope.run(() => e.promise), h.isCancelled)
  scope.cancel(); e.resolve('discard'); await cancelled
})

test('assets retain metadata and malformed XYZ data is rejected before rendering', async () => {
  const h = make(async () => new Response(new Uint8Array([1, 2]), { headers: { 'X-Size': '8,9' } }))
  const result = await h.fetchAsset('/api/grid')
  assert.equal(result.headers.get('X-Size'), '8,9')
  assert.equal((await result.arrayBuffer()).byteLength, 2)
  assert.throws(() => h.xyz(new ArrayBuffer(8)), code('response_invalid'))
  assert.throws(() => h.xyz(new Float32Array([0, NaN, 1]).buffer), code('response_invalid'))
  assert.equal(h.xyz(new Float32Array([0, 1, 2]).buffer).length, 3)
})

test('class-specific deadlines are selected and cleared on success', async () => {
  const times = [], clears = []
  const h = make(async () => json({ ok: true }), {
    setTimeout: (fn, ms) => { times.push(ms); return times.length }, clearTimeout: id => clears.push(id),
  })
  await h.request('GET', '/api/query')
  await h.request('POST', '/api/control')
  await h.request('POST', '/api/long', undefined, { category: 'long' })
  assert.deepEqual(times, [15000, 60000, 660000])
  assert.deepEqual(clears, [1, 2, 3])
})
