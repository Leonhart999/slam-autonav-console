// Replay of a recorded run for the 3D view: a stand-in for the console's live mapping stream
// (createMappingStream), fed from the demo data instead of the backend's WebSocket.
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { PointBuffer, type MappingStatus, type MappingStream } from '@/mapping/stream'
import { apeAt, indexAt, poseAt, speedAt, type DemoData } from './data'

export const SPEEDS = [1, 5, 10, 20] as const
export type Speed = (typeof SPEEDS)[number]

/** Points above this height (map frame, m) are the roof and the top of the walls: hidden by default, so the
 *  view from above shows the aisles and the robot instead of the roof */
export const ROOF_Z = 5

/** Shortest wall-clock time between two rebuilds of the trajectory lines (they are rebuilt from scratch) */
const LINE_UPDATE_MS = 60

function below(points: Float32Array, z: number): Float32Array {
  const out = new Float32Array(points.length)
  let n = 0
  for (let i = 0; i < points.length; i += 3) {
    if (points[i + 2] >= z) continue
    out[n++] = points[i]
    out[n++] = points[i + 1]
    out[n++] = points[i + 2]
  }
  return out.slice(0, n)
}

export function useReplay(data: DemoData) {
  const { run, track } = data
  const duration = run.duration
  const time = ref(0)
  const playing = ref(false)
  const speed = ref<Speed>(10)
  const roof = ref(false)

  const status = shallowRef<MappingStatus | null>(null)
  const map = new PointBuffer()
  const scan = new PointBuffer() // the demo has no recorded scans: stays empty
  const est = new PointBuffer()
  const gt = new PointBuffer()
  const lowMap = below(data.map, ROOF_Z)
  map.append(lowMap)
  watch(roof, (show) => map.replace(show ? data.map : lowMap))

  // The 3D view only reads the fields below; the rest of MappingStatus belongs to the live console
  const base = {
    type: 'mapping',
    epoch: 0,
    frame: run.frame,
    voxel: run.map.voxel,
    initialized: true,
    voxels: run.voxels,
    extent: run.extent,
    has_gt: true,
  }

  const index = computed(() => indexAt(track.t, time.value))
  let lineIndex = -1
  let lineAt = 0
  let shownTime = -1

  function update(force = false) {
    const i = index.value
    if (!force && i === lineIndex && time.value === shownTime) return // paused: nothing changed
    const now = performance.now()
    if (i !== lineIndex && (force || now - lineAt >= LINE_UPDATE_MS)) {
      lineIndex = i
      lineAt = now
      est.replace(track.est.subarray(0, (i + 1) * 3))
      gt.replace(track.gt.subarray(0, (i + 1) * 3))
    }
    shownTime = time.value
    status.value = {
      ...base,
      pose: poseAt(run, time.value),
      speed: speedAt(track, i),
      duration: time.value,
      length: track.dist[i],
      error: apeAt(run, i),
    } as MappingStatus
  }

  let raf = 0
  let last = 0
  function frame(now: number) {
    raf = requestAnimationFrame(frame)
    if (playing.value) {
      const dt = last ? Math.min(0.25, (now - last) / 1000) : 0 // a tab coming back from the background does not jump
      time.value = Math.min(duration, time.value + dt * speed.value)
      if (time.value >= duration) playing.value = false
    }
    last = now
    update()
  }
  raf = requestAnimationFrame(frame)
  update(true)
  onBeforeUnmount(() => cancelAnimationFrame(raf))

  function toggle() {
    if (!playing.value && time.value >= duration) time.value = 0
    playing.value = !playing.value
  }

  function seek(t: number) {
    time.value = Math.min(duration, Math.max(0, t))
    update(true)
  }

  const stream: MappingStream = { status, map, scan, est, gt, start() {}, stop() {} }
  return { stream, time, playing, speed, roof, duration, index, toggle, seek }
}
