<!-- Thin ECharts wrapper for the evaluation page: renders `option`, follows the container size.
     Only the chart types the page uses are registered, which keeps the bundle small. -->
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsCoreOption } from 'echarts/core'

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, MarkLineComponent, CanvasRenderer])

const props = defineProps<{ option: EChartsCoreOption; height?: string; square?: boolean }>()
const el = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null

onMounted(() => {
  if (!el.value) return
  chart = echarts.init(el.value, undefined, { renderer: 'canvas' })
  chart.setOption(props.option)
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(el.value)
})
watch(() => props.option, (o) => chart?.setOption(o, { notMerge: true }))
onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div ref="el" class="chart" :class="{ square }" :style="square ? undefined : { height: height ?? '260px' }" />
</template>

<style scoped>
.chart {
  width: 100%;
  min-width: 0;
}
/* Trajectories need equal x/y scale: a square box with equal axis spans (set by the caller) */
.square {
  aspect-ratio: 1 / 1;
  max-width: 460px;
  margin: 0 auto;
}
</style>
