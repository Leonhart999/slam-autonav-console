<!-- Bag converter page: pick a ROS1 bag (or upload one), choose topics, convert into a ROS2 bag that the Playback
     page finds. Jobs run in the plugin backend (worker.py); this page only starts and watches them. -->
<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import {
  ElButton, ElInput, ElMessage, ElMessageBox, ElProgress, ElRadioButton, ElRadioGroup, ElSwitch, ElTable,
  ElTableColumn, ElTag,
} from 'element-plus'
import { RC } from '../../../_shared/web/sdk'
import { fmtSize, fmtTime, makeKit, provideKit, type Job } from '../../../_shared/web/kit'
import { useJobs } from '../../../_shared/web/useJobs'
import JobList from '../../../_shared/web/JobList.vue'
import PythonGate from '../../../_shared/web/PythonGate.vue'

interface BagFile { path: string; name: string; folder: string; size: number; mtime: number; uploaded: boolean }
interface Sources { upload_dir: string; files: BagFile[]; free: number; storage: string }
interface Topic { topic: string; type: string; count: number; hz: number | null; livox: boolean; image: boolean }
interface Inspect { path: string; duration: number; messages: number; size: number; topics: Topic[] }
interface Result {
  output: string; name: string; size: number; seconds: number; messages: number
  topics: { topic: string; type: string; source_type: string; count: number; points: number | null }[]
}

const kit = makeKit('bag_convert')
provideKit(kit)
const { t } = kit
const { GlassPanel, InfoTip } = RC().ui
const { jobs, python, ready, refresh, onFinished } = useJobs(kit)

const sources = ref<Sources | null>(null)
const loading = ref(false)
const current = ref<BagFile | null>(null)
const info = ref<Inspect | null>(null)
const inspecting = ref(false)
const inspectError = ref('')
const selected = ref<Topic[]>([])
const livox = ref(true)
const outName = ref('')
const storage = ref('sqlite3')
const starting = ref(false)
const upload = ref<{ name: string; pct: number } | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const table = ref<{ toggleRowSelection: (row: Topic, selected?: boolean) => void } | null>(null)
const selectedJob = ref('')

const hasLivox = computed(() => info.value?.topics.some((x) => x.livox) ?? false)
const lowSpace = computed(() => !!sources.value && !!current.value && sources.value.free < current.value.size * 1.1)

async function loadSources() {
  loading.value = true
  try {
    sources.value = await kit.request<Sources>('GET', '/sources')
    storage.value = sources.value.storage
    if (current.value && !sources.value.files.some((f) => f.path === current.value?.path)) pick(null)
  } catch (e) {
    ElMessage.error(kit.errorText(e))
  } finally {
    loading.value = false
  }
}

async function pick(f: BagFile | null) {
  current.value = f
  info.value = null
  inspectError.value = ''
  selected.value = []
  if (!f) return
  outName.value = f.name.replace(/\.bag$/, '').replace(/[^A-Za-z0-9_.-]/g, '_').replace(/^\.+/, '').slice(0, 58) + '_ros2'
  if (!ready.value) return
  inspecting.value = true
  try {
    const out = await kit.request<Inspect>('POST', '/inspect', { path: f.path }, 200000)
    if (current.value?.path !== f.path) return
    info.value = out
    livox.value = out.topics.some((x) => x.livox)
    await nextTick()
    for (const row of out.topics) table.value?.toggleRowSelection(row, !row.image)
  } catch (e) {
    inspectError.value = kit.errorText(e)
  } finally {
    inspecting.value = false
  }
}

function onCurrent(row: unknown) {
  if (row) pick(row as BagFile)
}

async function start() {
  if (!current.value || !info.value) return
  if (!selected.value.length) {
    ElMessage.warning(t('convert.noTopic'))
    return
  }
  starting.value = true
  try {
    const all = selected.value.length === info.value.topics.length
    const job = await kit.request<Job>('POST', '/convert', {
      path: current.value.path, name: outName.value.trim(), storage: storage.value, livox: livox.value,
      topics: all ? [] : selected.value.map((x) => x.topic),
    })
    selectedJob.value = job.id
    ElMessage.success(t('convert.started'))
    await refresh()
  } catch (e) {
    ElMessage.error(kit.errorText(e))
  } finally {
    starting.value = false
  }
}

function chooseFile() {
  fileInput.value?.click()
}

function uploadFile(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  upload.value = { name: file.name, pct: 0 }
  const xhr = new XMLHttpRequest()
  xhr.open('PUT', `${kit.base}/upload?name=${encodeURIComponent(file.name)}&size=${file.size}`)
  xhr.upload.onprogress = (e) => {
    if (upload.value && e.lengthComputable) upload.value.pct = Math.round((e.loaded / e.total) * 100)
  }
  xhr.onload = async () => {
    upload.value = null
    if (xhr.status >= 200 && xhr.status < 300) {
      ElMessage.success(t('sources.uploaded', { name: file.name }))
      await loadSources()
      const f = sources.value?.files.find((x) => x.uploaded && x.name === file.name)
      if (f) pick(f)
      return
    }
    try {
      const detail = JSON.parse(xhr.responseText).detail
      ElMessage.error(kit.codedText(detail))
    } catch {
      ElMessage.error(`HTTP ${xhr.status}`)
    }
  }
  xhr.onerror = () => {
    upload.value = null
    ElMessage.error(t('errors.unknown', { reason: 'network' }))
  }
  xhr.send(file)
}

async function removeUpload(row: unknown) {
  const f = row as BagFile
  try {
    await ElMessageBox.confirm(t('sources.removeConfirm', { name: f.name }), t('sources.remove'), { type: 'warning' })
  } catch {
    return
  }
  try {
    await kit.request('DELETE', `/upload?path=${encodeURIComponent(f.path)}`)
    await loadSources()
  } catch (e) {
    ElMessage.error(kit.errorText(e))
  }
}

const phaseText = (j: Job) => (j.phase ? t(`phases.${j.phase}`) : '')
const asResult = (j: Job) => j.result as unknown as Result | null

onFinished((j) => {
  if (j.state === 'succeeded') loadSources()
})
watch(ready, (ok) => {
  if (ok && current.value && !info.value && !inspecting.value) void pick(current.value)
})
onMounted(loadSources)
</script>

<template>
  <div class="bc-page">
    <GlassPanel :title="t('title')">
      <p class="bc-intro">{{ t('intro') }}</p>
      <PythonGate :python="python" @changed="refresh" />
    </GlassPanel>

    <GlassPanel :title="t('sources.title')" :tip="t('sources.tip')">
      <template #extra>
        <ElButton size="small" :loading="loading" @click="loadSources">{{ t('sources.refresh') }}</ElButton>
        <ElButton size="small" type="primary" :disabled="!!upload" @click="chooseFile">{{ t('sources.upload') }}</ElButton>
        <input ref="fileInput" type="file" accept=".bag" class="bc-hidden" @change="uploadFile">
      </template>
      <div v-if="upload" class="bc-upload">
        <span>{{ t('sources.uploading', { name: upload.name }) }}</span>
        <ElProgress :percentage="upload.pct" :stroke-width="6" />
      </div>
      <ElTable
        :data="sources?.files ?? []" highlight-current-row size="small" max-height="320" :empty-text="t('sources.empty')"
        row-key="path" @current-change="onCurrent"
      >
        <ElTableColumn :label="t('sources.name')" prop="name" min-width="220" show-overflow-tooltip />
        <ElTableColumn :label="t('sources.folder')" min-width="240" show-overflow-tooltip>
          <template #default="{ row }"><span class="mono bc-dim">{{ row.folder }}</span></template>
        </ElTableColumn>
        <ElTableColumn :label="t('sources.size')" width="100" align="right">
          <template #default="{ row }"><span class="mono">{{ fmtSize(row.size) }}</span></template>
        </ElTableColumn>
        <ElTableColumn :label="t('sources.modified')" width="150">
          <template #default="{ row }"><span class="mono">{{ fmtTime(row.mtime) }}</span></template>
        </ElTableColumn>
        <ElTableColumn width="80" align="center">
          <template #default="{ row }">
            <ElButton v-if="row.uploaded" size="small" text @click.stop="removeUpload(row)">{{ t('sources.remove') }}</ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
      <div v-if="sources" class="bc-free mono">{{ t('sources.free', { free: fmtSize(sources.free) }) }}</div>
    </GlassPanel>

    <GlassPanel :title="t('convert.title')">
      <div v-if="!current" class="bc-dim">{{ t('convert.pick') }}</div>
      <template v-else>
        <div class="bc-file mono">{{ current.path }}</div>
        <div v-if="inspecting" class="bc-dim">{{ t('convert.inspecting') }}</div>
        <div v-if="inspectError" class="bc-error">{{ inspectError }}</div>
        <template v-if="info">
          <div class="bc-dim mono">
            {{ t('convert.summary', { duration: info.duration.toFixed(1), messages: info.messages, size: fmtSize(info.size) }) }}
          </div>
          <ElTable
            ref="table" :data="info.topics" size="small" row-key="topic" class="bc-topics"
            @selection-change="(rows: Topic[]) => (selected = rows)"
          >
            <ElTableColumn type="selection" width="40" />
            <ElTableColumn :label="t('convert.topic')" prop="topic" min-width="240" show-overflow-tooltip />
            <ElTableColumn :label="t('convert.type')" min-width="250">
              <template #default="{ row }">
                <div class="bc-type-cell">
                  <span class="mono bc-type">{{ row.type }}</span>
                  <ElTag v-if="row.livox && livox" size="small" type="success" class="bc-tag">{{ t('convert.livoxTag') }}</ElTag>
                  <ElTag v-if="row.image" size="small" type="info" class="bc-tag">{{ t('convert.imageTag') }}</ElTag>
                </div>
              </template>
            </ElTableColumn>
            <ElTableColumn :label="t('convert.count')" prop="count" width="90" align="right" />
            <ElTableColumn :label="t('convert.hz')" width="90" align="right">
              <template #default="{ row }"><span class="mono">{{ row.hz ?? '–' }}</span></template>
            </ElTableColumn>
          </ElTable>
          <div class="bc-dim">{{ t('convert.selected', { n: selected.length, total: info.topics.length }) }}</div>
          <div class="bc-form">
            <label v-if="hasLivox" class="bc-field">
              <ElSwitch v-model="livox" />
              <span>{{ t('convert.livox') }}</span>
              <InfoTip :content="t('convert.livoxTip')" />
            </label>
            <label class="bc-field">
              <span>{{ t('convert.name') }}</span>
              <InfoTip :content="t('convert.nameTip', { dir: sources?.upload_dir ?? '' })" />
              <ElInput v-model="outName" size="small" class="bc-name" maxlength="64" />
            </label>
            <label class="bc-field">
              <span>{{ t('convert.storage') }}</span>
              <ElRadioGroup v-model="storage" size="small">
                <ElRadioButton value="sqlite3">sqlite3</ElRadioButton>
                <ElRadioButton value="mcap">MCAP</ElRadioButton>
              </ElRadioGroup>
            </label>
            <ElButton type="primary" :loading="starting" :disabled="!ready || !selected.length" @click="start">
              {{ t('convert.start') }}
            </ElButton>
          </div>
          <div v-if="lowSpace" class="bc-warn">{{ t('sources.lowSpace', { size: fmtSize(current.size) }) }}</div>
        </template>
      </template>
    </GlassPanel>

    <GlassPanel :title="t('common.jobs.title')">
      <JobList :jobs="jobs" :phase-text="phaseText" :selected="selectedJob" @changed="refresh" @select="(id: string) => (selectedJob = id)">
        <template #result="{ job }">
          <div v-if="asResult(job)" class="bc-result">
            <div class="bc-kv"><span>{{ t('result.output') }}</span><span class="mono">{{ asResult(job)!.output }}</span></div>
            <div class="bc-kv">
              <span>{{ t('result.size') }}</span>
              <span class="mono">{{ fmtSize(asResult(job)!.size) }} · {{ asResult(job)!.messages }} {{ t('result.messages') }}
                · {{ t('result.seconds', { s: asResult(job)!.seconds }) }}</span>
            </div>
            <div class="bc-kv">
              <span>{{ t('result.topics') }}</span>
              <div class="bc-topic-list mono">
                <div v-for="tp in asResult(job)!.topics" :key="tp.topic">
                  {{ tp.topic }} · {{ tp.type }}<template v-if="tp.type !== tp.source_type"> ← {{ tp.source_type }}</template>
                  · {{ tp.count }}<template v-if="tp.points"> · {{ t('result.points', { n: tp.points }) }}</template>
                </div>
              </div>
            </div>
            <a href="#/playback"><ElButton size="small" type="primary">{{ t('result.play') }}</ElButton></a>
          </div>
        </template>
      </JobList>
    </GlassPanel>
  </div>
</template>

<style scoped>
.bc-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.bc-intro {
  margin: 0 0 10px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink-2);
}
.bc-hidden {
  display: none;
}
.bc-upload {
  margin-bottom: 10px;
  font-size: 12.5px;
  color: var(--ink-2);
}
.bc-dim {
  font-size: 12.5px;
  color: var(--ink-3);
}
.bc-free {
  margin-top: 6px;
  font-size: 12px;
  color: var(--ink-3);
}
.bc-file {
  margin-bottom: 6px;
  font-size: 12.5px;
  color: var(--ink);
  overflow-wrap: anywhere;
}
.bc-error {
  font-size: 12.5px;
  color: color-mix(in srgb, var(--bad) 80%, var(--ink));
}
.bc-warn {
  margin-top: 8px;
  font-size: 12.5px;
  color: color-mix(in srgb, var(--warn) 70%, var(--ink));
}
.bc-topics {
  margin: 8px 0 4px;
}
.bc-type-cell {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 6px;
}
.bc-type {
  font-size: 12px;
  overflow-wrap: anywhere;
}
.bc-tag {
  margin-left: 0;
}
.bc-form {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 22px;
  margin-top: 12px;
}
.bc-field {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--ink);
}
.bc-name {
  width: 260px;
}
.bc-result {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12.5px;
}
.bc-kv {
  display: grid;
  grid-template-columns: 70px 1fr;
  gap: 8px;
  color: var(--ink-2);
}
.bc-kv .mono {
  color: var(--ink);
  overflow-wrap: anywhere;
}
.bc-topic-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  color: var(--ink);
}
</style>
