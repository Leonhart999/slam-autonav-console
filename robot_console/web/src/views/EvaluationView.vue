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
import { openFolder } from '@/openFolder'
import type { MappingPreset } from '@/types'
import { evalApi, type ExportResult, type NavGoalRecord, type NavMetrics, type RunDetail, type RunEntry, type RunEval,
  type RunTrashEntry } from '@/evaluation/api'
import { evalMessages } from '@/evaluation/messages'
import { AXIS, MONO, REFERENCE, SERIES } from '@/evaluation/colors'
import GlassPanel from '@/components/GlassPanel.vue'
import KpiTile from '@/components/KpiTile.vue'
import EvalChart from '@/components/EvalChart.vue'
import BatchPanel from '@/components/BatchPanel.vue'
import TrashBin, { type TrashItem } from '@/components/TrashBin.vue'
import BatchCompare, { type BatchCompareItem } from '@/components/BatchCompare.vue'
import PlaybackDetail from '@/components/PlaybackDetail.vue'
import PlaybackCompare from '@/components/PlaybackCompare.vue'
import { partText } from '@/evaluation/charts'

const { t } = useI18n({ messages: evalMessages })
const store = useStatusStore()
const route = useRoute()
const router = useRouter()

// ---------------- List and filters ----------------
type TypeFilter = 'all' | 'mapping' | 'navigation' | 'playback'
const runs = ref<RunEntry[]>([])
const params = ref({ max_diff: 0.01, rpe_delta: 1.0 })
const loading = ref(false)
const listError = ref('')
const presets = ref<MappingPreset[]>([])
const typeFilter = ref<TypeFilter>(route.query.bag ? 'playback' : 'all')
// a scene preset name, or 'bag:<bag path>' for playback runs of one bag
const sceneFilter = ref(typeof route.query.bag === 'string' ? `bag:${route.query.bag}` : '')

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

const scenes = computed(() => [...new Set(runs.value.filter((r) => r.type !== 'playback')
  .map((r) => r.preset).filter((p): p is string => !!p))])
// bags of the playback runs: path -> name
const bags = computed(() => {
  const m = new Map<string, string>()
  for (const r of runs.value) if (r.playback?.bag) m.set(r.playback.bag, r.playback.bag_name)
  return [...m.entries()].map(([path, name]) => ({ path, name }))
})
const showScenes = computed(() => typeFilter.value !== 'playback')
const showBags = computed(() => typeFilter.value === 'all' || typeFilter.value === 'playback')
const sourcePlaceholder = computed(() => t(typeFilter.value === 'playback' ? 'evalPage.allBags'
  : typeFilter.value === 'all' && bags.value.length ? 'evalPage.allSources' : 'evalPage.allScenes'))
// a filter that the new type cannot match is dropped
watch(typeFilter, (k) => {
  const f = sceneFilter.value
  if (f && (f.startsWith('bag:') ? k !== 'all' && k !== 'playback' : k === 'playback')) sceneFilter.value = ''
})
function matchSource(r: RunEntry) {
  const f = sceneFilter.value
  if (!f) return true
  return f.startsWith('bag:') ? r.playback?.bag === f.slice(4) : r.type !== 'playback' && r.preset === f
}
const filtered = computed(() => runs.value.filter((r) =>
  (typeFilter.value === 'all' || r.type === typeFilter.value) && matchSource(r)))
function showBag(bag: string) {
  typeFilter.value = 'playback'
  sceneFilter.value = `bag:${bag}`
}

function sceneLabel(preset: string | null) {
  if (!preset) return '—'
  const p = presets.value.find((x) => x.name === preset)
  // An uploaded world that has since been deleted: show its name without the internal prefix
  return p ? pick(p.label) : preset.replace(/^custom_/, '')
}
/** Scene of a simulation run, or "bag · part" of a playback run */
function sourceLabel(r: RunEntry) {
  if (r.type === 'playback' && r.playback) return `${r.playback.bag_name} · ${partText(r.playback.range, t('evalPage.pb.whole'))}`
  return sceneLabel(r.preset)
}
const m3 = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(3))

const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`)
const f1 = (v: number | null | undefined, unit = '') => (v == null ? '—' : `${v.toFixed(1)}${unit}`)

type StateKey = 'recording' | 'running' | 'evaluated' | 'notEvaluated' | 'failed' | 'noGt' | 'navPending'
  | 'noGtBag' | 'hasRef' | 'suspect'
const STATE_CHIP: Record<StateKey, string> = {
  recording: 'primary live', running: 'primary live', evaluated: 'success', notEvaluated: 'info', failed: 'danger',
  noGt: 'warning', navPending: 'info', noGtBag: 'info', hasRef: 'info', suspect: 'warning',
}
function stateOf(r: RunEntry): StateKey {
  if (r.type === 'navigation') return r.recording ? 'running' : r.evaluable ? 'evaluated' : 'navPending'
  if (r.recording) return 'recording'
  const pb = r.playback
  if (pb && pb.gt_kind !== 'gt') {
    // no ground truth is normal for a real robot's bag: neutral chips, not a warning
    if (pb.gt_suspect) return 'suspect'
    return pb.gt_kind === 'ref' ? (pb.ref_error ? 'failed' : 'hasRef') : 'noGtBag'
  }
  if (!r.evaluable) return 'noGt'
  if (r.eval_error) return 'failed'
  return r.ape_rmse != null ? 'evaluated' : 'notEvaluated'
}
/** Playback with reference odometry but no ground truth: evo runs against the reference instead */
const refOnly = (r: RunEntry | null | undefined) => !!r?.playback && r.playback.gt_kind === 'ref'

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
    const { info: _info, eval: _eval, ref_eval: _refEval, amcl: _amcl, ...entry } = d
    runs.value.splice(i, 1, entry)
  }
}

/** Load a run's details; evaluates automatically when that is cheap and meaningful (finished, has GT or a
 *  playback reference, not failed) */
async function ensure(id: string, mode: 'auto' | 'evaluate' | 'force' = 'auto') {
  if (!id || busy.value.has(id)) return
  const entry = runs.value.find((r) => r.id === id)
  const loaded = details.value[id]
  const withRef = refOnly(entry) && !entry?.playback?.ref_error
  if (mode === 'auto' && loaded && (loaded.eval || (withRef ? loaded.ref_eval : !entry?.evaluable)
    || (loaded.type === 'navigation' && !entry?.recording))) return
  setBusy(id, true)
  try {
    const auto = (entry?.evaluable || withRef) && !entry?.recording && !entry?.eval_error
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
  else if (refOnly(r)) ensure(r.id, r.playback?.ref_ape_rmse != null || r.playback?.ref_error ? 'force' : 'evaluate')
  else ensure(r.id, r.ape_rmse != null || r.eval_error ? 'force' : 'evaluate')
}
const canEvaluate = (r: RunEntry) => r.evaluable || refOnly(r)

// ---------------- Navigation runs ----------------
const currentNav = computed(() => (currentEntry.value?.type === 'navigation' ? currentEntry.value.nav : null))
// How the shown numbers were computed: in the panel title's "i" instead of a paragraph under the charts
const methodTip = computed(() => {
  if (currentNav.value) return t('evalPage.nav.method')
  const e = currentEval.value
  return e ? t('evalPage.method', { evo: e.evo, diff: e.params.max_diff, d: e.params.rpe_delta }) : undefined
})
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
const compareCount = computed(() => compareRows.value.length + navCompareRows.value.length + batchItems.value.length
  + playbackRows.value.filter((r) => r.ape_rmse == null).length)
// Ticked playback runs (PlaybackCompare); ones with ground truth are also in the ATE comparison above it
const playbackRows = computed(() => selection.value.map((id) => runs.value.find((r) => r.id === id))
  .filter((r): r is RunEntry => !!r?.playback))

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
const trashLoading = ref(false)
const trashBusy = ref('')          // entry being restored / deleted ('*' = emptying)
// Batch records share <runs_dir>/.trash; they have their own recycle bin in the batch panel
const trashItems = computed<TrashItem[]>(() => trashEntries.value.filter((e) => e.type !== 'batch').map((e) => ({
  id: e.entry, name: e.run ?? e.entry, deleted: e.deleted, size: e.size, restorable: e.restorable,
  detail: e.type === 'group' ? t('evalPage.trash.group', { n: e.runs }) : t(`evalPage.type.${e.type}`),
})))
async function loadTrash() {
  trashLoading.value = true
  try {
    trashEntries.value = (await evalApi.trashList()).entries
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    trashLoading.value = false
  }
}
async function trashAct(action: 'restore' | 'purge' | 'empty', entry = '') {
  trashBusy.value = entry || '*'
  try {
    if (action === 'restore') {
      const r = await evalApi.trashRestore(entry)
      ElMessage.success(t('evalPage.trash.restored', { id: r.restored }))
      await loadList()
    } else if (action === 'purge') {
      await evalApi.trashPurge(entry)
      ElMessage.success(t('evalPage.trash.purged'))
    } else {
      await evalApi.trashEmpty('runs')
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
          <el-radio-button value="playback">{{ t('evalPage.type.playback') }}</el-radio-button>
        </el-radio-group>
        <el-select v-model="sceneFilter" size="small" class="scene-select" :placeholder="sourcePlaceholder" clearable>
          <template v-if="showScenes && showBags && bags.length">
            <el-option-group :label="t('evalPage.groupScenes')">
              <el-option v-for="s in scenes" :key="s" :value="s" :label="sceneLabel(s)" />
            </el-option-group>
            <el-option-group :label="t('evalPage.groupBags')">
              <el-option v-for="b in bags" :key="b.path" :value="`bag:${b.path}`" :label="b.name" :title="b.path" />
            </el-option-group>
          </template>
          <template v-else-if="showBags">
            <el-option v-for="b in bags" :key="b.path" :value="`bag:${b.path}`" :label="b.name" :title="b.path" />
          </template>
          <template v-else>
            <el-option v-for="s in scenes" :key="s" :value="s" :label="sceneLabel(s)" />
          </template>
        </el-select>
        <span class="count">{{ t('evalPage.count', { n: filtered.length }) }}</span>
        <el-button size="small" circle :title="t('openFolder.runs')" :aria-label="t('openFolder.runs')" @click="openFolder(store.paths.runs)">
          <el-icon><FolderOpened /></el-icon>
        </el-button>
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
        <el-table-column :label="t('evalPage.col.id')" min-width="150">
          <template #default="{ row }">
            <span class="swatch" :style="{ background: row.id in slots ? colorOf(row.id) : 'transparent' }" />
            <span class="name mono" :title="row.id">{{ row.id }}</span>
            <span v-if="row.algorithm" class="algo">{{ row.algorithm }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('evalPage.col.started')" width="152">
          <template #default="{ row }"><span class="num">{{ row.started ?? '—' }}</span></template>
        </el-table-column>
        <el-table-column :label="t('evalPage.col.type')" width="84">
          <template #default="{ row }">{{ t(`evalPage.type.${row.type}`) }}</template>
        </el-table-column>
        <el-table-column :label="typeFilter === 'playback' ? t('evalPage.col.bag') : t('evalPage.col.scene')" min-width="86" show-overflow-tooltip>
          <template #default="{ row }">{{ sourceLabel(row) }}</template>
        </el-table-column>
        <el-table-column :label="t('evalPage.col.duration')" width="82" align="right">
          <template #default="{ row }"><span class="num">{{ fmtDuration(row.duration) }}</span></template>
        </el-table-column>
        <el-table-column :label="t('evalPage.col.length')" width="85" align="right">
          <template #default="{ row }"><span class="num">{{ row.length == null ? '—' : `${row.length.toFixed(1)} m` }}</span></template>
        </el-table-column>
        <!-- "All": one column with the main number of each type; the type filters show the full columns -->
        <el-table-column v-if="typeFilter === 'all'" :label="t('evalPage.col.metric')" width="150" align="right">
          <template #default="{ row }">
            <span v-if="row.type === 'navigation' && row.nav" class="num">
              <b class="strong">{{ row.nav.succeeded }}/{{ row.nav.finished }}</b>
              <span class="dim"> · AMCL {{ m3(row.nav.amcl_rmse_m) }}</span>
            </span>
            <span v-else-if="row.ape_rmse != null" class="num"><span class="dim">ATE </span><b class="strong">{{ m3(row.ape_rmse) }}</b></span>
            <span v-else-if="row.playback?.stats" class="num" :title="t('evalPage.col.startEnd')">
              <span class="dim">{{ row.playback.loop ? t('evalPage.col.loopShort') : t('evalPage.col.startEndShort') }} </span><b class="strong">{{ m3(row.playback.stats.start_end) }}</b>
            </span>
            <span v-else class="num">—</span>
          </template>
        </el-table-column>
        <el-table-column v-if="typeFilter === 'mapping'" :label="t('evalPage.col.ate')" width="100" align="right">
          <template #default="{ row }"><span class="num strong">{{ m3(row.ape_rmse) }}</span></template>
        </el-table-column>
        <el-table-column v-if="typeFilter === 'mapping'" :label="t('evalPage.col.rpe')" width="100" align="right">
          <template #default="{ row }"><span class="num">{{ m3(row.rpe_rmse) }}</span></template>
        </el-table-column>
        <!-- Playback: numbers that need no ground truth, plus ATE or the deviation from the bag's reference -->
        <el-table-column v-if="typeFilter === 'playback'" :label="t('evalPage.col.startEnd')" width="100" align="right">
          <template #default="{ row }">
            <span class="num strong">{{ m3(row.playback?.stats?.start_end) }}</span><span v-if="row.playback?.loop" class="dim"> {{ t('evalPage.col.loopShort') }}</span>
          </template>
        </el-table-column>
        <el-table-column v-if="typeFilter === 'playback'" :label="t('evalPage.col.zEnd')" width="90" align="right">
          <template #default="{ row }"><span class="num">{{ m3(row.playback?.stats?.z_end) }}</span></template>
        </el-table-column>
        <el-table-column v-if="typeFilter === 'playback'" :label="t('evalPage.col.versus')" width="118" align="right">
          <template #default="{ row }">
            <span v-if="row.ape_rmse != null" class="num"><span class="dim">ATE </span>{{ m3(row.ape_rmse) }}</span>
            <span v-else-if="row.playback?.ref_ape_rmse != null" class="num"><span class="dim">{{ t('evalPage.pb.chart.ref') }} </span>{{ m3(row.playback.ref_ape_rmse) }}</span>
            <span v-else class="num">—</span>
          </template>
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
        <el-table-column :label="t('evalPage.col.state')" min-width="132">
          <template #default="{ row }">
            <!-- The normal case is plain text; only states that need attention get a colored chip -->
            <span v-if="stateOf(row) === 'evaluated'" class="state-ok">{{ t('evalPage.state.evaluated') }}</span>
            <span v-else class="chip" :class="STATE_CHIP[stateOf(row)]">{{ t(`evalPage.state.${stateOf(row)}`) }}</span>
          </template>
        </el-table-column>
      </el-table>
      <div class="list-foot">
        <span class="trash-open">
          <TrashBin
            v-model="trashOpen" :title="t('evalPage.trash.title')" :entries="trashItems" :loading="trashLoading"
            :busy="trashBusy" :dirs="[`${store.paths.runs}/.trash`]" @open="loadTrash"
            @restore="trashAct('restore', $event)" @purge="trashAct('purge', $event)" @empty="trashAct('empty')"
          />
        </span>
        <span class="count">{{ t('evalPage.selected', { n: selection.length }) }}</span>
        <el-button size="small" type="danger" plain :disabled="!selection.length" :loading="deleting"
          :title="t('evalPage.trash.deleteHint')" @click="deleteSelected">
          <el-icon><Delete /></el-icon><span>{{ t('evalPage.trash.delete') }}</span>
        </el-button>
        <el-button size="small" type="primary" :disabled="!selection.length" :loading="exporting"
          :title="t('evalPage.exportHint')" @click="exportSelected">
          <el-icon><Download /></el-icon><span>{{ t('evalPage.export') }}</span>
        </el-button>
        <el-button
          v-if="store.paths.results" size="small" circle :title="t('openFolder.results')" :aria-label="t('openFolder.results')"
          @click="openFolder(store.paths.results)"
        >
          <el-icon><FolderOpened /></el-icon>
        </el-button>
      </div>
      <div v-if="exportResult" class="export-result">
        <span>{{ t('evalPage.exported', { n: exportResult.files.length }) }}</span>
        <code class="mono">{{ exportResult.dir }}</code>
        <el-button size="small" link type="primary" @click="openFolder(exportResult.dir)">{{ t('openFolder.button') }}</el-button>
        <el-button size="small" link type="primary" @click="copyPath(exportResult.dir)">{{ t('evalPage.copy') }}</el-button>
        <span v-if="exportResult.skipped.length" class="warn">{{ t('evalPage.exportSkipped', { n: exportResult.skipped.length }) }}</span>
        <el-button size="small" link class="close" @click="exportResult = null">×</el-button>
      </div>
    </GlassPanel>

    <!-- Focused run -->
    <GlassPanel :title="t('evalPage.detail')" :tip="methodTip">
      <template v-if="currentEntry" #extra>
        <el-button size="small" @click="openFolder(`${store.paths.runs}/${currentEntry.id}`)">
          <el-icon><FolderOpened /></el-icon><span>{{ t('openFolder.button') }}</span>
        </el-button>
        <el-button v-if="currentEntry.type === 'navigation'" size="small" :loading="busy.has(currentEntry.id)" @click="evaluateCurrent">
          {{ t('evalPage.refresh') }}
        </el-button>
        <el-button v-else-if="canEvaluate(currentEntry)" size="small" :loading="busy.has(currentEntry.id)" @click="evaluateCurrent">
          {{ currentEntry.ape_rmse != null || currentEntry.eval_error || currentEntry.playback?.ref_ape_rmse != null
            ? t('evalPage.reevaluate') : t('evalPage.evaluate') }}
        </el-button>
      </template>
      <p v-if="!currentEntry" class="hint">{{ t('evalPage.pickHint') }}</p>
      <template v-else>
        <div class="title-row">
          <h2 class="mono">{{ currentEntry.id }}</h2>
          <span class="chip" :class="STATE_CHIP[stateOf(currentEntry)]">{{ t(`evalPage.state.${stateOf(currentEntry)}`) }}</span>
          <span class="chip primary">{{ t(`evalPage.type.${currentEntry.type}`) }} · {{ sourceLabel(currentEntry) }}</span>
          <span v-if="currentEntry.playback?.rate != null" class="chip info">{{ currentEntry.playback.rate }}x</span>
          <span v-if="currentEntry.algorithm" class="chip info">{{ currentEntry.algorithm }}</span>
          <span v-if="currentEntry.maps.length" class="maps">
            {{ t('evalPage.col.maps') }}:
            <router-link v-for="m in currentEntry.maps" :key="m" class="mono" :to="{ path: '/maps', query: { map: m } }">{{ m }}</router-link>
          </span>
        </div>
        <p v-if="currentEntry.recording && currentEntry.type !== 'navigation'" class="note">{{ t('evalPage.recordingNote') }}</p>
        <p v-if="currentEntry.type === 'navigation'">
          <span v-if="currentEntry.recording" class="note">{{ t('evalPage.navNote') }}</span>
          <span v-if="currentNav && !currentNav.has_gt" class="note warn">{{ t('evalPage.nav.noGt') }}</span>
        </p>
        <p v-else-if="!currentEntry.evaluable && currentEntry.type === 'mapping'" class="note warn">{{ t('evalPage.noGtNote') }}</p>
        <p v-if="currentEntry.eval_error" class="note bad">{{ t('evalPage.failedNote', { reason: currentEntry.eval_error }) }}</p>
        <p v-else-if="errors[currentEntry.id]" class="note bad">{{ errors[currentEntry.id] }}</p>
        <p v-if="busy.has(currentEntry.id) && !currentEval && canEvaluate(currentEntry)" class="hint">{{ t('evalPage.evaluating') }}</p>

        <!-- Playback run: source, numbers without ground truth, links to the Playback page -->
        <PlaybackDetail v-if="currentEntry.playback" :entry="currentEntry" :detail="current"
          @filter-bag="showBag" @changed="absorb" />

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
        </template>
      </template>
    </GlassPanel>

    <!-- Batch mapping experiments (batch.py); its ticks feed the comparison below -->
    <BatchPanel :selected-runs="selection" :run-color="colorOf" @finished="loadList" @focus="(id: string) => (focused = id)"
      @pick-run="pickRun" @batches="(b: BatchCompareItem[]) => (batchItems = b)" />

    <!-- Comparison of the ticked runs and batches -->
    <GlassPanel :title="compareCount ? `${t('evalPage.compare')} · ${compareCount}` : t('evalPage.compare')">
      <p v-if="compareRows.length < 2 && navCompareRows.length < 2 && batchItems.length < 2 && !playbackRows.length" class="hint">{{ t('evalPage.compareHint') }}</p>
      <BatchCompare v-if="batchItems.length >= 2" :items="batchItems" />
      <template v-if="compareRows.length >= 2">
        <h4 v-if="batchItems.length >= 2" class="sub-title">{{ t('evalPage.compareMapRuns') }}</h4>
        <el-table :data="compareRows" size="small" :row-key="(r: { id: string }) => r.id">
          <el-table-column :label="t('evalPage.col.id')" min-width="190">
            <template #default="{ row }">
              <span class="swatch" :style="{ background: colorOf(row.id) }" />
              <span class="name mono" :title="row.id">{{ row.id }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('evalPage.col.scene')" min-width="110" show-overflow-tooltip>
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
              <span class="name mono" :title="row.id">{{ row.id }}</span>
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

      <PlaybackCompare v-if="playbackRows.length" :entries="playbackRows" :color-of="colorOf" />
    </GlassPanel>

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
  width: 150px;
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
/* Run ids (map_<time>_<hash>) stay on one line; the full id is in the title */
td .name {
  display: inline-block;
  max-width: calc(100% - 16px);
  vertical-align: middle;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.algo {
  display: block;
  margin-left: 16px;
  font-size: 11px;
  line-height: 1.3;
  color: var(--ink-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
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
.state-ok {
  font-size: 12px;
  color: var(--ink-3);
}
.state-ok::before {
  content: '✓ ';
  color: var(--ok);
  font-weight: 700;
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
