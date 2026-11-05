<!-- 2D grid editor: eraser (free) / obstacle pen / unknown pen on map.pgm, undo/redo, zoom/pan, compare with the
     original. Saving always creates a new map version (robot_console/map_edit.py); the source map is never changed. -->
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { errorText } from '@/messages'
import type { MapDetail } from '@/maps/api'
import { editApi, FREE, OCCUPIED, UNKNOWN, type EditGrid } from '@/maps/edit'

const props = defineProps<{ modelValue: boolean; map: MapDetail | null }>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; saved: [string] }>()
const { t } = useI18n({ messages: {
  'zh-CN': { ed: {
    title: '编辑 2D 地图', desc: '在栅格上直接涂改，例如擦掉噪点、补上没扫到的墙。保存时另存为新版本，原地图不变；分辨率和原点不变，点云和航点照原样复制。',
    mode: '画笔', free: '擦成空闲', occupied: '画障碍', unknown: '设为未知', pan: '平移',
    brush: '笔刷', cells: '{n} 格', meters: '≈ {m} m',
    undo: '撤销', redo: '重做', fit: '适应窗口', original: '显示原图', marks: '标出改动',
    changed: '改动：空闲 {f} · 障碍 {o} · 未知 {u} 格', none: '还没有改动',
    help: '左键涂画；右键或中键拖动平移，按住空格也可以用左键平移；滚轮缩放。Ctrl+Z 撤销，Ctrl+Y 重做，[ ] 调笔刷。',
    name: '新版本名称', save: '另存为新版本', close: '关闭', saved: '已另存为 {name}',
    leave: '有未保存的改动，确定关闭？', loading: '加载栅格…', size: '{w} × {h} 格 · {res} m/格',
    legacy: '这张地图的 map.yaml 用的是旧阈值（free_thresh {ft}），Nav2 会把 {n} 格本应是未知的区域（灰色 205）当作空闲。编辑器按 Nav2 实际读到的结果显示，这些格显示为空闲，另存后含义不变。',
    legacyFix: '按 pcd2grid 规则把这 {n} 格改回未知（只改动你没涂过的格子）',
  } },
  en: { ed: {
    title: 'Edit 2D map', desc: 'Paint directly on the grid, e.g. erase noise or close walls that were not scanned. Saving creates a new version and leaves the source unchanged; resolution and origin stay the same, the cloud and waypoints are copied as they are.',
    mode: 'Pen', free: 'Erase to free', occupied: 'Draw obstacle', unknown: 'Set unknown', pan: 'Pan',
    brush: 'Brush', cells: '{n} cells', meters: '≈ {m} m',
    undo: 'Undo', redo: 'Redo', fit: 'Fit', original: 'Show original', marks: 'Mark changes',
    changed: 'Changed: free {f} · obstacle {o} · unknown {u} cells', none: 'No changes yet',
    help: 'Left button paints; drag with the right or middle button (or hold Space) to pan; wheel zooms. Ctrl+Z undo, Ctrl+Y redo, [ ] brush size.',
    name: 'New version name', save: 'Save as new version', close: 'Close', saved: 'Saved as {name}',
    leave: 'Discard unsaved changes?', loading: 'Loading grid…', size: '{w} × {h} cells · {res} m/cell',
    legacy: 'This map.yaml uses an old threshold (free_thresh {ft}), so Nav2 reads {n} cells that were meant to be unknown (grey 205) as free. The editor shows what Nav2 actually reads, so these cells appear free and keep that meaning in the new version.',
    legacyFix: 'Set these {n} cells back to unknown with the pcd2grid rule (only cells you did not paint)',
  } },
} })

type Mode = 'free' | 'occupied' | 'unknown' | 'pan'
const VALUE: Record<Exclude<Mode, 'pan'>, number> = { free: FREE, occupied: OCCUPIED, unknown: UNKNOWN }
// Display colours (rgb) for the three values, and for changed cells when "mark changes" is on
const COLOR: Record<number, [number, number, number]> = {
  [FREE]: [255, 255, 255], [OCCUPIED]: [30, 41, 59], [UNKNOWN]: [203, 213, 225],
}
const MARK: Record<number, [number, number, number]> = {
  [FREE]: [165, 243, 252], [OCCUPIED]: [79, 70, 229], [UNKNOWN]: [253, 186, 116],
}

interface Stroke { value: number; idx: number[]; old: number[] }

const open = computed({
  get: () => props.modelValue,
  set: (v) => { if (!saving.value) emit('update:modelValue', v) },
})
const grid = shallowRef<EditGrid | null>(null)
let original: Uint8Array | null = null
let cells: Uint8Array | null = null
const loading = ref(false)
const loadError = ref('')
const saving = ref(false)
const mode = ref<Mode>('free')
const brush = ref(3)
const showOriginal = ref(false)
const marks = ref(true)
const name = ref('')
const legacyFix = ref(false)
const undoStack = shallowRef<Stroke[]>([])
const redoStack = shallowRef<Stroke[]>([])
const counts = ref({ free: 0, occupied: 0, unknown: 0 })
const canvas = ref<HTMLCanvasElement | null>(null)
const wrap = ref<HTMLDivElement | null>(null)
let off: HTMLCanvasElement | null = null
let image: ImageData | null = null
let view = { s: 1, x: 0, y: 0 }        // screen = cell * s + (x, y)
let abort = new AbortController()
let observer: ResizeObserver | null = null
let hover: { cx: number; cy: number } | null = null
let raf = 0

const dirty = computed(() => counts.value.free + counts.value.occupied + counts.value.unknown > 0)
const canSave = computed(() => dirty.value || (legacyFix.value && (grid.value?.legacyFree ?? 0) > 0))

watch(open, async (v) => {
  if (!v) return cleanup()
  const m = props.map
  if (!m) return
  name.value = `${m.name}_edit`.slice(0, 64)
  legacyFix.value = false
  undoStack.value = []
  redoStack.value = []
  counts.value = { free: 0, occupied: 0, unknown: 0 }
  loading.value = true
  loadError.value = ''
  abort = new AbortController()
  try {
    const g = await editApi.grid(m.name, abort.signal)
    grid.value = g
    original = g.cells.slice()
    cells = g.cells.slice()
    off = document.createElement('canvas')
    off.width = g.width
    off.height = g.height
    image = new ImageData(g.width, g.height)
    repaintAll()
    await nextTick()
    observer = new ResizeObserver(() => { sizeCanvas(); draw() })
    if (wrap.value) observer.observe(wrap.value)
    sizeCanvas()
    fit()
  } catch (e) {
    if (!abort.signal.aborted) loadError.value = errorText(e)
  } finally {
    loading.value = false
  }
})

function cleanup() {
  abort.abort()
  observer?.disconnect()
  observer = null
  cancelAnimationFrame(raf)
}
onBeforeUnmount(cleanup)

// ---------------- rendering ----------------

function paintCell(i: number) {
  if (!image || !cells || !original) return
  const v = showOriginal.value ? original[i] : cells[i]
  const changed = !showOriginal.value && marks.value && cells[i] !== original[i]
  const c = (changed ? MARK : COLOR)[v] ?? COLOR[UNKNOWN]
  const k = i * 4
  image.data[k] = c[0]
  image.data[k + 1] = c[1]
  image.data[k + 2] = c[2]
  image.data[k + 3] = 255
}
function repaintAll() {
  if (!cells) return
  for (let i = 0; i < cells.length; i++) paintCell(i)
  off?.getContext('2d')?.putImageData(image!, 0, 0)
  draw()
}
watch([showOriginal, marks], repaintAll)

function sizeCanvas() {
  const c = canvas.value
  const w = wrap.value
  if (!c || !w) return
  const dpr = window.devicePixelRatio || 1
  c.width = Math.max(1, Math.round(w.clientWidth * dpr))
  c.height = Math.max(1, Math.round(w.clientHeight * dpr))
}
function fit() {
  const c = canvas.value
  const g = grid.value
  if (!c || !g) return
  const s = Math.min(c.width / g.width, c.height / g.height) * 0.95
  view = { s, x: (c.width - g.width * s) / 2, y: (c.height - g.height * s) / 2 }
  draw()
}
function draw() {
  cancelAnimationFrame(raf)
  raf = requestAnimationFrame(() => {
    const c = canvas.value
    const g = grid.value
    const ctx = c?.getContext('2d')
    if (!c || !g || !ctx || !off) return
    ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.fillStyle = '#eef2f8'
    ctx.fillRect(0, 0, c.width, c.height)
    ctx.imageSmoothingEnabled = false
    ctx.drawImage(off, view.x, view.y, g.width * view.s, g.height * view.s)
    ctx.strokeStyle = 'rgba(15,30,60,0.25)'
    ctx.lineWidth = 1
    ctx.strokeRect(view.x - 0.5, view.y - 0.5, g.width * view.s + 1, g.height * view.s + 1)
    if (hover && mode.value !== 'pan' && !panning) {
      const r = brushRange(hover.cx, hover.cy)
      ctx.strokeStyle = '#0891b2'
      ctx.lineWidth = 1.5 * (window.devicePixelRatio || 1)
      ctx.strokeRect(view.x + r.x0 * view.s, view.y + r.y0 * view.s, (r.x1 - r.x0 + 1) * view.s, (r.y1 - r.y0 + 1) * view.s)
    }
  })
}

// ---------------- painting ----------------

let stroke: Stroke | null = null
let touched: Set<number> | null = null
let last: { cx: number; cy: number } | null = null
let panning: { px: number; py: number; vx: number; vy: number } | null = null
let space = false

function toCell(ev: PointerEvent | WheelEvent) {
  const c = canvas.value!
  const r = c.getBoundingClientRect()
  const dpr = c.width / r.width
  const sx = (ev.clientX - r.left) * dpr
  const sy = (ev.clientY - r.top) * dpr
  return { sx, sy, cx: Math.floor((sx - view.x) / view.s), cy: Math.floor((sy - view.y) / view.s) }
}
function brushRange(cx: number, cy: number) {
  const n = brush.value
  const x0 = cx - Math.floor((n - 1) / 2)
  const y0 = cy - Math.floor((n - 1) / 2)
  return { x0, y0, x1: x0 + n - 1, y1: y0 + n - 1 }
}
function stamp(cx: number, cy: number) {
  const g = grid.value
  if (!g || !cells || !stroke || !touched) return
  const r = brushRange(cx, cy)
  for (let y = Math.max(0, r.y0); y <= Math.min(g.height - 1, r.y1); y++) {
    for (let x = Math.max(0, r.x0); x <= Math.min(g.width - 1, r.x1); x++) {
      const i = y * g.width + x
      if (touched.has(i) || cells[i] === stroke.value) continue
      touched.add(i)
      stroke.idx.push(i)
      stroke.old.push(cells[i])
      cells[i] = stroke.value
      paintCell(i)
    }
  }
}
function line(a: { cx: number; cy: number }, b: { cx: number; cy: number }) {
  const steps = Math.max(Math.abs(b.cx - a.cx), Math.abs(b.cy - a.cy), 1)
  for (let k = 1; k <= steps; k++) {
    stamp(Math.round(a.cx + ((b.cx - a.cx) * k) / steps), Math.round(a.cy + ((b.cy - a.cy) * k) / steps))
  }
}
function flush() {
  if (image && off) off.getContext('2d')?.putImageData(image, 0, 0)
  draw()
}

function onDown(ev: PointerEvent) {
  if (!grid.value || showOriginal.value && ev.button === 0 && mode.value !== 'pan' && !space) return
  canvas.value?.setPointerCapture(ev.pointerId)
  const p = toCell(ev)
  if (ev.button !== 0 || mode.value === 'pan' || space) {
    panning = { px: p.sx, py: p.sy, vx: view.x, vy: view.y }
    return
  }
  const m = mode.value as Exclude<Mode, 'pan'>
  stroke = { value: VALUE[m], idx: [], old: [] }
  touched = new Set()
  last = { cx: p.cx, cy: p.cy }
  stamp(p.cx, p.cy)
  flush()
}
function onMove(ev: PointerEvent) {
  if (!grid.value) return
  const p = toCell(ev)
  hover = { cx: p.cx, cy: p.cy }
  if (panning) {
    view.x = panning.vx + p.sx - panning.px
    view.y = panning.vy + p.sy - panning.py
  } else if (stroke && last) {
    line(last, p)
    last = { cx: p.cx, cy: p.cy }
    flush()
    return
  }
  draw()
}
function onUp() {
  panning = null
  if (stroke && stroke.idx.length) {
    undoStack.value = [...undoStack.value, stroke]
    redoStack.value = []
  }
  stroke = null
  touched = null
  last = null
  recount()
}
function onLeave() {
  hover = null
  draw()
}
function onWheel(ev: WheelEvent) {
  if (!grid.value) return
  const p = toCell(ev)
  const k = Math.exp(-ev.deltaY * 0.0015)
  const s = Math.min(80, Math.max(0.2, view.s * k))
  view.x = p.sx - ((p.sx - view.x) * s) / view.s
  view.y = p.sy - ((p.sy - view.y) * s) / view.s
  view.s = s
  draw()
}

function apply(st: Stroke, undo: boolean) {
  if (!cells) return
  st.idx.forEach((i, k) => {
    cells![i] = undo ? st.old[k] : st.value
    paintCell(i)
  })
  flush()
  recount()
}
function undo() {
  const s = undoStack.value.at(-1)
  if (!s) return
  undoStack.value = undoStack.value.slice(0, -1)
  redoStack.value = [...redoStack.value, s]
  apply(s, true)
}
function redo() {
  const s = redoStack.value.at(-1)
  if (!s) return
  redoStack.value = redoStack.value.slice(0, -1)
  undoStack.value = [...undoStack.value, s]
  apply(s, false)
}
function recount() {
  if (!cells || !original) return
  const c = { free: 0, occupied: 0, unknown: 0 }
  for (let i = 0; i < cells.length; i++) {
    if (cells[i] === original[i]) continue
    if (cells[i] === FREE) c.free++
    else if (cells[i] === OCCUPIED) c.occupied++
    else c.unknown++
  }
  counts.value = c
}

function onKey(ev: KeyboardEvent) {
  if (!open.value || (ev.target as HTMLElement)?.tagName === 'INPUT') return
  if (ev.code === 'Space') {
    space = ev.type === 'keydown'
    ev.preventDefault()
    return
  }
  if (ev.type !== 'keydown') return
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'z') { ev.preventDefault(); ev.shiftKey ? redo() : undo() }
  else if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'y') { ev.preventDefault(); redo() }
  else if (ev.key === '[') { brush.value = Math.max(1, brush.value - 1); draw() }
  else if (ev.key === ']') { brush.value = Math.min(41, brush.value + 1); draw() }
}
window.addEventListener('keydown', onKey)
window.addEventListener('keyup', onKey)
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('keyup', onKey)
})
watch(brush, draw)

// ---------------- save / close ----------------

async function save() {
  const m = props.map
  if (!m || !cells) return
  saving.value = true
  try {
    const out = await editApi.save(m.name, name.value.trim(), cells, legacyFix.value)
    ElMessage.success(t('ed.saved', { name: out.name }))
    original = cells.slice()
    undoStack.value = []
    redoStack.value = []
    recount()
    emit('update:modelValue', false)
    emit('saved', out.name)
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    saving.value = false
  }
}
function beforeClose(done: () => void) {
  if (saving.value) return
  if (dirty.value && !window.confirm(t('ed.leave'))) return
  done()
}
const brushMeters = computed(() => (grid.value ? (brush.value * grid.value.resolution).toFixed(2) : '—'))
</script>

<template>
  <el-dialog v-model="open" :title="`${t('ed.title')} · ${map?.name ?? ''}`" width="min(1180px, 96vw)" top="4vh"
    :close-on-click-modal="false" :before-close="beforeClose" destroy-on-close>
    <p class="desc">{{ t('ed.desc') }}</p>
    <div v-if="grid && grid.legacyFree > 0" class="legacy">
      <p>{{ t('ed.legacy', { n: grid.legacyFree, ft: grid.freeThresh }) }}</p>
      <el-checkbox v-model="legacyFix" size="small">{{ t('ed.legacyFix', { n: grid.legacyFree }) }}</el-checkbox>
    </div>
    <div class="bar">
      <el-radio-group v-model="mode" size="small">
        <el-radio-button value="free">{{ t('ed.free') }}</el-radio-button>
        <el-radio-button value="occupied">{{ t('ed.occupied') }}</el-radio-button>
        <el-radio-button value="unknown">{{ t('ed.unknown') }}</el-radio-button>
        <el-radio-button value="pan">{{ t('ed.pan') }}</el-radio-button>
      </el-radio-group>
      <span class="lbl">{{ t('ed.brush') }}</span>
      <el-slider v-model="brush" :min="1" :max="41" class="slider" :show-tooltip="false" />
      <span class="mono dim">{{ t('ed.cells', { n: brush }) }} {{ t('ed.meters', { m: brushMeters }) }}</span>
      <span class="spacer" />
      <el-button size="small" :disabled="!undoStack.length" @click="undo">{{ t('ed.undo') }}</el-button>
      <el-button size="small" :disabled="!redoStack.length" @click="redo">{{ t('ed.redo') }}</el-button>
      <el-button size="small" @click="fit">{{ t('ed.fit') }}</el-button>
      <el-checkbox v-model="showOriginal" size="small">{{ t('ed.original') }}</el-checkbox>
      <el-checkbox v-model="marks" size="small" :disabled="showOriginal">{{ t('ed.marks') }}</el-checkbox>
    </div>
    <div ref="wrap" class="stage" :class="{ pan: mode === 'pan' }">
      <canvas ref="canvas" @pointerdown="onDown" @pointermove="onMove" @pointerup="onUp" @pointercancel="onUp"
        @pointerleave="onLeave" @wheel.prevent="onWheel" @contextmenu.prevent />
      <p v-if="loading" class="overlay">{{ t('ed.loading') }}</p>
      <p v-else-if="loadError" class="overlay bad">{{ loadError }}</p>
    </div>
    <div class="foot">
      <span class="mono dim">
        <template v-if="grid">{{ t('ed.size', { w: grid.width, h: grid.height, res: grid.resolution }) }} · </template>
        {{ dirty ? t('ed.changed', { f: counts.free, o: counts.occupied, u: counts.unknown }) : t('ed.none') }}
      </span>
      <span class="help">{{ t('ed.help') }}</span>
    </div>
    <template #footer>
      <div class="actions">
        <span class="lbl">{{ t('ed.name') }}</span>
        <el-input v-model="name" size="small" class="name" maxlength="64" />
        <el-button @click="open = false">{{ t('ed.close') }}</el-button>
        <el-button type="primary" :disabled="!canSave || !name.trim()" :loading="saving" @click="save">{{ t('ed.save') }}</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.legacy { margin: 0 0 10px; padding: 8px 12px; border-radius: 10px; font-size: 12.5px; line-height: 1.6; color: var(--ink-2);
  background: color-mix(in srgb, var(--warn) 8%, transparent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--warn) 25%, transparent); }
.legacy p { margin: 0 0 4px; }
.desc { margin: 0 0 10px; font-size: 12.5px; line-height: 1.6; color: var(--ink-3); }
.bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin-bottom: 8px; }
.lbl { font-size: 12.5px; color: var(--ink-2); }
.slider { width: 140px; }
.spacer { flex: 1; }
.mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.dim { font-size: 12px; color: var(--ink-3); }
.stage { position: relative; height: min(62vh, 620px); border-radius: 10px; overflow: hidden;
  box-shadow: inset 0 0 0 1px var(--hairline); background: #eef2f8; cursor: crosshair; touch-action: none; }
.stage.pan { cursor: grab; }
.stage canvas { display: block; width: 100%; height: 100%; }
.overlay { position: absolute; inset: 0; display: grid; place-items: center; margin: 0; font-size: 13px; color: var(--ink-3); }
.bad { color: var(--bad); }
.foot { display: flex; flex-wrap: wrap; gap: 6px 16px; margin-top: 8px; }
.help { font-size: 12px; color: var(--ink-3); }
.actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; }
.name { width: 240px; }
</style>
