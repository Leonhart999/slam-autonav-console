<!-- Dynamic object removal page: pick a map with raw scans, run DUFOMap in a job, look at what it would remove,
     save the cleaned cloud as a new map version. -->
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  ElButton, ElInput, ElInputNumber, ElMessage, ElOption, ElRadioButton, ElRadioGroup, ElSelect,
} from 'element-plus'
import { RC } from '../../../_shared/web/sdk'
import { makeKit, provideKit, type Job } from '../../../_shared/web/kit'
import { useJobs } from '../../../_shared/web/useJobs'
import JobList from '../../../_shared/web/JobList.vue'
import PythonGate from '../../../_shared/web/PythonGate.vue'

interface MapItem { name: string; created: string; points: number | null; has_grid: boolean; has_scans: boolean }
interface Result {
  map: string; scans: number; scans_total: number; points: number; static: number; dynamic: number
  dynamic_ratio: number; seconds: number
}

const kit = makeKit('dynamic_removal')
provideKit(kit)
const { t } = kit
const { GlassPanel, InfoTip } = RC().ui
const { jobs, python, ready, refresh } = useJobs(kit)

const maps = ref<MapItem[]>([])
const loadingMaps = ref(false)
const mapName = ref('')
const starting = ref(false)
const params = reactive({ resolution: 0.1, d_s: 0.2, d_p: 1, min_range: 0.3, max_range: 0, scan_stride: 1 })
const names = reactive<Record<string, string>>({})
const grids = reactive<Record<string, string>>({})
const saving = ref('')
const saved = reactive<Record<string, string>>({})
const selectedJob = ref('')
const current = computed(() => maps.value.find((m) => m.name === mapName.value))

async function loadMaps() {
  loadingMaps.value = true
  try {
    maps.value = await kit.request<MapItem[]>('GET', '/maps', undefined, 60000)
    if (!mapName.value || !maps.value.some((m) => m.name === mapName.value)) {
      mapName.value = (maps.value.find((m) => m.has_scans) ?? maps.value[0])?.name ?? ''
    }
  } catch (e) {
    ElMessage.error(kit.errorText(e))
  } finally {
    loadingMaps.value = false
  }
}

async function run() {
  starting.value = true
  try {
    const job = await kit.request<Job>('POST', '/run', { map: mapName.value, params: { ...params } })
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
    const out = await kit.request<{ name: string }>('POST', `/jobs/${job.id}/save`,
      { name: newName, grid: grids[job.id] ?? 'regrid' }, 600000)
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
const nameFor = (j: Job) => names[j.id] ?? `${String(j.params.map ?? '').slice(0, 57)}_static`
const phaseText = (j: Job) => (j.phase ? t(`phases.${j.phase}`) : '')
onMounted(loadMaps)
</script>

<template>
  <div class="dr-page">
    <GlassPanel :title="t('title')">
      <p class="dr-intro">{{ t('intro') }}</p>
      <PythonGate :python="python" @changed="refresh" />
    </GlassPanel>

    <GlassPanel :title="t('form.title')">
      <template #extra>
        <ElButton size="small" :loading="loadingMaps" @click="loadMaps">{{ t('common.jobs.refresh') }}</ElButton>
      </template>
      <div v-if="!maps.length" class="dr-dim">{{ t('form.noMaps') }}</div>
      <div class="dr-grid">
        <label class="dr-field wide">
          <span>{{ t('form.map') }}</span>
          <ElSelect v-model="mapName" filterable size="small" :placeholder="t('form.pickMap')" class="dr-map">
            <ElOption v-for="m in maps" :key="m.name" :value="m.name" :label="m.name">
              <span>{{ m.name }}</span>
              <span v-if="m.has_scans" class="dr-opt">{{ t('form.scans') }}</span>
            </ElOption>
          </ElSelect>
        </label>
        <div v-if="current && !current.has_scans" class="dr-warn wide">{{ t('form.noScans') }}</div>
        <label class="dr-field">
          <span>{{ t('form.resolution') }}</span><InfoTip :content="t('form.resolutionTip')" />
          <ElInputNumber v-model="params.resolution" :min="0.05" :max="0.5" :step="0.05" :precision="2" size="small" controls-position="right" />
        </label>
        <label class="dr-field">
          <span>{{ t('form.ds') }}</span><InfoTip :content="t('form.dsTip')" />
          <ElInputNumber v-model="params.d_s" :min="0" :max="1" :step="0.05" :precision="2" size="small" controls-position="right" />
        </label>
        <label class="dr-field">
          <span>{{ t('form.dp') }}</span><InfoTip :content="t('form.dpTip')" />
          <ElInputNumber v-model="params.d_p" :min="0" :max="5" size="small" controls-position="right" />
        </label>
        <label class="dr-field">
          <span>{{ t('form.minRange') }}</span>
          <ElInputNumber v-model="params.min_range" :min="0" :max="5" :step="0.1" :precision="1" size="small" controls-position="right" />
        </label>
        <label class="dr-field">
          <span>{{ t('form.maxRange') }}</span><InfoTip :content="t('form.maxRangeTip')" />
          <ElInputNumber v-model="params.max_range" :min="0" :max="200" :step="5" size="small" controls-position="right" />
        </label>
        <label class="dr-field">
          <span>{{ t('form.stride') }}</span>
          <ElInputNumber v-model="params.scan_stride" :min="1" :max="20" size="small" controls-position="right" />
        </label>
      </div>
      <ElButton type="primary" class="dr-run" :loading="starting" :disabled="!ready || !current?.has_scans" @click="run">
        {{ t('form.run') }}
      </ElButton>
    </GlassPanel>

    <GlassPanel :title="t('common.jobs.title')">
      <JobList :jobs="jobs" :phase-text="phaseText" :selected="selectedJob" @changed="refresh" @select="(id: string) => (selectedJob = id)">
        <template #result="{ job }">
          <div v-if="res(job)" class="dr-result">
            <div class="dr-kpis">
              <div class="dr-kpi"><span>{{ t('result.scans') }}</span><b class="mono">{{ res(job)!.scans }} / {{ res(job)!.scans_total }}</b></div>
              <div class="dr-kpi"><span>{{ t('result.points') }}</span><b class="mono">{{ res(job)!.points.toLocaleString() }}</b></div>
              <div class="dr-kpi"><span>{{ t('result.dynamic') }}</span><b class="mono">{{ res(job)!.dynamic.toLocaleString() }}</b></div>
              <div class="dr-kpi"><span>{{ t('result.ratio') }}</span><b class="mono">{{ (res(job)!.dynamic_ratio * 100).toFixed(2) }}%</b></div>
            </div>
            <div class="dr-dim">{{ t('result.legend') }} · {{ t('result.seconds', { s: res(job)!.seconds }) }}</div>
            <div class="dr-image"><img :src="kit.fileUrl(job.id, 'preview.png')" alt=""></div>
            <div class="dr-actions">
              <span class="dr-dim">{{ t('result.download') }}:</span>
              <a :href="kit.fileUrl(job.id, 'static.pcd')" download>{{ t('result.staticPcd') }}</a>
              <a :href="kit.fileUrl(job.id, 'dynamic.pcd')" download>{{ t('result.dynamicPcd') }}</a>
              <a :href="kit.fileUrl(job.id, 'result.json')" download>result.json</a>
            </div>
            <div class="dr-actions">
              <span>{{ t('result.grid') }}</span>
              <ElRadioGroup :model-value="grids[job.id] ?? 'regrid'" size="small" @update:model-value="(v: unknown) => (grids[job.id] = String(v))">
                <ElRadioButton value="regrid">{{ t('result.regrid') }}</ElRadioButton>
                <ElRadioButton value="keep">{{ t('result.keep') }}</ElRadioButton>
              </ElRadioGroup>
            </div>
            <div class="dr-actions">
              <span>{{ t('result.saveName') }}</span>
              <ElInput :model-value="nameFor(job)" size="small" class="dr-name" maxlength="64" @update:model-value="(v: string) => (names[job.id] = v)" />
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
.dr-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.dr-intro {
  margin: 0 0 10px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink-2);
}
.dr-dim {
  font-size: 12.5px;
  color: var(--ink-3);
}
.dr-warn {
  font-size: 12.5px;
  line-height: 1.6;
  color: color-mix(in srgb, var(--warn) 70%, var(--ink));
}
.dr-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px 20px;
  align-items: center;
}
.dr-field {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--ink);
}
.dr-field > span:first-child {
  white-space: nowrap;
}
.dr-field .el-input-number {
  width: 120px;
  margin-left: auto;
}
.wide {
  grid-column: 1 / -1;
}
.dr-map {
  width: min(420px, 100%);
}
.dr-opt {
  float: right;
  margin-left: 16px;
  font-size: 12px;
  color: var(--ink-3);
}
.dr-run {
  margin-top: 14px;
}
.dr-result {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.dr-kpis {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 8px;
}
.dr-kpi {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border: 1px solid var(--hairline);
  border-radius: 8px;
  font-size: 12px;
  color: var(--ink-3);
}
.dr-kpi b {
  font-size: 16px;
  font-weight: 600;
  color: var(--ink);
}
.dr-image {
  max-height: 560px;
  overflow: auto;
  border: 1px solid var(--hairline);
  border-radius: 8px;
  background: var(--canvas-bg);
}
.dr-image img {
  display: block;
  max-width: 100%;
}
.dr-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  font-size: 12.5px;
}
.dr-actions a {
  color: var(--accent);
}
.dr-name {
  width: min(280px, 100%);
}
</style>
