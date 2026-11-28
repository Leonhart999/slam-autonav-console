<!-- Edge glow (from ClipDeck): a band of moving colour light inside the panel's rounded edge, drawn by a WebGL2
     worker on an OffscreenCanvas. Shown while `active` (the assistant is working). Nothing is drawn without
     OffscreenCanvas / WebGL2, with reduced motion, or with animations turned off. -->
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { EDGE_GLOW_WORKER } from './edgeGlowWorker'
import { noMotion } from './physics'
import { shown } from '../prefs'

const props = defineProps<{ active: boolean }>()
const box = ref<HTMLElement | null>(null)
const cvs = ref<HTMLCanvasElement | null>(null)

let worker: Worker | null = null
let url = ''
let ro: ResizeObserver | null = null
let lastSize = ''
let lastState = ''

function post(msg: Record<string, unknown>, transfer?: Transferable[]) {
  worker?.postMessage(msg, transfer ?? [])
}

function resize() {
  const el = box.value
  if (!worker || !el) return
  const r = el.getBoundingClientRect()
  if (r.width < 1 || r.height < 1) return
  const dpr = window.devicePixelRatio || 1
  const radius = Number.parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0
  const msg = { type: 'resize', width: Math.round(r.width * dpr), height: Math.round(r.height * dpr), dpr, radius }
  const key = JSON.stringify(msg)
  if (key !== lastSize) { lastSize = key; post(msg) }
}

function state() {
  if (!worker) return
  const msg = { type: 'state', lit: props.active, asleep: document.hidden, motion: !noMotion(), divisor: 1 }
  const key = JSON.stringify(msg)
  if (key !== lastState) { lastState = key; post(msg) }
}

function start() {
  if (worker) return true
  const c = cvs.value
  if (!c || typeof Worker === 'undefined' || !('transferControlToOffscreen' in c)) return false
  try {
    const off = c.transferControlToOffscreen()
    url = URL.createObjectURL(new Blob([EDGE_GLOW_WORKER], { type: 'text/javascript;charset=utf-8' }))
    worker = new Worker(url, { name: 'aia-edge-glow' })
    worker.postMessage({ type: 'init', canvas: off }, [off])
  } catch {
    worker = null
    return false
  }
  resize()
  state()
  return true
}

const onVisibility = () => state()
onMounted(() => {
  ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(resize)
  if (box.value) ro?.observe(box.value)
  document.addEventListener('visibilitychange', onVisibility)
  if (props.active) start()
})
// the worker starts the first time the glow is needed
watch(() => [props.active, shown.value.animations], () => { if (props.active) start(); state() })
onBeforeUnmount(() => {
  ro?.disconnect()
  document.removeEventListener('visibilitychange', onVisibility)
  worker?.terminate()
  worker = null
  if (url) URL.revokeObjectURL(url)
})
</script>

<template>
  <div ref="box" class="aia-edge-glow" :class="{ 'is-active': active }" aria-hidden="true">
    <canvas ref="cvs" class="aia-edge-glow-canvas" />
  </div>
</template>
