<!-- Stacked token bars per hour or day (cache read at the bottom, then input, then output), plain SVG sized by a
     ResizeObserver. Hovering a column (the whole column is the hit area) shows its numbers; the legend hides a series. -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { UsagePoint } from './api'
import { compact, day, grouped, hour, moneyList } from './format'
import { t } from './sdk'

const props = defineProps<{ points: UsagePoint[]; bucket: 'hour' | 'day'; loading?: boolean }>()

type Series = 'cache_read' | 'cache_write' | 'input' | 'output'
const SERIES: Series[] = ['cache_read', 'cache_write', 'input', 'output']
const H = 200
const PAD = { t: 8, r: 4, b: 24, l: 44 }

const box = ref<HTMLElement | null>(null)
const width = ref(600)
const hidden = ref<Set<Series>>(new Set())
const hover = ref(-1)
const played = ref(false)
let ro: ResizeObserver | null = null

onMounted(() => {
  ro = new ResizeObserver(([e]) => (width.value = Math.max(240, e.contentRect.width)))
  if (box.value) ro.observe(box.value)
  requestAnimationFrame(() => requestAnimationFrame(() => (played.value = true)))
})
onUnmounted(() => ro?.disconnect())

const shown = computed(() => SERIES.filter((s) => !hidden.value.has(s)))
const totals = computed(() => props.points.map((p) => shown.value.reduce((a, s) => a + (p[s] || 0), 0)))
function niceCeil(v: number) {
  if (v <= 0) return 4
  const e = 10 ** Math.floor(Math.log10(v))
  const f = v / e
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * e
}
const max = computed(() => niceCeil(Math.max(0, ...totals.value) * 1.08))
const plotW = computed(() => width.value - PAD.l - PAD.r)
const plotH = H - PAD.t - PAD.b
const step = computed(() => plotW.value / Math.max(1, props.points.length))
const barW = computed(() => Math.max(2, Math.min(24, step.value * 0.6)))
const y = (v: number) => PAD.t + plotH - (v / max.value) * plotH
const ticks = computed(() => [0, 1, 2, 3, 4].map((i) => (max.value / 4) * i))
const empty = computed(() => !props.loading && totals.value.every((v) => v === 0))

/** every k-th label so they do not overlap */
const labelEvery = computed(() => Math.max(1, Math.ceil((props.points.length * 40) / plotW.value)))
const label = (ts: number) => (props.bucket === 'hour' ? hour(ts) : day(ts))

const bars = computed(() => props.points.map((p, i) => {
  let base = 0
  const segs = shown.value.map((s) => {
    const v = p[s] || 0
    const seg = { s, y: y(base + v), h: y(base) - y(base + v) }
    base += v
    return seg
  }).filter((g) => g.h > 0)
  return { i, x: PAD.l + step.value * i + (step.value - barW.value) / 2, segs, top: segs[segs.length - 1]?.s }
}))

function toggle(s: Series) {
  const next = new Set(hidden.value)
  if (next.has(s)) next.delete(s)
  else if (next.size < SERIES.length - 1) next.add(s)
  hidden.value = next
}

const tip = computed(() => {
  const i = hover.value
  const p = props.points[i]
  if (!p) return null
  const cx = PAD.l + step.value * (i + 0.5)
  const right = cx > width.value - 190
  return {
    p,
    total: SERIES.reduce((a, s) => a + (p[s] || 0), 0),
    style: right ? { right: `${width.value - cx + 12}px` } : { left: `${cx + 12}px` },
    when: props.bucket === 'hour' ? `${day(p.t)} ${hour(p.t)}` : day(p.t),
  }
})
</script>

<template>
  <div ref="box" class="aiu-chart" @mouseleave="hover = -1">
    <div class="aiu-legend">
      <button v-for="s in SERIES" :key="s" type="button" class="aiu-leg" :class="{ off: hidden.has(s) }" @click="toggle(s)">
        <i :class="`c-${s}`" />{{ t(`usage.series.${s}`) }}
      </button>
    </div>
    <svg :width="width" :height="H" class="aiu-svg" :class="{ played }" role="img" :aria-label="t('usage.chart.tokens')">
      <defs>
        <clipPath v-for="b in bars" :id="`aiu-top-${b.i}`" :key="b.i">
          <rect :x="b.x" :y="PAD.t" :width="barW" :height="plotH + 4" rx="2" />
        </clipPath>
      </defs>
      <g class="aiu-grid">
        <template v-for="(v, k) in ticks" :key="k">
          <line :x1="PAD.l" :x2="width - PAD.r" :y1="y(v)" :y2="y(v)" :class="{ base: k === 0 }" />
          <text :x="PAD.l - 8" :y="y(v) + 4" text-anchor="end">{{ compact(v) }}</text>
        </template>
      </g>
      <rect v-if="hover >= 0" class="aiu-band" :x="PAD.l + step * hover" :y="PAD.t" :width="step" :height="plotH" rx="4" />
      <g v-if="!loading" class="aiu-bars" :class="{ hovering: hover >= 0 }">
        <g v-for="b in bars" :key="b.i" :class="{ on: hover === b.i }" :style="{ transitionDelay: `${Math.min(b.i * 10, 280)}ms` }"
           :clip-path="`url(#aiu-top-${b.i})`">
          <rect v-for="g in b.segs" :key="g.s" :class="`c-${g.s}`" :x="b.x" :y="g.y" :width="barW" :height="g.h + (g.s === b.top ? 3 : 0)" />
        </g>
      </g>
      <g v-else class="aiu-skel">
        <rect v-for="(_, i) in points.length || 14" :key="i" :x="PAD.l + (plotW / (points.length || 14)) * i + 3"
              :y="PAD.t + plotH * (0.35 + ((i * 37) % 50) / 100)" :width="Math.max(2, plotW / (points.length || 14) - 6)"
              :height="plotH * (0.65 - ((i * 37) % 50) / 100)" rx="2" />
      </g>
      <g class="aiu-xlab">
        <template v-for="(p, i) in points" :key="i">
          <text v-if="i % labelEvery === 0" :x="PAD.l + step * (i + 0.5)" :y="H - 6" text-anchor="middle">{{ label(p.t) }}</text>
        </template>
      </g>
      <rect v-for="(_, i) in points" :key="`h${i}`" class="aiu-hit" :x="PAD.l + step * i" :y="PAD.t" :width="step" :height="plotH"
            @mouseenter="hover = i" />
      <text v-if="empty" :x="PAD.l + plotW / 2" :y="PAD.t + plotH / 2" text-anchor="middle" class="aiu-empty">{{ t('usage.chart.empty') }}</text>
    </svg>
    <div v-if="tip" class="aiu-tip" :style="tip.style">
      <div class="aiu-tip-head"><span>{{ tip.when }}</span><span class="aiu-grow" /><span>{{ t('usage.chart.total') }} {{ compact(tip.total) }}</span></div>
      <div v-for="s in [...SERIES].reverse()" :key="s" class="aiu-tip-row"><i :class="`c-${s}`" />{{ t(`usage.series.${s}`) }}<span class="aiu-grow" />{{ grouped(tip.p[s]) }}</div>
      <div v-if="tip.p.reasoning" class="aiu-tip-row sub">{{ t('usage.series.reasoning') }}<span class="aiu-grow" />{{ grouped(tip.p.reasoning) }}</div>
      <div class="aiu-tip-row sub">{{ t('usage.chart.calls') }}<span class="aiu-grow" />{{ grouped(tip.p.calls) }}</div>
      <div v-if="tip.p.cost?.length" class="aiu-tip-row sub">{{ t('usage.chart.cost') }}<span class="aiu-grow" />{{ moneyList(tip.p.cost) }}</div>
    </div>
  </div>
</template>
