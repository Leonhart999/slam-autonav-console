<!-- "Fetch model list": asks the service which models it has (with the editor's unsaved values) and lets the user
     tick the ones to add. -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElButton, ElCheckbox, ElDialog, ElInput } from 'element-plus'
import { probeModels, type ProfileInput, type RemoteModel } from './api'
import { RC, t } from './sdk'

const props = defineProps<{ modelValue: boolean; profileId: string | null; input: () => ProfileInput; have: string[] }>()
const emit = defineEmits<{ 'update:modelValue': [open: boolean]; add: [ids: string[]] }>()

const loading = ref(false)
const error = ref('')
const models = ref<RemoteModel[]>([])
const query = ref('')
const picked = ref<Set<string>>(new Set())

const have = computed(() => new Set(props.have))
const shown = computed(() => {
  const q = query.value.trim().toLowerCase()
  return q ? models.value.filter((m) => m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q)) : models.value
})
const pickable = computed(() => shown.value.filter((m) => !have.value.has(m.id)))
const allPicked = computed(() => pickable.value.length > 0 && pickable.value.every((m) => picked.value.has(m.id)))

async function load() {
  loading.value = true
  error.value = ''
  models.value = []
  picked.value = new Set()
  try {
    models.value = (await probeModels(props.profileId, props.input())).models
  } catch (e) {
    error.value = RC().errorText(e)
  } finally {
    loading.value = false
  }
}
watch(() => props.modelValue, (open) => { if (open) { query.value = ''; void load() } })

function toggle(id: string) {
  const next = new Set(picked.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  picked.value = next
}
function toggleAll() {
  const next = new Set(picked.value)
  if (allPicked.value) for (const m of pickable.value) next.delete(m.id)
  else for (const m of pickable.value) next.add(m.id)
  picked.value = next
}
function add() {
  emit('add', models.value.filter((m) => picked.value.has(m.id)).map((m) => m.id))
  emit('update:modelValue', false)
}
const ctx = (n: number | null) => (n ? (n >= 1e6 ? `${+(n / 1e6).toFixed(1)}M` : `${Math.round(n / 1000)}K`) : '')
</script>

<template>
  <el-dialog :model-value="modelValue" :title="t('fetch.title')" width="560px" append-to-body class="aip-dialog"
             @update:model-value="emit('update:modelValue', $event)">
    <div class="aip-fetch">
      <div class="aip-row">
        <el-input v-model="query" :placeholder="t('fetch.search')" clearable />
        <el-button :loading="loading" @click="load">{{ t('fetch.reload') }}</el-button>
      </div>
      <div v-if="error" class="aip-error">{{ error }}</div>
      <div v-else-if="loading" class="aip-help">{{ t('fetch.loading') }}</div>
      <div v-else-if="!models.length" class="aip-help">{{ t('fetch.empty') }}</div>
      <template v-else>
        <div class="aip-fetch-bar">
          <el-checkbox :model-value="allPicked" :disabled="!pickable.length" @change="toggleAll">{{ t('fetch.all') }}</el-checkbox>
          <span class="aip-grow" />
          <span class="aip-help">{{ t('fetch.count', { shown: shown.length, total: models.length }) }}</span>
        </div>
        <div class="aip-fetch-list">
          <label v-for="m in shown" :key="m.id" class="aip-fetch-row" :class="{ have: have.has(m.id) }">
            <el-checkbox :model-value="have.has(m.id) || picked.has(m.id)" :disabled="have.has(m.id)" @change="toggle(m.id)" />
            <span class="aip-fetch-id mono">{{ m.id }}</span>
            <span v-if="m.name !== m.id" class="aip-dim">{{ m.name }}</span>
            <span class="aip-grow" />
            <span v-if="m.context" class="aip-tag">{{ ctx(m.context) }}</span>
            <span v-if="have.has(m.id)" class="aip-tag on">{{ t('fetch.added') }}</span>
          </label>
        </div>
      </template>
    </div>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">{{ t('editor.cancel') }}</el-button>
      <el-button type="primary" :disabled="!picked.size" @click="add">{{ t('fetch.add', { n: picked.size }) }}</el-button>
    </template>
  </el-dialog>
</template>
