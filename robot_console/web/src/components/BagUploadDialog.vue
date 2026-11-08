<!-- Playback page: upload a rosbag2 bag from this computer. A bag is a folder (metadata.yaml + .db3 / .mcap files,
     often several GB), so the dialog picks a folder and sends every file of the bag as its own request
     (robot_console/bag_files.py): begin (name, size, free disk space) → one PUT per file → finish (metadata check,
     folder renamed into the upload folder). A failed or cancelled upload is removed on the backend. -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import { fmtBytes } from '@/format'
import { errorText } from '@/messages'
import type { PlaybackStorage } from '@/mapping/stream'

const props = defineProps<{ modelValue: boolean; storage: PlaybackStorage | null; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; uploaded: [path: string] }>()
const { t } = useI18n()

const NAME_RE = /^[A-Za-z0-9_.-]{1,64}$/
const SUFFIXES = ['.yaml', '.db3', '.mcap']
const MARGIN = 2 * 1024 ** 3 // bytes that must stay free after the upload (same as the backend)
const busy = ref(false)
const cancelled = ref(false)
const open = computed({
  get: () => props.modelValue,
  set: (v: boolean) => { if (!busy.value) emit('update:modelValue', v) },
})
const folderInput = ref<HTMLInputElement | null>(null)
function browse() {
  const el = folderInput.value
  if (!el) return
  el.webkitdirectory = true // pick a folder (Chromium / Edge / Firefox)
  el.click()
}
let abort: AbortController | null = null
function stop() {
  cancelled.value = true
  abort?.abort()
}
const files = ref<File[]>([]) // the bag's files: metadata.yaml and the storage files next to it
const folder = ref('')
const pickError = ref('')
const name = ref('')
const error = ref('')
const sent = ref(0)
const current = ref('')

watch(() => props.modelValue, (v) => {
  if (!v || busy.value) return
  files.value = []
  folder.value = ''
  pickError.value = ''
  name.value = ''
  error.value = ''
  sent.value = 0
  if (folderInput.value) folderInput.value.value = ''
})

const total = computed(() => files.value.reduce((n, f) => n + f.size, 0))
const nameInvalid = computed(() => !!name.value && (!NAME_RE.test(name.value) || name.value.startsWith('.')))
const pct = computed(() => (total.value ? Math.min(100, Math.round((100 * sent.value) / total.value)) : 0))
// Free space of every checked disk after the upload (the backend refuses below 2 GB)
const space = computed(() => (props.storage?.free ?? []).map((f) => ({ ...f, after: f.free - total.value })))
const tooBig = computed(() => space.value.some((s) => s.after < MARGIN))
const canSubmit = computed(() =>
  !props.disabled && !busy.value && files.value.length > 0 && NAME_RE.test(name.value) && !name.value.startsWith('.') && !tooBig.value,
)

/** The picked folder may be the bag itself or contain it: use the folder that holds metadata.yaml. */
function pick(event: Event) {
  const list = Array.from((event.target as HTMLInputElement).files ?? [])
  pickError.value = ''
  files.value = []
  const metas = list.filter((f) => f.name === 'metadata.yaml')
  if (metas.length !== 1) {
    pickError.value = metas.length ? t('playback.upload.manyBags', { n: metas.length }) : t('playback.upload.noMeta')
    return
  }
  const rel = metas[0].webkitRelativePath || metas[0].name
  const dir = rel.includes('/') ? rel.slice(0, rel.lastIndexOf('/') + 1) : ''
  files.value = list.filter((f) => {
    const r = f.webkitRelativePath || f.name
    return r.startsWith(dir) && !r.slice(dir.length).includes('/') && SUFFIXES.some((s) => f.name.toLowerCase().endsWith(s))
  })
  folder.value = dir.replace(/\/$/, '') || metas[0].name
  const base = (dir.replace(/\/$/, '').split('/').pop() ?? '').replace(/[^A-Za-z0-9_.-]+/g, '_').replace(/^\.+/, '')
  name.value = base.slice(0, 64)
}

async function submit() {
  if (!canSubmit.value) return
  busy.value = true
  cancelled.value = false
  error.value = ''
  sent.value = 0
  let token = ''
  abort = new AbortController()
  try {
    token = (await api.beginBagUpload(name.value, total.value, files.value.length)).token
    let done = 0
    for (const f of files.value) {
      if (cancelled.value) throw new Error('cancelled')
      current.value = f.name
      await api.putBagFile(token, f, (p) => (sent.value = done + p.loaded), abort.signal)
      done += f.size
      sent.value = done
    }
    current.value = ''
    const r = await api.finishBagUpload(token)
    ElMessage.success(t('playback.upload.success', { name: r.name, size: fmtBytes(r.size) }))
    busy.value = false
    emit('uploaded', r.path)
    emit('update:modelValue', false)
  } catch (e) {
    if (token) api.cancelBagUpload(token).catch(() => undefined)
    error.value = cancelled.value ? t('playback.upload.cancelled') : errorText(e)
    busy.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="open" class="import-dialog" :title="t('playback.upload.title')"
    width="min(600px, calc(100vw - 32px))" append-to-body destroy-on-close
    :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy"
  >
    <p class="import-note">{{ t('playback.upload.desc') }}</p>
    <el-form label-position="top" :disabled="busy" class="import-form" @submit.prevent="submit">
      <el-form-item :label="t('playback.upload.folder')" required>
        <div class="import-file">
          <input ref="folderInput" type="file" multiple hidden :aria-label="t('playback.upload.folder')" @change="pick" />
          <el-button @click="browse">{{ t('playback.upload.browse') }}</el-button>
          <span class="import-filename" :title="folder">
            {{ files.length ? t('playback.upload.picked', { folder, n: files.length, size: fmtBytes(total) }) : t('uploads.noFile') }}
          </span>
        </div>
      </el-form-item>
      <p v-if="pickError" class="import-note bad">{{ pickError }}</p>
      <el-form-item :label="t('uploads.name')" :error="nameInvalid ? t('playback.upload.nameRule') : undefined" required>
        <el-input v-model="name" maxlength="64" :aria-label="t('uploads.name')">
          <template #prefix><span class="mono prefix">{{ storage?.upload_dir }}/</span></template>
        </el-input>
      </el-form-item>
      <p class="import-note">{{ t('playback.upload.nameRule') }}</p>
    </el-form>
    <ul v-if="files.length" class="space">
      <li v-for="s in space" :key="s.path" :class="{ bad: s.after < MARGIN }">
        {{ t('playback.upload.space', { path: s.path, free: fmtBytes(s.free), after: fmtBytes(Math.max(0, s.after)) }) }}
      </li>
    </ul>
    <p v-if="files.length && tooBig" class="import-note bad">{{ t('playback.upload.noSpace') }}</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" />
    <div v-if="busy" class="import-progress" role="status" aria-live="polite">
      <el-progress :percentage="pct" :stroke-width="6" />
      <p>{{ current ? t('playback.upload.sending', { file: current, sent: fmtBytes(sent), total: fmtBytes(total) }) : t('playback.upload.checking') }}</p>
    </div>
    <template #footer>
      <el-button v-if="busy" @click="stop">{{ t('playback.upload.stop') }}</el-button>
      <el-button v-else @click="open = false">{{ t('uploads.cancel') }}</el-button>
      <el-button type="primary" :loading="busy" :disabled="!canSubmit" @click="submit">{{ t('playback.upload.start') }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.prefix {
  font-size: 11.5px;
  color: var(--ink-3);
}
.space {
  margin: 0 0 10px;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--ink-2);
  word-break: break-all;
}
.space .bad,
.bad {
  color: var(--bad);
}
</style>
