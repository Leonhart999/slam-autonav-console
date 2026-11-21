import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { loadSource, loadSfc, mountSetup, deferred, task } from './load-source.mjs'
const vue = createRequire(import.meta.url)('vue')
const http = loadSource('../src/http.ts')
const messages = { errorText: e => e.message }
const query = loadSource('../src/composables/useQuery.ts', { vue, '@/http': http, '@/messages': messages })
const flush = async () => { for (let i = 0; i < 8; i++) { await Promise.resolve(); await vue.nextTick() } }

test('changing relative-comparison baseline invalidates an outstanding result', async t => {
  const pending = deferred()
  let requestSignal
  const Component = loadSfc('../src/components/PlaybackCompare.vue', {
    vue, 'vue-i18n': { useI18n: () => ({ t: key => key }) },
    '@/composables/useQuery': query,
    '@/evaluation/api': { evalApi: {
      trajectory: async () => ({}), relative: (ids, baseline, signal) => { requestSignal = signal; return pending.promise },
    } },
    '@/evaluation/messages': { evalMessages: {} }, '@/evaluation/colors': { SERIES: ['red', 'blue'] },
    '@/evaluation/charts': { partKey: bag => bag, partText: () => '', timeSeries() {}, topView() {}, zipXY() {} },
    '@/components/EvalChart.vue': { default: {} },
  }, { AbortController })
  const h = mountSetup(vue, Component, {
    entries: ['a', 'b'].map(id => ({ id, playback: { bag: 'bag', range: null, bag_name: 'bag' } })), colorOf: () => 'red',
  })
  t.after(() => h.app.unmount())
  const computing = h.state.computeRelative(h.state.comparable[0])
  h.state.baselines.bag = 'b'; await flush()
  assert.equal(requestSignal.aborted, true)
  pending.resolve({ baseline: 'a', rows: [] }); await computing
  assert.equal(h.state.relative.bag, undefined)
  assert.equal(h.state.relBusy, '')
})

test('overlapping task-center refreshes keep one polling timer', async t => {
  const a = deferred(), b = deferred(), timers = new Map()
  let calls = 0, nextTimer = 0
  const Component = loadSfc('../src/components/TaskCenter.vue', {
    vue, 'vue-i18n': { useI18n: () => ({ t: key => key }) }, 'vue-router': { useRouter: () => ({ push() {} }) },
    'element-plus': { ElMessage: { error() {}, success() {} } },
    '@/tasks': { tasksApi: { list: () => (++calls === 1 ? a : b).promise } },
    '@/taskMessages': { taskMessages: {} }, '@/composables/useQuery': query,
    '@/messages': messages, '@/openFolder': { openFolder() {} }, '@/http': http,
    '@/format': { fmtDuration: () => '' },
  }, {
    window: { addEventListener() {}, removeEventListener() {} },
    setTimeout: (fn, ms) => { timers.set(++nextTimer, { fn, ms }); return nextTimer }, clearTimeout: id => timers.delete(id),
  })
  const h = mountSetup(vue, Component)
  t.after(() => h.app.unmount())
  const refresh = h.state.refresh()
  a.resolve({ tasks: [] }); await flush()
  assert.equal(timers.size, 0)
  b.resolve({ tasks: [task()] }); await refresh
  assert.equal(timers.size, 1)
  assert.equal([...timers.values()][0].ms, 1000)
})
