<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import type { MappingPreset } from '@/types'
import { IMPORT_LIMITS, IMPORT_NAME_RE, useImportUpload, validateImportFile, validateImportName, validateImportTotal } from '@/uploads'

const props = defineProps<{ modelValue: boolean; disabled: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; imported: [preset: MappingPreset] }>()
const { t } = useI18n()
const { busy, error, phase, percentage, loadedMiB, run } = useImportUpload()
const open = computed({
  get: () => props.modelValue,
  set: (value: boolean) => { if (!busy.value) emit('update:modelValue', value) },
})
const name = ref('')
const file = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const spawn = reactive<{ x: number | undefined; y: number | undefined; z: number | undefined; yaw: number | undefined }>({
  x: 0, y: 0, z: 0.1, yaw: 0,
})
const nameInvalid = computed(() => !!name.value && !IMPORT_NAME_RE.test(name.value))
const spawnValid = computed(() => Object.values(spawn).every((v) => typeof v === 'number' && Number.isFinite(v)))
const canSubmit = computed(() => !props.disabled && !busy.value && IMPORT_NAME_RE.test(name.value) && !!file.value && spawnValid.value)

watch(() => props.modelValue, (value) => {
  if (!value) return
  name.value = ''
  file.value = null
  error.value = ''
  Object.assign(spawn, { x: 0, y: 0, z: 0.1, yaw: 0 })
  if (fileInput.value) fileInput.value.value = ''
})

function chooseFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null
  error.value = ''
}

async function submit() {
  if (!canSubmit.value) return
  const result = await run(async (progress) => {
    validateImportName(name.value)
    validateImportFile(file.value, ['.sdf', '.zip'], file.value?.name.toLowerCase().endsWith('.sdf') ? IMPORT_LIMITS.sdf : IMPORT_LIMITS.scene)
    validateImportTotal([file.value])
    const data = new FormData()
    data.append('name', name.value)
    data.append('file', file.value)
    data.append('x', String(spawn.x))
    data.append('y', String(spawn.y))
    data.append('z', String(spawn.z))
    // 表单显示角度，接口只接收弧度。
    data.append('yaw', String(spawn.yaw! * Math.PI / 180))
    return api.importMapping(data, progress)
  })
  if (!result) return
  emit('imported', result)
  ElMessage.success(t('mapping.import.success', { name: result.name }))
  open.value = false
}
</script>

<template>
  <el-dialog
    v-model="open" class="import-dialog" :title="t('mapping.import.title')"
    width="min(600px, calc(100vw - 32px))" append-to-body destroy-on-close
    :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy"
  >
    <p class="import-note">{{ t('mapping.import.desc') }}</p>
    <el-alert :title="t('mapping.import.safety')" type="info" :closable="false" show-icon />
    <el-form label-position="top" :disabled="busy" class="import-form" @submit.prevent="submit">
      <el-form-item :label="t('uploads.name')" :error="nameInvalid ? t('uploads.nameRule') : undefined" required>
        <el-input v-model="name" maxlength="48" :placeholder="t('uploads.namePlaceholder')" :aria-label="t('uploads.name')" />
      </el-form-item>
      <p class="import-note">{{ t('uploads.nameRule') }}</p>
      <el-form-item :label="t('mapping.import.file')" required>
        <div class="import-file">
          <input ref="fileInput" type="file" accept=".sdf,.zip" hidden :disabled="busy" :aria-label="t('mapping.import.file')" @change="chooseFile" />
          <el-button @click="fileInput?.click()">{{ t('uploads.browse') }}</el-button>
          <span class="import-filename" :title="file?.name">{{ file?.name ?? t('uploads.noFile') }}</span>
        </div>
      </el-form-item>
      <p class="import-note">{{ t('mapping.import.fileHint') }}</p>
      <div class="import-spawn">
        <el-form-item v-for="axis in (['x', 'y', 'z'] as const)" :key="axis" :label="`${axis} (m)`" required>
          <el-input-number v-model="spawn[axis]" :step="0.1" :precision="3" controls-position="right" :aria-label="`${axis} (m)`" />
        </el-form-item>
        <el-form-item :label="t('mapping.import.yaw')" required>
          <el-input-number v-model="spawn.yaw" :step="5" :precision="2" controls-position="right" :aria-label="t('mapping.import.yaw')" />
        </el-form-item>
      </div>
      <p class="import-note">{{ t('mapping.import.spawnHint') }}</p>
    </el-form>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" />
    <p v-if="disabled" class="import-note">{{ t('mapping.import.locked') }}</p>
    <div v-if="busy" class="import-progress" role="status" aria-live="polite">
      <el-progress v-if="phase === 'uploading' && percentage !== null" :percentage="percentage" :stroke-width="6" />
      <p>{{ phase === 'uploading' ? t('uploads.uploading', { size: loadedMiB }) : t(`uploads.${phase}`) }}</p>
    </div>
    <template #footer>
      <el-button :disabled="busy" @click="open = false">{{ t('uploads.cancel') }}</el-button>
      <el-button type="primary" :loading="busy" :disabled="!canSubmit" @click="submit">{{ t('uploads.confirm') }}</el-button>
    </template>
  </el-dialog>
</template>
