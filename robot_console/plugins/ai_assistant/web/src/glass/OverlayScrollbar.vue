<!-- Overlay scrollbar (from ClipDeck): a thin thumb over the right edge of a scroller (whose own bar is hidden in
     the glass skin). It widens when the pointer comes near, and can be dragged or clicked to jump. Put it next to the
     scroller inside a position: relative box. -->
<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{ scroller: HTMLElement | null }>()
const bar = ref<HTMLElement | null>(null)
const thumb = ref<HTMLElement | null>(null)

const NEAR = 36
const GRAB = 20
const MIN_THUMB = 32
const PAD = 4

let cleanup: (() => void) | null = null

function flag(el: HTMLElement, name: string, on: boolean) {
  if (on) el.dataset[name] = ''
  else delete el.dataset[name]
}

function bind() {
  cleanup?.()
  cleanup = null
  const n = props.scroller
  const r = bar.value
  const u = thumb.value
  if (!n || !r || !u) return
  let range = 0 // scrollable distance
  let travel = 0 // thumb travel
  let drag: { pointerId: number; grab: number } | null = null
  let raf = 0
  let rect: DOMRect | null = null
  let scrollable = false
  let near = false
  let lastH = ''
  let lastT = ''
  const setNear = (on: boolean) => { if (near !== on) { near = on; flag(r, 'near', on) } }
  const box = () => (rect ??= n.getBoundingClientRect())
  const forget = () => { rect = null }
  const markScroller = (on: boolean) => {
    cancelAnimationFrame(raf)
    if (('scrollable' in n.dataset) !== on) raf = requestAnimationFrame(() => flag(n, 'scrollable', on))
  }
  const place = () => {
    const tf = `translateY(${(range > 0 ? (n.scrollTop / range) * travel : 0).toFixed(2)}px)`
    if (tf !== lastT) { lastT = tf; u.style.transform = tf }
  }
  const update = () => {
    const h = n.clientHeight
    const total = n.scrollHeight
    range = Math.max(0, total - h)
    const can = range > 1
    markScroller(can)
    if (scrollable !== can) { scrollable = can; flag(r, 'scrollable', can) }
    const track = Math.max(0, h - PAD * 2)
    const size = can ? Math.min(track, Math.max(MIN_THUMB, (track * h) / total)) : 0
    travel = track - size
    const hs = `${size}px`
    if (hs !== lastH) { lastH = hs; u.style.height = hs }
    place()
  }
  const zoom = (b: DOMRect) => (n.offsetHeight ? b.height / n.offsetHeight : 1) || 1
  const fromRight = (e: PointerEvent) => box().right - e.clientX
  const inZone = (e: PointerEvent) => {
    const b = box()
    const d = b.right - e.clientX
    return range > 1 && d >= 0 && d <= NEAR && e.clientY >= b.top && e.clientY <= b.bottom
  }
  const dragTo = (e: PointerEvent) => {
    if (!drag) return
    const b = box()
    const y = (e.clientY - b.top) / zoom(b) - PAD - drag.grab
    n.scrollTop = travel > 0 ? (Math.min(travel, Math.max(0, y)) / travel) * range : 0
  }
  const move = (e: PointerEvent) => {
    if (drag) { dragTo(e); return }
    const z = inZone(e)
    if (z && !near) update()
    setNear(z)
  }
  const down = (e: PointerEvent) => {
    if (e.button !== 0 || range <= 1) return
    const d = fromRight(e)
    if (d < 0 || d > GRAB) return
    e.preventDefault()
    update()
    const b = box()
    const z = zoom(b)
    const t = u.getBoundingClientRect()
    const onThumb = e.clientY >= t.top && e.clientY <= t.bottom
    drag = { pointerId: e.pointerId, grab: onThumb ? (e.clientY - t.top) / z : t.height / z / 2 }
    n.setPointerCapture?.(e.pointerId)
    flag(r, 'dragging', true)
    setNear(true)
    if (!onThumb) dragTo(e)
  }
  const up = (e: PointerEvent) => {
    if (!drag || e.pointerId !== drag.pointerId) return
    drag = null
    if (n.hasPointerCapture?.(e.pointerId)) n.releasePointerCapture(e.pointerId)
    flag(r, 'dragging', false)
    setNear(inZone(e))
  }
  const leave = () => { if (!drag) setNear(false) }
  const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => { forget(); update() })
  const watchKids = () => {
    if (!ro) return
    ro.disconnect()
    ro.observe(n)
    for (const c of Array.from(n.children)) ro.observe(c)
  }
  const mo = typeof MutationObserver === 'undefined' ? null : new MutationObserver(() => { watchKids(); update() })
  watchKids()
  mo?.observe(n, { childList: true })
  update()
  window.addEventListener('resize', forget)
  n.addEventListener('pointerenter', forget)
  n.addEventListener('scroll', update, { passive: true })
  n.addEventListener('pointermove', move)
  n.addEventListener('pointerdown', down)
  n.addEventListener('pointerup', up)
  n.addEventListener('pointercancel', up)
  n.addEventListener('lostpointercapture', up)
  n.addEventListener('pointerleave', leave)
  cleanup = () => {
    cancelAnimationFrame(raf)
    ro?.disconnect()
    mo?.disconnect()
    window.removeEventListener('resize', forget)
    n.removeEventListener('pointerenter', forget)
    n.removeEventListener('scroll', update)
    n.removeEventListener('pointermove', move)
    n.removeEventListener('pointerdown', down)
    n.removeEventListener('pointerup', up)
    n.removeEventListener('pointercancel', up)
    n.removeEventListener('lostpointercapture', up)
    n.removeEventListener('pointerleave', leave)
    delete n.dataset.scrollable
    for (const k of ['scrollable', 'near', 'dragging']) delete r.dataset[k]
  }
}

watch(() => [props.scroller, bar.value, thumb.value], bind, { flush: 'post', immediate: true })
onBeforeUnmount(() => cleanup?.())
</script>

<template>
  <div ref="bar" class="aia-oscroll" aria-hidden="true"><div ref="thumb" class="aia-oscroll-thumb" /></div>
</template>
