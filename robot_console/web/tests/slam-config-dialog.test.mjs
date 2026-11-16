// Exercise the real SFC setup with Vue's renderer and controllable network responses.
// No DOM or additional test framework is needed: node --test tests/slam-config-dialog.test.mjs
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import vm from 'node:vm'
import { compileScript, parse } from '@vue/compiler-sfc'
import ts from 'typescript'

const require = createRequire(import.meta.url)
const vue = require('vue')
const filename = new URL('../src/components/SlamConfigDialog.vue', import.meta.url)
const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename: filename.pathname })
const script = compileScript(descriptor, { id: 'slam-config-dialog-test' })
const compiled = ts.transpileModule(script.content, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText

function deferred() {
  let resolve, reject
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail })
  return { promise, resolve, reject }
}

function info(stack, bag, value = 1) {
  return {
    stack, algorithm: stack, sensor: bag, user_count: 0,
    params: [{ key: 'rate', base: 1, auto: 1, value, user: null, source: 'base', suggest: null,
      tunable: { type: 'float', min: 0, max: 100 } }],
  }
}

async function flush() {
  await Promise.resolve()
  await vue.nextTick()
}

function mount(t, initial = {}) {
  const loads = [], saves = [], messages = [], events = []
  const api = {
    slamConfig(stack, bag) {
      const request = { ...deferred(), stack, bag }
      loads.push(request)
      return request.promise
    },
    saveSlamConfig(stack, bag, values) {
      const request = { ...deferred(), stack, bag, values }
      saves.push(request)
      return request.promise
    },
  }
  const mocks = {
    vue,
    'vue-i18n': { useI18n: () => ({ t: key => key }) },
    'element-plus': { ElMessage: { success: message => messages.push(message) } },
    '@/api': { api },
    '@/i18n': { pick: value => value },
    '@/messages': { errorText: error => error.message },
    '@/components/InfoTip.vue': { default: {} },
  }
  const module = { exports: {} }
  vm.runInNewContext(compiled, {
    module, exports: module.exports,
    require: name => {
      assert.ok(name in mocks, `Unexpected import: ${name}`)
      return mocks[name]
    },
  }, { filename: filename.pathname })
  const Component = module.exports.default
  Component.render = () => null
  const renderer = vue.createRenderer({
    insert() {}, remove() {}, patchProp() {},
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    setText() {}, setElementText() {}, parentNode: () => null, nextSibling: () => null,
  })
  const props = vue.reactive({ modelValue: true, stack: 'fastlio', bag: 'bag-a', ...initial })
  let instance
  const app = renderer.createApp({
    render: () => vue.h(Component, {
      ...props,
      ref: component => { if (component) instance = component.$ },
      onSaved: () => events.push('saved'),
      'onUpdate:modelValue': value => { events.push(value); props.modelValue = value },
    }),
  })
  app.mount({})
  t.after(() => app.unmount())
  return { props, loads, saves, messages, events, state: instance.setupState, app }
}

test('changing the bag reloads and a late old success cannot replace the new form', async t => {
  const h = mount(t)
  h.props.bag = 'bag-b'
  await flush()
  assert.equal(h.loads.length, 2)
  assert.equal(h.state.info, null)
  await h.state.save()
  assert.equal(h.saves.length, 0)
  h.loads[1].resolve(info('fastlio', 'bag-b', 2))
  await flush()
  h.loads[0].resolve(info('fastlio', 'bag-a', 9))
  await flush()
  assert.equal(h.state.info.sensor, 'bag-b')
  assert.equal(h.state.text.rate, '2')
  assert.equal(h.state.loading, false)
})

test('late errors and finally handlers cannot clear the new loading state', async t => {
  const h = mount(t)
  h.props.bag = 'bag-b'
  await flush()
  h.loads[0].reject(new Error('old request failed'))
  await flush()
  assert.equal(h.state.error, '')
  assert.equal(h.state.loading, true)
  h.loads[1].resolve(info('fastlio', 'bag-b'))
  await flush()
  assert.equal(h.state.loading, false)
})

test('closing and reopening the same bag invalidates the previous session', async t => {
  const h = mount(t)
  h.props.modelValue = false
  await flush()
  h.props.modelValue = true
  await flush()
  h.loads[0].resolve(info('fastlio', 'bag-a', 9))
  await flush()
  assert.equal(h.state.info, null)
  assert.equal(h.state.loading, true)
  h.loads[1].resolve(info('fastlio', 'bag-a', 2))
  await flush()
  assert.equal(h.state.text.rate, '2')
})

test('switching algorithms reloads and save uses the loaded stack and bag', async t => {
  const h = mount(t)
  h.props.stack = 'glim'
  await flush()
  assert.equal(h.loads[1].stack, 'glim')
  h.loads[1].resolve(info('glim', 'bag-a'))
  await flush()
  h.state.text.rate = '5'
  const saving = h.state.save()
  assert.equal(h.saves[0].stack, 'glim')
  assert.equal(h.saves[0].bag, 'bag-a')
  assert.equal(h.saves[0].values.rate, 5)
  h.saves[0].resolve({ ...info('glim', 'bag-a', 5), user_count: 1 })
  await saving
  await flush()
  assert.deepEqual(h.events, ['saved', false])
  assert.equal(h.messages.length, 1)
})

test('an old save result cannot close or overwrite a newly loaded bag', async t => {
  const h = mount(t)
  h.loads[0].resolve(info('fastlio', 'bag-a'))
  await flush()
  h.state.text.rate = '5'
  const saving = h.state.save()
  h.props.bag = 'bag-b'
  await flush()
  h.loads[1].resolve(info('fastlio', 'bag-b', 2))
  await flush()
  h.saves[0].resolve(info('fastlio', 'bag-a', 5))
  await saving
  await flush()
  assert.equal(h.state.info.sensor, 'bag-b')
  assert.equal(h.state.text.rate, '2')
  assert.equal(h.props.modelValue, true)
  assert.deepEqual(h.events, [])
  assert.deepEqual(h.messages, [])
})

test('a late save failure cannot change the new session or release its busy state', async t => {
  const h = mount(t)
  h.loads[0].resolve(info('fastlio', 'bag-a'))
  await flush()
  const oldSave = h.state.save()
  h.props.bag = 'bag-b'
  await flush()
  h.loads[1].resolve(info('fastlio', 'bag-b'))
  await flush()
  const newSave = h.state.save()
  h.saves[0].reject(new Error('old save failed'))
  await oldSave
  await flush()
  assert.equal(h.state.error, '')
  assert.equal(h.state.busy, true)
  h.saves[1].resolve(info('fastlio', 'bag-b'))
  await newSave
})

test('unmount invalidates pending requests without emitting or displaying results', async t => {
  const h = mount(t)
  h.app.unmount()
  h.loads[0].resolve(info('fastlio', 'bag-a'))
  await flush()
  assert.equal(h.state.info, null)
  assert.deepEqual(h.events, [])
})
