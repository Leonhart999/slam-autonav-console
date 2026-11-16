import { computed, type Ref } from 'vue'
import type { EChartsCoreOption } from 'echarts/core'
import { AXIS, MONO, REFERENCE, SERIES } from './colors'
import { axisCommon, tooltipCommon } from './charts'
import type { RunDetail, RunEntry, RunEval, NavMetrics } from './api'

export function useEvaluationCharts(currentEval: Ref<RunEval | null>, current: Ref<RunDetail | null>,
  currentNav: Ref<NavMetrics | null>, compareRows: Ref<{ id: string; entry: RunEntry; ev: RunEval }[]>,
  navCompareRows: Ref<{ id: string; entry: RunEntry & { nav: NavMetrics } }[]>,
  details: Ref<Record<string, RunDetail>>, colorOf: (id: string) => string,
  labels: Ref<{ gt: string; est: string; time: string; error: string; rmse: (value: string) => string }>) {
// ---------------- Charts ----------------

function zip(s: [number[], number[]]) {
  return s[0].map((x, i) => [x, s[1][i]])
}

const trajOption = computed<EChartsCoreOption | null>(() => {
  const ev = currentEval.value
  if (!ev) return null
  const pts = [...ev.series.traj_gt, ...ev.series.traj_est]
  const xs = pts.map((p) => p[0])
  const ys = pts.map((p) => p[1])
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
  // Equal spans on both axes inside a square plot area, so the path is not stretched
  const half = (Math.max(x1 - x0, y1 - y0) / 2) * 1.08 + 0.2
  const cx = (x0 + x1) / 2
  const cy = (y0 + y1) / 2
  const fmt = (v: number) => v.toFixed(1)
  return {
    animation: false,
    grid: { left: 52, right: 24, top: 40, bottom: 52 },
    legend: { top: 4, right: 8, itemWidth: 18, textStyle: { color: AXIS.ink, fontSize: 12 } },
    tooltip: { ...tooltipCommon, trigger: 'none', axisPointer: { type: 'cross', label: { fontFamily: MONO, precision: 2 } } },
    xAxis: { type: 'value', name: 'x (m)', nameLocation: 'middle', nameGap: 28, min: cx - half, max: cx + half,
      ...axisCommon, axisLabel: { ...axisCommon.axisLabel, formatter: fmt, showMinLabel: false, showMaxLabel: false } },
    yAxis: { type: 'value', name: 'y (m)', nameLocation: 'middle', nameGap: 36, min: cy - half, max: cy + half,
      ...axisCommon, axisLabel: { ...axisCommon.axisLabel, formatter: fmt, showMinLabel: false, showMaxLabel: false } },
    series: [
      { name: labels.value.gt, type: 'line', data: ev.series.traj_gt, showSymbol: false,
        lineStyle: { color: REFERENCE, width: 2, type: 'dashed' }, itemStyle: { color: REFERENCE } },
      { name: labels.value.est, type: 'line', data: ev.series.traj_est, showSymbol: false,
        lineStyle: { color: SERIES[0], width: 2 }, itemStyle: { color: SERIES[0] } },
    ],
  }
})

function errorOption(series: [number[], number[]], rmse: number, color: string): EChartsCoreOption {
  return {
    animation: false,
    grid: { left: 52, right: 20, top: 16, bottom: 44 },
    tooltip: {
      ...tooltipCommon, trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: AXIS.line } },
      valueFormatter: (v: number) => `${v.toFixed(3)} m`,
    },
    dataZoom: [{ type: 'inside' }],
    xAxis: { type: 'value', name: labels.value.time, nameLocation: 'middle', nameGap: 28, ...axisCommon,
      max: 'dataMax', splitLine: { show: false } },
    yAxis: { type: 'value', name: labels.value.error, nameLocation: 'middle', nameGap: 40, min: 0, ...axisCommon },
    series: [{
      name: 'error', type: 'line', data: zip(series), showSymbol: false,
      lineStyle: { color, width: 2 }, itemStyle: { color },
      markLine: {
        silent: true, symbol: 'none', lineStyle: { color: REFERENCE, type: 'dashed', width: 1 },
        label: { formatter: labels.value.rmse(rmse.toFixed(3)), color: AXIS.ink, position: 'insideEndTop' },
        data: [{ yAxis: rmse }],
      },
    }],
  }
}
const apeOption = computed(() => currentEval.value
  ? errorOption(currentEval.value.series.ape, currentEval.value.ape.rmse, SERIES[0]) : null)
const rpeOption = computed(() => {
  const ev = currentEval.value
  return ev?.series.rpe && ev.rpe ? errorOption(ev.series.rpe, ev.rpe.rmse, SERIES[0]) : null
})

const compareOption = computed<EChartsCoreOption | null>(() => {
  const rows = compareRows.value.slice(0, SERIES.length)
  if (rows.length < 2) return null
  return {
    animation: false,
    grid: { left: 52, right: 20, top: 40, bottom: 44 },
    legend: { top: 4, left: 8, type: 'scroll', textStyle: { color: AXIS.ink, fontSize: 12, fontFamily: MONO } },
    tooltip: { ...tooltipCommon, trigger: 'axis', valueFormatter: (v: number) => `${v.toFixed(3)} m` },
    dataZoom: [{ type: 'inside' }],
    xAxis: { type: 'value', name: labels.value.time, nameLocation: 'middle', nameGap: 28, ...axisCommon,
      max: 'dataMax', splitLine: { show: false } },
    yAxis: { type: 'value', name: labels.value.error, nameLocation: 'middle', nameGap: 40, min: 0, ...axisCommon },
    series: rows.map((r) => ({
      name: r.id, type: 'line', data: zip(r.ev.series.ape), showSymbol: false,
      lineStyle: { color: colorOf(r.id), width: 2 }, itemStyle: { color: colorOf(r.id) },
    })),
  }
})

const amclOption = computed<EChartsCoreOption | null>(() => {
  const a = current.value?.amcl
  const m = currentNav.value
  if (!a || !a.t.length) return null
  const option = errorOption([a.t, a.error], m?.amcl_rmse_m ?? 0, SERIES[0]) as Record<string, any>
  option.grid.top = 30           // room for the goal numbers above the plot
  const line = option.series[0]
  line.markLine.data = [
    ...(m?.amcl_rmse_m != null ? [{ yAxis: m.amcl_rmse_m }] : []),
    ...a.goals.map(([k, t0]) => ({
      xAxis: t0, label: { formatter: String(k), position: 'end', color: AXIS.label },
      lineStyle: { color: AXIS.line, type: 'dotted', width: 1 },
    })),
  ]
  return option
})

const navCompareOption = computed<EChartsCoreOption | null>(() => {
  const rows = navCompareRows.value.slice(0, SERIES.length)
    .map((r) => ({ id: r.id, variant: r.entry.nav.variant, a: details.value[r.id]?.amcl }))
    .filter((r) => r.a && r.a.t.length)
  if (rows.length < 2) return null
  return {
    animation: false,
    grid: { left: 52, right: 20, top: 40, bottom: 44 },
    legend: { top: 4, left: 8, type: 'scroll', textStyle: { color: AXIS.ink, fontSize: 12, fontFamily: MONO } },
    tooltip: { ...tooltipCommon, trigger: 'axis', valueFormatter: (v: number) => `${v.toFixed(3)} m` },
    dataZoom: [{ type: 'inside' }],
    xAxis: { type: 'value', name: labels.value.time, nameLocation: 'middle', nameGap: 28, ...axisCommon,
      max: 'dataMax', splitLine: { show: false } },
    yAxis: { type: 'value', name: labels.value.error, nameLocation: 'middle', nameGap: 40, min: 0, ...axisCommon },
    series: rows.map((r) => ({
      name: `${r.id} · ${r.variant ?? '—'}`, type: 'line', data: zip([r.a!.t, r.a!.error]), showSymbol: false,
      lineStyle: { color: colorOf(r.id), width: 2 }, itemStyle: { color: colorOf(r.id) },
    })),
  }
})

return { trajOption, apeOption, rpeOption, compareOption, amclOption, navCompareOption }
}
