<!-- 2D navigation view: occupancy grid image in the map frame, with Nav2 costmaps, robot, laser points, Nav2 paths,
     waypoints and the goal. World units are meters, +y up. Wheel to zoom, drag to pan, double-click to fit.
     tool = 'pose' / 'goal' / 'waypoint': press to place the position, drag to set the heading, release to emit "pick". -->
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { CostmapLayer, MapTool, NavMap, NavStatus, PickedPose, WaypointMark } from '@/types'

const props = withDefaults(defineProps<{
  map: NavMap | null
  image: ImageBitmap | null
  status: NavStatus | null
  tool: MapTool
  waypoints?: WaypointMark[]
  costmaps?: { global: CostmapLayer | null; local: CostmapLayer | null }
}>(), { waypoints: () => [], costmaps: () => ({ global: null, local: null }) })
const emit = defineEmits<{ pick: [tool: Exclude<MapTool, 'pan'>, pose: PickedPose] }>()

const TOOL_COLOR = { pose: '#7c3aed', goal: '#16a34a', waypoint: '#0891b2' } as const
const WAYPOINT_COLOR: Record<WaypointMark['kind'], string> = {
  draft: '#0891b2', waiting: '#64748b', active: '#16a34a', ok: '#16a34a', fail: '#e11d48',
}
// Pose being placed with the mouse (press = position, drag = heading)
let placing: { tool: Exclude<MapTool, 'pan'>; x: number; y: number; yaw: number } | null = null

const host = ref<HTMLDivElement | null>(null)
const canvas = ref<HTMLCanvasElement | null>(null)
// View: screen = (world - center) * scale, y flipped; scale in CSS px per meter
const view = { cx: 0, cy: 0, scale: 20 }
let fitted = ''
let raf = 0
let resizeObs: ResizeObserver | null = null

function size() {
  const el = host.value
  return { w: Math.max(1, el?.clientWidth ?? 1), h: Math.max(1, el?.clientHeight ?? 1) }
}

function fit() {
  const m = props.map
  if (!m) return
  const { w, h } = size()
  const [ox, oy] = m.grid.origin
  const mw = m.grid.width * m.grid.resolution
  const mh = m.grid.height * m.grid.resolution
  view.cx = ox + mw / 2
  view.cy = oy + mh / 2
  view.scale = Math.min(w / mw, h / mh) * 0.92
  request()
}

function request() {
  if (!raf) raf = requestAnimationFrame(draw)
}

function polyline(ctx: CanvasRenderingContext2D, pts: number[][], color: string, px: number, dash: number[] = []) {
  if (pts.length < 2) return
  ctx.beginPath()
  ctx.moveTo(pts[0][0], pts[0][1])
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1])
  ctx.strokeStyle = color
  ctx.lineWidth = px / view.scale
  ctx.setLineDash(dash.map((d) => d / view.scale))
  ctx.lineJoin = 'round'
  ctx.lineCap = 'round'
  ctx.stroke()
  ctx.setLineDash([])
}

function draw() {
  raf = 0
  const c = canvas.value
  if (!c) return
  const { w, h } = size()
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
    c.width = Math.round(w * dpr)
    c.height = Math.round(h * dpr)
  }
  const ctx = c.getContext('2d')!
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, w, h)
  // World transform: meters, +y up
  ctx.setTransform(dpr * view.scale, 0, 0, -dpr * view.scale, dpr * (w / 2 - view.cx * view.scale), dpr * (h / 2 + view.cy * view.scale))

  const m = props.map
  if (m && props.image) {
    const res = m.grid.resolution
    const [ox, oy, yaw] = m.grid.origin
    ctx.save()
    ctx.translate(ox, oy)
    ctx.rotate(yaw)
    // PGM row 0 is the top (largest y): flip the image back
    ctx.translate(0, m.grid.height * res)
    ctx.scale(1, -1)
    ctx.imageSmoothingEnabled = false
    ctx.drawImage(props.image, 0, 0, m.grid.width * res, m.grid.height * res)
    ctx.restore()
  }
  // Costmaps (semi-transparent; the local one is drawn on top)
  layer(ctx, props.costmaps.global, 0.55)
  layer(ctx, props.costmaps.local, 0.8)

  const s = props.status
  if (s?.tf_ok) {
    // Laser points
    ctx.fillStyle = '#e11d48'
    const r = 2.2 / view.scale
    for (const p of s.scan) ctx.fillRect(p[0] - r / 2, p[1] - r / 2, r, r)
    polyline(ctx, s.global_path, '#2a78d6', 3)
    polyline(ctx, s.local_path, '#eb6834', 3, [6, 4])
  }
  if (s?.pose) {
    const { x, y, yaw } = s.pose
    ctx.save()
    ctx.translate(x, y)
    ctx.rotate(yaw)
    const k = Math.max(1, 14 / (0.5 * view.scale)) // keep the marker visible when zoomed out
    ctx.scale(k, k)
    ctx.beginPath()
    ctx.roundRect(-0.28, -0.21, 0.56, 0.42, 0.08)
    ctx.fillStyle = '#ffffff'
    ctx.fill()
    ctx.lineWidth = 2 / (view.scale * k)
    ctx.strokeStyle = '#0891b2'
    ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(0.22, 0)
    ctx.lineTo(-0.06, 0.13)
    ctx.lineTo(0.02, 0)
    ctx.lineTo(-0.06, -0.13)
    ctx.closePath()
    ctx.fillStyle = '#0891b2'
    ctx.fill()
    ctx.restore()
  }
  // Waypoints: dashed line in visiting order, numbered markers
  const wps = props.waypoints
  if (wps.length) {
    polyline(ctx, wps.map((p) => [p.x, p.y]), 'rgba(100, 116, 139, 0.7)', 1.5, [5, 5])
    wps.forEach((p, i) => waypoint(ctx, p, i + 1))
  }
  // Goal of the running task (cleared once it finishes)
  const g = s?.goal
  if (g && (g.state === 'pending' || g.state === 'active' || g.state === 'canceling')) {
    arrow(ctx, g.x, g.y, g.yaw, TOOL_COLOR.goal, g.state === 'canceling')
  }
  if (placing) arrow(ctx, placing.x, placing.y, placing.yaw, TOOL_COLOR[placing.tool], false)
}

function layer(ctx: CanvasRenderingContext2D, c: CostmapLayer | null, alpha: number) {
  if (!c) return
  ctx.save()
  ctx.globalAlpha = alpha
  ctx.translate(c.origin[0], c.origin[1])
  ctx.rotate(c.origin[2])
  ctx.translate(0, c.height * c.resolution)
  ctx.scale(1, -1)
  ctx.imageSmoothingEnabled = false
  ctx.drawImage(c.image, 0, 0, c.width * c.resolution, c.height * c.resolution)
  ctx.restore()
}

/** Numbered waypoint: disc with the number and a short heading tick (constant size on screen) */
function waypoint(ctx: CanvasRenderingContext2D, p: WaypointMark, n: number) {
  const u = 1 / view.scale
  const color = WAYPOINT_COLOR[p.kind]
  const hollow = p.kind === 'draft' || p.kind === 'waiting'
  ctx.save()
  ctx.translate(p.x, p.y)
  ctx.rotate(p.yaw)
  ctx.beginPath()
  ctx.moveTo(9 * u, 0)
  ctx.lineTo(20 * u, 0)
  ctx.strokeStyle = color
  ctx.lineWidth = 2.5 * u
  ctx.stroke()
  ctx.rotate(-p.yaw)
  ctx.beginPath()
  ctx.arc(0, 0, 9 * u, 0, Math.PI * 2)
  ctx.fillStyle = hollow ? '#ffffff' : color
  ctx.fill()
  ctx.lineWidth = 2 * u
  ctx.stroke()
  // Text in screen orientation (the world transform flips y)
  ctx.scale(u, -u)
  ctx.fillStyle = hollow ? color : '#ffffff'
  ctx.font = '600 10px ui-monospace, monospace'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(String(n), 0, 0.5)
  ctx.restore()
}

/** Pose marker: ring at the position plus an arrow along the heading (constant size on screen) */
function arrow(ctx: CanvasRenderingContext2D, x: number, y: number, yaw: number, color: string, faded: boolean) {
  const u = 1 / view.scale // one screen pixel in meters
  ctx.save()
  ctx.globalAlpha = faded ? 0.45 : 1
  ctx.translate(x, y)
  ctx.beginPath()
  ctx.arc(0, 0, 7 * u, 0, Math.PI * 2)
  ctx.fillStyle = '#ffffff'
  ctx.fill()
  ctx.lineWidth = 2.5 * u
  ctx.strokeStyle = color
  ctx.stroke()
  ctx.rotate(yaw)
  ctx.beginPath()
  ctx.moveTo(7 * u, 0)
  ctx.lineTo(30 * u, 0)
  ctx.stroke()
  ctx.beginPath()
  ctx.moveTo(36 * u, 0)
  ctx.lineTo(26 * u, 6 * u)
  ctx.lineTo(26 * u, -6 * u)
  ctx.closePath()
  ctx.fillStyle = color
  ctx.fill()
  ctx.restore()
}

/** Screen position of a pointer event -> map coordinates */
function toWorld(e: PointerEvent) {
  const rect = canvas.value!.getBoundingClientRect()
  const { w, h } = size()
  return {
    x: view.cx + (e.clientX - rect.left - w / 2) / view.scale,
    y: view.cy - (e.clientY - rect.top - h / 2) / view.scale,
  }
}

// ---------------- Pan & zoom, or placing a pose ----------------
let drag: { x: number; y: number; cx: number; cy: number } | null = null
function onDown(e: PointerEvent) {
  ;(e.target as Element).setPointerCapture(e.pointerId)
  if (props.tool !== 'pan' && e.button === 0) {
    const p = toWorld(e)
    // Keep the robot's current heading until the user drags
    placing = { tool: props.tool, x: p.x, y: p.y, yaw: props.status?.pose?.yaw ?? 0 }
    request()
    return
  }
  drag = { x: e.clientX, y: e.clientY, cx: view.cx, cy: view.cy }
}
function onMove(e: PointerEvent) {
  if (placing) {
    const p = toWorld(e)
    // Heading follows the drag once the pointer is a few pixels away
    if (Math.hypot(p.x - placing.x, p.y - placing.y) * view.scale > 6) {
      placing.yaw = Math.atan2(p.y - placing.y, p.x - placing.x)
    }
    request()
    return
  }
  if (!drag) return
  view.cx = drag.cx - (e.clientX - drag.x) / view.scale
  view.cy = drag.cy + (e.clientY - drag.y) / view.scale
  request()
}
function onUp(e: PointerEvent) {
  if (placing) {
    const p = placing
    placing = null
    request()
    if (e.type === 'pointerup') emit('pick', p.tool, { x: p.x, y: p.y, yaw: p.yaw })
    return
  }
  drag = null
}
function onWheel(e: WheelEvent) {
  const rect = canvas.value!.getBoundingClientRect()
  const { w, h } = size()
  const sx = e.clientX - rect.left - w / 2
  const sy = e.clientY - rect.top - h / 2
  // Keep the point under the cursor fixed
  const wx = view.cx + sx / view.scale
  const wy = view.cy - sy / view.scale
  view.scale = Math.min(400, Math.max(1, view.scale * Math.exp(-e.deltaY * 0.0015)))
  view.cx = wx - sx / view.scale
  view.cy = wy + sy / view.scale
  request()
}

watch(
  () => [props.map?.name, props.image] as const,
  ([name, image]) => {
    const key = image ? String(name) : ''
    if (key && key !== fitted) {
      fitted = key
      fit()
    }
    request()
  },
)
watch(() => props.status, request)
watch(() => [props.waypoints, props.costmaps.global, props.costmaps.local], request)

onMounted(() => {
  resizeObs = new ResizeObserver(() => {
    if (fitted) fit()
    else request()
  })
  resizeObs.observe(host.value!)
})
onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  resizeObs?.disconnect()
})

defineExpose({ fit })
</script>

<template>
  <div ref="host" class="grid2d" :class="{ picking: tool !== 'pan' }">
    <canvas
      ref="canvas"
      @pointerdown="onDown"
      @pointermove="onMove"
      @pointerup="onUp"
      @pointercancel="onUp"
      @wheel.prevent="onWheel"
      @dblclick="fit"
    />
  </div>
</template>

<style scoped>
.grid2d {
  position: absolute;
  inset: 0;
  cursor: grab;
}
.grid2d:active {
  cursor: grabbing;
}
.grid2d.picking,
.grid2d.picking:active {
  cursor: crosshair;
}
canvas {
  display: block;
  width: 100%;
  height: 100%;
  touch-action: none;
}
</style>
