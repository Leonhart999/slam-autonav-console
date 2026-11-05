<!-- Evaluation page (step 5a): run records, evo APE/RPE of mapping runs, comparison, CSV/PNG export. -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Download, Refresh } from '@element-plus/icons-vue'
import type { EChartsCoreOption } from 'echarts/core'
import { api } from '@/api'
import { fmtDuration } from '@/format'
import { pick } from '@/i18n'
import { errorText } from '@/messages'
import { useStatusStore } from '@/stores/status'
import type { MappingPreset } from '@/types'
import { evalApi, type ExportResult, type NavGoalRecord, type NavMetrics, type RunDetail, type RunEntry, type RunEval,
  type RunTrashEntry } from '@/evaluation/api'
import { fmtBytes } from '@/maps/api'
import { evalMessages } from '@/evaluation/messages'
import { AXIS, MONO, REFERENCE, SERIES } from '@/evaluation/colors'
import GlassPanel from '@/components/GlassPanel.vue'
import KpiTile from '@/components/KpiTile.vue'
import EvalChart from '@/components/EvalChart.vue'
import BatchPanel from '@/components/BatchPanel.vue'
import BatchCompare, { type BatchCompareItem } from '@/components/BatchCompare.vue'

const { t } = useI18n({ messages: evalMessages })
const store = useStatusStore()
const route = useRoute()
const router = useRouter()

// ---------------- List and filters ----------------
type TypeFilter = 'all' | 'mapping' | 'navigation'
const runs = ref<RunEntry[]>([])
const params = ref({ max_diff: 0.01, rpe_delta: 1.0 })
const loading = ref(false)
const listError = ref('')
const presets = ref<MappingPreset[]>([])
const typeFilter = ref<TypeFilter>('all')
const sceneFilter = ref('')

async function loadList() {
  loading.value = true
  try {
    const r = await evalApi.runs()
    runs.value = r.runs
    params.value = r.params
    listError.value = ''
  } catch (e) {
    listError.value = errorText(e)
  } finally {
    loading.value = false
  }
  try {
    presets.value = await api.mappingPresets()
  } catch {
    // Scene labels fall back to the raw preset name
  }
}
onMounted(loadList)
watch(() => store.connected, (c) => { if (c) loadList() })

const scenes = computed(() => [...new Set(runs.value.map((r) => r.preset).filter((p): p is string => !!p))])
const filtered = computed(() => runs.value.filter((r) =>
  (typeFilter.value === 'all' || r.type === typeFilter.value) && (!sceneFilter.value || r.preset === sceneFilter.value)))

function sceneLabel(preset: string | null) {
  if (!preset) return '—'
  const p = presets.value.find((x) => x.name === preset)
  return p ? pick(p.label) : preset
}
const m3 = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(3))

const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`)
const f1 = (v: number | null | undefined, unit = '') => (v == null ? '—' : `${v.toFixed(1)}${unit}`)

type StateKey = 'recording' | 'running' | 'evaluated' | 'notEvaluated' | 'failed' | 'noGt' | 'navPending'
const STATE_CHIP: Record<StateKey, string> = {
  recording: 'primary live', running: 'primary live', evaluated: 'success', notEvaluated: 'info', failed: 'danger',
  noGt: 'warning', navPending: 'info',
}
function stateOf(r: RunEntry): StateKey {
  if (r.type === 'navigation') return r.recording ? 'running' : r.evaluable ? 'evaluated' : 'navPending'
  if (r.recording) return 'recording'
  if (!r.evaluable) return 'noGt'
  if (r.eval_error) return 'failed'
  return r.ape_rmse != null ? 'evaluated' : 'notEvaluated'
}

// ---------------- Details (cached per run) ----------------
const details = ref<Record<string, RunDetail>>({})
const errors = ref<Record<string, string>>({})
const busy = ref<Set<string>>(new Set())
const focused = ref(typeof route.query.run === 'string' ? route.query.run : '')

function setBusy(id: string, on: boolean) {
  const s = new Set(busy.value)
  if (on) s.add(id)
  else s.delete(id)
  busy.value = s
}

function absorb(d: RunDetail) {
  details.value = { ...details.value, [d.id]: d }
  const i = runs.value.findIndex((r) => r.id === d.id)
  if (i >= 0) {
    const { info: _info, eval: _eval, amcl: _amcl, ...entry } = d
    runs.value.splice(i, 1, entry)
  }
}

/** Load a run's details; evaluates automatically when that is cheap and meaningful (finished, has GT, not failed) */
async function ensure(id: string, mode: 'auto' | 'evaluate' | 'force' = 'auto') {
  if (!id || busy.value.has(id)) return
  const entry = runs.value.find((r) => r.id === id)
  const loaded = details.value[id]
  if (mode === 'auto' && loaded && (loaded.eval || !entry?.evaluable || (loaded.type === 'navigation' && !entry?.recording))) return
  setBusy(id, true)
  try {
    const auto = entry?.evaluable && !entry.recording && !entry.eval_error
    const d = mode === 'force' ? await evalApi.evaluate(id, true)
      : mode === 'evaluate' || auto ? await evalApi.evaluate(id) : await evalApi.detail(id)
    absorb(d)
    const { [id]: _, ...rest } = errors.value
    errors.value = rest
  } catch (e) {
    errors.value = { ...errors.value, [id]: errorText(e) }
    try {
      absorb(await evalApi.detail(id))   // Still show the run's info (and the cached failure)
    } catch {
      // The run itself is gone; the list refresh will drop it
    }
  } finally {
    setBusy(id, false)
  }
}

watch(focused, (id) => {
  if (route.query.run !== id) router.replace({ query: { ...route.query, run: id || undefined } })
  ensure(id)
})
watch(runs, () => { if (focused.value && !details.value[focused.value]) ensure(focused.value) })

const current = computed(() => (focused.value ? details.value[focused.value] ?? null : null))
const currentEntry = computed(() => runs.value.find((r) => r.id === focused.value) ?? null)
const currentEval = computed<RunEval | null>(() => current.value?.eval ?? null)

function evaluateCurrent() {
  const r = currentEntry.value
  if (!r) return
  if (r.type === 'navigation') ensure(r.id, 'evaluate')     // just reloads the record
  else ensure(r.id, r.ape_rmse != null || r.eval_error ? 'force' : 'evaluate')
}

// ---------------- Navigation runs ----------------
const currentNav = computed(() => (currentEntry.value?.type === 'navigation' ? currentEntry.value.nav : null))
const currentGoals = computed<NavGoalRecord[]>(() => current.value?.info.goals ?? [])
const RESULT_CHIP: Record<string, string> = {
  succeeded: 'success', aborted: 'danger', timeout: 'warning', canceled: 'info', rejected: 'danger',
}
const resultKey = (r: string | null) =>
  r && ['succeeded', 'aborted', 'canceled', 'rejected', 'timeout'].includes(r) ? r : 'none'

// ---------------- Selection: comparison and export ----------------
const selection = ref<string[]>([])
const slots = ref<Record<string, number>>({})   // run id -> color slot, kept while the run stays selected

function onSelection(rows: RunEntry[]) {
  const ids = rows.map((r) => r.id)
  const keep: Record<string, number> = {}
  for (const id of ids) if (id in slots.value) keep[id] = slots.value[id]
  for (const id of ids) {
    if (id in keep) continue
    const used = new Set(Object.values(keep))
    let k = 0
    while (used.has(k)) k++
    keep[id] = k
  }
  slots.value = keep
  selection.value = ids
  for (const id of ids) ensure(id)
}
const colorOf = (id: string) => SERIES[(slots.value[id] ?? 0) % SERIES.length]

// Ticks from the batch panel go through the run table, so there is one selection (also when the row is filtered out)
const runTable = ref<{ toggleRowSelection: (row: RunEntry, selected?: boolean) => void } | null>(null)
function pickRun(id: string, on: boolean) {
  const row = runs.value.find((r) => r.id === id)
  if (row?.evaluable && selection.value.includes(id) !== on) runTable.value?.toggleRowSelection(row, on)
}
const batchItems = ref<BatchCompareItem[]>([])
const compareCount = computed(() => compareRows.value.length + navCompareRows.value.length + batchItems.value.length)

const compareRows = computed(() => selection.value
  .map((id) => ({ id, entry: runs.value.find((r) => r.id === id), ev: details.value[id]?.eval ?? null }))
  .filter((r): r is { id: string; entry: RunEntry; ev: RunEval } => !!r.ev && !!r.entry))

const navCompareRows = computed(() => selection.value
  .map((id) => ({ id, entry: runs.value.find((r) => r.id === id) }))
  .filter((r): r is { id: string; entry: RunEntry & { nav: NavMetrics } } => !!r.entry?.nav))

/** Pooled per variant, same definitions as nav_eval.by_variant (means weighted by goal counts) */
const variantRows = computed(() => {
  const groups = new Map<string, NavMetrics[]>()
  for (const r of navCompareRows.value) {
    const k = r.entry.nav.variant ?? '—'
    groups.set(k, [...(groups.get(k) ?? []), r.entry.nav])
  }
  const wmean = (ms: NavMetrics[], key: 'mean_time_s' | 'mean_path_m' | 'mean_goal_error_m', w: 'succeeded') => {
    const pairs = ms.filter((m) => m[key] != null && m[w] > 0)
    const n = pairs.reduce((a, m) => a + m[w], 0)
    return n ? pairs.reduce((a, m) => a + (m[key] as number) * m[w], 0) / n : null
  }
  return [...groups.entries()].map(([variant, ms]) => {
    const finished = ms.reduce((a, m) => a + m.finished, 0)
    const ok = ms.reduce((a, m) => a + m.succeeded, 0)
    const withAmcl = ms.filter((m) => m.amcl_rmse_m != null && m.amcl_samples)
    const n = withAmcl.reduce((a, m) => a + (m.amcl_samples as number), 0)
    const locs = ms.filter((m) => m.mean_loc_error_m != null && m.finished > 0)
    const nl = locs.reduce((a, m) => a + m.finished, 0)
    return {
      variant, runs: ms.length, goals: finished, succeeded: ok,
      success_rate: finished ? ok / finished : null,
      mean_time_s: wmean(ms, 'mean_time_s', 'succeeded'),
      mean_path_m: wmean(ms, 'mean_path_m', 'succeeded'),
      mean_goal_error_m: wmean(ms, 'mean_goal_error_m', 'succeeded'),
      mean_loc_error_m: nl ? locs.reduce((a, m) => a + (m.mean_loc_error_m as number) * m.finished, 0) / nl : null,
      amcl_rmse_m: n ? Math.sqrt(withAmcl.reduce((a, m) => a + (m.amcl_samples as number) * (m.amcl_rmse_m as number) ** 2, 0) / n) : null,
      recoveries: ms.reduce((a, m) => a + m.recoveries, 0),
    }
  }).sort((a, b) => a.variant.localeCompare(b.variant))
})

const exporting = ref(false)
const exportResult = ref<ExportResult | null>(null)
async function exportSelected() {
  exporting.value = true
  try {
    exportResult.value = await evalApi.exportRuns(selection.value)
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    exporting.value = false
  }
}
// ---------------- Delete (to <runs_dir>/.trash) and the trash ----------------
const deleting = ref(false)
async function deleteSelected() {
  const ids = [...selection.value]
  if (!ids.length) return
  try {
    await ElMessageBox.confirm(t('evalPage.trash.confirm', { n: ids.length }), t('evalPage.trash.confirmTitle'), {
      type: 'warning', confirmButtonText: t('evalPage.trash.ok'), cancelButtonText: t('evalPage.trash.cancel'),
    })
  } catch {
    return
  }
  deleting.value = true
  try {
    const r = await evalApi.trashRuns(ids)
    for (const id of r.moved) {
      const row = runs.value.find((x) => x.id === id)
      if (row) runTable.value?.toggleRowSelection(row, false)
      if (focused.value === id) focused.value = ''
      const { [id]: _, ...rest } = details.value
      details.value = rest
    }
    if (r.moved.length) ElMessage.success(t('evalPage.trash.moved', { n: r.moved.length }))
    if (r.refused.length) {
      const lines = r.refused.map((x) => t(`evalPage.trash.refuse.${x.reason}`,
        { id: x.id, maps: (x.maps ?? []).join(', '), detail: x.detail ?? '' }))
      ElMessageBox.alert(lines.join('\n'), t('evalPage.trash.refusedTitle', { n: r.refused.length }),
        { type: 'warning', customClass: 'trash-refused' })
    }
    await loadList()
    if (trashOpen.value) loadTrash()
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    deleting.value = false
  }
}

const trashOpen = ref(false)
const trashEntries = ref<RunTrashEntry[]>([])
const trashSize = ref(0)
const trashLoading = ref(false)
const trashBusy = ref('')          // entry being restored / deleted ('*' = emptying)
async function loadTrash() {
  trashLoading.value = true
  try {
    const r = await evalApi.trashList()
    trashEntries.value = r.entries
    trashSize.value = r.size
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    trashLoading.value = false
  }
}
function openTrash() {
  trashOpen.value = true
  loadTrash()
}
const trashName = (e: RunTrashEntry) => e.run ?? e.entry
async function trashAct(e: RunTrashEntry | null, action: 'restore' | 'purge' | 'empty') {
  if (action !== 'restore') {
    const msg = action === 'empty'
      ? t('evalPage.trash.emptyConfirm', { n: trashEntries.value.length, size: fmtBytes(trashSize.value) })
      : t('evalPage.trash.purgeConfirm', { name: e ? trashName(e) : '' })
    try {
      await ElMessageBox.confirm(msg, t('evalPage.trash.title'), {
        type: 'warning', confirmButtonText: action === 'empty' ? t('evalPage.trash.emptyAll') : t('evalPage.trash.purge'),
        cancelButtonText: t('evalPage.trash.cancel'), confirmButtonClass: 'el-button--danger',
      })
    } catch {
      return
    }
  }
  trashBusy.value = e?.entry ?? '*'
  try {
    if (action === 'restore' && e) {
      const r = await evalApi.trashRestore(e.entry)
      ElMessage.success(t('evalPage.trash.restored', { id: r.restored }))
      await loadList()
    } else if (action === 'purge' && e) {
      await evalApi.trashPurge(e.entry)
      ElMessage.success(t('evalPage.trash.purged'))
    } else {
      await evalApi.trashEmpty()
      ElMessage.success(t('evalPage.trash.emptied'))
    }
  } catch (err) {
    ElMessage.error(errorText(err))
  } finally {
    trashBusy.value = ''
    loadTrash()
  }
}

async function copyPath(path: string) {
  try {
    await navigator.clipboard.writeText(path)
    ElMessage.success(t('evalPage.copied'))
  } catch {
    ElMessage.warning(path)
  }
}

// ---------------- Charts ----------------
const axisCommon = {
  axisLine: { lineStyle: { color: AXIS.line } },
  axisTick: { show: false },
  axisLabel: { color: AXIS.label, fontFamily: MONO, fontSize: 11 },
  splitLine: { lineStyle: { color: AXIS.split } },
  nameTextStyle: { color: AXIS.label, fontSize: 11 },
}
const tooltipCommon = {
  backgroundColor: 'rgba(255,255,255,0.96)',
  borderColor: AXIS.line,
  textStyle: { color: '#0b1526', fontSize: 12 },
}

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
      { name: t('evalPage.chart.gt'), type: 'line', data: ev.series.traj_gt, showSymbol: false,
        lineStyle: { color: REFERENCE, width: 2, type: 'dashed' }, itemStyle: { color: REFERENCE } },
      { name: t('evalPage.chart.est'), type: 'line', data: ev.series.traj_est, showSymbol: false,
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
    xAxis: { type: 'value', name: t('evalPage.chart.time'), nameLocation: 'middle', nameGap: 28, ...axisCommon,
      max: 'dataMax', splitLine: { show: false } },
    yAxis: { type: 'value', name: t('evalPage.chart.error'), nameLocation: 'middle', nameGap: 40, min: 0, ...axisCommon },
    series: [{
      name: 'error', type: 'line', data: zip(series), showSymbol: false,
      lineStyle: { color, width: 2 }, itemStyle: { color },
      markLine: {
        silent: true, symbol: 'none', lineStyle: { color: REFERENCE, type: 'dashed', width: 1 },
        label: { formatter: t('evalPage.chart.rmse', { v: rmse.toFixed(3) }), color: AXIS.ink, position: 'insideEndTop' },
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
    xAxis: { type: 'value', name: t('evalPage.chart.time'), nameLocation: 'middle', nameGap: 28, ...axisCommon,
      max: 'dataMax', splitLine: { show: false } },
    yAxis: { type: 'value', name: t('evalPage.chart.error'), nameLocation: 'middle', nameGap: 40, min: 0, ...axisCommon },
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
    xAxis: { type: 'value', name: t('evalPage.chart.time'), nameLocation: 'middle', nameGap: 28, ...axisCommon,
      max: 'dataMax', splitLine: { show: false } },
    yAxis: { type: 'value', name: t('evalPage.chart.error'), nameLocation: 'middle', nameGap: 40, min: 0, ...axisCommon },
    series: rows.map((r) => ({
      name: `${r.id} · ${r.variant ?? '—'}`, type: 'line', data: zip([r.a!.t, r.a!.error]), showSymbol: false,
      lineStyle: { color: colorOf(r.id), width: 2 }, itemStyle: { color: colorOf(r.id) },
    })),
  }
})
</script>

<template>
  <div class="eval-page">
    <!-- Run list -->
    <GlassPanel :title="t('evalPage.list')" flush>
      <template #extra>
        <el-radio-group v-model="typeFilter" size="small">
          <el-radio-button value="all">{{ t('evalPage.type.all') }}</el-radio-button>
          <el-radio-button value="mapping">{{ t('evalPage.type.mapping') }}</el-radio-button>
          <el-radio-button value="navigation">{{ t('evalPage.type.navigation') }}</el-radio-button>
        </el-radio-group>
        <el-select v-model="sceneFilter" size="small" class="scene-select" :placeholder="t('evalPage.allScenes')" clearable>
          <el-option v-for="s in scenes" :key="s" :value="s" :label="sceneLabel(s)" />
        </el-select>
        <span class="count">{{ t('evalPage.count', { n: filtered.length }) }}</span>
        <el-button size="small" circle :title="t('evalPage.refresh')" :loading="loading" @click="loadList">
          <el-icon><Refresh /></el-icon>
        </el-button>
      </template>
      <p v-if="listError" class="warn pad">{{ listError }}</p>
      <el-table
        ref="runTable"
        :data="filtered"
        size="small"
        max-height="340"
        :row-key="(r: RunEntry) => r.id"
        :row-class-name="({ row }: { row: RunEntry }) => (row.id === focused ? 'is-selected' : '')"
        :empty-text="loading ? t('evalPage.loading') : runs.length ? t('evalPage.emptyFiltered') : t('evalPage.empty')"
        @row-click="(r: RunEntry) => (focused = r.id)"
        @selection-change="onSelection"
      >
        <el-table-column type="selection" width="40" reserve-selection />
        <el-table-column :label="t('evalPage.col.id')" min-width="170">
          <template #default="{ row }">
            <span class="swatch" :style="{ background: row.id in slots ? colorOf(row.id) : 'transparent' }" />
            <span class="name mono">{{ row.id }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('evalPage.col.started')" width="152">
          <template #default="{ row }"><span class="num">{{ row.started ?? '—' }}</span></template>
        </el-table-column>
        <el-table-column :label="t('evalPage.col.type')" width="84">
          <template #default="{ row }">{{ t(`evalPage.type.${row.type}`) }}</template>
        </el-table-column>
        <el-table-column :label="t('evalPage.col.scene')" min-width="100" show-overflow-tooltip>
          <template #default="{ row }">{{ sceneLabel(row.preset) }}</template>
        </el-table-column>
        <el-table-column :label="t('evalPage.col.duration')" width="82" align="right">
          <template #default="{ row }"><span class="num">{{ fmtDuration(row.duration) }}</span></template>
        </el-table-column>
        <el-table-column :label="t('evalPage.col.length')" width="85" align="right">
          <template #default="{ row }"><span class="num">{{ row.length == null ? '—' : `${row.length.toFixed(1)} m` }}</span></template>
        </el-table-column>
        <!-- "All": one column with the main number of each type; the type filters show the full columns -->
        <el-table-column v-if="typeFilter === 'all'" :label="t('evalPage.col.metric')" width="130" align="right">
          <template #default="{ row }">
            <span v-if="row.type === 'navigation' && row.nav" class="num">
              <b class="strong">{{ row.nav.succeeded }}/{{ row.nav.finished }}</b>
              <span class="dim"> · AMCL {{ m3(row.nav.amcl_rmse_m) }}</span>
            </span>
            <span v-else-if="row.ape_rmse != null" class="num"><span class="dim">ATE </span><b class="strong">{{ m3(row.ape_rmse) }}</b></span>
            <span v-else class="num">—</span>
          </template>
        </el-table-column>
        <el-table-column v-if="typeFilter === 'mapping'" :label="t('evalPage.col.ate')" width="100" align="right">
          <template #default="{ row }"><span class="num strong">{{ m3(row.ape_rmse) }}</span></template>
        </el-table-column>
        <el-table-column v-if="typeFilter === 'mapping'" :label="t('evalPage.col.rpe')" width="100" align="right">
          <template #default="{ row }"><span class="num">{{ m3(row.rpe_rmse) }}</span></template>
        </el-table-column>
        <el-table-column v-if="typeFilter === 'navigation'" :label="t('evalPage.nav.variant')" width="110">
          <template #default="{ row }"><span class="mono">{{ row.variant ?? '—' }}</span></template>
        </el-table-column>
        <el-table-column v-if="typeFilter === 'navigation'" :label="t('evalPage.nav.success')" width="90" align="right">
          <template #default="{ row }">
            <span class="num strong">{{ row.nav ? `${row.nav.succeeded}/${row.nav.finished}` : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column v-if="typeFilter === 'navigation'" :label="t('evalPage.nav.amcl')" width="100" align="right">
          <template #default="{ row }"><span class="num">{{ m3(row.nav?.amcl_rmse_m) }}</span></template>
        </el-table-column>
        <el-table-column :label="t('evalPage.col.state')" min-width="118">
          <template #default="{ row }">
            <span class="chip" :class="STATE_CHIP[stateOf(row)]">{{ t(`evalPage.state.${stateOf(row)}`) }}</span>
          </template>
        </el-table-column>
      </el-table>
      <div class="list-foot">
        <el-button size="small" class="trash-open" @click="openTrash">{{ t('evalPage.trash.open') }}</el-button>
        <span class="count">{{ t('evalPage.selected', { n: selection.length }) }}</span>
        <el-button size="small" type="danger" plain :disabled="!selection.length" :loading="deleting"
          :title="t('evalPage.trash.deleteHint')" @click="deleteSelected">
          <el-icon><Delete /></el-icon><span>{{ t('evalPage.trash.delete') }}</span>
        </el-button>
        <el-button size="small" type="primary" :disabled="!selection.length" :loading="exporting"
          :title="t('evalPage.exportHint')" @click="exportSelected">
          <el-icon><Download /></el-icon><span>{{ t('evalPage.export') }}</span>
        </el-button>
      </div>
      <div v-if="exportResult" class="export-result">
        <span>{{ t('evalPage.exported', { n: exportResult.files.length }) }}</span>
        <code class="mono">{{ exportResult.dir }}</code>
        <el-button size="small" link type="primary" @click="copyPath(exportResult.dir)">{{ t('evalPage.copy') }}</el-button>
        <span v-if="exportResult.skipped.length" class="warn">{{ t('evalPage.exportSkipped', { n: exportResult.skipped.length }) }}</span>
        <el-button size="small" link class="close" @click="exportResult = null">×</el-button>
      </div>
    </GlassPanel>

    <!-- Focused run -->
    <GlassPanel :title="t('evalPage.detail')">
      <template v-if="currentEntry" #extra>
        <el-button v-if="currentEntry.type === 'navigation'" size="small" :loading="busy.has(currentEntry.id)" @click="evaluateCurrent">
          {{ t('evalPage.refresh') }}
        </el-button>
        <el-button v-else-if="currentEntry.evaluable" size="small" :loading="busy.has(currentEntry.id)" @click="evaluateCurrent">
          {{ currentEntry.ape_rmse != null || currentEntry.eval_error ? t('evalPage.reevaluate') : t('evalPage.evaluate') }}
        </el-button>
      </template>
      <p v-if="!currentEntry" class="hint">{{ t('evalPage.pickHint') }}</p>
      <template v-else>
        <div class="title-row">
          <h2 class="mono">{{ currentEntry.id }}</h2>
          <span class="chip" :class="STATE_CHIP[stateOf(currentEntry)]">{{ t(`evalPage.state.${stateOf(currentEntry)}`) }}</span>
          <span class="chip primary">{{ t(`evalPage.type.${currentEntry.type}`) }} · {{ sceneLabel(currentEntry.preset) }}</span>
          <span v-if="currentEntry.maps.length" class="maps">
            {{ t('evalPage.col.maps') }}:
            <router-link v-for="m in currentEntry.maps" :key="m" class="mono" :to="{ path: '/maps', query: { map: m } }">{{ m }}</router-link>
          </span>
        </div>
        <p v-if="currentEntry.recording && currentEntry.type === 'mapping'" class="note">{{ t('evalPage.recordingNote') }}</p>
        <p v-if="currentEntry.type === 'navigation'">
          <span v-if="currentEntry.recording" class="note">{{ t('evalPage.navNote') }}</span>
          <span v-if="currentNav && !currentNav.has_gt" class="note warn">{{ t('evalPage.nav.noGt') }}</span>
        </p>
        <p v-else-if="!currentEntry.evaluable" class="note warn">{{ t('evalPage.noGtNote') }}</p>
        <p v-if="currentEntry.eval_error" class="note bad">{{ t('evalPage.failedNote', { reason: currentEntry.eval_error }) }}</p>
        <p v-else-if="errors[currentEntry.id]" class="note bad">{{ errors[currentEntry.id] }}</p>
        <p v-if="busy.has(currentEntry.id) && !currentEval && currentEntry.type === 'mapping'" class="hint">{{ t('evalPage.evaluating') }}</p>

        <!-- Navigation run -->
        <template v-if="currentNav">
          <div class="kpis">
            <KpiTile :label="t('evalPage.nav.kpi.success')" :value="currentNav.succeeded" :total="currentNav.finished"
              :tone="currentNav.finished && currentNav.succeeded === currentNav.finished ? 'ok' : 'warn'"
              :sub="t('evalPage.nav.kpi.successSub', { ok: currentNav.succeeded, n: currentNav.goals })" />
            <KpiTile :label="t('evalPage.nav.kpi.time')" :value="Number((currentNav.mean_time_s ?? 0).toFixed(1))"
              :tone="currentNav.mean_time_s == null ? 'idle' : 'accent'"
              :sub="t('evalPage.nav.kpi.timeSub', { p: f1(currentNav.mean_path_m) })" />
            <KpiTile v-if="currentNav.amcl_rmse_m != null" :label="t('evalPage.nav.kpi.amcl')"
              :value="Number(currentNav.amcl_rmse_m.toFixed(3))"
              :sub="t('evalPage.nav.kpi.amclSub', { mean: m3(currentNav.amcl_mean_m), max: m3(currentNav.amcl_max_m), n: currentNav.amcl_samples ?? 0 })" />
            <KpiTile v-else :label="t('evalPage.nav.kpi.amcl')" :value="0" tone="idle" :sub="t('evalPage.nav.kpi.amclNone')" />
          </div>
          <h4 class="sub-title">{{ t('evalPage.nav.goalsTitle') }}</h4>
          <el-table :data="currentGoals" size="small" :row-key="(g: NavGoalRecord) => g.index">
            <el-table-column :label="t('evalPage.nav.goal')" width="60" align="right">
              <template #default="{ row }"><span class="num">{{ row.index }}</span></template>
            </el-table-column>
            <el-table-column label="x, y (m)" width="120" align="right">
              <template #default="{ row }"><span class="num">{{ row.x.toFixed(2) }}, {{ row.y.toFixed(2) }}</span></template>
            </el-table-column>
            <el-table-column :label="t('evalPage.nav.result')" width="100">
              <template #default="{ row }">
                <span class="chip" :class="RESULT_CHIP[row.result ?? ''] ?? 'info'">{{ t(`evalPage.nav.results.${resultKey(row.result)}`) }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="t('evalPage.nav.time')" width="80" align="right">
              <template #default="{ row }"><span class="num">{{ f1(row.time_s, ' s') }}</span></template>
            </el-table-column>
            <el-table-column :label="t('evalPage.nav.path')" width="85" align="right">
              <template #default="{ row }"><span class="num">{{ f1(row.path_m, ' m') }}</span></template>
            </el-table-column>
            <el-table-column :label="t('evalPage.nav.goalErr')" width="95" align="right">
              <template #default="{ row }"><span class="num">{{ m3(row.goal_error_m) }}</span></template>
            </el-table-column>
            <el-table-column :label="t('evalPage.nav.yawErr')" width="90" align="right">
              <template #default="{ row }"><span class="num">{{ f1(row.goal_yaw_error_deg, '°') }}</span></template>
            </el-table-column>
            <el-table-column :label="t('evalPage.nav.locErr')" min-width="110" align="right">
              <template #default="{ row }"><span class="num">{{ m3(row.loc_error_m) }}</span></template>
            </el-table-column>
            <el-table-column :label="t('evalPage.nav.recoveries')" width="90" align="right">
              <template #default="{ row }"><span class="num">{{ row.recoveries ?? '—' }}</span></template>
            </el-table-column>
            <el-table-column :label="t('evalPage.nav.clearance')" width="95" align="right">
              <template #default="{ row }"><span class="num">{{ m3(row.min_clearance_m) }}</span></template>
            </el-table-column>
          </el-table>
          <div class="charts">
            <figure>
              <figcaption>{{ t('evalPage.nav.pathFig') }}</figcaption>
              <img v-if="currentEntry.figures.includes('path.png')" class="fig"
                :src="evalApi.figureUrl(currentEntry.id, 'path.png')" alt="path.png" />
              <p v-else class="hint">{{ t('evalPage.nav.noFigure') }}</p>
            </figure>
            <figure>
              <figcaption>{{ t('evalPage.nav.amclChart') }}</figcaption>
              <EvalChart v-if="amclOption" :option="amclOption" height="300px" />
              <p v-else class="hint">{{ t('evalPage.nav.kpi.amclNone') }}</p>
            </figure>
          </div>
          <p class="method">
            {{ t('evalPage.nav.variant') }}: <b class="mono">{{ currentNav.variant ?? '—' }}</b> ·
            {{ t('evalPage.nav.recoveries') }} {{ currentNav.recoveries }} ·
            {{ t('evalPage.nav.clearance') }} {{ m3(currentNav.min_clearance_m) }} m ·
            {{ t('evalPage.nav.locErr') }} {{ m3(currentNav.mean_loc_error_m) }} m
          </p>
          <p class="method">{{ t('evalPage.nav.method') }}</p>
        </template>

        <template v-if="currentEval">
          <div class="kpis">
            <KpiTile :label="t('evalPage.kpi.ate')" :value="Number(currentEval.ape.rmse.toFixed(3))"
              :sub="t('evalPage.kpi.ateSub', { mean: currentEval.ape.mean.toFixed(3), max: currentEval.ape.max.toFixed(3) })" />
            <KpiTile v-if="currentEval.rpe" :label="t('evalPage.kpi.rpe')" :value="Number(currentEval.rpe.rmse.toFixed(3))"
              :sub="t('evalPage.kpi.rpeSub', { d: currentEval.params.rpe_delta, n: currentEval.rpe.pairs })" />
            <KpiTile v-else :label="t('evalPage.kpi.rpe')" :value="0" tone="idle"
              :sub="t('evalPage.kpi.rpeNone', { d: currentEval.params.rpe_delta })" />
            <KpiTile :label="t('evalPage.kpi.poses')" :value="currentEval.matched_poses"
              :sub="t('evalPage.kpi.posesSub', { t: fmtDuration(currentEval.duration), l: currentEval.length.toFixed(1) })" />
          </div>
          <div class="charts">
            <figure>
              <figcaption>{{ t('evalPage.chart.traj') }}</figcaption>
              <EvalChart v-if="trajOption" :option="trajOption" square />
            </figure>
            <div class="stack">
              <figure>
                <figcaption>{{ t('evalPage.chart.ape') }}</figcaption>
                <EvalChart v-if="apeOption" :option="apeOption" height="210px" />
              </figure>
              <figure v-if="rpeOption">
                <figcaption>{{ t('evalPage.chart.rpe', { d: currentEval.params.rpe_delta }) }}</figcaption>
                <EvalChart :option="rpeOption" height="210px" />
              </figure>
            </div>
          </div>
          <p class="method">{{ t('evalPage.method', { evo: currentEval.evo, diff: currentEval.params.max_diff, d: currentEval.params.rpe_delta }) }}</p>
        </template>
      </template>
    </GlassPanel>

    <!-- Batch mapping experiments (batch.py); its ticks feed the comparison below -->
    <BatchPanel :selected-runs="selection" :run-color="colorOf" @finished="loadList" @focus="(id: string) => (focused = id)"
      @pick-run="pickRun" @batches="(b: BatchCompareItem[]) => (batchItems = b)" />

    <!-- Comparison of the ticked runs and batches -->
    <GlassPanel :title="compareCount ? `${t('evalPage.compare')} · ${compareCount}` : t('evalPage.compare')">
      <p v-if="compareRows.length < 2 && navCompareRows.length < 2 && batchItems.length < 2" class="hint">{{ t('evalPage.compareHint') }}</p>
      <BatchCompare v-if="batchItems.length >= 2" :items="batchItems" />
      <template v-if="compareRows.length >= 2">
        <h4 v-if="batchItems.length >= 2" class="sub-title">{{ t('evalPage.compareMapRuns') }}</h4>
        <el-table :data="compareRows" size="small" :row-key="(r: { id: string }) => r.id">
          <el-table-column :label="t('evalPage.col.id')" min-width="190">
            <template #default="{ row }">
              <span class="swatch" :style="{ background: colorOf(row.id) }" />
              <span class="name mono">{{ row.id }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('evalPage.col.scene')" width="110">
            <template #default="{ row }">{{ sceneLabel(row.entry.preset) }}</template>
          </el-table-column>
          <el-table-column :label="t('evalPage.col.ate')" width="100" align="right">
            <template #default="{ row }"><span class="num strong">{{ m3(row.ev.ape.rmse) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.col.ateMean')" width="100" align="right">
            <template #default="{ row }"><span class="num">{{ m3(row.ev.ape.mean) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.col.ateMax')" width="100" align="right">
            <template #default="{ row }"><span class="num">{{ m3(row.ev.ape.max) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.col.rpe')" width="100" align="right">
            <template #default="{ row }"><span class="num">{{ m3(row.ev.rpe?.rmse) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.col.length')" width="95" align="right">
            <template #default="{ row }"><span class="num">{{ row.ev.length.toFixed(1) }} m</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.col.duration')" width="80" align="right">
            <template #default="{ row }"><span class="num">{{ fmtDuration(row.ev.duration) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.col.poses')" width="100" align="right">
            <template #default="{ row }"><span class="num">{{ row.ev.matched_poses }}</span></template>
          </el-table-column>
        </el-table>
        <figure class="compare-chart">
          <figcaption>{{ t('evalPage.compareApe') }}</figcaption>
          <EvalChart v-if="compareOption" :option="compareOption" height="260px" />
          <p v-if="compareRows.length > SERIES.length" class="hint">{{ t('evalPage.compareCap') }}</p>
        </figure>
      </template>

      <!-- Navigation runs: per run, then pooled per Nav2 parameter variant -->
      <template v-if="navCompareRows.length >= 2">
        <h4 class="sub-title">{{ t('evalPage.nav.compareRuns') }}</h4>
        <el-table :data="navCompareRows" size="small" :row-key="(r: { id: string }) => r.id">
          <el-table-column :label="t('evalPage.col.id')" min-width="190">
            <template #default="{ row }">
              <span class="swatch" :style="{ background: colorOf(row.id) }" />
              <span class="name mono">{{ row.id }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('evalPage.nav.variant')" width="110">
            <template #default="{ row }"><span class="mono">{{ row.entry.nav.variant ?? '—' }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.nav.map')" min-width="130">
            <template #default="{ row }"><span class="mono">{{ row.entry.nav.map ?? '—' }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.nav.success')" width="100" align="right">
            <template #default="{ row }">
              <span class="num strong">{{ pct(row.entry.nav.success_rate) }}</span>
              <span class="num dim"> {{ row.entry.nav.succeeded }}/{{ row.entry.nav.finished }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('evalPage.nav.meanTime')" width="90" align="right">
            <template #default="{ row }"><span class="num">{{ f1(row.entry.nav.mean_time_s, ' s') }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.nav.meanPath')" width="90" align="right">
            <template #default="{ row }"><span class="num">{{ f1(row.entry.nav.mean_path_m, ' m') }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.nav.goalErr')" width="95" align="right">
            <template #default="{ row }"><span class="num">{{ m3(row.entry.nav.mean_goal_error_m) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.nav.locErr')" width="120" align="right">
            <template #default="{ row }"><span class="num">{{ m3(row.entry.nav.mean_loc_error_m) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.nav.amcl')" width="100" align="right">
            <template #default="{ row }"><span class="num">{{ m3(row.entry.nav.amcl_rmse_m) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.nav.recoveries')" width="90" align="right">
            <template #default="{ row }"><span class="num">{{ row.entry.nav.recoveries }}</span></template>
          </el-table-column>
        </el-table>

        <h4 class="sub-title">{{ t('evalPage.nav.compareVariant') }}</h4>
        <el-table :data="variantRows" size="small" :row-key="(r: { variant: string }) => r.variant">
          <el-table-column :label="t('evalPage.nav.variant')" min-width="130">
            <template #default="{ row }"><span class="name mono">{{ row.variant }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.nav.runs')" width="80" align="right">
            <template #default="{ row }"><span class="num">{{ row.runs }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.nav.goals')" width="80" align="right">
            <template #default="{ row }"><span class="num">{{ row.goals }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.nav.success')" width="100" align="right">
            <template #default="{ row }"><span class="num strong">{{ pct(row.success_rate) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.nav.meanTime')" width="90" align="right">
            <template #default="{ row }"><span class="num">{{ f1(row.mean_time_s, ' s') }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.nav.meanPath')" width="90" align="right">
            <template #default="{ row }"><span class="num">{{ f1(row.mean_path_m, ' m') }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.nav.goalErr')" width="95" align="right">
            <template #default="{ row }"><span class="num">{{ m3(row.mean_goal_error_m) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.nav.locErr')" width="120" align="right">
            <template #default="{ row }"><span class="num">{{ m3(row.mean_loc_error_m) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.nav.amcl')" width="100" align="right">
            <template #default="{ row }"><span class="num strong">{{ m3(row.amcl_rmse_m) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.nav.recoveries')" width="90" align="right">
            <template #default="{ row }"><span class="num">{{ row.recoveries }}</span></template>
          </el-table-column>
        </el-table>
        <figure class="compare-chart">
          <figcaption>{{ t('evalPage.nav.compareAmcl') }}</figcaption>
          <EvalChart v-if="navCompareOption" :option="navCompareOption" height="260px" />
          <p v-if="navCompareRows.length > SERIES.length" class="hint">{{ t('evalPage.compareCap') }}</p>
        </figure>
      </template>
    </GlassPanel>

    <!-- Trash of run records (<runs_dir>/.trash) -->
    <el-dialog v-model="trashOpen" :title="t('evalPage.trash.title')" width="min(820px, 94vw)" append-to-body>
      <el-table v-loading="trashLoading" :data="trashEntries" size="small" max-height="420"
        :empty-text="t('evalPage.trash.empty')" :row-key="(e: RunTrashEntry) => e.entry">
        <el-table-column :label="t('evalPage.trash.col.run')" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="name mono">{{ trashName(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('evalPage.trash.col.type')" min-width="120" show-overflow-tooltip>
          <template #default="{ row }">
            {{ row.type === 'group' ? t('evalPage.trash.group', { n: row.runs }) : t(`evalPage.type.${row.type}`) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('evalPage.trash.col.deleted')" width="152">
          <template #default="{ row }"><span class="num">{{ row.deleted }}</span></template>
        </el-table-column>
        <el-table-column :label="t('evalPage.trash.col.size')" width="84" align="right">
          <template #default="{ row }"><span class="num">{{ fmtBytes(row.size) }}</span></template>
        </el-table-column>
        <el-table-column width="190" align="right">
          <template #default="{ row }">
            <el-button v-if="row.restorable" size="small" link type="primary" :disabled="!!trashBusy"
              :loading="trashBusy === row.entry" @click="trashAct(row, 'restore')">{{ t('evalPage.trash.restore') }}</el-button>
            <el-button size="small" link type="danger" :disabled="!!trashBusy" @click="trashAct(row, 'purge')">
              {{ t('evalPage.trash.purge') }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <div class="trash-foot">
          <span class="count">{{ t('evalPage.trash.total', { n: trashEntries.length, size: fmtBytes(trashSize) }) }}</span>
          <el-button size="small" type="danger" plain :disabled="!trashEntries.length || !!trashBusy"
            :loading="trashBusy === '*'" @click="trashAct(null, 'empty')">{{ t('evalPage.trash.emptyAll') }}</el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.eval-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 100%;
}
.scene-select {
  width: 130px;
}
.count {
  margin: 0 4px;
  font-size: 12px;
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
}
.pad {
  padding: 0 16px;
}
.name {
  font-weight: 600;
  color: var(--ink);
}
.num {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
.strong {
  font-weight: 600;
  color: var(--ink);
}
.swatch {
  display: inline-block;
  width: 10px;
  height: 3px;
  margin-right: 6px;
  border-radius: 2px;
  vertical-align: middle;
}
:deep(.el-table__row) {
  cursor: pointer;
}
:deep(.el-table__row.is-selected > td) {
  background: rgba(8, 145, 178, 0.08) !important;
}
.list-foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 10px 2px;
}
.list-foot .trash-open {
  margin-right: auto;
}
.trash-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.list-foot .el-icon {
  margin-right: 6px;
}
.export-result {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 8px 10px 0;
  padding: 8px 12px;
  border-radius: 10px;
  font-size: 12.5px;
  background: color-mix(in srgb, var(--ok) 8%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--ok) 22%, transparent);
}
.export-result code {
  font-size: 12px;
  word-break: break-all;
}
.export-result .close {
  margin-left: auto;
}
.hint {
  margin: 4px 0;
  font-size: 13px;
  color: var(--ink-3);
}
.title-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.title-row h2 {
  margin: 0 6px 0 0;
  font-size: 17px;
  font-weight: 650;
}
.maps {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 12.5px;
  color: var(--ink-2);
}
.maps a {
  color: var(--accent);
}
.note {
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--ink-2);
}
.warn {
  color: var(--warn);
}
.bad {
  color: var(--bad);
}
.kpis {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
}
.charts {
  display: grid;
  grid-template-columns: minmax(280px, 460px) minmax(0, 1fr);
  gap: 16px;
  margin-top: 16px;
}
.stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
figure {
  margin: 0;
  min-width: 0;
}
figcaption {
  margin-bottom: 4px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-2);
}
.method {
  margin: 12px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-3);
}
.compare-chart {
  margin-top: 14px;
}
.sub-title {
  margin: 16px 0 6px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-2);
}
.dim {
  color: var(--ink-3);
  font-size: 11.5px;
}
.fig {
  display: block;
  width: 100%;
  max-width: 560px;
  border-radius: 10px;
  background: #fff;
  box-shadow: inset 0 0 0 1px var(--hairline);
}
@media (max-width: 1100px) {
  .charts {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 720px) {
  .kpis {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
