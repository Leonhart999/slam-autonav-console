<!-- Liquid glass surface (ported from ClipDeck's liquid-glass-react based control). Under the content: a layer that
     blurs and saturates what lies behind the control and bends it at the rim through an SVG displacement filter, a
     shade, two rims of light (screen + overlay), and on magnetic controls a spotlight that follows the pointer.
     In the classic skin it is only a box around its content (display: contents). -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { isGlass, liquid } from '../prefs'
import { liquidMaps } from './maps'
import { magnetize } from './magnet'

const props = withDefaults(defineProps<{
  radius?: number
  magnetic?: boolean
  variant?: 'surface' | 'lens'
  /** keep the current map while the size animates (the map is redrawn when this turns false) */
  freezeMap?: boolean
}>(), { radius: 999, magnetic: false, variant: 'surface', freezeMap: false })

let seq = 0
const id = `aia-liquid-${Math.random().toString(36).slice(2, 8)}-${++seq}`
const root = ref<HTMLElement | null>(null)
defineExpose({ root })

const LENS_DISPLACEMENT = 16
const LENS_FILTER = 'blur(0.4px) saturate(180%) brightness(1.14)'

const on = computed(() => isGlass.value && liquid.value.liquidEnabled)
const lens = computed(() => props.variant === 'lens')
const magnetOn = computed(() => isGlass.value && props.magnetic && !lens.value && liquid.value.animations)
// The panel's glass is live (the page under it shows through), and on live glass ClipDeck's chromatic aberration
// turns grey at the rim, so only the plain displacement is used here (as ClipDeck does on live glass)

const box = ref({ width: 0, height: 0 }) // filter region
const mapBox = ref({ width: 0, height: 0 }) // size the map was drawn for
let ro: ResizeObserver | null = null
let measure: (() => void) | null = null

function setSize(w: number, h: number) {
  if (box.value.width !== w || box.value.height !== h) box.value = { width: w, height: h }
  const m = mapBox.value
  if (props.freezeMap && m.width > 0 && m.height > 0) return
  if (m.width !== w || m.height !== h) mapBox.value = { width: w, height: h }
}

function observe() {
  ro?.disconnect()
  ro = null
  measure = null
  const el = root.value
  if (!el || !on.value || typeof ResizeObserver === 'undefined') return
  measure = () => {
    const cs = getComputedStyle(el)
    const px = (s: string) => parseFloat(s) || 0
    setSize(Math.round(el.clientWidth - px(cs.paddingLeft) - px(cs.paddingRight)),
      Math.round(el.clientHeight - px(cs.paddingTop) - px(cs.paddingBottom)))
  }
  ro = new ResizeObserver(([entry]) => setSize(Math.round(entry.contentRect.width), Math.round(entry.contentRect.height)))
  ro.observe(el)
  measure()
}

watch(() => props.freezeMap, (now, was) => { if (was && !now) measure?.() })

const mode = computed(() => (lens.value ? 'lens' : liquid.value.liquidMode))
const maps = computed(() => on.value
  ? liquidMaps(mode.value, mapBox.value.width, mapBox.value.height, props.radius, false)
  : null)
const scale = computed(() => (lens.value ? LENS_DISPLACEMENT : liquid.value.displacementScale))
const showFilter = computed(() => !!maps.value && scale.value > 0 && box.value.width > 0 && box.value.height > 0)

const warpStyle = computed(() => {
  const f = lens.value ? LENS_FILTER : `blur(${4 + liquid.value.frost * 32}px) saturate(${liquid.value.liquidSaturation}%)`
  return {
    backdropFilter: f,
    WebkitBackdropFilter: f,
    filter: maps.value && scale.value > 0 ? `url(#${id})` : undefined,
  }
})

let unMagnet: (() => void) | null = null
function bindMagnet() {
  unMagnet?.()
  unMagnet = null
  if (root.value && magnetOn.value) unMagnet = magnetize(root.value, liquid.value.elasticity / 0.15)
}

onMounted(() => {
  observe()
  bindMagnet()
})
watch(on, observe, { flush: 'post' })
watch([magnetOn, () => liquid.value.elasticity], bindMagnet, { flush: 'post' })
onBeforeUnmount(() => {
  ro?.disconnect()
  unMagnet?.()
})
</script>

<template>
  <div
    ref="root"
    :class="['aia-liquid', on ? 'is-liquid' : 'is-plain', { 'is-magnetic': magnetOn, 'is-lens': lens }]"
    :aria-hidden="lens || undefined"
    :style="{ '--lg-radius': `${radius}px`, '--lg-shade': liquid.liquidShade / 100 }"
  >
    <template v-if="isGlass">
      <svg v-if="showFilter && maps" class="aia-liquid-defs" aria-hidden="true" focusable="false">
        <filter :id="id" color-interpolation-filters="sRGB" filterUnits="userSpaceOnUse" x="0" y="0"
                :width="box.width" :height="box.height">
          <feImage :href="maps.map" preserveAspectRatio="none" result="DISPLACEMENT_MAP" x="0" y="0"
                   :width="box.width" :height="box.height" />
          <feImage :href="maps.shape" preserveAspectRatio="none" result="SHAPE" x="0" y="0"
                   :width="box.width" :height="box.height" />
          <feDisplacementMap in="SourceGraphic" in2="DISPLACEMENT_MAP" result="UNCLIPPED" :scale="scale"
                             xChannelSelector="R" yChannelSelector="B" />
          <feComposite in="UNCLIPPED" in2="SHAPE" operator="in" />
        </filter>
      </svg>
      <span class="aia-liquid-warp" aria-hidden="true" :style="warpStyle" />
      <span v-if="liquid.liquidShade !== 0" class="aia-liquid-shade" aria-hidden="true" />
      <span class="aia-liquid-rim is-screen" aria-hidden="true" />
      <span class="aia-liquid-rim is-overlay" aria-hidden="true" />
      <span v-if="magnetOn" class="aia-liquid-spot" aria-hidden="true" />
    </template>
    <div v-if="$slots.default" class="aia-liquid-content"><slot /></div>
  </div>
</template>
