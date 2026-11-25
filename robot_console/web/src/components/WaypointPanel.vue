<!-- Waypoint missions on the navigation page (step 5): single mission or batch N repetitions with auto-reset. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { errorText } from '@/messages'
import { useStatusStore } from '@/stores/status'
import { navBatchApi, type NavBatchRun, type NavBatchStatus, type NavLocalizer } from '@/evaluation/navBatch'
import type { NavGoalResult, NavMission, PickedPose } from '@/types'
import InfoTip from './InfoTip.vue'

type TagType = 'success' | 'warning' | 'danger' | 'info' | 'primary'

const props = defineProps<{
  modelValue: PickedPose[]
  mapName: string
  mission: NavMission | null
  picking: boolean
  canPick: boolean
  canRun: boolean
  variant: string
  localizer: NavLocalizer
  speedScale: number
  hasScene: boolean
  navBusy: boolean
}>()
const emit = defineEmits<{
  'update:modelValue': [PickedPose[]]
  togglePick: []
  'batch-running': [boolean]
}>()
const { t, te } = useI18n()
const router = useRouter()
const statusStore = useStatusStore()

const mode = ref<'single' | 'batch'>('single')
const busy = ref(false)
const saved = ref<string | null>(null)
const running = computed(() => props.mission?.state === 'running')
const list = computed(() => props.modelValue)

watch(
  () => props.mapName,
  async (name) => {
    saved.value = null
    emit('update:modelValue', [])
    if (!name) return
    try {
      const r = await api.loadGoals(name)
      if (name !== props.mapName) return
      saved.value = r.saved
      emit('update:modelValue', r.goals)
    } catch {
      // Legacy flat maps have no goals.yaml
    }
  },
  { immediate: true },
)

function remove(i: number) {
  emit('update:modelValue', list.value.filter((_, k) => k !== i))
}
async function clear() {
  if (!list.value.length) return
  try {
    await ElMessageBox.confirm(t('navMission.clearAsk'), t('navMission.confirm'), {
      type: 'warning', confirmButtonText: t('navMission.confirm'), cancelButtonText: t('navMission.cancel'),
    })
  } catch {
    return
  }
  emit('update:modelValue', [])
}

async function act(fn: () => Promise<void>) {
  busy.value = true
  try { await fn() }
  catch (e) { ElMessage.error(errorText(e)) }
  finally { busy.value = false }
}
const save = () => act(async () => {
  const r = await api.saveGoals(props.mapName, list.value)
  saved.value = r.saved
  ElMessage.success(t('navMission.saved', { n: r.goals.length }))
})
const reload = () => act(async () => {
  const r = await api.loadGoals(props.mapName)
  saved.value = r.saved
  emit('update:modelValue', r.goals)
})
const run = () => act(async () => {
  await api.startMission(list.value)
})
const stop = () => act(async () => {
  await api.cancelGoal()
})

// ---------------- Batch navigation ----------------
const batchN = ref(5)
const batchMax = ref(20)
const batchLive = ref<NavBatchStatus>({ state: 'idle' })
const batchStarting = ref(false)
const batchRunning = computed(() => batchLive.value.state === 'running')
let batchTimer = 0

watch(batchRunning, (v) => {
  emit('batch-running', v)
  if (v) mode.value = 'batch'
}, { immediate: true })

async function pollBatch() {
  try {
    const prev = batchLive.value.state
    const next = await navBatchApi.status()
    batchLive.value = next
    if (prev === 'running' && next.state === 'finished' && next.summary) {
      ElMessage.success(t('navBatch.ended', {
        ok: next.summary.completed, n: next.summary.runs,
        gok: next.summary.total_succeeded, gtot: next.summary.total_goals,
      }))
    }
  } catch {
    // ignore transient offline
  }
  batchTimer = window.setTimeout(pollBatch, batchRunning.value ? 800 : 3500)
}

onMounted(async () => {
  try {
    const opt = await navBatchApi.options()
    batchMax.value = opt.max_runs
  } catch { /* ignore */ }
  void pollBatch()
})
onBeforeUnmount(() => window.clearTimeout(batchTimer))

async function startBatch() {
  batchStarting.value = true
  try {
    batchLive.value = await navBatchApi.start({
      map: props.mapName,
      variant: props.variant,
      localizer: props.localizer,
      goals: list.value,
      n: batchN.value,
      speed_scale: props.speedScale,
    })
    window.clearTimeout(batchTimer)
    batchTimer = window.setTimeout(pollBatch, 800)
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    batchStarting.value = false
  }
}

async function cancelBatch() {
  try {
    batchLive.value = await navBatchApi.cancel()
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}

function batchFailText(r: NavBatchRun) {
  if (r.ok) return t('navBatch.ok')
  if (!r.code) return '—'
  const key = `navBatch.fail.${r.code}`
  return te(key) ? t(key, r.params) : r.code
}

const deg = (r: number) => ((r * 180) / Math.PI).toFixed(0)
const fmt = (v: number | null | undefined, d = 2) => (v == null ? '—' : v.toFixed(d))
const fmtPct = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)

const RESULT_TAG: Record<NavGoalResult, TagType> = {
  pending: 'warning', active: 'primary', canceling: 'warning', succeeded: 'success', aborted: 'danger',
  canceled: 'info', rejected: 'danger', timeout: 'danger', stopped: 'info', preempted: 'info',
}
const MISSION_TAG: Record<NavMission['state'], TagType> = {
  running: 'primary', finished: 'success', canceled: 'info', stopped: 'info', preempted: 'info',
}
const BATCH_TAG: Record<NavBatchStatus['state'], TagType> = {
  idle: 'info', running: 'primary', finished: 'success', canceled: 'warning', failed: 'danger',
}
const summary = computed(() => props.mission?.summary ?? null)
const batchSum = computed(() => batchLive.value.summary ?? null)
</script>

<template>
  <div class="wp">
    <div class="tools">
      <el-button size="small" :type="picking ? 'primary' : 'default'" :disabled="!canPick || running || batchRunning || busy"
                 @click="emit('togglePick')">
        <i class="dot" />{{ picking ? t('navMission.picking') : t('navMission.add') }}
      </el-button>
      <el-button size="small" :disabled="!list.length || running || batchRunning || busy" @click="clear">
        {{ t('navMission.clear') }}
      </el-button>
    </div>
    <p v-if="picking" class="hint">{{ t('navigation.goal.how.waypoint') }}</p>

    <ol v-if="list.length" class="list">
      <li v-for="(p, i) in list" :key="i">
        <span class="n">{{ i + 1 }}</span>
        <span class="xy mono">{{ p.x.toFixed(2) }}, {{ p.y.toFixed(2) }} · {{ deg(p.yaw) }}°</span>
        <el-button text size="small" :disabled="running || batchRunning || busy" :title="t('navMission.remove')" @click="remove(i)">
          ✕
        </el-button>
      </li>
    </ol>
    <p v-else class="muted">{{ t('navMission.empty') }}</p>

    <div class="tools">
      <el-button size="small" :disabled="!mapName || !list.length || running || batchRunning || busy" @click="save">
        {{ t('navMission.save') }}
      </el-button>
      <el-button size="small" :disabled="!mapName || running || batchRunning || busy" @click="reload">
        {{ t('navMission.load') }}
      </el-button>
    </div>
    <p class="muted">
      {{ saved ? t('navMission.savedAt', { time: saved }) : t('navMission.notSaved') }}
      <InfoTip :content="t('navMission.tips.file')" />
    </p>

    <el-radio-group v-model="mode" size="small" class="mode-switch" :disabled="running || batchRunning">
      <el-radio-button value="single">{{ t('navBatch.modeSingle') }}</el-radio-button>
      <el-radio-button value="batch">{{ t('navBatch.modeBatch') }}</el-radio-button>
    </el-radio-group>

    <template v-if="mode === 'single'">
      <el-button v-if="!running" class="wide" type="primary" :disabled="!canRun || !list.length || batchRunning || busy" @click="run">
        {{ t('navMission.run', { n: list.length }) }}
      </el-button>
      <el-button v-else class="wide" type="danger" plain :disabled="busy" @click="stop">
        {{ t('navMission.stop') }}
      </el-button>

      <!-- Current or last mission -->
      <div v-if="mission && summary" class="result">
        <div class="head">
          <span>{{ t('navMission.last') }}</span>
          <el-tag size="small" :type="MISSION_TAG[mission.state]" effect="light">
            {{ t(`navMission.state.${mission.state}`) }}
            <template v-if="mission.state === 'running'"> {{ mission.index + 1 }}/{{ mission.total }}</template>
          </el-tag>
        </div>
        <table class="goals">
          <thead>
            <tr>
              <th>#</th>
              <th>{{ t('navMission.col.result') }}</th>
              <th>{{ t('navMission.col.time') }}</th>
              <th>{{ t('navMission.col.error') }}<InfoTip :content="t('navMission.tips.error')" /></th>
              <th>{{ t('navMission.col.clearance') }}<InfoTip :content="t('navMission.tips.clearance')" /></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="g in mission.goals" :key="g.index">
              <td>{{ g.index }}</td>
              <td>
                <el-tag v-if="g.result" size="small" :type="RESULT_TAG[g.result]" effect="light">
                  {{ t(`navMission.result.${g.result}`) }}
                </el-tag>
                <span v-else-if="g.state === 'active'" class="active">{{ t('navMission.result.active') }}</span>
                <span v-else class="muted">—</span>
              </td>
              <td class="mono">
                {{ g.state === 'active' && mission.live ? fmt(mission.live.elapsed_s, 0) : fmt(g.time_s, 1) }}
              </td>
              <td class="mono">{{ fmt(g.goal_error_m) }}</td>
              <td class="mono">
                {{ g.state === 'active' && mission.live ? fmt(mission.live.clearance_m) : fmt(g.min_clearance_m) }}
              </td>
            </tr>
          </tbody>
        </table>
        <ul class="kv">
          <li><span>{{ t('navMission.sum.success') }}</span><b>{{ summary.succeeded }}/{{ summary.goals }}</b></li>
          <li><span>{{ t('navMission.sum.time') }}</span><b>{{ fmt(summary.total_time_s, 1) }} s</b></li>
          <li><span>{{ t('navMission.sum.path') }}</span><b>{{ fmt(summary.total_path_m) }} m</b></li>
          <li><span>{{ t('navMission.sum.recoveries') }}</span><b>{{ summary.recoveries }}</b></li>
          <li>
            <span>{{ t('navMission.sum.amcl') }}<InfoTip :content="t('navMission.tips.amcl')" /></span>
            <b>{{ summary.amcl_error ? `${summary.amcl_error.rmse_m.toFixed(3)} / ${summary.amcl_error.max_m.toFixed(3)} m` : '—' }}</b>
          </li>
        </ul>
        <p v-if="!mission.has_gt" class="warn">{{ t('navMission.noGt') }}</p>
        <p class="muted mono">{{ statusStore.paths.runs }}/{{ mission.id }}/</p>
      </div>
    </template>

    <template v-else>
      <div class="batch-cfg">
        <div class="cfg-row">
          <span>{{ t('navBatch.n') }}</span>
          <el-input-number v-model="batchN" size="small" :min="1" :max="batchMax" :disabled="batchRunning" />
        </div>
        <p class="muted mono">
          {{ variant || '—' }} · {{ localizer === 'gt' ? t('navigation.localizer.gt') : t('navigation.localizer.amcl') }} · {{ speedScale.toFixed(2) }}×
        </p>
      </div>
      <p v-if="mapName && !hasScene" class="warn">{{ t('navBatch.needScene') }}</p>

      <el-button v-if="!batchRunning" class="wide" type="primary"
        :disabled="!mapName || !hasScene || !list.length || navBusy || running || batchStarting"
        :loading="batchStarting" @click="startBatch">
        {{ t('navBatch.start', { n: batchN }) }}
      </el-button>
      <el-popconfirm v-else :title="t('navBatch.confirmCancel')" :confirm-button-text="t('navBatch.yes')"
        :cancel-button-text="t('navBatch.no')" width="240" @confirm="cancelBatch">
        <template #reference>
          <el-button class="wide" type="danger" plain>{{ t('navBatch.cancel') }}</el-button>
        </template>
      </el-popconfirm>

      <div v-if="batchLive.state !== 'idle'" class="result">
        <div class="head">
          <span class="mono">{{ batchLive.id?.replace(/^nav_batch_/, '') }}</span>
          <el-tag size="small" :type="BATCH_TAG[batchLive.state]" effect="light">
            {{ t(`navBatch.state.${batchLive.state}`) }}
            <template v-if="batchRunning"> {{ batchLive.index }}/{{ batchLive.n }}</template>
          </el-tag>
        </div>
        <p v-if="batchRunning" class="hint">
          {{ t(`navBatch.phase.${batchLive.phase ?? 'starting'}`) }}
          <template v-if="batchLive.phase === 'drive' && batchLive.mission">
            · {{ t('navBatch.goalProgress', { done: batchLive.mission.index + 1, total: batchLive.mission.total }) }}
          </template>
        </p>
        <div v-if="batchRunning && batchLive.n" class="track">
          <i :style="{ width: `${((batchLive.index ?? 1) - 1) / batchLive.n * 100}%` }" />
        </div>

        <table v-if="batchLive.runs?.length" class="goals">
          <thead>
            <tr>
              <th>#</th>
              <th>{{ t('navBatch.col.goals') }}</th>
              <th>{{ t('navBatch.col.time') }}</th>
              <th>{{ t('navBatch.col.recoveries') }}</th>
              <th>{{ t('navBatch.col.reset') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in batchLive.runs" :key="r.index">
              <td class="mono">{{ r.index }}</td>
              <td class="mono">
                <template v-if="r.ok">{{ r.succeeded }}/{{ r.finished }}</template>
                <span v-else-if="!r.ended" class="active">{{ t('navBatch.state.running') }}</span>
                <span v-else class="warn" :title="batchFailText(r)">!</span>
              </td>
              <td class="mono">{{ fmt(r.total_time_s, 1) }}</td>
              <td class="mono">{{ r.recoveries ?? '—' }}</td>
              <td>{{ t(`navBatch.reset.${r.reset_kind}`) }}</td>
            </tr>
          </tbody>
        </table>

        <ul v-if="batchSum && batchSum.completed > 0" class="kv">
          <li>
            <span>{{ t('navBatch.m.pooled_success_rate') }}</span>
            <b>{{ fmtPct(batchSum.pooled_success_rate) }} ({{ batchSum.total_succeeded }}/{{ batchSum.total_goals }})</b>
          </li>
          <li>
            <span>{{ t('navBatch.m.total_time_s') }}</span>
            <b>{{ fmt(batchSum.total_time_s?.mean, 1) }} ± {{ fmt(batchSum.total_time_s?.std, 1) }}</b>
          </li>
          <li>
            <span>{{ t('navBatch.m.total_path_m') }}</span>
            <b>{{ fmt(batchSum.total_path_m?.mean, 2) }} ± {{ fmt(batchSum.total_path_m?.std, 2) }}</b>
          </li>
          <li>
            <span>{{ t('navBatch.m.recoveries') }}</span>
            <b>{{ fmt(batchSum.recoveries?.mean, 2) }} ({{ batchSum.total_recoveries }})</b>
          </li>
          <li>
            <span>{{ t('navBatch.m.loc_error_m') }}</span>
            <b>{{ fmt(batchSum.loc_error_m?.mean, 3) }} ± {{ fmt(batchSum.loc_error_m?.std, 3) }}</b>
          </li>
        </ul>

        <el-button class="wide eval-link" size="small" plain
          @click="router.push({ path: '/evaluation', query: { pane: 'batch' } })">
          {{ t('navBatch.openEval') }}
        </el-button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.tools {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-bottom: 8px;
}
.tools .el-button { margin: 0; }
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-right: 6px;
  border-radius: 50%;
  border: 2px solid var(--accent);
}
.wide { width: 100%; margin-top: 4px; }
.eval-link { margin-top: 8px; }
.mode-switch { width: 100%; display: grid; grid-template-columns: 1fr 1fr; margin: 2px 0 8px; }
.mode-switch :deep(.el-radio-button) { width: 100%; }
.mode-switch :deep(.el-radio-button__inner) { width: 100%; }
.batch-cfg { margin-bottom: 6px; }
.cfg-row { display: flex; align-items: center; justify-content: space-between; font-size: 12.5px; color: var(--ink-2); margin-bottom: 4px; }
.track { height: 4px; margin: 4px 0 8px; border-radius: 2px; background: var(--hairline); overflow: hidden; }
.track i { display: block; height: 100%; border-radius: 2px; background: var(--accent); transition: width 0.3s; }
.list {
  max-height: 180px;
  overflow-y: auto;
  margin: 0 0 8px;
  padding: 0;
  list-style: none;
  font-size: 12.5px;
}
.list li { display: flex; align-items: center; gap: 8px; padding: 1px 0; }
.list .n {
  display: inline-grid; place-items: center; width: 18px; height: 18px;
  border: 1.5px solid var(--accent); border-radius: 50%; color: var(--accent);
  font: 600 10.5px/1 var(--font-mono);
}
.list .xy { flex: 1; color: var(--ink-2); }
.result {
  margin-top: 12px;
  padding: 8px 10px;
  border-radius: 10px;
  background: rgba(var(--accent-rgb), 0.05);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 6px; font-size: 12.5px; color: var(--ink-3);
}
.goals { width: 100%; border-collapse: collapse; font-size: 12px; }
.goals th { font-weight: 500; color: var(--ink-3); text-align: left; white-space: nowrap; }
.goals td, .goals th { padding: 2px 3px; }
.goals .active { color: var(--accent); }
.kv { display: flex; flex-direction: column; gap: 5px; margin: 8px 0 0; padding: 0; list-style: none; font-size: 12.5px; }
.kv li { display: flex; justify-content: space-between; gap: 10px; }
.kv span { color: var(--ink-3); }
.kv b { font-weight: 600; font-variant-numeric: tabular-nums; }
.hint, .muted, .warn { margin: 4px 0 8px; font-size: 12px; line-height: 1.6; color: var(--ink-3); }
.hint { color: var(--accent); }
.warn { color: var(--warn); }
.mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; word-break: break-all; }
</style>
