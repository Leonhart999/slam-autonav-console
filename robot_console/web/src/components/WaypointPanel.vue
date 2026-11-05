<!-- Waypoint missions on the navigation page (step 3b).
     The draft list is edited by clicking on the 2D map (parent adds points while picking = true). "Run" sends the
     list as one mission: goals go out one by one with NavigateToPose, each with its own result and metrics, and the
     run is recorded in ~/runs/nav_<time>/. The list can be saved next to the map as goals.yaml (ROS parameter
     format, usable by lidar_nav/nav_goals.py in batch experiments). -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { errorText } from '@/messages'
import { useStatusStore } from '@/stores/status'
import type { NavGoalResult, NavMission, PickedPose } from '@/types'
import InfoTip from './InfoTip.vue'

type TagType = 'success' | 'warning' | 'danger' | 'info' | 'primary'

const props = defineProps<{
  modelValue: PickedPose[]
  mapName: string
  mission: NavMission | null
  picking: boolean
  canPick: boolean // navigation running and localized
  canRun: boolean // ... and Nav2 ready
}>()
const emit = defineEmits<{ 'update:modelValue': [PickedPose[]]; togglePick: [] }>()
const { t } = useI18n()
const statusStore = useStatusStore()

const busy = ref(false)
const saved = ref<string | null>(null) // time goals.yaml was written, null = never
const running = computed(() => props.mission?.state === 'running')
const list = computed(() => props.modelValue)

// Load the saved list whenever the map changes (an unsaved draft of another map is dropped)
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
      // Legacy flat maps have no goals.yaml; nothing to load
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
  try {
    await fn()
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    busy.value = false
  }
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

const deg = (r: number) => ((r * 180) / Math.PI).toFixed(0)
const fmt = (v: number | null | undefined, d = 2) => (v == null ? '—' : v.toFixed(d))

const RESULT_TAG: Record<NavGoalResult, TagType> = {
  pending: 'warning', active: 'primary', canceling: 'warning', succeeded: 'success', aborted: 'danger',
  canceled: 'info', rejected: 'danger', timeout: 'danger', stopped: 'info', preempted: 'info',
}
const MISSION_TAG: Record<NavMission['state'], TagType> = {
  running: 'primary', finished: 'success', canceled: 'info', stopped: 'info', preempted: 'info',
}
const summary = computed(() => props.mission?.summary ?? null)
</script>

<template>
  <div class="wp">
    <div class="tools">
      <el-button size="small" :type="picking ? 'primary' : 'default'" :disabled="!canPick || running || busy"
                 @click="emit('togglePick')">
        <i class="dot" />{{ picking ? t('navMission.picking') : t('navMission.add') }}
      </el-button>
      <el-button size="small" :disabled="!list.length || running || busy" @click="clear">
        {{ t('navMission.clear') }}
      </el-button>
    </div>
    <p v-if="picking" class="hint">{{ t('navigation.goal.how.waypoint') }}</p>

    <ol v-if="list.length" class="list">
      <li v-for="(p, i) in list" :key="i">
        <span class="n">{{ i + 1 }}</span>
        <span class="xy mono">{{ p.x.toFixed(2) }}, {{ p.y.toFixed(2) }} · {{ deg(p.yaw) }}°</span>
        <el-button text size="small" :disabled="running || busy" :title="t('navMission.remove')" @click="remove(i)">
          ✕
        </el-button>
      </li>
    </ol>
    <p v-else class="muted">{{ t('navMission.empty') }}</p>

    <div class="tools">
      <el-button size="small" :disabled="!mapName || !list.length || running || busy" @click="save">
        {{ t('navMission.save') }}
      </el-button>
      <el-button size="small" :disabled="!mapName || running || busy" @click="reload">
        {{ t('navMission.load') }}
      </el-button>
    </div>
    <p class="muted">
      {{ saved ? t('navMission.savedAt', { time: saved }) : t('navMission.notSaved') }}
      <InfoTip :content="t('navMission.tips.file')" />
    </p>

    <el-button v-if="!running" class="wide" type="primary" :disabled="!canRun || !list.length || busy" @click="run">
      {{ t('navMission.run', { n: list.length }) }}
    </el-button>
    <el-button v-else class="wide" type="danger" plain :disabled="busy" @click="stop">
      {{ t('navMission.stop') }}
    </el-button>

    <!-- Current or last mission (a single clicked goal is a mission of one) -->
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
  </div>
</template>

<style scoped>
.tools {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-bottom: 8px;
}
.tools .el-button {
  margin: 0;
}
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-right: 6px;
  border-radius: 50%;
  border: 2px solid #0891b2;
}
.wide {
  width: 100%;
  margin-top: 4px;
}
.list {
  max-height: 180px;
  overflow-y: auto;
  margin: 0 0 8px;
  padding: 0;
  list-style: none;
  font-size: 12.5px;
}
.list li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 1px 0;
}
.list .n {
  display: inline-grid;
  place-items: center;
  width: 18px;
  height: 18px;
  border: 1.5px solid #0891b2;
  border-radius: 50%;
  color: #0891b2;
  font: 600 10.5px/1 var(--font-mono);
}
.list .xy {
  flex: 1;
  color: var(--ink-2);
}
.result {
  margin-top: 12px;
  padding: 8px 10px;
  border-radius: 10px;
  background: rgba(8, 145, 178, 0.05);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
  font-size: 12.5px;
  color: var(--ink-3);
}
.goals {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.goals th {
  font-weight: 500;
  color: var(--ink-3);
  text-align: left;
  white-space: nowrap;
}
.goals td,
.goals th {
  padding: 2px 3px;
}
.goals .active {
  color: var(--accent);
}
.kv {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
  font-size: 12.5px;
}
.kv li {
  display: flex;
  justify-content: space-between;
  gap: 10px;
}
.kv span {
  color: var(--ink-3);
}
.kv b {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.hint,
.muted,
.warn {
  margin: 4px 0 8px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-3);
}
.hint {
  color: var(--accent);
}
.warn {
  color: var(--warn);
}
.mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  word-break: break-all;
}
</style>
