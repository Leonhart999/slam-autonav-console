// Data of the static web demo: one finished mapping run (saved voxel map + trajectories + evo evaluation),
// written by tools/make_web_demo_data.py into demo/public/data/. Nothing here talks to a console backend.

export interface MapInfo {
  file: string
  encoding: 'voxel-key-delta-u32-gzip'
  voxel: number
  min_index: [number, number, number]
  bits: [number, number, number]
  count: number
  bytes: number
}

export interface ErrorStats {
  rmse: number
  mean: number
  median: number
  std: number
  min: number
  max: number
  pairs?: number
}

export interface DemoRun {
  version: number
  scene: string
  algorithm: string
  frame: string
  duration: number
  length: number
  ref_length: number
  extent: [number, number, number, number, number, number]
  voxels: number
  map: MapInfo
  eval: {
    tool: string
    align: string
    pose_relation: string
    max_diff: number
    rpe_delta_m: number | null
    matched_poses: number
    ape: ErrorStats
    rpe: ErrorStats
  }
  /** estimate in the map frame; ape: error of each pose after alignment, null without a ground truth match */
  est: { t: number[]; x: number[]; y: number[]; z: number[]; yaw: number[]; ape: (number | null)[] }
  /** ground truth in the map frame, at the estimate stamps */
  gt: { x: number[]; y: number[]; z: number[] }
  /** aligned estimate and ground truth in the world frame (x, y), for the top-view chart */
  top_view: { t: number[]; est: [number, number][]; gt: [number, number][] }
}

/** Trajectory arrays prepared for playback */
export interface Track {
  t: Float64Array
  /** x y z of every estimate pose / ground truth sample, in the map frame */
  est: Float32Array
  gt: Float32Array
  /** distance travelled up to each pose (m) */
  dist: Float64Array
}

export interface Pose {
  x: number
  y: number
  z: number
  yaw: number
}

const DATA_VERSION = 1

/** Decode the packed voxel keys (sorted, stored as deltas) into voxel centres */
export function decodeMap(deltas: Uint32Array, info: MapInfo): Float32Array {
  const [bx, by, bz] = info.bits
  if (bx + by + bz > 32) throw new Error('map keys wider than 32 bits')
  const [ix0, iy0, iz0] = info.min_index
  const v = info.voxel
  const zSpan = 2 ** bz
  const ySpan = 2 ** by
  const out = new Float32Array(deltas.length * 3)
  let key = 0
  for (let i = 0; i < deltas.length; i++) {
    key += deltas[i]
    // keys use up to 32 bits: split with division, not bit shifts (those work on signed 32-bit numbers)
    const iz = key % zSpan
    const rest = Math.floor(key / zSpan)
    const iy = rest % ySpan
    const ix = Math.floor(rest / ySpan)
    out[i * 3] = (ix + ix0 + 0.5) * v
    out[i * 3 + 1] = (iy + iy0 + 0.5) * v
    out[i * 3 + 2] = (iz + iz0 + 0.5) * v
  }
  return out
}

export function buildTrack(run: DemoRun): Track {
  const n = run.est.t.length
  const est = new Float32Array(n * 3)
  const gt = new Float32Array(n * 3)
  const dist = new Float64Array(n)
  for (let i = 0; i < n; i++) {
    est[i * 3] = run.est.x[i]
    est[i * 3 + 1] = run.est.y[i]
    est[i * 3 + 2] = run.est.z[i]
    gt[i * 3] = run.gt.x[i]
    gt[i * 3 + 1] = run.gt.y[i]
    gt[i * 3 + 2] = run.gt.z[i]
    if (i > 0) {
      dist[i] = dist[i - 1] + Math.hypot(
        run.est.x[i] - run.est.x[i - 1], run.est.y[i] - run.est.y[i - 1], run.est.z[i] - run.est.z[i - 1])
    }
  }
  return { t: Float64Array.from(run.est.t), est, gt, dist }
}

/** Index of the last sample at or before `time` (0 before the first one) */
export function indexAt(t: ArrayLike<number>, time: number): number {
  let lo = 0
  let hi = t.length - 1
  if (time <= t[0]) return 0
  if (time >= t[hi]) return hi
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (t[mid] <= time) lo = mid
    else hi = mid
  }
  return lo
}

/** Pose of the estimate at `time`, interpolated between the two recorded poses around it */
export function poseAt(run: DemoRun, time: number): Pose {
  const { t, x, y, z, yaw } = run.est
  const i = indexAt(t, time)
  const j = Math.min(i + 1, t.length - 1)
  const span = t[j] - t[i]
  const f = span > 0 ? Math.min(1, Math.max(0, (time - t[i]) / span)) : 0
  let dyaw = yaw[j] - yaw[i]
  if (dyaw > Math.PI) dyaw -= 2 * Math.PI
  else if (dyaw < -Math.PI) dyaw += 2 * Math.PI
  return {
    x: x[i] + (x[j] - x[i]) * f,
    y: y[i] + (y[j] - y[i]) * f,
    z: z[i] + (z[j] - z[i]) * f,
    yaw: yaw[i] + dyaw * f,
  }
}

/** Speed (m/s) around pose i, over about one second of the recording */
export function speedAt(track: Track, i: number): number {
  const n = track.t.length
  let a = i
  let b = i
  while (a > 0 && track.t[i] - track.t[a] < 0.5) a--
  while (b < n - 1 && track.t[b] - track.t[i] < 0.5) b++
  const dt = track.t[b] - track.t[a]
  return dt > 0 ? (track.dist[b] - track.dist[a]) / dt : 0
}

/** Last known APE at or before pose i (null when no pose up to i had a ground truth match) */
export function apeAt(run: DemoRun, i: number): number | null {
  for (let k = i; k >= 0; k--) {
    const v = run.est.ape[k]
    if (v != null) return v
  }
  return null
}

async function gunzip(bytes: Uint8Array): Promise<Uint8Array> {
  // A server may already have decompressed it (Content-Encoding: gzip): then there is no gzip header
  if (bytes[0] !== 0x1f || bytes[1] !== 0x8b) return bytes
  const stream = new Blob([bytes as BlobPart]).stream().pipeThrough(new DecompressionStream('gzip'))
  return new Uint8Array(await new Response(stream).arrayBuffer())
}

export interface DemoData {
  run: DemoRun
  track: Track
  map: Float32Array
}

/** Fetch and decode the demo data from `base` (a folder URL ending in '/') */
export async function loadDemo(base: string, signal?: AbortSignal): Promise<DemoData> {
  const res = await fetch(`${base}run.json`, { signal })
  if (!res.ok) throw new Error(`run.json: HTTP ${res.status}`)
  const run = (await res.json()) as DemoRun
  if (run.version !== DATA_VERSION) throw new Error(`run.json: data version ${run.version}`)
  if (run.map.encoding !== 'voxel-key-delta-u32-gzip') throw new Error(`map: encoding ${run.map.encoding}`)
  const mres = await fetch(`${base}${run.map.file}`, { signal })
  if (!mres.ok) throw new Error(`${run.map.file}: HTTP ${mres.status}`)
  const raw = await gunzip(new Uint8Array(await mres.arrayBuffer()))
  if (raw.byteLength !== run.map.count * 4) throw new Error(`${run.map.file}: ${raw.byteLength} bytes`)
  const deltas = new Uint32Array(raw.buffer, raw.byteOffset, run.map.count)
  return { run, track: buildTrack(run), map: decodeMap(deltas, run.map) }
}
