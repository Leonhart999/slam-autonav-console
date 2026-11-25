<!-- Import a Google Cloud service account key (JSON) into the CPA engine for Vertex AI. The file is read in the browser,
     checked (type service_account, project and client email shown) and sent to the console backend only. -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElButton, ElDialog, ElInput } from 'element-plus'
import { vertexImport } from './api'
import { RC, t } from './sdk'
import Icon from './Icon.vue'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [v: boolean]; done: [] }>()

const MAX = 64 * 1024
const file = ref<{ name: string; content: string; project: string; email: string } | null>(null)
const error = ref('')
const prefix = ref('')
const over = ref(false)
const busy = ref(false)
const picker = ref<HTMLInputElement | null>(null)
const ok = computed(() => !!file.value && !error.value)

watch(() => props.modelValue, (o) => {
  if (o) {
    file.value = null
    error.value = ''
    prefix.value = ''
  }
})

async function take(f: File | undefined | null) {
  error.value = ''
  file.value = null
  if (!f) return
  if (f.size > MAX) {
    error.value = t('oauth.vertex.tooBig')
    return
  }
  const content = await f.text()
  try {
    const data = JSON.parse(content) as Record<string, unknown>
    if (data?.type !== 'service_account' || typeof data.client_email !== 'string') throw new Error('type')
    file.value = { name: f.name, content, project: String(data.project_id ?? ''), email: data.client_email }
  } catch {
    error.value = t('oauth.vertex.invalid')
  }
}

function onDrop(e: DragEvent) {
  over.value = false
  void take(e.dataTransfer?.files?.[0])
}

async function submit() {
  if (!file.value || busy.value) return
  busy.value = true
  try {
    await vertexImport(file.value.name, file.value.content, prefix.value.trim() || undefined)
    RC().ui.ElMessage.success(t('oauth.vertex.done'))
    emit('done')
    emit('update:modelValue', false)
  } catch (e) {
    error.value = RC().errorText(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <el-dialog :model-value="modelValue" :title="t('oauth.vertex.title')" width="min(460px, 94vw)" append-to-body class="aip-dialog"
             @update:model-value="emit('update:modelValue', $event)">
    <div class="aio-flow-body">
      <button
        type="button" class="aio-drop" :class="{ over, has: !!file }"
        @click="picker?.click()" @dragover.prevent="over = true" @dragleave="over = false" @drop.prevent="onDrop"
      >
        <Icon :name="file ? 'check' : 'upload'" class="aio-drop-icon" />
        <span class="aio-drop-main">{{ file ? file.name : t('oauth.vertex.drop') }}</span>
        <span class="aio-drop-sub">{{ t('oauth.vertex.dropSub') }}</span>
      </button>
      <input ref="picker" type="file" accept="application/json,.json" hidden @change="take(($event.target as HTMLInputElement).files?.[0])">
      <dl v-if="file" class="aio-kv">
        <dt>{{ t('oauth.vertex.project') }}</dt><dd class="mono">{{ file.project || '—' }}</dd>
        <dt>{{ t('oauth.vertex.email') }}</dt><dd class="mono">{{ file.email }}</dd>
      </dl>
      <label class="aip-field">
        <span class="aip-label">{{ t('oauth.vertex.prefix') }}</span>
        <el-input v-model="prefix" size="small" maxlength="40" :placeholder="t('oauth.vertex.prefixPlaceholder')" />
      </label>
      <div v-if="error" class="aip-error">{{ error }}</div>
      <div class="aip-row"><span class="aip-grow" />
        <el-button @click="emit('update:modelValue', false)">{{ t('editor.cancel') }}</el-button>
        <el-button type="primary" :disabled="!ok" :loading="busy" @click="submit">{{ t('oauth.vertex.import') }}</el-button>
      </div>
    </div>
  </el-dialog>
</template>
