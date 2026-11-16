import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { loadSource, deferred } from './load-source.mjs'

const vue = createRequire(import.meta.url)('vue')

function createDom(initialTheme = null, { viewTransition = true, reducedMotion = false } = {}) {
  const storage = new Map(initialTheme ? [['robot_console.theme', initialTheme]] : [])
  const classes = new Set(initialTheme === 'dark' ? ['dark'] : [])
  const listeners = new Map()
  const timers = new Map()
  let nextTimer = 0
  let metaColor = '#eef2f8'
  const meta = { setAttribute(name, value) { if (name === 'content') metaColor = value } }
  const animations = []
  const transitions = []
  const root = {
    classList: {
      add: (...xs) => xs.forEach((x) => classes.add(x)),
      remove: (...xs) => xs.forEach((x) => classes.delete(x)),
      toggle: (x, force) => (force === undefined ? (classes.has(x) ? classes.delete(x) : classes.add(x)) : force ? classes.add(x) : classes.delete(x)),
      contains: (x) => classes.has(x),
    },
    animate(keyframes, options) {
      const done = deferred()
      animations.push({ keyframes, options, finished: done.promise, resolve: done.resolve })
      return { finished: done.promise }
    },
  }
  const document = {
    documentElement: root,
    querySelector: (sel) => (sel === 'meta[name="theme-color"]' ? meta : null),
  }
  if (viewTransition) {
    document.startViewTransition = (cb) => {
      const ready = deferred()
      const finished = deferred()
      let skipped = false
      const vt = {
        ready: ready.promise,
        finished: finished.promise,
        skipTransition() {
          skipped = true
          ready.reject(new Error('skipped'))
          finished.reject(new Error('skipped'))
        },
        resolveReady: () => !skipped && ready.resolve(),
        resolveFinished: () => !skipped && finished.resolve(),
        callbackDone: Promise.resolve().then(cb),
      }
      transitions.push(vt)
      return vt
    }
  }
  const window = {
    innerWidth: 1200,
    innerHeight: 800,
    matchMedia: () => ({ matches: reducedMotion }),
    addEventListener(type, fn) {
      listeners.set(type, [...(listeners.get(type) ?? []), fn])
    },
    setTimeout(fn, ms) {
      const id = ++nextTimer
      timers.set(id, { fn, ms })
      return id
    },
    clearTimeout(id) { timers.delete(id) },
  }
  const localStorage = {
    getItem: (k) => storage.get(k) ?? null,
    setItem: (k, v) => storage.set(k, String(v)),
  }
  const mod = loadSource('../src/theme.ts', { vue }, { document, window, localStorage })
  return { mod, storage, classes, listeners, timers, animations, transitions, getMetaColor: () => metaColor }
}

test('initTheme syncs saved dark mode, meta theme-color, and cross-window storage updates', () => {
  const env = createDom('dark')
  env.mod.initTheme()
  assert.equal(env.mod.theme.value, 'dark')
  assert.equal(env.mod.isDark.value, true)
  assert.equal(env.classes.has('dark'), true)
  assert.equal(env.getMetaColor(), '#080d18')
  assert.equal(env.mod.scenePalette.value.canvasBg, '#0d1424')

  // Simulate another console tab switching back to light
  env.storage.set('robot_console.theme', 'light')
  for (const fn of env.listeners.get('storage') ?? []) fn({ key: 'robot_console.theme' })
  assert.equal(env.mod.theme.value, 'light')
  assert.equal(env.classes.has('dark'), false)
  assert.equal(env.getMetaColor(), '#eef2f8')
  assert.equal(env.mod.scenePalette.value.canvasBg, '#eef2f8')
})

test('setTheme with view transitions captures cleanly, releases theme-capturing on ready, and cleans up rapid toggles', async () => {
  const env = createDom('light')
  env.mod.initTheme()
  const first = env.mod.setTheme('dark', { x: 40, y: 760 })
  assert.equal(env.classes.has('theme-reveal'), true)
  assert.equal(env.classes.has('theme-capturing'), true)
  await env.transitions[0].callbackDone
  assert.equal(env.mod.theme.value, 'dark')
  assert.equal(env.classes.has('dark'), true)
  assert.equal(env.storage.get('robot_console.theme'), 'dark')

  env.transitions[0].resolveReady()
  await Promise.resolve(); await Promise.resolve()
  assert.equal(env.classes.has('theme-capturing'), false)
  assert.equal(env.classes.has('theme-reveal'), true)
  assert.equal(env.animations.length, 1)
  assert.match(env.animations[0].keyframes.clipPath[0], /^circle\(0px at 40px 760px\)$/)

  // Rapid second toggle skips the first transition without clearing classes early
  const second = env.mod.setTheme('light', { x: 20, y: 760 })
  await env.transitions[1].callbackDone
  env.transitions[1].resolveReady()
  await Promise.resolve(); await Promise.resolve()
  assert.equal(env.classes.has('theme-reveal'), true)

  env.animations[0].resolve()
  env.animations[1].resolve()
  env.transitions[1].resolveFinished()
  await Promise.all([first, second])
  assert.equal(env.mod.theme.value, 'light')
  assert.equal(env.classes.has('theme-reveal'), false)
  assert.equal(env.classes.has('theme-capturing'), false)
})

test('setTheme falls back to CSS cross-fade without view transitions and switches immediately under reduced motion', async () => {
  const fade = createDom('light', { viewTransition: false })
  fade.mod.initTheme()
  await fade.mod.setTheme('dark')
  assert.equal(fade.classes.has('dark'), true)
  assert.equal(fade.classes.has('theme-fade'), true)
  assert.equal(fade.timers.size, 1)
  for (const t of fade.timers.values()) t.fn()
  assert.equal(fade.classes.has('theme-fade'), false)

  const reduced = createDom('light', { viewTransition: true, reducedMotion: true })
  reduced.mod.initTheme()
  await reduced.mod.setTheme('dark')
  assert.equal(reduced.classes.has('dark'), true)
  assert.equal(reduced.transitions.length, 0)
  assert.equal(reduced.classes.has('theme-reveal'), false)
})

test('evaluation chart colors update synchronously when theme switches', async () => {
  const env = createDom('light', { reducedMotion: true })
  const colors = loadSource('../src/evaluation/colors.ts', { vue, '@/theme': env.mod })
  const lightSeries0 = colors.SERIES[0]
  const lightTipBg = colors.AXIS.tipBg
  await env.mod.setTheme('dark')
  assert.notEqual(colors.SERIES[0], lightSeries0)
  assert.notEqual(colors.AXIS.tipBg, lightTipBg)
  assert.equal(colors.AXIS.tipInk, '#e8eef8')
  await env.mod.setTheme('light')
  assert.equal(colors.SERIES[0], lightSeries0)
  assert.equal(colors.AXIS.tipBg, lightTipBg)
})
