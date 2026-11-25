<!-- Ground segmentation page: pick a map and parameters, run Patchwork++ in a job, look at the new grid, save it
     as a new map version. -->
<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  ElButton, ElInput, ElInputNumber, ElMessage, ElOption, ElRadioButton, ElRadioGroup, ElSelect, ElSwitch,
} from 'element-plus'
import { RC } from '../../../_shared/web/sdk'
import { makeKit, provideKit, type Job } from '../../../_shared/web/kit'
import { useJobs } from '../../../_shared/web/useJobs'
import JobList from '../../../_shared/web/JobList.vue'
import PythonGate from '../../../_shared/web/PythonGate.vue'

interface MapItem { name: string; created: string; algorithm: string | null; points: number | null; has_scans: boolean; has_trajectory: boolean }
interface Cells { free: number; occupied: number; unknown: number }
interface Result {
  map: string; source: string; sensor_height: number; scans?: number; scans_total?: number; samples?: number
  points: number; ground_points: number; nonground_points: number; cells: Cells; original: Cells
  changes: Record<string, number>; seconds: number
}

const kit = makeKit('ground_seg')
provideKit(kit)
const { t } = kit
const { GlassPanel, InfoTip } = RC().ui
const { jobs, python, ready, refresh } = useJobs(kit)

const maps = ref<MapItem[]>([])
const loadingMaps = ref(false)
const mapName = ref('')
const source = ref<'auto' | 'scans' | 'map'>('auto')
const advanced = ref(false)
const starting = ref(false)
const params = reactive({
  sensor_height: 0, obs_min: 0.1, obs_max: 0.7, min_points: 2, closing: 2, fill: 2, voxel: 0.05,
  min_range: 0.3, max_range: 30, radius: 20, step: 1, scan_stride: 1,
})
const view = reactive<Record<string, string>>({})
const names = reactive<Record<string, string>>({})
const saving = ref('')
const saved = reactive<Record<string, string>>({})
const selectedJob = ref('')

const current = computed(() => maps.value.find((m) => m.name === mapName.value))
const usable = computed(() => !!current.value && (current.value.has_scans || current.value.has_trajectory))

async function loadMaps() {
  loadingMaps.value = true
  try {
    maps.value = await kit.request<MapItem[]>('GET', '/maps', undefined, 60000)
    if ((!mapName.value || !maps.value.some((m) => m.name === mapName.value)) && maps.value.length) {
      mapName.value = maps.value[0].name
    }
  } catch (e) {
    ElMessage.error(kit.errorText(e))
  } finally {
    loadingMaps.value = false
  }
}

watch(current, (m) => {
  if (!m) return
  if (source.value === 'scans' && !m.has_scans) source.value = 'auto'
  if (source.value === 'map' && !m.has_trajectory) source.value = 'auto'
})

async function run() {
  starting.value = true
  try {
    const job = await kit.request<Job>('POST', '/run', { map: mapName.value, source: source.value, params: { ...params } })
    selectedJob.value = job.id
    ElMessage.success(t('form.started'))
    await refresh()
  } catch (e) {
    ElMessage.error(kit.errorText(e))
  } finally {
    starting.value = false
  }
}

async function save(job: Job) {
  const newName = nameFor(job).trim()
  if (!newName) return
  saving.value = job.id
  try {
    const out = await kit.request<{ name: string }>('POST', `/jobs/${job.id}/save`, { name: newName }, 300000)
    saved[job.id] = out.name
    ElMessage.success(t('result.saved', { name: out.name }))
    void loadMaps()
  } catch (e) {
    ElMessage.error(kit.errorText(e))
  } finally {
    saving.value = ''
  }
}

const res = (j: Job) => j.result as unknown as Result | null
const nameFor = (j: Job) => names[j.id] ?? `${String(j.params.map ?? '').slice(0, 56)}_ground`
const phaseText = (j: Job) => (j.phase ? t(`phases.${j.phase}`) : '')
const pct = (n: number, c: Cells) => `${((100 * n) / Math.max(1, c.free + c.occupied + c.unknown)).toFixed(1)}%`
onMounted(loadMaps)
</script>

<template>
  <div class="gs-page">
    <GlassPanel :title="t('title')">
      <p class="gs-intro">{{ t('intro') }}</p>
      <PythonGate :python="python" @changed="refresh" />
    </GlassPanel>

    <GlassPanel :title="t('form.title')">
      <template #extra>
        <ElButton size="small" :loading="loadingMaps" @click="loadMaps">{{ t('common.jobs.refresh') }}</ElButton>
      </template>
      <div v-if="!maps.length" class="gs-dim">{{ t('form.noMaps') }}</div>
      <div class="gs-grid">
        <label class="gs-field wide">
          <span>{{ t('form.map') }}</span>
          <ElSelect v-model="mapName" filterable size="small" :placeholder="t('form.pickMap')" class="gs-map">
            <ElOption v-for="m in maps" :key="m.name" :value="m.name" :label="m.name">
              <span>{{ m.name }}</span>
              <span class="gs-opt">
                <template v-if="m.has_scans">{{ t('form.scans') }}</template>
                <template v-if="m.has_scans && m.has_trajectory"> · </template>
                <template v-if="m.has_trajectory">{{ t('form.trajectory') }}</template>
              </span>
            </ElOption>
          </ElSelect>
        </label>
        <label class="gs-field wide">
          <span>{{ t('form.source') }}</span>
          <InfoTip :content="t('form.sourceTip')" />
          <ElRadioGroup v-model="source" size="small">
            <ElRadioButton value="auto">{{ t('form.auto') }}</ElRadioButton>
            <ElRadioButton value="scans" :disabled="!current?.has_scans">{{ t('form.fromScans') }}</ElRadioButton>
            <ElRadioButton value="map" :disabled="!current?.has_trajectory">{{ t('form.fromMap') }}</ElRadioButton>
          </ElRadioGroup>
        </label>
        <div v-if="current && !usable" class="gs-warn wide">{{ t('form.unusable') }}</div>
        <div v-else-if="current && !current.has_trajectory" class="gs-dim wide">{{ t('form.needTrajectory') }}</div>
        <label class="gs-field">
          <span>{{ t('form.sensorHeight') }}</span><InfoTip :content="t('form.sensorHeightTip')" />
          <ElInputNumber v-model="params.sensor_height" :min="0" :max="5" :step="0.05" :precision="2" size="small" controls-position="right" />
        </label>
        <label class="gs-field">
          <span>{{ t('form.obsMin') }}</span><InfoTip :content="t('form.obsTip')" />
          <ElInputNumber v-model="params.obs_min" :min="-1" :max="3" :step="0.05" :precision="2" size="small" controls-position="right" />
        </label>
        <label class="gs-field">
          <span>{{ t('form.obsMax') }}</span>
          <ElInputNumber v-model="params.obs_max" :min="0" :max="5" :step="0.1" :precision="2" size="small" controls-position="right" />
        </label>
        <label class="gs-field">
          <span>{{ t('form.minPoints') }}</span>
          <ElInputNumber v-model="params.min_points" :min="1" :max="100" size="small" controls-position="right" />
        </label>
        <label class="gs-field">
          <span>{{ t('form.closing') }}</span><InfoTip :content="t('form.closingTip')" />
          <ElInputNumber v-model="params.closing" :min="0" :max="5" size="small" controls-position="right" />
        </label>
        <label class="gs-field">
          <span>{{ t('form.fill') }}</span><InfoTip :content="t('form.fillTip')" />
          <ElInputNumber v-model="params.fill" :min="0" :max="20" :step="0.5" size="small" controls-position="right" />
        </label>
        <label class="gs-field wide">
          <ElSwitch v-model="advanced" size="small" /><span>{{ t('form.advanced') }}</span>
        </label>
        <template v-if="advanced">
          <label class="gs-field">
            <span>{{ t('form.voxel') }}</span>
            <ElInputNumber v-model="params.voxel" :min="0.02" :max="0.5" :step="0.01" :precision="2" size="small" controls-position="right" />
          </label>
          <label class="gs-field">
            <span>{{ t('form.minRange') }}</span>
            <ElInputNumber v-model="params.min_range" :min="0" :max="10" :step="0.1" size="small" controls-position="right" />
          </label>
          <label class="gs-field">
            <span>{{ t('form.maxRange') }}</span>
            <ElInputNumber v-model="params.max_range" :min="5" :max="120" :step="5" size="small" controls-position="right" />
          </label>
          <label class="gs-field">
            <span>{{ t('form.radius') }}</span>
            <ElInputNumber v-model="params.radius" :min="5" :max="60" :step="5" size="small" controls-position="right" />
          </label>
          <label class="gs-field">
            <span>{{ t('form.step') }}</span>
            <ElInputNumber v-model="params.step" :min="0.2" :max="10" :step="0.5" size="small" controls-position="right" />
          </label>
          <label class="gs-field">
            <span>{{ t('form.scanStride') }}</span>
            <ElInputNumber v-model="params.scan_stride" :min="1" :max="50" size="small" controls-position="right" />
          </label>
        </template>
      </div>
      <ElButton type="primary" class="gs-run" :loading="starting" :disabled="!ready || !usable" @click="run">
        {{ t('form.run') }}
      </ElButton>
    </GlassPanel>

    <GlassPanel :title="t('common.jobs.title')">
      <JobList :jobs="jobs" :phase-text="phaseText" :selected="selectedJob" @changed="refresh" @select="(id: string) => (selectedJob = id)">
        <template #result="{ job }">
          <div v-if="res(job)" class="gs-result">
            <div class="gs-kpis">
              <div class="gs-kpi"><span>{{ t('result.sensorHeight') }}</span><b class="mono">{{ `${res(job)!.sensor_height} m` }}</b></div>
              <div class="gs-kpi"><span>{{ t('result.ground') }}</span><b class="mono">{{ res(job)!.ground_points.toLocaleString() }}</b></div>
              <div class="gs-kpi"><span>{{ t('result.nonground') }}</span><b class="mono">{{ res(job)!.nonground_points.toLocaleString() }}</b></div>
              <div class="gs-kpi"><span>{{ res(job)!.source === 'scans' ? t('result.scans') : t('result.samples') }}</span><b class="mono">{{ String(res(job)!.source === 'scans' ? res(job)!.scans : res(job)!.samples) }}</b></div>
              <div class="gs-kpi"><span>{{ t('result.newObstacles') }}</span><b class="mono">{{ (res(job)!.changes.free_to_occupied + res(job)!.changes.unknown_to_occupied).toLocaleString() }}</b></div>
              <div class="gs-kpi"><span>{{ t('result.removedObstacles') }}</span><b class="mono">{{ (res(job)!.changes.occupied_to_free + res(job)!.changes.occupied_to_unknown).toLocaleString() }}</b></div>
            </div>
            <div class="gs-cells mono">
              <span>{{ t('result.grid') }}: {{ t('result.free') }} {{ pct(res(job)!.cells.free, res(job)!.cells) }} ·
                {{ t('result.occupied') }} {{ pct(res(job)!.cells.occupied, res(job)!.cells) }} ·
                {{ t('result.unknown') }} {{ pct(res(job)!.cells.unknown, res(job)!.cells) }}</span>
              <span>{{ t('result.before') }}: {{ t('result.free') }} {{ pct(res(job)!.original.free, res(job)!.original) }} ·
                {{ t('result.occupied') }} {{ pct(res(job)!.original.occupied, res(job)!.original) }} ·
                {{ t('result.unknown') }} {{ pct(res(job)!.original.unknown, res(job)!.original) }}</span>
              <span>{{ t('result.seconds', { s: res(job)!.seconds }) }}</span>
            </div>
            <ElRadioGroup :model-value="view[job.id] ?? 'grid'" size="small" @update:model-value="(v: unknown) => (view[job.id] = String(v))">
              <ElRadioButton value="grid">{{ t('result.grid') }}</ElRadioButton>
              <ElRadioButton value="diff">{{ t('result.diff') }}</ElRadioButton>
              <ElRadioButton value="labels">{{ t('result.labels') }}</ElRadioButton>
            </ElRadioGroup>
            <div v-if="(view[job.id] ?? 'grid') !== 'grid'" class="gs-dim">
              {{ t(view[job.id] === 'diff' ? 'result.diffLegend' : 'result.labelsLegend') }}
            </div>
            <div class="gs-image">
              <img :src="kit.fileUrl(job.id, `${view[job.id] ?? 'grid'}.png`)" alt="">
            </div>
            <div class="gs-actions">
              <span class="gs-dim">{{ t('result.download') }}:</span>
              <a :href="kit.fileUrl(job.id, 'ground.pcd')" download>{{ t('result.groundPcd') }}</a>
              <a :href="kit.fileUrl(job.id, 'nonground.pcd')" download>{{ t('result.nongroundPcd') }}</a>
              <a :href="kit.fileUrl(job.id, 'result.json')" download>result.json</a>
            </div>
            <div class="gs-actions">
              <span>{{ t('result.saveName') }}</span>
              <ElInput :model-value="nameFor(job)" size="small" class="gs-name" maxlength="64" @update:model-value="(v: string) => (names[job.id] = v)" />
              <ElButton size="small" type="primary" :loading="saving === job.id" :disabled="!nameFor(job).trim()" @click="save(job)">{{ t('result.save') }}</ElButton>
              <a v-if="saved[job.id]" href="#/maps"><ElButton size="small">{{ t('result.openMaps') }}</ElButton></a>
            </div>
          </div>
        </template>
      </JobList>
    </GlassPanel>
  </div>
</template>

<style scoped>
.gs-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.gs-intro {
  margin: 0 0 10px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink-2);
}
.gs-dim {
  font-size: 12.5px;
  color: var(--ink-3);
}
.gs-warn {
  font-size: 12.5px;
  color: color-mix(in srgb, var(--warn) 70%, var(--ink));
}
.gs-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px 20px;
  align-items: center;
}
.gs-field {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--ink);
}
.gs-field > span:first-child {
  white-space: nowrap;
}
.gs-field .el-input-number {
  width: 120px;
  margin-left: auto;
}
.wide {
  grid-column: 1 / -1;
}
.gs-map {
  width: min(420px, 100%);
}
.gs-opt {
  float: right;
  margin-left: 16px;
  font-size: 12px;
  color: var(--ink-3);
}
.gs-run {
  margin-top: 14px;
}
.gs-result {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.gs-kpis {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 8px;
}
.gs-kpi {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border: 1px solid var(--hairline);
  border-radius: 8px;
  font-size: 12px;
  color: var(--ink-3);
}
.gs-kpi b {
  font-size: 16px;
  font-weight: 600;
  color: var(--ink);
}
.gs-cells {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
  color: var(--ink-2);
}
.gs-image {
  max-height: 560px;
  overflow: auto;
  border: 1px solid var(--hairline);
  border-radius: 8px;
  background: var(--canvas-bg);
}
.gs-image img {
  display: block;
  max-width: 100%;
  image-rendering: pixelated;
}
.gs-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  font-size: 12.5px;
}
.gs-actions a {
  color: var(--accent);
}
.gs-name {
  width: min(280px, 100%);
}
</style>
