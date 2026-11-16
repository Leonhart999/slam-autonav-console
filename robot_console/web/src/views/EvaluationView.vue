<!-- Evaluation page (step 5a): run records, evo APE/RPE of mapping runs, comparison, CSV/PNG export.
     Two views, chosen with the switch in the list header and kept in the browser. Side by side (default): the run
     list fills the left column; the right column shows one pane at a time (details of the clicked run, comparison
     of the ticked runs, batch experiments) and both columns scroll on their own. Stacked: the earlier page, every
     panel under the list. Below about 1100 px the columns stack and the page scrolls. -->
<script setup lang="ts">
import { FolderOpened } from '@element-plus/icons-vue'
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Download, Refresh } from '@element-plus/icons-vue'
import { api } from '@/api'
import { fmtDuration } from '@/format'
import { pick } from '@/i18n'
import { errorText } from '@/messages'
import { useStatusStore } from '@/stores/status'
import { openFolder } from '@/openFolder'
import type { MappingPreset } from '@/types'
import { evalApi, type ExportResult, type NavGoalRecord, type NavMetrics, type RunEntry, type RunEval,
  type RunTrashEntry } from '@/evaluation/api'
import { evalMessages } from '@/evaluation/messages'
import { SERIES } from '@/evaluation/colors'
import { useEvaluationCharts } from '@/evaluation/useEvaluationCharts'
import { useRunDetails } from '@/evaluation/useRunDetails'
import { useQuery } from '@/composables/useQuery'
import { taskMessages } from '@/taskMessages'
import { isCancelled } from '@/http'
import GlassPanel from '@/components/GlassPanel.vue'
import KpiTile from '@/components/KpiTile.vue'
import EvalChart from '@/components/EvalChart.vue'
import BatchPanel from '@/components/BatchPanel.vue'
import TrashBin, { type TrashItem } from '@/components/TrashBin.vue'
import BatchCompare, { type BatchCompareItem } from '@/components/BatchCompare.vue'
import PlaybackDetail from '@/components/PlaybackDetail.vue'
import PlaybackCompare from '@/components/PlaybackCompare.vue'
import { partText } from '@/evaluation/charts'

const { t } = useI18n({ messages: {
  'zh-CN': { ...evalMessages['zh-CN'], ...taskMessages['zh-CN'] },
  en: { ...evalMessages.en, ...taskMessages.en },
} })
const store = useStatusStore()
const route = useRoute()
const router = useRouter()

// ---------------- List and filters ----------------
type TypeFilter = 'all' | 'mapping' | 'navigation' | 'playback'
const runs = ref<RunEntry[]>([])
const params = ref({ max_diff: 0.01, rpe_delta: 1.0 })
const loading = computed(() => listQuery.loading.value)
const listError = computed(() => listQuery.error.value)
const presets = ref<MappingPreset[]>([])
const typeFilter = ref<TypeFilter>(route.query.bag ? 'playback' : 'all')
// a scene preset name, or 'bag:<bag path>' for playback runs of one bag
const sceneFilter = ref(typeof route.query.bag === 'string' ? `bag:${route.query.bag}` : '')

const listQuery = useQuery()
async function loadList() {
  await listQuery.run(signal => evalApi.runs(signal), r => {
    runs.value = r.runs
    params.value = r.params
  })
  try { presets.value = await api.mappingPresets() } catch { /* Fall back to the preset name. */ }
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
const focused = ref(typeof route.query.run === 'string' ? route.query.run : '')
const { details, errors, busy, ensure, absorb, recover, recoveredTask } = useRunDetails(runs)
watch([() => route.query.task, () => route.query.run], ([task, id]) => {
  if (typeof task === 'string' && typeof id === 'string') void recover(id, task)
}, { immediate: true })
watch(() => route.query.run, id => { if (typeof id === 'string') focused.value = id })

watch(focused, (id) => {
  if (route.query.run !== id) router.replace({ query: { ...route.query, run: id || undefined, task: undefined } })
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
const runTable = ref<{
  toggleRowSelection: (row: RunEntry, selected?: boolean) => void
  clearSelection: () => void
  doLayout: () => void
} | null>(null)
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

// ---------------- Two views of the page, chosen with the switch in the list header ----------------
// split: list on the left, one pane (details / comparison / batch) on the right, each scrolling on its own.
// stacked: the earlier page, every panel under the list with one table column per number.
const LAYOUT_KEY = 'robot_console.evaluation_layout'
function savedLayout() {
  try { return localStorage.getItem(LAYOUT_KEY) } catch { return null }   // storage may be disabled
}
const split = ref(savedLayout() !== 'stacked')
watch(split, (on) => {
  try { localStorage.setItem(LAYOUT_KEY, on ? 'split' : 'stacked') } catch { /* nothing is kept */ }
})
const evalPage = ref<HTMLElement | null>(null)
const paneBox = ref<HTMLElement | null>(null)
let layoutTransition: ViewTransition | null = null
let layoutFadeTimer = 0
function clearLayoutClasses() {
  const root = document.documentElement
  root.classList.remove('eval-vt', 'eval-vt-capturing')
  root.style.removeProperty('--eval-vt-clip')
}
async function setLayout(next: boolean) {
  if (next === split.value) return
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    split.value = next
    return
  }
  if (typeof document.startViewTransition !== 'function') {
    const el = evalPage.value
    el?.classList.add('layout-fade')
    split.value = next
    await nextTick()
    runTable.value?.doLayout()
    window.dispatchEvent(new Event('console:layout-resize'))
    window.clearTimeout(layoutFadeTimer)
    layoutFadeTimer = window.setTimeout(() => el?.classList.remove('layout-fade'), 260)
    return
  }
  layoutTransition?.skipTransition()
  const root = document.documentElement
  const content = evalPage.value?.closest('.content')?.getBoundingClientRect()
  if (content) {
    const t = Math.max(0, Math.round(content.top))
    const r = Math.max(0, Math.round(window.innerWidth - content.right))
    const b = Math.max(0, Math.round(window.innerHeight - content.bottom))
    const l = Math.max(0, Math.round(content.left))
    root.style.setProperty('--eval-vt-clip', `inset(${t}px ${r}px ${b}px ${l}px)`)
  }
  root.classList.add('eval-vt', 'eval-vt-capturing')
  const transition = document.startViewTransition(async () => {
    split.value = next
    if (paneBox.value) paneBox.value.scrollTop = 0
    await nextTick()
    runTable.value?.doLayout()
    window.dispatchEvent(new Event('console:layout-resize'))
  })
  layoutTransition = transition
  try {
    await transition.ready
    root.classList.remove('eval-vt-capturing')
    await transition.finished
  } catch { /* skipped or interrupted: layout is already switched */ } finally {
    if (layoutTransition === transition) {
      layoutTransition = null
      clearLayoutClasses()
    }
  }
}
onBeforeUnmount(() => {
  window.clearTimeout(layoutFadeTimer)
  layoutTransition?.skipTransition()
  layoutTransition = null
  clearLayoutClasses()
})
type Pane = 'detail' | 'compare' | 'batch'
const pane = ref<Pane>('detail')
const panes = computed<{ id: Pane; label: string; n: number }[]>(() => [
  { id: 'detail', label: t('evalPage.detail'), n: 0 },
  { id: 'compare', label: t('evalPage.compare'), n: compareCount.value },
  { id: 'batch', label: t('evalPage.tab.batch'), n: batchItems.value.length },
])
function focusRun(id: string) {
  focused.value = id
  pane.value = 'detail'
}
function onRowClick(r: RunEntry, column?: { type?: string } | null) {
  if (column?.type === 'selection') return   // a tick is not a request for the details
  focusRun(r.id)
}
// The second tick is a request to compare. Ticks made from the batch pane leave that pane open.
watch(() => selection.value.length, (n, before) => {
  if (n >= 2 && n > before && pane.value === 'detail') pane.value = 'compare'
})
const pickedRuns = computed(() => selection.value.map((id) => runs.value.find((r) => r.id === id))
  .filter((r): r is RunEntry => !!r))
const unpick = (r: RunEntry) => runTable.value?.toggleRowSelection(r, false)
/** 'YYYY-MM-DD HH:MM:SS' as 'MM-DD HH:MM' (the full time is in the cell's title) */
const shortTime = (started: string | null | undefined) => (started ? started.slice(5, 16) : '—')

const exporting = ref(false)
const exportResult = ref<ExportResult | null>(null)
const exportAbort = new AbortController()
onBeforeUnmount(() => exportAbort.abort())
async function exportSelected() {
  exporting.value = true
  try {
    exportResult.value = await evalApi.exportRuns(selection.value, exportAbort.signal)
  } catch (e) {
    if (!isCancelled(e)) ElMessage.error(errorText(e))
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

const { trajOption, apeOption, rpeOption, compareOption, amclOption, navCompareOption } =
  useEvaluationCharts(currentEval, current, currentNav, compareRows, navCompareRows, details, colorOf,
    computed(() => ({ gt: t('evalPage.chart.gt'), est: t('evalPage.chart.est'), time: t('evalPage.chart.time'),
      error: t('evalPage.chart.error'), rmse: (v: string) => t('evalPage.chart.rmse', { v }) })))
</script>

<template>
  <div ref="evalPage" class="eval-page" :class="{ stacked: !split }">
    <p v-if="recoveredTask && recoveredTask.target === focused" role="status" class="hint">
      {{ t('tasks.id') }}: {{ recoveredTask.id }} · {{ t(`tasks.phases.${recoveredTask.phase}`) }}
    </p>
    <div class="split">
      <!-- Run list -->
      <GlassPanel class="list-panel" :title="t('evalPage.list')" flush>
        <template #extra>
          <el-switch
            :model-value="split" size="small" class="layout-switch" :inactive-text="t('evalPage.layout.stacked')"
            :active-text="t('evalPage.layout.split')" :aria-label="t('evalPage.layout.label')"
            @update:model-value="setLayout(Boolean($event))"
          />
          <span class="count">{{ t('evalPage.count', { n: filtered.length }) }}</span>
          <el-button size="small" circle :title="t('openFolder.runs')" :aria-label="t('openFolder.runs')" @click="openFolder(store.paths.runs)">
            <el-icon><FolderOpened /></el-icon>
          </el-button>
          <el-button size="small" circle :title="t('evalPage.refresh')" :loading="loading" @click="loadList">
            <el-icon><Refresh /></el-icon>
          </el-button>
        </template>
        <div class="filters">
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
        </div>
        <p v-if="listError" class="warn pad">{{ listError }}</p>
        <!-- Two lines per row keep the list narrow: what the run is on the left, its main numbers on the right -->
        <div class="table-box">
          <div class="table-fill">
            <el-table
              ref="runTable"
              :data="filtered"
              size="small"
              height="100%"
              :row-key="(r: RunEntry) => r.id"
              :row-class-name="({ row }: { row: RunEntry }) => (row.id === focused ? 'is-selected' : '')"
              :empty-text="loading ? t('evalPage.loading') : runs.length ? t('evalPage.emptyFiltered') : t('evalPage.empty')"
              @row-click="onRowClick"
              @selection-change="onSelection"
            >
              <!-- @vue-generic {RunEntry} -->
              <el-table-column type="selection" width="36" reserve-selection />
              <!-- @vue-generic {RunEntry} -->
              <el-table-column :label="t('evalPage.col.id')" min-width="150">
                <template #default="{ row }">
                  <span class="swatch" :style="{ background: row.id in slots ? colorOf(row.id) : 'transparent' }" />
                  <span class="name mono" :title="row.id">{{ row.id }}</span>
                  <span v-if="!split" class="sub">{{ row.algorithm }}</span>
                  <span v-else class="sub" :title="[sourceLabel(row), row.algorithm ?? row.variant].filter(Boolean).join(' · ')">
                    <span class="type-tag">{{ t(`evalPage.type.${row.type}`) }}</span>{{ sourceLabel(row) }}<template v-if="row.algorithm ?? row.variant"> · {{ row.algorithm ?? row.variant }}</template>
                  </span>
                </template>
              </el-table-column>
              <!-- Stacked view: one column per number, the per-type columns follow the type filter -->
              <!-- @vue-generic {RunEntry} -->
              <el-table-column v-if="!split" :label="t('evalPage.col.started')" width="152">
                <template #default="{ row }"><span class="num">{{ row.started ?? '—' }}</span></template>
              </el-table-column>
              <!-- @vue-generic {RunEntry} -->
              <el-table-column v-if="!split" :label="t('evalPage.col.type')" width="84">
                <template #default="{ row }">{{ t(`evalPage.type.${row.type}`) }}</template>
              </el-table-column>
              <!-- @vue-generic {RunEntry} -->
              <el-table-column v-if="!split" :label="typeFilter === 'playback' ? t('evalPage.col.bag') : t('evalPage.col.scene')" min-width="86" show-overflow-tooltip>
                <template #default="{ row }">{{ sourceLabel(row) }}</template>
              </el-table-column>
              <!-- @vue-generic {RunEntry} -->
              <el-table-column v-if="!split" :label="t('evalPage.col.duration')" width="82" align="right">
                <template #default="{ row }"><span class="num">{{ fmtDuration(row.duration) }}</span></template>
              </el-table-column>
              <!-- @vue-generic {RunEntry} -->
              <el-table-column v-if="!split" :label="t('evalPage.col.length')" width="85" align="right">
                <template #default="{ row }"><span class="num">{{ row.length == null ? '—' : `${row.length.toFixed(1)} m` }}</span></template>
              </el-table-column>
              <!-- "All": one column with the main number of each type; the type filters show the full columns -->
              <!-- @vue-generic {RunEntry} -->
              <el-table-column v-if="!split && typeFilter === 'all'" :label="t('evalPage.col.metric')" width="150" align="right">
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
              <!-- @vue-generic {RunEntry} -->
              <el-table-column v-if="!split && typeFilter === 'mapping'" :label="t('evalPage.col.ate')" width="100" align="right">
                <template #default="{ row }"><span class="num strong">{{ m3(row.ape_rmse) }}</span></template>
              </el-table-column>
              <!-- @vue-generic {RunEntry} -->
              <el-table-column v-if="!split && typeFilter === 'mapping'" :label="t('evalPage.col.rpe')" width="100" align="right">
                <template #default="{ row }"><span class="num">{{ m3(row.rpe_rmse) }}</span></template>
              </el-table-column>
              <!-- Playback: numbers that need no ground truth, plus ATE or the deviation from the bag's reference -->
              <!-- @vue-generic {RunEntry} -->
              <el-table-column v-if="!split && typeFilter === 'playback'" :label="t('evalPage.col.startEnd')" width="100" align="right">
                <template #default="{ row }">
                  <span class="num strong">{{ m3(row.playback?.stats?.start_end) }}</span><span v-if="row.playback?.loop" class="dim"> {{ t('evalPage.col.loopShort') }}</span>
                </template>
              </el-table-column>
              <!-- @vue-generic {RunEntry} -->
              <el-table-column v-if="!split && typeFilter === 'playback'" :label="t('evalPage.col.zEnd')" width="90" align="right">
                <template #default="{ row }"><span class="num">{{ m3(row.playback?.stats?.z_end) }}</span></template>
              </el-table-column>
              <!-- @vue-generic {RunEntry} -->
              <el-table-column v-if="!split && typeFilter === 'playback'" :label="t('evalPage.col.versus')" width="118" align="right">
                <template #default="{ row }">
                  <span v-if="row.ape_rmse != null" class="num"><span class="dim">ATE </span>{{ m3(row.ape_rmse) }}</span>
                  <span v-else-if="row.playback?.ref_ape_rmse != null" class="num"><span class="dim">{{ t('evalPage.pb.chart.ref') }} </span>{{ m3(row.playback.ref_ape_rmse) }}</span>
                  <span v-else class="num">—</span>
                </template>
              </el-table-column>
              <!-- @vue-generic {RunEntry} -->
              <el-table-column v-if="!split && typeFilter === 'navigation'" :label="t('evalPage.nav.variant')" width="110">
                <template #default="{ row }"><span class="mono">{{ row.variant ?? '—' }}</span></template>
              </el-table-column>
              <!-- @vue-generic {RunEntry} -->
              <el-table-column v-if="!split && typeFilter === 'navigation'" :label="t('evalPage.nav.success')" width="90" align="right">
                <template #default="{ row }">
                  <span class="num strong">{{ row.nav ? `${row.nav.succeeded}/${row.nav.finished}` : '—' }}</span>
                </template>
              </el-table-column>
              <!-- @vue-generic {RunEntry} -->
              <el-table-column v-if="!split && typeFilter === 'navigation'" :label="t('evalPage.nav.amcl')" width="100" align="right">
                <template #default="{ row }"><span class="num">{{ m3(row.nav?.amcl_rmse_m) }}</span></template>
              </el-table-column>
              <!-- Side-by-side view: started/size and the main numbers in two-line cells -->
              <!-- @vue-generic {RunEntry} -->
              <el-table-column v-if="split" :label="t('evalPage.col.started')" width="132">
                <template #default="{ row }">
                  <span class="num" :title="row.started ?? undefined">{{ shortTime(row.started) }}</span>
                  <span class="sub num">{{ fmtDuration(row.duration) }}<template v-if="row.length != null"> · {{ row.length.toFixed(1) }} m</template></span>
                </template>
              </el-table-column>
              <!-- The main number of the run's type, with the next most useful one under it -->
              <!-- @vue-generic {RunEntry} -->
              <el-table-column v-if="split" :label="t('evalPage.col.metric')" width="140" align="right">
                <template #default="{ row }">
                  <template v-if="row.type === 'navigation' && row.nav">
                    <span class="num"><b class="strong">{{ row.nav.succeeded }}/{{ row.nav.finished }}</b></span>
                    <span class="sub num">AMCL {{ m3(row.nav.amcl_rmse_m) }}</span>
                  </template>
                  <template v-else-if="row.ape_rmse != null">
                    <span class="num"><span class="dim">ATE </span><b class="strong">{{ m3(row.ape_rmse) }}</b></span>
                    <span v-if="row.playback?.stats" class="sub num" :title="t('evalPage.col.startEnd')">
                      {{ row.playback.loop ? t('evalPage.col.loopShort') : t('evalPage.col.startEndShort') }} {{ m3(row.playback.stats.start_end) }}
                    </span>
                    <span v-else-if="row.rpe_rmse != null" class="sub num">RPE {{ m3(row.rpe_rmse) }}</span>
                  </template>
                  <template v-else-if="row.playback?.stats">
                    <span class="num" :title="t('evalPage.col.startEnd')">
                      <span class="dim">{{ row.playback.loop ? t('evalPage.col.loopShort') : t('evalPage.col.startEndShort') }} </span><b class="strong">{{ m3(row.playback.stats.start_end) }}</b>
                    </span>
                    <span v-if="row.playback.ref_ape_rmse != null" class="sub num" :title="t('evalPage.col.versus')">
                      {{ t('evalPage.pb.chart.ref') }} {{ m3(row.playback.ref_ape_rmse) }}
                    </span>
                    <span v-else class="sub num">{{ t('evalPage.col.zEnd') }} {{ m3(row.playback.stats.z_end) }}</span>
                  </template>
                  <span v-else class="num">—</span>
                </template>
              </el-table-column>
              <!-- @vue-generic {RunEntry} -->
              <el-table-column :label="t('evalPage.col.state')" :width="split ? 142 : undefined" min-width="142">
                <template #default="{ row }">
                  <!-- The normal case is plain text; only states that need attention get a colored chip -->
                  <span v-if="stateOf(row) === 'evaluated'" class="state-ok">{{ t('evalPage.state.evaluated') }}</span>
                  <span v-else class="chip" :class="STATE_CHIP[stateOf(row)]">{{ t(`evalPage.state.${stateOf(row)}`) }}</span>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </div>
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

      <div class="right">
        <div v-if="split" class="tabs glass" role="tablist">
          <button
            v-for="p in panes" :key="p.id" type="button" role="tab" class="tab" :class="{ on: pane === p.id }"
            :aria-selected="pane === p.id" @click="pane = p.id"
          >
            {{ p.label }}<b v-if="p.n" class="badge mono">{{ p.n }}</b>
          </button>
        </div>
        <div ref="paneBox" class="pane">
        <!-- Focused run -->
        <GlassPanel v-show="!split || pane === 'detail'" :title="t('evalPage.detail')" :tip="methodTip">
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
              <span v-if="currentEntry.params_changed" class="chip warning">
                {{ t('evalPage.paramsChanged', { n: Object.keys(currentEntry.params_changed).length }, Object.keys(currentEntry.params_changed).length) }}
              </span>
              <span v-if="currentEntry.maps.length" class="maps">
                {{ t('evalPage.col.maps') }}:
                <router-link v-for="m in currentEntry.maps" :key="m" class="mono" :to="{ path: '/maps', query: { map: m } }">{{ m }}</router-link>
              </span>
            </div>
            <p v-if="currentEntry.recording && currentEntry.type !== 'navigation'" class="note">{{ t('evalPage.recordingNote') }}</p>
            <p v-if="currentEntry.params_changed" class="note">
              {{ t('evalPage.paramsChangedNote', { list: Object.entries(currentEntry.params_changed).map(([k, v]) => `${k} = ${v}`).join(', ') }) }}
            </p>
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
              <el-table :data="currentGoals" size="small" :row-key="(g: NavGoalRecord) => String(g.index)">
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
        <BatchPanel v-show="!split || pane === 'batch'" :selected-runs="selection" :run-color="colorOf" @finished="loadList" @focus="focusRun"
          @pick-run="pickRun" @batches="(b: BatchCompareItem[]) => (batchItems = b)" />

        <!-- Comparison of the ticked runs and batches -->
        <GlassPanel v-show="!split || pane === 'compare'" :title="compareCount ? `${t('evalPage.compare')} · ${compareCount}` : t('evalPage.compare')">
          <!-- The ticked runs with their chart colors: untick here without going back to the list -->
          <div v-if="split && pickedRuns.length" class="picked">
            <span class="count">{{ t('evalPage.selected', { n: pickedRuns.length }) }}</span>
            <span v-for="r in pickedRuns" :key="r.id" class="pick">
              <span class="swatch" :style="{ background: colorOf(r.id) }" />
              <button type="button" class="pick-name mono" :title="r.id" @click="focusRun(r.id)">{{ r.id }}</button>
              <button type="button" class="pick-x" :title="t('evalPage.unpick')" :aria-label="t('evalPage.unpick')" @click="unpick(r)">×</button>
            </span>
            <el-button size="small" link type="primary" @click="runTable?.clearSelection()">{{ t('evalPage.clearPicked') }}</el-button>
          </div>
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
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ---- layout: list | panes, each scrolling on its own ---- */
.eval-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: 100%;
  min-height: 0;
  container: eval-page / inline-size;
}
.split {
  flex: 1;
  display: grid;
  grid-template-columns: minmax(640px, 0.85fr) minmax(0, 1fr);
  gap: 14px;
  min-height: 0;
}
.list-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.list-panel > :deep(.body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 0 10px 8px;
}
.scene-select {
  flex: 1;
  min-width: 150px;
}
/* The table takes the height that is left; its body scrolls under the fixed header */
.table-box {
  position: relative;
  flex: 1;
  min-height: 160px;
}
.table-fill {
  position: absolute;
  inset: 0;
}
.right {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  min-height: 0;
  container: eval-right / inline-size;
}
.tabs {
  flex: none;
  display: flex;
  gap: 4px;
  padding: 4px;
  border-radius: 12px;
}
.tab {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  height: 30px;
  padding: 0 12px;
  border: 0;
  border-radius: 9px;
  font: inherit;
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  color: var(--ink-2);
  background: none;
  cursor: pointer;
  transition: color 0.15s, background-color 0.15s, box-shadow 0.15s;
}
.tab:hover,
.tab:focus-visible {
  color: var(--ink);
  background: rgba(var(--paper-rgb), 0.6);
  outline: none;
}
.tab.on {
  font-weight: 600;
  color: var(--accent);
  background: var(--paper);
  box-shadow: 0 0 0 1px var(--hairline), 0 4px 10px -6px rgba(var(--shade-rgb), 0.35);
}
.badge {
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  font-size: 11px;
  font-weight: 600;
  line-height: 18px;
  text-align: center;
  color: #fff;
  background: var(--accent);
}
.tab:not(.on) .badge {
  color: var(--ink-2);
  background: rgba(var(--tint-rgb), 0.1);
}
/* Room below and beside the panels so the scroll box does not cut their shadows */
.pane {
  flex: 1;
  min-height: 0;
  margin: 0 -6px -6px;
  padding: 0 6px 18px;
  overflow-y: auto;
}

/* ---- list ---- */
.count {
  margin: 0 4px;
  font-size: 12px;
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
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
/* Second line of a list cell */
.sub {
  display: block;
  font-size: 11px;
  line-height: 1.45;
  color: var(--ink-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.swatch + .name + .sub {
  margin-left: 16px;
}
.type-tag {
  margin-right: 6px;
  padding: 0 4px;
  border-radius: 3px;
  font-size: 10.5px;
  color: var(--ink-2);
  background: rgba(var(--tint-rgb), 0.07);
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
  background: rgba(var(--accent-rgb), 0.08) !important;
}
/* Label and number of a list cell stay on one line, a small gap apart */
.list-panel td .num {
  white-space: nowrap;
}
.list-panel td .dim {
  margin-right: 4px;
}
/* The row whose details are shown */
.list-panel :deep(.el-table__row.is-selected > td:first-child) {
  box-shadow: inset 3px 0 0 var(--accent);
}
.list-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 10px 2px;
}
.list-foot .el-button {
  margin-left: 0;
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

/* ---- ticked runs at the top of the comparison ---- */
.picked {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--hairline);
}
.pick {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 4px 0 8px;
  border-radius: 7px;
  background: rgba(var(--paper-rgb), 0.75);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.pick-name,
.pick-x {
  padding: 0;
  border: 0;
  font: inherit;
  background: none;
  cursor: pointer;
}
.pick-name {
  max-width: 190px;
  font-family: var(--font-mono);
  font-size: 11.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--ink);
}
.pick-name:hover,
.pick-name:focus-visible {
  color: var(--accent);
  outline: none;
}
.pick-x {
  width: 18px;
  height: 18px;
  margin-left: 4px;
  border-radius: 5px;
  font-size: 14px;
  line-height: 18px;
  color: var(--ink-3);
}
.pick-x:hover,
.pick-x:focus-visible {
  color: var(--bad);
  background: color-mix(in srgb, var(--bad) 10%, transparent);
  outline: none;
}

/* ---- details and comparison ---- */
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
  white-space: nowrap;
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
  background: var(--paper);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
html.dark .fig {
  filter: brightness(0.92);
}

/* Not enough room for readable time charts beside the trajectory: they go under it at full width,
   side by side when there are two */
@container eval-right (max-width: 900px) {
  .charts {
    grid-template-columns: minmax(0, 1fr);
  }
  .stack {
    flex-direction: row;
    flex-wrap: wrap;
    gap: 8px 16px;
  }
  .stack > figure {
    flex: 1 1 300px;
  }
}
@container eval-right (max-width: 460px) {
  .kpis {
    grid-template-columns: minmax(0, 1fr);
  }
}
/* Stacked view: the list keeps a fixed height, the panels follow and the page scrolls as a whole */
.stacked .split {
  display: flex;
  flex-direction: column;
  flex: none;
}
.stacked .table-box {
  flex: none;
  height: 340px;
}
.stacked .scene-select {
  flex: none;
  width: 190px;
}
.stacked .right {
  container-type: normal;
}
.stacked .pane {
  display: flex;
  flex-direction: column;
  gap: 14px;
  flex: none;
  overflow-y: visible;
}
@media (max-width: 1100px) {
  .stacked .charts {
    grid-template-columns: minmax(0, 1fr);
  }
}
.layout-switch {
  --el-switch-on-color: var(--accent);
  margin-right: 6px;
}
.layout-switch :deep(.el-switch__label) {
  color: var(--ink-3);
  font-weight: 400;
}
.layout-switch :deep(.el-switch__label.is-active) {
  color: var(--ink);
}
.layout-switch :deep(.el-switch__label span) {
  font-size: 12px;
}
/* Narrow window: the columns stack and the page scrolls as a whole; the list keeps a fixed height */
@container eval-page (max-width: 1100px) {
  .split {
    display: flex;
    flex-direction: column;
    flex: none;
  }
  .table-box {
    flex: none;
    height: 340px;
  }
  .pane {
    flex: none;
    overflow-y: visible;
  }
}
.layout-fade .list-panel,
.layout-fade .right {
  animation: eval-fade-in 0.24s ease;
}
@keyframes eval-fade-in {
  from { opacity: 0.45; transform: translateY(4px); }
  to { opacity: 1; transform: none; }
}
</style>

<style>
/* View-transition morph for EvaluationView split <-> stacked switch (unscoped because ::view-transition lives on :root) */
html.eval-vt {
  view-transition-name: none;
}
html.eval-vt-capturing .eval-page,
html.eval-vt-capturing .eval-page * {
  transition: none !important;
}
html.eval-vt .eval-page .list-panel {
  view-transition-name: eval-list;
}
html.eval-vt .eval-page .right {
  view-transition-name: eval-right;
}
html.eval-vt::view-transition {
  clip-path: var(--eval-vt-clip, none);
}
html.eval-vt::view-transition-group(eval-list) {
  z-index: 3;
  animation-duration: 0.36s;
  animation-timing-function: cubic-bezier(0.22, 1, 0.36, 1);
}
html.eval-vt::view-transition-group(eval-right) {
  z-index: 2;
  animation-duration: 0.36s;
  animation-timing-function: cubic-bezier(0.22, 1, 0.36, 1);
}
html.eval-vt::view-transition-image-pair(eval-list) {
  isolation: isolate;
  overflow: clip;
  border-radius: var(--radius);
  background: var(--card-solid);
  border: 1px solid var(--glass-edge);
  box-shadow: var(--shadow);
}
html.eval-vt::view-transition-image-pair(eval-right) {
  isolation: isolate;
  overflow: clip;
  border-radius: var(--radius);
}
html.eval-vt::view-transition-old(eval-list),
html.eval-vt::view-transition-new(eval-list),
html.eval-vt::view-transition-old(eval-right),
html.eval-vt::view-transition-new(eval-right) {
  width: 100%;
  height: 100%;
  object-fit: none;
  object-position: top left;
  mix-blend-mode: normal;
}
/* Quick fade-out then fade-in so old and new table columns / charts never ghost over each other */
html.eval-vt::view-transition-old(eval-list),
html.eval-vt::view-transition-old(eval-right) {
  animation: eval-vt-out 0.15s cubic-bezier(0.4, 0, 1, 1) both;
}
html.eval-vt::view-transition-new(eval-list),
html.eval-vt::view-transition-new(eval-right) {
  animation: eval-vt-in 0.23s cubic-bezier(0, 0, 0.2, 1) 0.08s both;
}
@keyframes eval-vt-out {
  from { opacity: 1; }
  to { opacity: 0; }
}
@keyframes eval-vt-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
</style>
