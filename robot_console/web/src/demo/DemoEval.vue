<!-- Evaluation of the demo run: the evo statistics saved with the run, APE over time and the trajectories
     from above, with the replay time marked on both charts -->
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { EChartsCoreOption } from 'echarts/core'
import GlassPanel from '@/components/GlassPanel.vue'
import EvalChart from '@/components/EvalChart.vue'
import { timeSeries, topView } from '@/evaluation/charts'
import { AXIS, REFERENCE, SERIES } from '@/evaluation/colors'
import { indexAt, type DemoRun } from './data'

const props = defineProps<{ run: DemoRun; time: number }>()
const { t } = useI18n()

const ev = computed(() => props.run.eval)
const cm = (m: number) => `${(m * 100).toFixed(1)} cm`
const stats = computed(() => [
  { key: 'ate', value: cm(ev.value.ape.rmse), sub: t('eval.ateSub'), main: true },
  { key: 'mean', value: cm(ev.value.ape.mean) },
  { key: 'max', value: cm(ev.value.ape.max) },
  { key: 'rpe', value: cm(ev.value.rpe.rmse), sub: t('eval.rpeSub', { delta: ev.value.rpe_delta_m ?? 1 }) },
])

// The charts are rebuilt a few times a second at most, not on every animation frame
const chartTime = ref(props.time)
let timer = 0
watch(() => props.time, (v) => {
  if (Math.abs(v - chartTime.value) > props.run.duration / 4) { // a long seek shows at once
    chartTime.value = v
    return
  }
  if (timer) return
  timer = window.setTimeout(() => {
    timer = 0
    chartTime.value = props.time
  }, 250)
})
onBeforeUnmount(() => window.clearTimeout(timer))

const apePoints = computed(() => {
  const { t: ts, ape } = props.run.est
  const out: [number, number][] = []
  for (let i = 0; i < ts.length; i++) if (ape[i] != null) out.push([ts[i], ape[i] as number])
  return out
})

const apeOption = computed<EChartsCoreOption | null>(() => {
  const option = timeSeries(
    [{ name: 'APE', points: apePoints.value, color: SERIES[0] }],
    { x: t('eval.axisTime'), y: t('eval.axisApe'), unit: 'm' },
    { min: 0 },
  ) as (EChartsCoreOption & { series: Record<string, unknown>[] }) | null
  if (!option) return null
  option.series[0].markLine = {
    silent: true, symbol: 'none', animation: false, label: { show: false },
    data: [
      { xAxis: chartTime.value, lineStyle: { color: AXIS.ink, width: 1.5, type: 'solid' } },
      { yAxis: ev.value.ape.rmse, lineStyle: { color: REFERENCE, type: 'dashed', width: 1 } },
    ],
  }
  return option
})

const topOption = computed<EChartsCoreOption | null>(() => {
  const tv = props.run.top_view
  const option = topView(
    [
      { name: t('eval.truth'), points: tv.gt, color: REFERENCE, dashed: true },
      { name: t('eval.estimate'), points: tv.est, color: SERIES[0], ends: true },
    ],
    { start: t('eval.start'), end: t('eval.end') },
  ) as (EChartsCoreOption & { series: Record<string, unknown>[] }) | null
  if (!option) return null
  const now = tv.est[indexAt(tv.t, chartTime.value)]
  option.series.push({
    name: t('eval.now'), type: 'line', data: [now], symbol: 'circle', symbolSize: 11, z: 5,
    itemStyle: { color: AXIS.paper, borderColor: SERIES[1], borderWidth: 3 }, tooltip: { show: false },
  })
  return option
})
</script>

<template>
  <GlassPanel :title="t('eval.title')">
    <div class="stats">
      <div v-for="s in stats" :key="s.key" class="stat" :class="{ main: s.main }">
        <span class="k">{{ t(`eval.${s.key}`) }}</span>
        <span class="v mono">{{ s.value }}</span>
        <span v-if="s.sub" class="sub">{{ s.sub }}</span>
      </div>
    </div>
    <p class="method">
      {{ t('eval.method', { tool: ev.tool, align: ev.align === 'se3' ? 'SE(3)' : ev.align, poses: ev.matched_poses }) }}
    </p>

    <h4 class="chart-title">{{ t('eval.apeChart') }}</h4>
    <EvalChart v-if="apeOption" :option="apeOption" height="190px" />

    <h4 class="chart-title">{{ t('eval.topChart') }}</h4>
    <EvalChart v-if="topOption" :option="topOption" square />
  </GlassPanel>
</template>

<style scoped>
.stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(var(--paper-rgb), 0.5);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.stat.main {
  box-shadow: inset 0 0 0 1px rgba(var(--accent-rgb), 0.3), 0 6px 16px -10px rgba(var(--accent-rgb), 0.6);
}
.stat .k {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--ink-2);
}
.stat .v {
  font-size: 20px;
  font-weight: 600;
}
.stat.main .v {
  color: var(--accent);
}
.stat .sub {
  font-size: 11.5px;
  color: var(--ink-3);
}
.method {
  margin: 10px 2px 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--ink-3);
}
.chart-title {
  margin: 16px 2px 4px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-2);
}
</style>
