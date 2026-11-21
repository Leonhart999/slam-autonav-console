<!-- Batch navigation experiments panel on the Evaluation page: history, live view, export and trash. -->
<script setup lang="ts">
import { FolderOpened, Delete } from '@element-plus/icons-vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { pick } from '@/i18n'
import { errorText } from '@/messages'
import { isCancelled } from '@/http'
import { fmtDuration } from '@/format'
import type { MappingPreset } from '@/types'
import type { MeanStd } from '@/evaluation/batch'
import {
  navBatchApi, type NavBatchHistoryEntry, type NavBatchRun, type NavBatchStatus,
} from '@/evaluation/navBatch'
import { evalApi, type ExportResult, type RunTrashEntry } from '@/evaluation/api'
import { openFolder } from '@/openFolder'
import { useStatusStore } from '@/stores/status'
import { SERIES } from '@/evaluation/colors'
import GlassPanel from '@/components/GlassPanel.vue'
import InfoTip from '@/components/InfoTip.vue'
import TrashBin, { type TrashItem } from '@/components/TrashBin.vue'
import type { NavBatchCompareItem } from '@/components/NavBatchCompare.vue'

const props = defineProps<{ selectedRuns: string[]; runColor: (id: string) => string }>()
const emit = defineEmits<{
  finished: []
  focus: [id: string]
  pickRun: [id: string, on: boolean]
  batches: [items: NavBatchCompareItem[]]
}>()

const { t, te } = useI18n()
const router = useRouter()
const store = useStatusStore()

const presets = ref<MappingPreset[]>([])
const live = ref<NavBatchStatus>({ state: 'idle' })
const history = ref<NavBatchHistoryEntry[]>([])
const viewing = ref('')
const shown = ref<NavBatchStatus | null>(null)
const exporting = ref(false)
const exportResult = ref<ExportResult | null>(null)
let timer: number | undefined

const running = computed(() => live.value.state === 'running')
const view = computed<NavBatchStatus | null>(() =>
  viewing.value ? shown.value : live.value.state === 'idle' ? null : live.value,
)

async function loadPresets() {
  try { presets.value = await api.mappingPresets() } catch { /* fall back to raw name */ }
}
async function loadHistory() {
  try { history.value = await navBatchApi.history() } catch { /* ignore */ }
}
async function poll() {
  try {
    const was = live.value.state
    live.value = await navBatchApi.status()
    if (was === 'running' && live.value.state !== 'running') {
      loadHistory()
      emit('finished')
    }
  } catch {
    // keep last
  }
  timer = window.setTimeout(poll, running.value ? 1000 : 5000)
}
onMounted(() => {
  loadPresets()
  loadHistory()
  poll()
})
onBeforeUnmount(() => window.clearTimeout(timer))

watch(viewing, async (id) => {
  exportResult.value = null
  shown.value = null
  if (!id) return
  try { shown.value = await navBatchApi.get(id) } catch (e) { ElMessage.error(errorText(e)) }
})

async function cancel() {
  try { live.value = await navBatchApi.cancel() } catch (e) { ElMessage.error(errorText(e)) }
}

let exportAbort: AbortController | undefined
onBeforeUnmount(() => exportAbort?.abort())
watch(() => view.value?.id, () => { exportAbort?.abort(); exporting.value = false; exportResult.value = null })
async function exportBatch() {
  if (!view.value) return
  exportAbort?.abort()
  const ctrl = exportAbort = new AbortController()
  exporting.value = true
  try {
    const res = await navBatchApi.exportBatch(view.value, ctrl.signal)
    if (!ctrl.signal.aborted) exportResult.value = res
  } catch (e) {
    if (!isCancelled(e)) ElMessage.error(errorText(e))
  } finally {
    if (exportAbort === ctrl) exporting.value = false
  }
}

function sceneLabel(name?: string) {
  if (!name) return '—'
  const p = presets.value.find((x) => x.name === name)
  return p ? pick(p.label) : name.replace(/^custom_/, '')
}
const speedTag = (v?: number) => (v && v !== 1 ? ` · ${v}×` : '')
const locTag = (loc?: string) => (loc === 'gt' ? 'GT' : 'AMCL')
const m3 = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(3))
const f1 = (v: number | null | undefined, u = '') => (v == null ? '—' : `${v.toFixed(1)}${u}`)

function failText(r: NavBatchRun) {
  if (r.ok) return t('navBatch.ok')
  if (!r.code) return '—'
  const key = `navBatch.fail.${r.code}`
  return te(key) ? t(key, r.params) : r.code
}

const STATE_CHIP: Record<NavBatchStatus['state'], string> = {
  idle: 'info', running: 'primary live', finished: 'success', canceled: 'warning', failed: 'danger',
}

const STAT_KEYS = [
  'success_rate', 'total_time_s', 'mean_time_s', 'total_path_m', 'mean_path_m',
  'recoveries', 'goal_error_m', 'loc_error_m', 'amcl_rmse_m', 'min_clearance_m', 'wall_s',
] as const
const statRows = computed(() => {
  const s = view.value?.summary
  if (!s) return []
  return STAT_KEYS.map((k) => ({ key: k, v: (s[k] ?? null) as MeanStd | null }))
})
function fmtStat(k: string, v: number | undefined): string {
  if (v == null) return '—'
  if (k === 'success_rate') return `${(v * 100).toFixed(1)}%`
  if (k === 'total_time_s' || k === 'mean_time_s' || k === 'wall_s') return v.toFixed(1)
  if (k === 'total_path_m' || k === 'mean_path_m' || k === 'recoveries') return v.toFixed(2)
  return v.toFixed(3)
}

const picked = ref<string[]>([])
const canCompare = (b: NavBatchHistoryEntry) => (b.summary?.completed ?? 0) > 0
function onPick(rows: NavBatchHistoryEntry[]) {
  const ids = rows.map((r) => r.id)
  picked.value = [...picked.value.filter((id) => ids.includes(id)), ...ids.filter((id) => !picked.value.includes(id))]
}
const colorOf = (id: string) => SERIES[SERIES.length - 1 - (Math.max(0, picked.value.indexOf(id)) % SERIES.length)]
watch([picked, history, presets], () => {
  const items = picked.value
    .map((id) => history.value.find((h) => h.id === id))
    .filter((b): b is NavBatchHistoryEntry => !!b && canCompare(b))
    .map((b) => ({ entry: b, color: colorOf(b.id), scene: sceneLabel(b.preset) }))
  emit('batches', items)
}, { deep: true })

const tickable = (r: NavBatchRun) => r.ok && !!r.run_id
const okRuns = computed(() => (view.value?.runs ?? []).filter(tickable).map((r) => r.run_id as string))
const allPicked = computed(() => okRuns.value.length > 0 && okRuns.value.every((id) => props.selectedRuns.includes(id)))
const somePicked = computed(() => okRuns.value.some((id) => props.selectedRuns.includes(id)))
function pickAll(on: boolean) {
  for (const id of okRuns.value) if (props.selectedRuns.includes(id) !== on) emit('pickRun', id, on)
}

const shownId = computed(() => view.value?.id ?? '')
function showBatch(b: NavBatchHistoryEntry) {
  viewing.value = b.id === live.value.id && live.value.state !== 'idle' ? '' : b.id
}
watch([history, () => live.value.state], () => {
  if (!viewing.value && live.value.state === 'idle' && history.value.length) viewing.value = history.value[0].id
})

const batchTable = ref<{ toggleRowSelection: (row: NavBatchHistoryEntry, selected?: boolean) => void } | null>(null)
const deleting = ref(false)
async function deleteSelected() {
  const ids = [...picked.value]
  if (!ids.length) return
  try {
    await ElMessageBox.confirm(t('navBatch.trash.confirm', { n: ids.length }), t('navBatch.trash.confirmTitle'), {
      type: 'warning', confirmButtonText: t('navBatch.trash.ok'), cancelButtonText: t('navBatch.trash.cancel'),
    })
  } catch {
    return
  }
  deleting.value = true
  try {
    const r = await evalApi.trashRuns(ids)
    for (const id of r.moved) {
      const row = history.value.find((b) => b.id === id)
      if (row) batchTable.value?.toggleRowSelection(row, false)
      if (viewing.value === id) viewing.value = ''
    }
    if (r.moved.length) ElMessage.success(t('navBatch.trash.moved', { n: r.moved.length }))
    if (r.refused.length) {
      const lines = r.refused.map((x) => t(`navBatch.trash.refuse.${x.reason}`,
        { id: x.id, maps: (x.maps ?? []).join(', '), detail: x.detail ?? '' }))
      ElMessageBox.alert(lines.join('\n'), t('navBatch.trash.refusedTitle', { n: r.refused.length }),
        { type: 'warning', customClass: 'trash-refused' })
    }
    await loadHistory()
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
const trashBusy = ref('')
const trashItems = computed<TrashItem[]>(() => trashEntries.value.filter((e) => e.type === 'nav_batch').map((e) => ({
  id: e.entry, name: e.run ?? e.entry, deleted: e.deleted, size: e.size, restorable: e.restorable,
  detail: t('navBatch.trash.detail', { map: sceneLabel(e.preset ?? undefined), variant: e.entry.split('__')[0] }),
})))
async function loadTrash() {
  trashLoading.value = true
  try { trashEntries.value = (await evalApi.trashList()).entries }
  catch (e) { ElMessage.error(errorText(e)) }
  finally { trashLoading.value = false }
}
async function trashAct(action: 'restore' | 'purge' | 'empty', entry = '') {
  trashBusy.value = entry || '*'
  try {
    if (action === 'restore') {
      const r = await evalApi.trashRestore(entry)
      ElMessage.success(t('navBatch.trash.restored', { id: r.restored }))
      await loadHistory()
    } else if (action === 'purge') {
      await evalApi.trashPurge(entry)
      ElMessage.success(t('navBatch.trash.purged'))
    } else {
      await evalApi.trashEmpty('nav_batch')
      ElMessage.success(t('navBatch.trash.emptied'))
    }
  } catch (err) {
    ElMessage.error(errorText(err))
  } finally {
    trashBusy.value = ''
    loadTrash()
  }
}

const runElapsed = computed(() =>
  live.value.run_started ? fmtDuration(Date.now() / 1000 - live.value.run_started) : '—')
</script>

<template>
  <GlassPanel :title="t('navBatch.title')" :tip="t('navBatch.hint')">
    <template #extra>
      <el-button size="small" @click="router.push('/navigation')">{{ t('navBatch.goNav') }}</el-button>
    </template>

    <template v-if="history.length">
      <h4 class="sub">
        {{ picked.length ? `${t('navBatch.cmp.title')} · ${picked.length}` : t('navBatch.cmp.title') }}<InfoTip :content="t('navBatch.cmp.hint')" />
      </h4>
      <el-table ref="batchTable" :data="history" size="small" max-height="220" class="batch-list"
        :row-key="(b: NavBatchHistoryEntry) => b.id"
        :row-class-name="({ row }: { row: NavBatchHistoryEntry }) => (row.id === shownId ? 'is-selected' : '')"
        @selection-change="onPick" @row-click="showBatch">
        <!-- @vue-generic {NavBatchHistoryEntry} -->
        <el-table-column type="selection" width="40" reserve-selection />
        <!-- @vue-generic {NavBatchHistoryEntry} -->
        <el-table-column :label="t('navBatch.col.batch')" min-width="235" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="swatch" :style="{ background: picked.includes(row.id) && canCompare(row) ? colorOf(row.id) : 'transparent' }" />
            <span class="mono name nowrap">{{ row.id }}</span>
          </template>
        </el-table-column>
        <!-- @vue-generic {NavBatchHistoryEntry} -->
        <el-table-column :label="t('navBatch.col.map')" min-width="115" show-overflow-tooltip>
          <template #default="{ row }"><span class="mono">{{ row.map }}</span></template>
        </el-table-column>
        <!-- @vue-generic {NavBatchHistoryEntry} -->
        <el-table-column :label="t('navBatch.col.variant')" min-width="105" show-overflow-tooltip>
          <template #default="{ row }"><span class="mono">{{ row.variant }}{{ speedTag(row.speed_scale) }}</span></template>
        </el-table-column>
        <!-- @vue-generic {NavBatchHistoryEntry} -->
        <el-table-column :label="t('navBatch.col.localizer')" width="96">
          <template #default="{ row }"><span class="mono">{{ locTag(row.localizer) }}</span></template>
        </el-table-column>
        <!-- @vue-generic {NavBatchHistoryEntry} -->
        <el-table-column :label="t('navBatch.col.goals')" width="84" align="right">
          <template #default="{ row }">
            <span class="mono">{{ row.summary ? `${row.summary.total_succeeded}/${row.summary.total_goals}` : `—/${row.n}` }}</span>
          </template>
        </el-table-column>
        <!-- @vue-generic {NavBatchHistoryEntry} -->
        <el-table-column :label="t('navBatch.col.time')" width="115" align="right">
          <template #default="{ row }">
            <span class="mono strong">{{ f1(row.summary?.total_time_s?.mean) }}</span>
            <span v-if="row.summary?.total_time_s" class="mono dim"> ± {{ f1(row.summary.total_time_s.std) }}</span>
          </template>
        </el-table-column>
        <!-- @vue-generic {NavBatchHistoryEntry} -->
        <el-table-column :label="t('navBatch.col.recoveries')" width="72" align="right">
          <template #default="{ row }"><span class="mono">{{ row.summary ? row.summary.total_recoveries : '—' }}</span></template>
        </el-table-column>
        <!-- @vue-generic {NavBatchHistoryEntry} -->
        <el-table-column :label="t('navBatch.col.result')" width="96">
          <template #default="{ row }">
            <span v-if="row.state === 'finished'" class="state-ok">{{ t('navBatch.state.finished') }}</span>
            <span v-else class="chip" :class="STATE_CHIP[row.state]">{{ t(`navBatch.state.${row.state}`) }}</span>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <div class="list-foot">
      <span class="trash-open">
        <TrashBin
          v-model="trashOpen" :title="t('navBatch.trash.title')" :entries="trashItems" :loading="trashLoading"
          :busy="trashBusy" :dirs="store.paths.runs ? [`${store.paths.runs}/.trash`] : []" @open="loadTrash"
          @restore="trashAct('restore', $event)" @purge="trashAct('purge', $event)" @empty="trashAct('empty')"
        />
      </span>
      <template v-if="history.length">
        <span class="count">{{ t('navBatch.trash.selected', { n: picked.length }) }}</span>
        <el-button size="small" type="danger" plain :disabled="!picked.length || running" :loading="deleting"
          :title="running ? t('navBatch.trash.runningHint') : t('navBatch.trash.deleteHint')" @click="deleteSelected">
          <el-icon><Delete /></el-icon><span>{{ t('navBatch.trash.delete') }}</span>
        </el-button>
      </template>
    </div>

    <p v-if="!view" class="hint top">{{ t('navBatch.none') }}</p>
    <template v-else>
      <div class="status">
        <span class="mono name">{{ view.id }}</span>
        <span class="chip" :class="STATE_CHIP[view.state]">{{ t(`navBatch.state.${view.state}`) }}</span>
        <span class="chip primary">{{ view.map }} · {{ view.variant }} · {{ locTag(view.localizer) }}{{ speedTag(view.speed_scale) }}</span>
        <template v-if="!viewing && view.state === 'running'">
          <b class="mono">{{ t('navBatch.progress', { i: view.index, n: view.n }) }}</b>
          <span>{{ t(`navBatch.phase.${view.phase ?? 'starting'}`) }}</span>
          <span v-if="view.phase === 'drive' && view.mission" class="mono">
            {{ t('navBatch.goalProgress', { done: view.mission.index + 1, total: view.mission.total }) }}
          </span>
          <span class="mono dim">{{ t('navBatch.runElapsed', { t: runElapsed }) }}</span>
          <el-popconfirm :title="t('navBatch.confirmCancel')" :confirm-button-text="t('navBatch.yes')"
            :cancel-button-text="t('navBatch.no')" width="260" @confirm="cancel">
            <template #reference><el-button size="small" type="danger">{{ t('navBatch.cancel') }}</el-button></template>
          </el-popconfirm>
        </template>
        <span v-if="view.elapsed != null" class="mono dim">{{ t('navBatch.elapsed', { t: fmtDuration(view.elapsed) }) }}</span>
        <span v-if="view.reason && view.state !== 'running'" class="dim">{{ t('navBatch.reason', { r: view.reason }) }}</span>
        <span class="spacer" />
        <el-button v-if="store.paths.runs && view.id" size="small" :title="t('navBatch.trash.openBatch')"
          @click="openFolder(`${store.paths.runs}/${view.id}`)">
          <el-icon><FolderOpened /></el-icon><span>{{ t('openFolder.button') }}</span>
        </el-button>
        <el-button v-if="view.state !== 'running' && view.runs?.some((r) => r.ok)" size="small" :loading="exporting"
          @click="exportBatch">{{ t('navBatch.export') }}</el-button>
      </div>
      <div v-if="!viewing && view.state === 'running' && view.n" class="track">
        <i :style="{ width: `${((view.index ?? 1) - 1) / view.n * 100}%` }" />
      </div>

      <el-table :data="view.runs ?? []" size="small" class="batch-runs" :row-key="(r: NavBatchRun) => String(r.index)"
        @row-click="(r: NavBatchRun) => r.run_id && emit('focus', r.run_id)">
        <!-- @vue-generic {NavBatchRun} -->
        <el-table-column width="40">
          <template #header>
            <el-checkbox :model-value="allPicked" :indeterminate="somePicked && !allPicked" :disabled="!okRuns.length"
              :title="t('navBatch.cmp.pickAll')" @change="(v: unknown) => pickAll(!!v)" />
          </template>
          <template #default="{ row }">
            <span @click.stop>
              <el-checkbox :model-value="!!row.run_id && selectedRuns.includes(row.run_id)" :disabled="!tickable(row)"
                @change="(v: unknown) => row.run_id && emit('pickRun', row.run_id, !!v)" />
            </span>
          </template>
        </el-table-column>
        <!-- @vue-generic {NavBatchRun} -->
        <el-table-column :label="t('navBatch.col.index')" width="44" align="right">
          <template #default="{ row }"><span class="mono">{{ row.index }}</span></template>
        </el-table-column>
        <!-- @vue-generic {NavBatchRun} -->
        <el-table-column :label="t('navBatch.col.run')" min-width="175">
          <template #default="{ row }">
            <span class="swatch" :style="{ background: row.run_id && selectedRuns.includes(row.run_id) ? runColor(row.run_id) : 'transparent' }" />
            <span class="mono">{{ row.run_id ?? '—' }}</span>
          </template>
        </el-table-column>
        <!-- @vue-generic {NavBatchRun} -->
        <el-table-column :label="t('navBatch.col.result')" width="110">
          <template #default="{ row }">
            <span v-if="!row.ended" class="chip primary live">{{ t('navBatch.state.running') }}</span>
            <span v-else-if="row.ok" class="state-ok">{{ failText(row) }}</span>
            <span v-else class="chip danger">{{ failText(row) }}</span>
          </template>
        </el-table-column>
        <!-- @vue-generic {NavBatchRun} -->
        <el-table-column :label="t('navBatch.col.reset')" width="84">
          <template #default="{ row }"><span class="dim">{{ t(`navBatch.reset.${row.reset_kind}`) }}</span></template>
        </el-table-column>
        <!-- @vue-generic {NavBatchRun} -->
        <el-table-column :label="t('navBatch.col.goals')" width="78" align="right">
          <template #default="{ row }"><span class="mono strong">{{ row.finished != null ? `${row.succeeded}/${row.finished}` : '—' }}</span></template>
        </el-table-column>
        <!-- @vue-generic {NavBatchRun} -->
        <el-table-column :label="t('navBatch.col.time')" width="102" align="right">
          <template #default="{ row }"><span class="mono">{{ f1(row.total_time_s, ' s') }}</span></template>
        </el-table-column>
        <!-- @vue-generic {NavBatchRun} -->
        <el-table-column :label="t('navBatch.col.path')" width="102" align="right">
          <template #default="{ row }"><span class="mono">{{ f1(row.total_path_m, ' m') }}</span></template>
        </el-table-column>
        <!-- @vue-generic {NavBatchRun} -->
        <el-table-column :label="t('navBatch.col.recoveries')" width="78" align="right">
          <template #default="{ row }"><span class="mono">{{ row.recoveries ?? '—' }}</span></template>
        </el-table-column>
        <!-- @vue-generic {NavBatchRun} -->
        <el-table-column :label="t('navBatch.col.locErr')" width="92" align="right">
          <template #default="{ row }"><span class="mono">{{ m3(row.mean_loc_error_m) }}</span></template>
        </el-table-column>
        <!-- @vue-generic {NavBatchRun} -->
        <el-table-column :label="t('navBatch.col.wall')" width="94" align="right">
          <template #default="{ row }"><span class="mono">{{ row.wall_s == null ? '—' : fmtDuration(row.wall_s) }}</span></template>
        </el-table-column>
      </el-table>

      <template v-if="view.summary">
        <h4 class="sub">
          {{ t('navBatch.summaryTitle', { ok: view.summary.completed, n: view.summary.runs, gok: view.summary.total_succeeded, gtot: view.summary.total_goals }) }}
        </h4>
        <el-table :data="statRows" size="small" :row-key="(r: { key: string }) => r.key">
          <!-- @vue-generic {{ key: string; v: MeanStd | null }} -->
        <el-table-column :label="t('navBatch.metric')" min-width="135">
            <template #default="{ row }">{{ t(`navBatch.m.${row.key}`) }}</template>
          </el-table-column>
          <!-- @vue-generic {{ key: string; v: MeanStd | null }} -->
        <el-table-column :label="`${t('navBatch.mean')} ± ${t('navBatch.std')}`" min-width="135" align="right">
            <template #default="{ row }">
              <span class="mono strong">{{ fmtStat(row.key, row.v?.mean) }}</span>
              <span class="mono dim"> ± {{ fmtStat(row.key, row.v?.std) }}</span>
            </template>
          </el-table-column>
          <!-- @vue-generic {{ key: string; v: MeanStd | null }} -->
        <el-table-column :label="t('navBatch.min')" width="76" align="right">
            <template #default="{ row }"><span class="mono">{{ fmtStat(row.key, row.v?.min) }}</span></template>
          </el-table-column>
          <!-- @vue-generic {{ key: string; v: MeanStd | null }} -->
        <el-table-column :label="t('navBatch.max')" width="76" align="right">
            <template #default="{ row }"><span class="mono">{{ fmtStat(row.key, row.v?.max) }}</span></template>
          </el-table-column>
        </el-table>
      </template>

      <div v-if="exportResult" class="export">
        <span>{{ t('navBatch.exported', { n: exportResult.files.length }) }}</span>
        <code class="mono">{{ exportResult.dir }}</code>
        <el-button size="small" link type="primary" @click="openFolder(exportResult.dir)">{{ t('openFolder.button') }}</el-button>
      </div>
    </template>
  </GlassPanel>
</template>

<style scoped>
.hint.top { margin-top: 12px; }
.hint { margin: 0 0 10px; font-size: 12.5px; line-height: 1.6; color: var(--ink-3); }
.spacer { flex: 1; }
.status { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin: 12px 0 8px; font-size: 13px; }
.name { font-size: 13px; font-weight: 600; color: var(--ink); }
.nowrap { white-space: nowrap; }
.mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.strong { font-weight: 600; color: var(--ink); }
.dim { color: var(--ink-3); font-size: 12px; }
.state-ok { color: var(--ok); font-weight: 500; font-size: 12.5px; white-space: nowrap; }
.track { height: 4px; margin-bottom: 10px; border-radius: 2px; background: var(--hairline); overflow: hidden; }
.track i { display: block; height: 100%; border-radius: 2px; background: var(--accent); transition: width 0.3s; }
.sub { display: flex; align-items: center; gap: 6px; margin: 14px 0 6px; font-size: 12.5px; font-weight: 600; color: var(--ink-2); }
.export { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 10px; padding: 6px 10px; border-radius: 8px; background: rgba(var(--ok-rgb), 0.08); font-size: 12.5px; }
.export code { color: var(--ink); word-break: break-all; }
.swatch { display: inline-block; width: 8px; height: 8px; margin-right: 6px; border-radius: 2px; vertical-align: 1px; }
.batch-list :deep(.el-table__row), .batch-runs :deep(.el-table__row) { cursor: pointer; }
.batch-list :deep(.is-selected > td.el-table__cell) { background: rgba(var(--accent-rgb), 0.08); }
.list-foot { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.list-foot .trash-open { margin-right: auto; }
.list-foot .count { font-size: 12px; color: var(--ink-3); font-variant-numeric: tabular-nums; }
</style>
