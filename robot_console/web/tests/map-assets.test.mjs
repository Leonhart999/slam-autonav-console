import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { loadSource, deferred } from './load-source.mjs'

const vue = createRequire(import.meta.url)('vue')
const http = loadSource('../src/http.ts')
const flush = async () => { for (let i = 0; i < 8; i++) { await Promise.resolve(); await vue.nextTick() } }

test('switching maps disposes a late bitmap and keeps the newer map visible', async () => {
  const a = deferred(), b = deferred()
  const bitmaps = { a: { closes: 0, close() { this.closes++ } }, b: { closes: 0, close() { this.closes++ } } }
  const { useMapAssets } = loadSource('../src/navigation/useMapAssets.ts', {
    vue, '@/api': { api: {} },
    '@/http': { ...http, fetchAsset: async path => ({ blob: async () => path.includes('/a/') ? 'a' : 'b' }) },
    '@/composables/useQuery': loadSource('../src/composables/useQuery.ts', {
      vue, '@/http': http, '@/messages': { errorText: e => e.message },
    }), '@/messages': { errorText: e => e.message },
  }, { createImageBitmap: key => (key === 'a' ? a : b).promise })
  const scope = vue.effectScope(), map = vue.ref({ name: 'a', has_cloud: false, has_solid: false })
  const assets = scope.run(() => useMapAssets(vue.computed(() => map.value)))
  await flush()
  map.value = { ...map.value, name: 'b' }; await flush()
  b.resolve(bitmaps.b); await flush()
  assert.equal(assets.image.value, bitmaps.b)
  a.resolve(bitmaps.a); await flush()
  assert.equal(assets.image.value, bitmaps.b)
  assert.equal(bitmaps.a.closes, 1)
  assert.equal(assets.loadingAssets.value, false)
  scope.stop()
  assert.equal(bitmaps.b.closes, 1)
})

test('a failed cloud request still allows the grid image to be displayed', async () => {
  const bitmap = { close() {} }
  const { useQuery } = loadSource('../src/composables/useQuery.ts', { vue, '@/http': http, '@/messages': { errorText: e => e.message } })
  const { useMapAssets } = loadSource('../src/navigation/useMapAssets.ts', {
    vue, '@/api': { api: {} }, '@/composables/useQuery': { useQuery }, '@/messages': { errorText: e => e.message },
    '@/http': { ...http, fetchAsset: async path => {
      if (path.endsWith('/cloud')) throw new Error('Cloud unavailable')
      return { blob: async () => 'grid' }
    } },
  }, { createImageBitmap: async () => bitmap })
  const scope = vue.effectScope()
  const assets = scope.run(() => useMapAssets(vue.computed(() => ({ name: 'a', has_cloud: true, has_solid: false }))))
  await flush()
  assert.equal(assets.image.value, bitmap)
  assert.equal(assets.cloud.value, null)
  assert.equal(assets.assetError.value, 'Cloud unavailable')
  scope.stop()
})
