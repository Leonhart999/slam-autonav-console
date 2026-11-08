<!-- Batch mapping experiments: N repetitions of sim -> SLAM -> waypoints, then evo on every run (batch.py). -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { pick } from '@/i18n'
import { errorText } from '@/messages'
import { fmtDuration } from '@/format'
import { batchApi, type BatchHistoryEntry, type BatchOptions, type BatchRun, type BatchStatus, type MeanStd } from '@/evaluation/batch'
import type { ExportResult } from '@/evaluation/api'
import { SERIES } from '@/evaluation/colors'
import GlassPanel from '@/components/GlassPanel.vue'
import InfoTip from '@/components/InfoTip.vue'
import type { BatchCompareItem } from '@/components/BatchCompare.vue'

// Ticks go to the page's comparison panel: runs share the run list's selection (selectedRuns / pickRun),
// ticked batches are sent up as `batches`.
const props = defineProps<{ selectedRuns: string[]; runColor: (id: string) => string }>()
const emit = defineEmits<{ finished: []; focus: [id: string]; pickRun: [id: string, on: boolean]; batches: [items: BatchCompareItem[]] }>()

const { t, te } = useI18n({ messages: {
  'zh-CN': { batch: {
    title: '批量建图实验',
    hint: '按顺序重复 N 次：启动仿真 → SLAM → 自动航点 → 跑完后结束记录并停掉全部进程。全部跑完自动用 evo 评估，给出 ATE / RPE 的平均值 ± 标准差。开始前仿真、建图、导航都必须是停止状态。',
    scene: '场景', slam: 'SLAM 算法', n: '重复次数', saveMaps: '每次保存地图', speed: '仿真倍速',
    speedTip: '让 Gazebo 以几倍于真实时间的速度运行（物理步长、传感器频率、路线都不变，只是墙钟时间变短）。电脑算不过来时达不到目标倍速，每次实际达到的实时率记在“实时率”列。SLAM 处理不过来可能丢帧、影响精度，换倍速前建议先用 1× 和目标倍速各跑一组对比。',
    start: '开始批量', cancel: '取消批量', confirmCancel: '取消正在进行的批量实验？当前这一次会被中止，已完成的记录保留。',
    yes: '确认', no: '返回',
    progress: '第 {i}/{n} 次', elapsed: '总用时 {t}', runElapsed: '本次 {t}', waypoints: '航点 {done}/{total}',
    timeout: '单次运行超过 {m} 分钟记为失败，批量继续下一次。',
    phase: {
      starting: '准备', start_sim: '启动仿真', wait_sim: '等待仿真时钟和真值', start_slam: '启动 SLAM',
      wait_slam: '等待 SLAM 初始化', drive: '自动航点行驶中', save_map: '保存地图', stopping: '停止进程',
      evaluate: 'evo 评估', done: '已结束',
    },
    state: { idle: '空闲', running: '进行中', finished: '已完成', canceled: '已取消', failed: '失败' },
    col: { index: '#', run: '运行记录', result: '结果', ate: 'ATE RMSE', rpe: 'RPE RMSE', length: '长度', map: '地图', rtf: '实时率', wall: '墙钟用时' },
    ok: '成功',
    fail: {
      run_timeout: '超时（{seconds} s）', sim_not_ready: '仿真没有就绪（{seconds} s 内没有时钟或真值）',
      slam_not_ready: 'SLAM 没有输出里程计（{seconds} s）', waypoints_failed: '航点程序异常退出（返回码 {code}）',
      slam_exited: 'SLAM 中途退出（返回码 {code}）', no_run_record: '没有生成运行记录',
      save_timeout: '保存地图超时', stack_running: '{name} 已在运行', start_failed: '{name} 启动失败：{reason}',
      leftover_processes: '停止后仍有残留进程：{leftover}', canceled: '已取消', estop: '急停中止',
      speed_failed: '设置仿真倍速失败：{reason}',
    },
    summaryTitle: '汇总（只统计成功的 {ok}/{n} 次）',
    metric: '指标', mean: '平均值', std: '标准差', min: '最小', max: '最大',
    m: { ape_rmse: 'ATE RMSE (m)', rpe_rmse: 'RPE RMSE (m)', length: '轨迹长度 (m)', duration: '时长 (s)', rtf: '行驶时实时率', wall_s: '单次墙钟用时 (s)' },
    export: '导出本批次', exported: '已导出 {n} 个文件到', reason: '结束原因：{r}',
    none: '还没有批量实验',
    cmp: {
      title: '历史批次',
      hint: '点击一行查看该批次；勾选两个或以上批次（至少成功一次），会在下方“对比”栏并排对比平均值 ± 标准差。批次里的单次运行也可以勾选，和上方运行记录的勾选是同一份。',
      batch: '批次', okN: '成功', pickAll: '勾选本批次全部成功的运行',
    },
  } },
  en: { batch: {
    title: 'Batch mapping experiments',
    hint: 'Repeats N times: start the simulation → SLAM → auto waypoints → end the run and stop every process. When all runs are done each is evaluated with evo and ATE / RPE are given as mean ± std. Simulation, mapping and navigation must be stopped before starting.',
    scene: 'Scene', slam: 'SLAM', n: 'Repetitions', saveMaps: 'Save a map each run', speed: 'Sim speed',
    speedTip: 'Runs Gazebo faster than real time (physics step, sensor rates and route unchanged; only the wall clock gets shorter). If the computer cannot keep up the target is not reached; the RTF column shows what each run reached. A SLAM that cannot keep up may drop scans and lose accuracy, so compare one batch at 1× with one at the target speed first.',
    start: 'Start batch', cancel: 'Cancel batch', confirmCancel: 'Cancel the running batch? The current run is aborted; finished runs are kept.',
    yes: 'Confirm', no: 'Back',
    progress: 'Run {i}/{n}', elapsed: 'total {t}', runElapsed: 'this run {t}', waypoints: 'waypoints {done}/{total}',
    timeout: 'A run that takes longer than {m} min is recorded as failed and the batch goes on.',
    phase: {
      starting: 'Preparing', start_sim: 'Starting simulation', wait_sim: 'Waiting for sim clock and ground truth',
      start_slam: 'Starting SLAM', wait_slam: 'Waiting for SLAM to initialize', drive: 'Driving the waypoints',
      save_map: 'Saving map', stopping: 'Stopping processes', evaluate: 'Evaluating with evo', done: 'Done',
    },
    state: { idle: 'Idle', running: 'Running', finished: 'Finished', canceled: 'Canceled', failed: 'Failed' },
    col: { index: '#', run: 'Run', result: 'Result', ate: 'ATE RMSE', rpe: 'RPE RMSE', length: 'Length', map: 'Map', rtf: 'RTF', wall: 'Wall time' },
    ok: 'OK',
    fail: {
      run_timeout: 'Timed out ({seconds} s)', sim_not_ready: 'Simulation not ready (no clock or ground truth in {seconds} s)',
      slam_not_ready: 'SLAM gave no odometry ({seconds} s)', waypoints_failed: 'Waypoint driver failed (exit code {code})',
      slam_exited: 'SLAM exited mid-run (exit code {code})', no_run_record: 'No run record was written',
      save_timeout: 'Map save timed out', stack_running: '{name} is already running', start_failed: '{name} failed to start: {reason}',
      leftover_processes: 'Processes left after stopping: {leftover}', canceled: 'Canceled', estop: 'Stopped by E-STOP',
      speed_failed: 'Could not set the sim speed: {reason}',
    },
    summaryTitle: 'Summary (successful runs only, {ok}/{n})',
    metric: 'Metric', mean: 'Mean', std: 'Std', min: 'Min', max: 'Max',
    m: { ape_rmse: 'ATE RMSE (m)', rpe_rmse: 'RPE RMSE (m)', length: 'Length (m)', duration: 'Duration (s)', rtf: 'RTF while driving', wall_s: 'Wall time per run (s)' },
    export: 'Export this batch', exported: 'Exported {n} files to', reason: 'Ended because: {r}',
    none: 'No batch experiments yet',
    cmp: {
      title: 'Batches',
      hint: 'Click a row to show that batch. Tick two or more batches (with at least one successful run) to compare their mean ± std in the Comparison panel below. Single runs of a batch can be ticked too; they share the run list\'s ticks above.',
      batch: 'Batch', okN: 'OK', pickAll: 'Tick every successful run of this batch',
    },
  } },
} })

const options = ref<BatchOptions | null>(null)
const form = ref({ preset: '', slam: '', n: 3, save_maps: false, speed: 1 })
const live = ref<BatchStatus>({ state: 'idle' })        // this console's current or last batch
const history = ref<BatchHistoryEntry[]>([])
const viewing = ref('')                                 // '' = current batch, otherwise a past batch id
const shown = ref<BatchStatus | null>(null)
const starting = ref(false)
const exporting = ref(false)
const exportResult = ref<ExportResult | null>(null)
let timer: number | undefined

const running = computed(() => live.value.state === 'running')
const view = computed<BatchStatus | null>(() => (viewing.value ? shown.value : live.value.state === 'idle' ? null : live.value))

async function loadOptions() {
  try {
    options.value = await batchApi.options()
    form.value.preset ||= options.value.presets[0]?.name ?? ''
    form.value.slam ||= options.value.slam[0]?.name ?? ''
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}
async function loadHistory() {
  try {
    history.value = await batchApi.history()
  } catch {
    // Shown as empty; the next refresh retries
  }
}
async function poll() {
  try {
    const was = live.value.state
    live.value = await batchApi.status()
    if (was === 'running' && live.value.state !== 'running') {
      loadHistory()
      emit('finished')
    }
  } catch {
    // Backend restarting: keep the last status
  }
  timer = window.setTimeout(poll, running.value ? 1000 : 5000)
}
onMounted(() => {
  loadOptions()
  loadHistory()
  poll()
})
onBeforeUnmount(() => window.clearTimeout(timer))

watch(viewing, async (id) => {
  exportResult.value = null
  shown.value = null
  if (!id) return
  try {
    shown.value = await batchApi.get(id)
  } catch (e) {
    ElMessage.error(errorText(e))
  }
})

async function start() {
  starting.value = true
  try {
    live.value = await batchApi.start({ ...form.value })
    viewing.value = ''
    exportResult.value = null
    window.clearTimeout(timer)
    timer = window.setTimeout(poll, 1000)
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    starting.value = false
  }
}
async function cancel() {
  try {
    live.value = await batchApi.cancel()
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}
async function exportBatch() {
  if (!view.value) return
  exporting.value = true
  try {
    exportResult.value = await batchApi.exportBatch(view.value)
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    exporting.value = false
  }
}

function labelOf(list: { name: string; label: { zh: string; en: string } }[] | undefined, name?: string) {
  const x = list?.find((p) => p.name === name)
  return x ? pick(x.label) : name ?? '—'
}
// " · 2×" after the SLAM name when a batch ran faster than real time
const speedTag = (v?: number) => (v && v !== 1 ? ` · ${v}×` : '')
function failText(r: BatchRun) {
  if (r.ok) return t('batch.ok')
  if (!r.code) return '—'
  const key = `batch.fail.${r.code}`
  return te(key) ? t(key, r.params) : r.code
}
const STATE_CHIP: Record<BatchStatus['state'], string> = {
  idle: 'info', running: 'primary live', finished: 'success', canceled: 'warning', failed: 'danger',
}
const m3 = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(3))
const statRows = computed(() => {
  const s = view.value?.summary
  if (!s) return []
  return (['ape_rmse', 'rpe_rmse', 'length', 'duration', 'rtf', 'wall_s'] as const)
    .map((k) => ({ key: k, v: (s[k] ?? null) as MeanStd | null }))
})
const fmtStat = (k: string, v: number | undefined) =>
  v == null ? '—' : k === 'duration' || k === 'wall_s' ? v.toFixed(1) : k === 'length' || k === 'rtf' ? v.toFixed(2) : v.toFixed(4)

// ---------------- Ticks: batches (for the comparison panel) and runs of the shown batch ----------------
const picked = ref<string[]>([])                                    // in tick order, so colors stay put
const canCompare = (b: BatchHistoryEntry) => (b.summary?.succeeded ?? 0) > 0
function onPick(rows: BatchHistoryEntry[]) {
  const ids = rows.map((r) => r.id)
  picked.value = [...picked.value.filter((id) => ids.includes(id)), ...ids.filter((id) => !picked.value.includes(id))]
}
// Batches take the palette from the end, runs (run list) from the start: both can sit in the comparison panel
const colorOf = (id: string) => SERIES[SERIES.length - 1 - (Math.max(0, picked.value.indexOf(id)) % SERIES.length)]
watch([picked, history, () => options.value], () => {
  const items = picked.value
    .map((id) => history.value.find((h) => h.id === id))
    .filter((b): b is BatchHistoryEntry => !!b)
    .map((b) => ({ entry: b, color: colorOf(b.id), scene: labelOf(options.value?.presets, b.preset),
      slam: labelOf(options.value?.slam, b.slam) + speedTag(b.speed) }))
  emit('batches', items)
}, { deep: true })

const tickable = (r: BatchRun) => r.ok && !!r.run_id
const okRuns = computed(() => (view.value?.runs ?? []).filter(tickable).map((r) => r.run_id as string))
const allPicked = computed(() => okRuns.value.length > 0 && okRuns.value.every((id) => props.selectedRuns.includes(id)))
const somePicked = computed(() => okRuns.value.some((id) => props.selectedRuns.includes(id)))
function pickAll(on: boolean) {
  for (const id of okRuns.value) if (props.selectedRuns.includes(id) !== on) emit('pickRun', id, on)
}

// Shown batch: the live one while it runs, otherwise the clicked row (newest batch by default)
const shownId = computed(() => view.value?.id ?? '')
function showBatch(b: BatchHistoryEntry) {
  viewing.value = b.id === live.value.id && live.value.state !== 'idle' ? '' : b.id
}
watch([history, () => live.value.state], () => {
  if (!viewing.value && live.value.state === 'idle' && history.value.length) viewing.value = history.value[0].id
})

// What a batch does and the per-run timeout: in the title's "i" instead of a paragraph above the form
const panelTip = computed(() => options.value
  ? `${t('batch.hint')} ${t('batch.timeout', { m: Math.round(options.value.run_timeout / 60) })}`
  : t('batch.hint'))

const runElapsed = computed(() =>
  live.value.run_started ? fmtDuration(Date.now() / 1000 - live.value.run_started) : '—')
</script>

<template>
  <GlassPanel :title="t('batch.title')" :tip="panelTip">
    <div class="form">
      <label>{{ t('batch.scene') }}
        <el-select v-model="form.preset" size="small" class="sel" :disabled="running">
          <el-option v-for="p in options?.presets ?? []" :key="p.name" :value="p.name" :label="pick(p.label)" />
        </el-select>
      </label>
      <label>{{ t('batch.slam') }}
        <el-select v-model="form.slam" size="small" class="sel" :disabled="running">
          <el-option v-for="s in options?.slam ?? []" :key="s.name" :value="s.name" :label="pick(s.label)" />
        </el-select>
      </label>
      <label>{{ t('batch.n') }}
        <el-input-number v-model="form.n" size="small" :min="1" :max="options?.max_runs ?? 20" :disabled="running" />
      </label>
      <label v-if="(options?.speeds?.length ?? 0) > 1">{{ t('batch.speed') }}<InfoTip :content="t('batch.speedTip')" />
        <el-select v-model="form.speed" size="small" class="speed" :disabled="running">
          <el-option v-for="v in options?.speeds ?? []" :key="v" :value="v" :label="`${v}×`" />
        </el-select>
      </label>
      <label class="sw">{{ t('batch.saveMaps') }} <el-switch v-model="form.save_maps" size="small" :disabled="running" /></label>
      <span class="spacer" />
      <el-popconfirm v-if="running" :title="t('batch.confirmCancel')" :confirm-button-text="t('batch.yes')"
        :cancel-button-text="t('batch.no')" width="280" @confirm="cancel">
        <template #reference><el-button size="small" type="danger">{{ t('batch.cancel') }}</el-button></template>
      </el-popconfirm>
      <el-button v-else size="small" type="primary" :loading="starting" :disabled="!form.preset || !form.slam" @click="start">
        {{ t('batch.start') }}
      </el-button>
    </div>

    <!-- Batch list: click = show below, tick = compare in the page's comparison panel -->
    <template v-if="history.length">
      <h4 class="sub">{{ picked.length ? `${t('batch.cmp.title')} · ${picked.length}` : t('batch.cmp.title') }}<InfoTip :content="t('batch.cmp.hint')" /></h4>
      <el-table :data="history" size="small" max-height="220" class="batch-list" :row-key="(b: BatchHistoryEntry) => b.id"
        :row-class-name="({ row }: { row: BatchHistoryEntry }) => (row.id === shownId ? 'is-selected' : '')"
        @selection-change="onPick" @row-click="showBatch">
        <el-table-column type="selection" width="40" reserve-selection :selectable="canCompare" />
        <el-table-column :label="t('batch.cmp.batch')" min-width="190">
          <template #default="{ row }">
            <span class="swatch" :style="{ background: picked.includes(row.id) ? colorOf(row.id) : 'transparent' }" />
            <span class="mono name">{{ row.id }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('batch.scene')" min-width="110" show-overflow-tooltip>
          <template #default="{ row }">{{ labelOf(options?.presets, row.preset) }}</template>
        </el-table-column>
        <el-table-column :label="t('batch.slam')" min-width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ labelOf(options?.slam, row.slam) }}{{ speedTag(row.speed) }}</template>
        </el-table-column>
        <el-table-column :label="t('batch.cmp.okN')" width="70" align="right">
          <template #default="{ row }"><span class="mono">{{ row.summary ? `${row.summary.succeeded}/${row.summary.runs}` : `—/${row.n}` }}</span></template>
        </el-table-column>
        <el-table-column :label="t('batch.col.ate')" width="150" align="right">
          <template #default="{ row }">
            <span class="mono strong">{{ fmtStat('ape_rmse', row.summary?.ape_rmse?.mean) }}</span>
            <span v-if="row.summary?.ape_rmse" class="mono dim"> ± {{ fmtStat('ape_rmse', row.summary.ape_rmse.std) }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('batch.col.rpe')" width="150" align="right">
          <template #default="{ row }">
            <span class="mono">{{ fmtStat('rpe_rmse', row.summary?.rpe_rmse?.mean) }}</span>
            <span v-if="row.summary?.rpe_rmse" class="mono dim"> ± {{ fmtStat('rpe_rmse', row.summary.rpe_rmse.std) }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('batch.col.result')" width="96">
          <template #default="{ row }">
            <!-- Normal outcomes are plain text, only the ones that need attention get a colored chip -->
            <span v-if="row.state === 'finished'" class="state-ok">{{ t('batch.state.finished') }}</span>
            <span v-else class="chip" :class="STATE_CHIP[row.state as BatchStatus['state']]">{{ t(`batch.state.${row.state}`) }}</span>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <p v-if="!view" class="hint top">{{ t('batch.none') }}</p>
    <template v-else>
      <div class="status">
        <span class="mono name">{{ view.id }}</span>
        <span class="chip" :class="STATE_CHIP[view.state]">{{ t(`batch.state.${view.state}`) }}</span>
        <span class="chip primary">{{ labelOf(options?.presets, view.preset) }} · {{ labelOf(options?.slam, view.slam) }}{{ speedTag(view.speed) }}</span>
        <template v-if="!viewing && view.state === 'running'">
          <b class="mono">{{ t('batch.progress', { i: view.index, n: view.n }) }}</b>
          <span>{{ t(`batch.phase.${view.phase ?? 'starting'}`) }}</span>
          <span v-if="view.phase === 'drive' && view.progress?.total" class="mono">
            {{ t('batch.waypoints', { done: view.progress.done, total: view.progress.total }) }}
          </span>
          <span class="mono dim">{{ t('batch.runElapsed', { t: runElapsed }) }}</span>
        </template>
        <span v-if="view.elapsed != null" class="mono dim">{{ t('batch.elapsed', { t: fmtDuration(view.elapsed) }) }}</span>
        <span v-if="view.reason && view.state !== 'running'" class="dim">{{ t('batch.reason', { r: view.reason }) }}</span>
        <span class="spacer" />
        <el-button v-if="view.state !== 'running' && view.runs?.some((r) => r.ok)" size="small" :loading="exporting"
          @click="exportBatch">{{ t('batch.export') }}</el-button>
      </div>
      <div v-if="!viewing && view.state === 'running' && view.n" class="track"><i :style="{ width: `${((view.index ?? 1) - 1) / view.n * 100}%` }" /></div>

      <el-table :data="view.runs ?? []" size="small" class="batch-runs" :row-key="(r: BatchRun) => r.index"
        @row-click="(r: BatchRun) => r.run_id && emit('focus', r.run_id)">
        <el-table-column width="40">
          <template #header>
            <el-checkbox :model-value="allPicked" :indeterminate="somePicked && !allPicked" :disabled="!okRuns.length"
              :title="t('batch.cmp.pickAll')" @change="(v: unknown) => pickAll(!!v)" />
          </template>
          <template #default="{ row }">
            <span @click.stop>
              <el-checkbox :model-value="!!row.run_id && selectedRuns.includes(row.run_id)" :disabled="!tickable(row)"
                @change="(v: unknown) => emit('pickRun', row.run_id, !!v)" />
            </span>
          </template>
        </el-table-column>
        <el-table-column :label="t('batch.col.index')" width="44" align="right">
          <template #default="{ row }"><span class="mono">{{ row.index }}</span></template>
        </el-table-column>
        <el-table-column :label="t('batch.col.run')" min-width="180">
          <template #default="{ row }">
            <span class="swatch" :style="{ background: row.run_id && selectedRuns.includes(row.run_id) ? runColor(row.run_id) : 'transparent' }" />
            <span class="mono">{{ row.run_id ?? '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('batch.col.result')" min-width="130">
          <template #default="{ row }">
            <span v-if="!row.ended" class="chip primary live">{{ t('batch.state.running') }}</span>
            <span v-else-if="row.ok" class="state-ok">{{ failText(row) }}</span>
            <span v-else class="chip danger">{{ failText(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('batch.col.ate')" width="100" align="right">
          <template #default="{ row }"><span class="mono strong">{{ m3(row.ape_rmse) }}</span></template>
        </el-table-column>
        <el-table-column :label="t('batch.col.rpe')" width="100" align="right">
          <template #default="{ row }"><span class="mono">{{ m3(row.rpe_rmse) }}</span></template>
        </el-table-column>
        <el-table-column :label="t('batch.col.length')" width="90" align="right">
          <template #default="{ row }"><span class="mono">{{ row.length == null ? '—' : `${row.length.toFixed(1)} m` }}</span></template>
        </el-table-column>
        <el-table-column :label="t('batch.col.rtf')" width="80" align="right">
          <template #default="{ row }"><span class="mono">{{ row.rtf == null ? '—' : row.rtf.toFixed(2) }}</span></template>
        </el-table-column>
        <el-table-column :label="t('batch.col.wall')" width="90" align="right">
          <template #default="{ row }"><span class="mono">{{ row.wall_s == null ? '—' : fmtDuration(row.wall_s) }}</span></template>
        </el-table-column>
        <el-table-column :label="t('batch.col.map')" min-width="84" show-overflow-tooltip>
          <template #default="{ row }"><span class="mono">{{ row.map ?? '—' }}</span></template>
        </el-table-column>
      </el-table>

      <template v-if="view.summary">
        <h4 class="sub">{{ t('batch.summaryTitle', { ok: view.summary.succeeded, n: view.summary.runs }) }}</h4>
        <el-table :data="statRows" size="small" :row-key="(r: { key: string }) => r.key">
          <el-table-column :label="t('batch.metric')" min-width="140">
            <template #default="{ row }">{{ t(`batch.m.${row.key}`) }}</template>
          </el-table-column>
          <el-table-column :label="`${t('batch.mean')} ± ${t('batch.std')}`" min-width="170" align="right">
            <template #default="{ row }">
              <span class="mono strong">{{ fmtStat(row.key, row.v?.mean) }}</span>
              <span class="mono dim"> ± {{ fmtStat(row.key, row.v?.std) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('batch.min')" width="100" align="right">
            <template #default="{ row }"><span class="mono">{{ fmtStat(row.key, row.v?.min) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('batch.max')" width="100" align="right">
            <template #default="{ row }"><span class="mono">{{ fmtStat(row.key, row.v?.max) }}</span></template>
          </el-table-column>
          <!-- n only when some metric is missing for some runs (otherwise it repeats the count in the heading) -->
          <el-table-column v-if="statRows.some((r) => (r.v?.n ?? 0) !== view?.summary?.succeeded)" label="n" width="60" align="right">
            <template #default="{ row }"><span class="mono">{{ row.v?.n ?? 0 }}</span></template>
          </el-table-column>
        </el-table>
      </template>
      <div v-if="exportResult" class="export">
        <span>{{ t('batch.exported', { n: exportResult.files.length }) }}</span>
        <code class="mono">{{ exportResult.dir }}</code>
      </div>
    </template>
  </GlassPanel>
</template>

<style scoped>
.hint.top { margin-top: 12px; }
.hint { margin: 0 0 10px; font-size: 12.5px; line-height: 1.6; color: var(--ink-3); }
.form { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px; margin-bottom: 12px; }
.form label { display: inline-flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--ink-2); }
.sel { width: 150px; }
.speed { width: 76px; }
.spacer { flex: 1; }
.dim { color: var(--ink-3); font-size: 12px; }
.mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.strong { font-weight: 600; color: var(--ink); }
.name { font-weight: 600; }
.status { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin: 16px 0 8px; font-size: 13px; }
.track { height: 3px; margin: 0 0 10px; border-radius: 3px; background: var(--hairline); overflow: hidden; }
.track i { display: block; height: 100%; background: var(--accent-grad); transition: width .6s ease; }
.sub { margin: 16px 0 6px; font-size: 12.5px; font-weight: 600; color: var(--ink-2); }
.export { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; font-size: 12.5px; }
.export code { word-break: break-all; }
:deep(.el-table__row) { cursor: pointer; }
.state-ok { font-size: 12px; color: var(--ink-3); }
.state-ok::before { content: '✓ '; color: var(--ok); font-weight: 700; }
:deep(.el-table__row.is-selected > td) { background: rgba(8, 145, 178, 0.06); }
.swatch { display: inline-block; width: 8px; height: 8px; margin-right: 8px; border-radius: 2px; vertical-align: 1px; }
</style>
