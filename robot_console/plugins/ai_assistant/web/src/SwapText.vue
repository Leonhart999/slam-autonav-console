<!-- Text that changes smoothly: the old text slides out and fades, the new one slides in, and the box glides from the
     old width to the new one (so the chips next to it move along instead of jumping). `dir` picks the direction:
     1 rolls upwards (a higher value), -1 downwards. -->
<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { reducedMotion } from './spring'

const props = withDefaults(defineProps<{ text: string; dir?: number }>(), { dir: 1 })

const box = ref<HTMLElement | null>(null)
const EASE = 'cubic-bezier(0.22, 1, 0.36, 1)'
let anim: Animation | null = null

watch(() => props.text, async () => {
  const el = box.value
  if (!el || reducedMotion()) return
  const from = el.getBoundingClientRect().width // includes a width animation still running
  anim?.cancel()
  await nextTick()
  const to = el.getBoundingClientRect().width
  if (Math.abs(to - from) < 0.5) return
  const a = el.animate([{ width: `${from}px` }, { width: `${to}px` }], { duration: 320, easing: EASE })
  anim = a
  // While the box glides, the new text is wider than the box for a moment: cut it at the edge instead of showing
  // an ellipsis. The ellipsis stays for text that is still too long once the box has its final width.
  el.classList.add('gliding')
  const done = () => {
    if (anim !== a) return // a newer change already took over
    anim = null
    el.classList.remove('gliding')
  }
  a.onfinish = done
  a.oncancel = done
}, { flush: 'pre' })
</script>

<template>
  <span ref="box" class="aia-swapt" :style="{ '--aia-dir': dir < 0 ? -1 : 1 }">
    <transition name="aia-swapt">
      <span :key="text" class="aia-swapt-in" :class="{ empty: !text }">{{ text }}</span>
    </transition>
  </span>
</template>
