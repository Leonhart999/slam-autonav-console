<!-- Step ④ "Save map" of the mapping and playback pages. The backend (map_library.py) saves in the background:
     the SLAM writes its cloud (FAST-LIO /map_save, or GLIM stops and its dump is read) → copy into
     <maps>/<name>/ → 2D grid → figures → meta.json; progress comes in the mapping status (ms.save). -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import { fmtCount } from '@/format'
import { errorText, resultText } from '@/messages'
import { useStatusStore } from '@/stores/status'
import type { MappingStatus, SaveJob } from '@/mapping/stream'

const props = defineProps<{
  ms: MappingStatus | null
  current: boolean // highlighted as the step to do now
  nameBase: string // default name = <nameBase>_<month><day>_<hour><minute>
  warn?: string // extra warning above the button (e.g. the SLAM has not caught up with the playback)
}>()
const { t } = useI18n()
const store = useStatusStore()

const SAVE_STEPS_ALL = ['stop_slam', 'map_save', 'copy', 'project', 'figures', 'meta'] as const
type SaveStep = (typeof SAVE_STEPS_ALL)[number]
// GLIM 只在退出时写出优化后的地图：保存时第一步是停止它，FAST-LIO 是调用 /map_save
const SAVE_STEPS = computed<SaveStep[]>(() =>
  SAVE_STEPS_ALL.filter((s) => s !== (props.ms?.save_mode === 'dump' ? 'map_save' : 'stop_slam')),
)
const NAME_RE = /^[A-Za-z0-9_-]{1,64}$/
const mapName = ref('')
const job = computed<SaveJob>(() => props.ms?.save ?? { state: 'idle' })
const saving = computed(() => job.value.state === 'running')
function suggestName() {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  // 日期后缀占 10 位，名称前缀最多 54 位，确保总长度不超过 64。
  const base = props.nameBase.replace(/[^A-Za-z0-9_-]+/g, '_') || 'map'
  return `${base.slice(0, 54)}_${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}`
}
const placeholder = ref(suggestName())
watch(() => props.nameBase, () => (placeholder.value = suggestName())) // 换场景或数据包后更新默认名
const nameInvalid = computed(() => mapName.value !== '' && !NAME_RE.test(mapName.value))
// SLAM 在发里程计、地图非空就能保存（后端会检查 /map_save 服务或 GLIM 进程在不在）
const slamLive = computed(() => (props.ms?.publishers?.odom ?? 0) > 0 && (props.ms?.voxels ?? 0) > 0)
const canSave = computed(() => store.connected && slamLive.value && !saving.value && !nameInvalid.value)
async function saveMap() {
  if (!canSave.value) return
  const name = mapName.value || placeholder.value
  try {
    await api.saveMap(name)
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}
// 保存完成后清空输入框、换一个新的默认名
watch(
  () => job.value.state,
  (s, old) => {
    if (old === 'running' && s === 'done') {
      ElMessage.success(t('mapping.save.savedTo', { dir: job.value.result?.dir ?? '' }))
      mapName.value = ''
      placeholder.value = suggestName()
    }
  },
)
const saveStepState = (s: SaveStep) => {
  const steps = SAVE_STEPS.value
  const cur = steps.indexOf((job.value.step as SaveStep | undefined) ?? steps[0])
  const i = steps.indexOf(s)
  return i < cur ? 'done' : i === cur ? 'current' : 'pending'
}
const previewUrl = computed(() =>
  job.value.state === 'done' && job.value.result?.grid
    ? `/api/maps/${encodeURIComponent(job.value.name ?? '')}/preview.png?t=${job.value.finished ?? 0}`
    : '',
)
</script>

<template>
  <section class="step" :class="{ current, locked: !slamLive && !saving }">
    <header class="step-head">
      <span class="no mono">4</span>
      <span class="title">{{ t('mapping.steps.save') }}</span>
      <span v-if="job.state === 'running'" class="chip warning live">{{ t('mapping.save.saving') }}</span>
      <span v-else-if="job.state === 'done'" class="chip success">{{ t('mapping.save.saved') }}</span>
      <span v-else-if="job.state === 'failed'" class="chip danger">{{ t('mapping.save.failedShort') }}</span>
    </header>
    <el-input
      v-model.trim="mapName" class="name-input" :placeholder="placeholder" :disabled="saving" maxlength="64"
      :aria-label="t('mapping.save.name')" @keydown.enter="saveMap"
    >
      <template #prefix><span class="name-prefix mono">{{ store.paths.maps }}/</span></template>
    </el-input>
    <p class="note" :class="{ bad: nameInvalid }">
      {{ nameInvalid ? t('mapping.save.nameRule') : slamLive ? t('mapping.save.hint') : t('mapping.save.needSlam') }}
    </p>
    <p v-if="ms?.save_mode === 'dump' && slamLive && !saving" class="note">{{ t('mapping.save.dumpHint') }}</p>
    <p v-if="warn && !saving" class="note bad">{{ warn }}</p>
    <el-button class="wide" type="primary" :loading="saving" :disabled="!canSave" @click="saveMap">
      {{ t('mapping.steps.save') }}
    </el-button>

    <!-- 进度 -->
    <ol v-if="saving" class="save-steps">
      <li v-for="s in SAVE_STEPS" :key="s" :class="saveStepState(s)">
        <i class="dot" />{{ t(`mapping.save.steps.${s}`) }}
      </li>
    </ol>

    <!-- 结果 -->
    <div v-if="job.state === 'done' && job.result" class="saved">
      <img v-if="previewUrl" :src="previewUrl" :alt="t('mapping.save.preview')" class="preview" />
      <div class="saved-text">
        <b class="mono">{{ job.name }}</b>
        <span>{{ t('mapping.save.points', { points: fmtCount(job.result.points ?? 0) }) }}</span>
        <span v-if="job.result.grid">
          {{ t('mapping.save.grid', { w: job.result.grid.size_m[0], h: job.result.grid.size_m[1] }) }}
        </span>
        <span v-if="job.result.figures_error" class="bad">{{ t('mapping.save.figuresFailed', { reason: job.result.figures_error }) }}</span>
        <span v-if="job.result.project_error" class="bad">
          {{ t('mapping.save.projectFailed', { reason: job.result.project_error }) }}
        </span>
      </div>
    </div>
    <p v-if="job.state === 'failed'" class="note bad">
      {{ t('mapping.save.failed', { reason: resultText({ code: job.code ?? 'save_error', params: job.params ?? {} }) }) }}
    </p>
    <p v-if="ms?.run" class="note run">{{ t('mapping.save.run', { run: ms.run, runs: store.paths.runs }) }}</p>
  </section>
</template>

<style scoped src="../mapping/steps.css"></style>
<style scoped src="../mapping/save.css"></style>
