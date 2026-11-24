import assert from 'node:assert/strict'
import test from 'node:test'
import { gzipSync } from 'node:zlib'
import { loadSource } from './load-source.mjs'

const data = loadSource('../src/demo/data.ts', {}, {
  fetch: (...args) => globalThis.fetch(...args), Blob, Response, DecompressionStream, Uint8Array, Uint32Array, Float64Array,
})

// Same packing as tools/make_web_demo_data.py: voxel indices -> sorted uint32 keys -> deltas
function pack(indices, bits) {
  const lo = [0, 1, 2].map((a) => Math.min(...indices.map((p) => p[a])))
  const keys = indices
    .map(([x, y, z]) => (x - lo[0]) * 2 ** (bits[1] + bits[2]) + (y - lo[1]) * 2 ** bits[2] + (z - lo[2]))
    .sort((a, b) => a - b)
  const deltas = new Uint32Array(keys.length)
  keys.forEach((k, i) => { deltas[i] = k - (i ? keys[i - 1] : 0) })
  return { deltas, info: { voxel: 0.1, min_index: lo, bits, count: keys.length } }
}

test('decodeMap returns the voxel centres, also for keys that use all 32 bits', () => {
  const pts = [[-422, -225, -9], [361, 283, 127], [0, 0, 0], [-1, 5, 3]]
  const { deltas, info } = pack(pts, [10, 9, 13]) // 32 bits: the highest keys are above 2^31
  const out = data.decodeMap(deltas, info)
  const got = []
  for (let i = 0; i < out.length; i += 3) got.push([out[i], out[i + 1], out[i + 2]].map((v) => Math.round(v / 0.1 - 0.5)))
  const want = [...pts].sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2])
  assert.deepEqual(got, want)
  assert.ok(Math.abs(out[0] - -42.15) < 1e-4)
})

test('decodeMap refuses keys wider than 32 bits', () => {
  assert.throws(() => data.decodeMap(new Uint32Array(1), { voxel: 0.1, min_index: [0, 0, 0], bits: [11, 11, 11], count: 1 }))
})

test('indexAt and poseAt follow the recording, with yaw interpolated across ±π', () => {
  const t = [0, 1, 2]
  assert.equal(data.indexAt(t, -1), 0)
  assert.equal(data.indexAt(t, 1.5), 1)
  assert.equal(data.indexAt(t, 9), 2)
  const run = { est: { t, x: [0, 1, 2], y: [0, 0, 0], z: [0, 0, 0], yaw: [0, 3.1, -3.1] } }
  assert.deepEqual({ ...data.poseAt(run, 0.5) }, { x: 0.5, y: 0, z: 0, yaw: 1.55 })
  const p = data.poseAt(run, 1.5)
  assert.ok(Math.abs(Math.abs(p.yaw) - (3.1 + (2 * Math.PI - 6.2) / 2)) < 1e-9, `yaw ${p.yaw} went the long way round`)
})

test('apeAt falls back to the last pose that had a ground truth match', () => {
  const run = { est: { ape: [null, 0.02, null, null] } }
  assert.equal(data.apeAt(run, 0), null)
  assert.equal(data.apeAt(run, 3), 0.02)
})

test('loadDemo reads run.json and the gzip map, and checks the data version', async () => {
  const { deltas, info } = pack([[0, 0, 0], [2, 1, 1]], [4, 4, 4])
  const run = {
    version: 1, map: { ...info, file: 'map.bin', encoding: 'voxel-key-delta-u32-gzip' },
    est: { t: [0, 1], x: [0, 3], y: [0, 4], z: [0, 0], yaw: [0, 0], ape: [0.01, 0.02] },
    gt: { x: [0, 3], y: [0, 4], z: [0, 0] },
  }
  const files = {
    'base/run.json': JSON.stringify(run),
    'base/map.bin': gzipSync(Buffer.from(deltas.buffer)),
  }
  const fetchStub = async (url) => (url in files ? new Response(files[url]) : new Response('', { status: 404 }))
  const mod = loadSource('../src/demo/data.ts', {}, {
    fetch: fetchStub, Blob, Response, DecompressionStream, Uint8Array, Uint32Array, Float64Array,
  })
  const d = await mod.loadDemo('base/')
  assert.equal(d.map.length, 6)
  assert.equal(d.track.dist[1], 5)
  files['base/run.json'] = JSON.stringify({ ...run, version: 2 })
  await assert.rejects(mod.loadDemo('base/'), /data version 2/)
})
