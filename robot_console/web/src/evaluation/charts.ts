// Chart building blocks shared by the playback parts of the evaluation page (PlaybackDetail, PlaybackCompare).
import type { EChartsCoreOption } from 'echarts/core'
import { AXIS, MONO } from './colors'

export const axisCommon = {
  axisLine: { lineStyle: { color: AXIS.line } },
  axisTick: { show: false },
  axisLabel: { color: AXIS.label, fontFamily: MONO, fontSize: 11 },
  splitLine: { lineStyle: { color: AXIS.split } },
  nameTextStyle: { color: AXIS.label, fontSize: 11 },
}

export const tooltipCommon = {
  backgroundColor: 'rgba(255,255,255,0.96)',
  borderColor: AXIS.line,
  textStyle: { color: '#0b1526', fontSize: 12 },
}

export interface XYSeries {
  name: string
  points: [number, number][]
  color: string
  dashed?: boolean
  /** draw start (circle) and end (square) markers */
  ends?: boolean
}

export const zipXY = (x: number[], y: number[]) => x.map((v, i) => [v, y[i]] as [number, number])

/** Top view with equal spans on both axes inside a square plot area, so paths are not stretched */
export function topView(series: XYSeries[], labels: { start: string; end: string }): EChartsCoreOption | null {
  const pts = series.flatMap((s) => s.points)
  if (!pts.length) return null
  const xs = pts.map((p) => p[0])
  const ys = pts.map((p) => p[1])
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
  const half = (Math.max(x1 - x0, y1 - y0) / 2) * 1.08 + 0.2
  const cx = (x0 + x1) / 2
  const cy = (y0 + y1) / 2
  const fmt = (v: number) => v.toFixed(Math.abs(half) > 20 ? 0 : 1)
  const out: Record<string, unknown>[] = []
  for (const s of series) {
    out.push({
      name: s.name, type: 'line', data: s.points, showSymbol: false,
      lineStyle: { color: s.color, width: 2, type: s.dashed ? 'dashed' : 'solid' }, itemStyle: { color: s.color },
    })
    if (s.ends && s.points.length) {
      out.push({ name: labels.start, type: 'line', data: [s.points[0]], symbol: 'circle', symbolSize: 9,
        itemStyle: { color: '#fff', borderColor: s.color, borderWidth: 2 }, tooltip: { show: false } })
      out.push({ name: labels.end, type: 'line', data: [s.points[s.points.length - 1]], symbol: 'rect', symbolSize: 8,
        itemStyle: { color: s.color }, tooltip: { show: false } })
    }
  }
  return {
    animation: false,
    grid: { left: 52, right: 24, top: 40, bottom: 52 },
    legend: { top: 4, right: 8, type: 'scroll', itemWidth: 18, textStyle: { color: AXIS.ink, fontSize: 12 },
      data: series.map((s) => s.name) },
    tooltip: { ...tooltipCommon, trigger: 'none', axisPointer: { type: 'cross', label: { fontFamily: MONO, precision: 2 } } },
    xAxis: { type: 'value', name: 'x (m)', nameLocation: 'middle', nameGap: 28, min: cx - half, max: cx + half,
      ...axisCommon, axisLabel: { ...axisCommon.axisLabel, formatter: fmt, showMinLabel: false, showMaxLabel: false } },
    yAxis: { type: 'value', name: 'y (m)', nameLocation: 'middle', nameGap: 36, min: cy - half, max: cy + half,
      ...axisCommon, axisLabel: { ...axisCommon.axisLabel, formatter: fmt, showMinLabel: false, showMaxLabel: false } },
    series: out,
  }
}

/** Value over time (one or more lines); `zero` draws a dashed line at 0 */
export function timeSeries(
  series: { name: string; points: [number, number][]; color: string }[],
  axis: { x: string; y: string; unit: string },
  opts: { zero?: boolean; min?: number } = {},
): EChartsCoreOption | null {
  if (!series.some((s) => s.points.length)) return null
  return {
    animation: false,
    grid: { left: 52, right: 20, top: series.length > 1 ? 40 : 16, bottom: 44 },
    legend: series.length > 1
      ? { top: 4, left: 8, type: 'scroll', textStyle: { color: AXIS.ink, fontSize: 12, fontFamily: MONO } } : undefined,
    tooltip: { ...tooltipCommon, trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: AXIS.line } },
      valueFormatter: (v: number) => `${v.toFixed(2)} ${axis.unit}` },
    dataZoom: [{ type: 'inside' }],
    xAxis: { type: 'value', name: axis.x, nameLocation: 'middle', nameGap: 28, ...axisCommon,
      max: 'dataMax', splitLine: { show: false } },
    yAxis: { type: 'value', name: axis.y, nameLocation: 'middle', nameGap: 40, min: opts.min, ...axisCommon },
    series: series.map((s, i) => ({
      name: s.name, type: 'line', data: s.points, showSymbol: false,
      lineStyle: { color: s.color, width: 2 }, itemStyle: { color: s.color },
      ...(opts.zero && i === 0 ? { markLine: { silent: true, symbol: 'none', label: { show: false },
        lineStyle: { color: AXIS.label, type: 'dashed', width: 1 }, data: [{ yAxis: 0 }] } } : {}),
    })),
  }
}

/** "0–1125 s" for a bag part, or the 'whole' text when it is missing */
export function partText(range: [number, number] | null | undefined, whole: string) {
  if (!range) return whole
  const f = (v: number) => (Math.abs(v - Math.round(v)) < 0.05 ? String(Math.round(v)) : v.toFixed(1))
  return `${f(range[0])}–${f(range[1])} s`
}

/** Group key of a playback run: same bag and (to 0.1 s) the same part */
export function partKey(bag: string, range: [number, number] | null | undefined) {
  return `${bag}|${range ? range.map((v) => v.toFixed(1)).join('|') : ''}`
}
