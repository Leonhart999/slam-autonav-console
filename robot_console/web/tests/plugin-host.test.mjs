import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { loadSource } from './load-source.mjs'

const vue = createRequire(import.meta.url)('vue')

// plugins/host.ts with real Vue reactivity and a fake document that "loads" scripts by running a callback
function loadHost(scripts = {}) {
  const head = []
  const document = {
    createElement: (tag) => ({ tag }),
    head: {
      appendChild(el) {
        head.push(el)
        if (el.tag !== 'script') return
        const run = scripts[el.src]
        setTimeout(() => {
          if (run === undefined) { el.onerror(); return }
          try { run(host) } catch { /* a throwing plugin script still fires onload */ }
          el.onload()
        }, 0)
      },
    },
  }
  const window = { setTimeout, clearTimeout }
  const host = loadSource('../src/plugins/host.ts', { vue }, { document, window })
  return { host, head }
}

const active = (id, frontend = { script: `/ext/${id}/plugin.js`, style: null }) =>
  ({ id, state: 'active', frontend })
const Page = { setup: () => () => null }
// values from the module's own realm (vm context) compared structurally
const plain = (v) => JSON.parse(JSON.stringify(v))

test('scripts are loaded in order and their pages and messages are registered', async () => {
  const merged = []
  const { host, head } = loadHost({
    '/ext/a/plugin.js': (h) => h.register({ id: 'a', messages: { en: { hi: 'Hi' } }, pages: [{ path: 'main', title: 'A', component: Page }] }),
    '/ext/b/plugin.js': (h) => h.register({ id: 'b', pages: [{ path: 'x', title: { zh: '乙' }, component: Page }] }),
  })
  host.setMessageMerger((id, m) => merged.push([id, m]))
  await host.loadPluginFrontends([active('a', { script: '/ext/a/plugin.js', style: '/ext/a/plugin.css' }), active('b'),
    { id: 'off', state: 'disabled', frontend: null }])
  assert.deepEqual(head.map((e) => e.src ?? e.href), ['/ext/a/plugin.css', '/ext/a/plugin.js', '/ext/b/plugin.js'])
  assert.deepEqual(plain(host.registry.registered), ['a', 'b'])
  assert.deepEqual(plain(host.registry.pages.map((p) => [host.pagePath(p), p.title])),
    [['/ext/a/main', { zh: 'A', en: 'A' }], ['/ext/b/x', { zh: '乙', en: '乙' }]])
  assert.equal(vue.isReactive(host.registry.pages[0].component), false)
  assert.deepEqual(merged, [['a', { en: { hi: 'Hi' } }]])
  assert.deepEqual(plain(host.registry.issues), {})
  assert.equal(host.registry.loading, false)
})

test('failed, silent and invalid plugin scripts are recorded per plugin', async () => {
  const { host } = loadHost({
    '/ext/silent/plugin.js': () => { throw new Error('boom') },
    '/ext/bad/plugin.js': (h) => h.register({ id: 'bad', pages: [{ path: 'Main Page', title: 'x', component: Page }] }),
    '/ext/twice/plugin.js': (h) => {
      h.register({ id: 'twice', pages: [{ path: 'p', title: 't', component: Page }, { path: 'p', title: 't', component: Page }] })
    },
  })
  await host.loadPluginFrontends([active('missing'), active('silent'), active('bad'), active('twice')])
  const codes = Object.fromEntries(Object.entries(host.registry.issues).map(([id, i]) => [id, i.code]))
  assert.deepEqual(codes, { missing: 'script_failed', silent: 'not_registered', bad: 'register_invalid', twice: 'register_invalid' })
  assert.deepEqual(plain(host.registry.pages), [])        // nothing of an invalid definition is applied
  assert.deepEqual(plain(host.registry.registered), [])
})

test('register refuses ids that are not active plugins and second registrations', async () => {
  const { host } = loadHost({ '/ext/a/plugin.js': (h) => h.register({ id: 'a' }) })
  await host.loadPluginFrontends([active('a')])
  assert.throws(() => host.register({ id: 'intruder' }), /not an active plugin/)
  assert.throws(() => host.register({ id: 'a' }), /registered twice/)
  assert.throws(() => host.register({ id: 'a', messages: { fr: {} } }))
})

test('header components are registered, checked, and removed when they crash', async () => {
  const Button = { setup: () => () => null }
  const { host } = loadHost({
    '/ext/a/plugin.js': (h) => h.register({ id: 'a', header: [Button] }),
    '/ext/b/plugin.js': (h) => h.register({ id: 'b', header: ['not a component'] }),
  })
  await host.loadPluginFrontends([active('a'), active('b')])
  assert.deepEqual(plain(host.registry.header.map((x) => [x.pluginId, x.key])), [['a', 'a:0']])
  assert.equal(vue.isReactive(host.registry.header[0].component), false)
  assert.equal(host.registry.issues.b.code, 'register_invalid')
  host.reportCrash('a', 'render failed')
  assert.deepEqual(plain(host.registry.header), [])
  assert.deepEqual(plain(host.registry.issues.a), { code: 'crashed', params: { error: 'render failed' } })
})
