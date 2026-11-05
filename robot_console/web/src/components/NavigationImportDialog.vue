<script lang="ts">
import type { NavMap } from '@/types'

export interface MapImportResult extends Omit<NavMap, 'grid'> {
  grid: NavMap['grid'] | null
}
</script>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import { pick } from '@/i18n'
import { errorText } from '@/messages'
import type { MappingPreset } from '@/types'
import {
  IMPORT_LIMITS, IMPORT_NAME_RE, useImportUpload, validateImportFile, validateImportName,
  validateImportTotal, validateMapImage, validatePcd,
} from '@/uploads'

const props = withDefaults(defineProps<{ modelValue: boolean; disabled: boolean; allowCloudOnly?: boolean }>(), {
  allowCloudOnly: false,
})
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  imported: [map: NavMap]
  completed: [map: MapImportResult]
}>()
const { t } = useI18n({
  messages: {
    en: {
      cloudImport: {
        title: 'Import map',
        yaml: 'Map parameters (YAML, paired with image)', image: 'Grid image (PGM / PNG, paired with YAML)',
        cloud: 'Point cloud (PCD; sufficient on its own)',
        cloudHint: 'ASCII or binary PCD. Cloud-only imports can generate a 2D grid in Maps; when supplied with a grid, both must use the same coordinate frame.',
        desc: 'Import a YAML/image pair, a PCD cloud, or both. A cloud-only map can be viewed in Maps but needs a 2D grid for navigation.',
        required: 'Supply both YAML and image, or a PCD cloud. YAML and image must always be paired.',
      },
    },
    'zh-CN': {
      cloudImport: {
        title: '导入地图',
        yaml: '地图参数（YAML，与图像配对）', image: '栅格图像（PGM / PNG，与 YAML 配对）',
        cloud: '点云（PCD，可单独导入）',
        cloudHint: '支持 ASCII 或 binary PCD。纯点云导入后可生成 2D 栅格；同时提供 2D 和 3D 时，两者必须使用同一坐标系。',
        desc: '可导入 YAML/图像配对文件、PCD 点云，或同时导入两者。纯点云可在地图库查看，导航仍需二维栅格。',
        required: '请选择 YAML 与图像配对文件，或 PCD 点云；YAML 和图像必须同时提供。',
      },
    },
  },
})
const { busy, error, phase, percentage, loadedMiB, run } = useImportUpload()
const open = computed({
  get: () => props.modelValue,
  set: (value: boolean) => { if (!busy.value) emit('update:modelValue', value) },
})
const name = ref('')
const yaml = ref<File | null>(null)
const image = ref<File | null>(null)
const cloud = ref<File | null>(null)
const yamlInput = ref<HTMLInputElement | null>(null)
const imageInput = ref<HTMLInputElement | null>(null)
const cloudInput = ref<HTMLInputElement | null>(null)
const preset = ref('')
const presets = ref<MappingPreset[]>([])
const presetsLoading = ref(false)
const presetsError = ref('')
let presetsSeq = 0
const nameInvalid = computed(() => !!name.value && !IMPORT_NAME_RE.test(name.value))
const filesValid = computed(() => !!yaml.value === !!image.value
  && (!!yaml.value || (props.allowCloudOnly && !!cloud.value)))
const canSubmit = computed(() => !props.disabled && !busy.value && IMPORT_NAME_RE.test(name.value) && filesValid.value)

async function loadPresets() {
  const seq = ++presetsSeq
  presetsLoading.value = true
  presetsError.value = ''
  try {
    const result = await api.mappingPresets()
    if (seq === presetsSeq) presets.value = result
  } catch (e) {
    if (seq === presetsSeq) presetsError.value = errorText(e)
  } finally {
    if (seq === presetsSeq) presetsLoading.value = false
  }
}

watch(() => props.modelValue, (value) => {
  if (!value) return
  name.value = ''
  yaml.value = image.value = cloud.value = null
  preset.value = ''
  presets.value = []
  error.value = ''
  for (const input of [yamlInput, imageInput, cloudInput]) {
    if (input.value) input.value.value = ''
  }
  void loadPresets()
})

function chooseFile(kind: 'yaml' | 'image' | 'cloud', event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0] ?? null
  if (kind === 'yaml') yaml.value = file
  else if (kind === 'image') image.value = file
  else cloud.value = file
  error.value = ''
}
function clearFile(kind: 'yaml' | 'image' | 'cloud') {
  const files = { yaml, image, cloud }
  const inputs = { yaml: yamlInput, image: imageInput, cloud: cloudInput }
  files[kind].value = null
  const input = inputs[kind].value
  if (input) input.value = ''
  error.value = ''
}

async function submit() {
  if (!canSubmit.value) return
  const result = await run<MapImportResult>(async (progress) => {
    validateImportName(name.value)
    // 先完成全部大小检查，之后才读取 YAML 和点云头部。
    const yamlFile = yaml.value
    const imageFile = image.value
    const cloudFile = cloud.value
    if (!!yamlFile !== !!imageFile || (!yamlFile && !(props.allowCloudOnly && cloudFile))) {
      throw new Error(t(props.allowCloudOnly ? 'cloudImport.required' : 'uploads.requiredFiles'))
    }
    if (yamlFile) validateImportFile(yamlFile, ['.yaml', '.yml'], IMPORT_LIMITS.yaml)
    if (imageFile) validateImportFile(imageFile, ['.pgm', '.png'], IMPORT_LIMITS.image)
    if (cloudFile) validateImportFile(cloudFile, ['.pcd'], IMPORT_LIMITS.cloud)
    validateImportTotal([yamlFile, imageFile, cloudFile].filter((file): file is File => file !== null))
    if (yamlFile && imageFile) await validateMapImage(yamlFile, imageFile)
    if (cloudFile) await validatePcd(cloudFile)
    // 校验读取文件时，远端状态可能已经切换为导航运行中。
    if (props.disabled) throw new Error(t('navigation.import.locked'))
    const data = new FormData()
    data.append('name', name.value)
    if (yamlFile && imageFile) {
      data.append('yaml_file', yamlFile)
      data.append('image_file', imageFile)
    }
    if (cloudFile) data.append('cloud_file', cloudFile)
    if (preset.value) data.append('preset', preset.value)
    return api.importNavigation(data, progress)
  })
  if (!result) return
  emit('completed', result)
  // Keep the existing navigation event strictly compatible with NavMap.
  if (result.grid !== null) emit('imported', { ...result, grid: result.grid })
  ElMessage.success(t('navigation.import.success', { name: result.name }))
  open.value = false
}
</script>

<template>
  <el-dialog
    v-model="open" class="import-dialog" :title="t(allowCloudOnly ? 'cloudImport.title' : 'navigation.import.title')"
    width="min(620px, calc(100vw - 32px))" append-to-body destroy-on-close
    :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy"
  >
    <p class="import-note">{{ t(allowCloudOnly ? 'cloudImport.desc' : 'navigation.import.desc') }}</p>
    <el-alert :title="t('navigation.import.sceneHint')" type="info" :closable="false" show-icon />
    <el-form label-position="top" :disabled="busy" class="import-form" @submit.prevent="submit">
      <el-form-item :label="t('uploads.name')" :error="nameInvalid ? t('uploads.nameRule') : undefined" required>
        <el-input v-model="name" maxlength="48" :placeholder="t('uploads.namePlaceholder')" :aria-label="t('uploads.name')" />
      </el-form-item>
      <p class="import-note">{{ t('uploads.nameRule') }}</p>
      <el-form-item :label="t(allowCloudOnly ? 'cloudImport.yaml' : 'navigation.import.yaml')" :required="!allowCloudOnly || !!image">
        <div class="import-file">
          <input ref="yamlInput" type="file" accept=".yaml,.yml" hidden :disabled="busy" :aria-label="t('navigation.import.yaml')" @change="chooseFile('yaml', $event)" />
          <el-button @click="yamlInput?.click()">{{ t('uploads.browse') }}</el-button>
          <span class="import-filename" :title="yaml?.name">{{ yaml?.name ?? t('uploads.noFile') }}</span>
          <el-button v-if="yaml" text :aria-label="t('uploads.remove')" @click="clearFile('yaml')">{{ t('uploads.remove') }}</el-button>
        </div>
      </el-form-item>
      <el-form-item :label="t(allowCloudOnly ? 'cloudImport.image' : 'navigation.import.image')" :required="!allowCloudOnly || !!yaml">
        <div class="import-file">
          <input ref="imageInput" type="file" accept=".pgm,.png" hidden :disabled="busy" :aria-label="t('navigation.import.image')" @change="chooseFile('image', $event)" />
          <el-button @click="imageInput?.click()">{{ t('uploads.browse') }}</el-button>
          <span class="import-filename" :title="image?.name">{{ image?.name ?? t('uploads.noFile') }}</span>
          <el-button v-if="image" text :aria-label="t('uploads.remove')" @click="clearFile('image')">{{ t('uploads.remove') }}</el-button>
        </div>
      </el-form-item>
      <p class="import-note">{{ t('navigation.import.imageHint') }}</p>
      <el-form-item :label="t(allowCloudOnly ? 'cloudImport.cloud' : 'navigation.import.cloud')">
        <div class="import-file">
          <input ref="cloudInput" type="file" accept=".pcd" hidden :disabled="busy" :aria-label="t('navigation.import.cloud')" @change="chooseFile('cloud', $event)" />
          <el-button @click="cloudInput?.click()">{{ t('uploads.browse') }}</el-button>
          <span class="import-filename" :title="cloud?.name">{{ cloud?.name ?? t('uploads.noFile') }}</span>
          <el-button v-if="cloud" text :aria-label="t('uploads.remove')" @click="clearFile('cloud')">{{ t('uploads.remove') }}</el-button>
        </div>
      </el-form-item>
      <p class="import-note">{{ t(allowCloudOnly ? 'cloudImport.cloudHint' : 'navigation.import.cloudHint') }}</p>
      <el-form-item :label="t('navigation.import.preset')">
        <el-select v-model="preset" clearable filterable :loading="presetsLoading" :placeholder="t('navigation.import.unbound')" :aria-label="t('navigation.import.preset')">
          <el-option v-for="p in presets" :key="p.name" :label="`${pick(p.label)} (${p.name})`" :value="p.name" />
        </el-select>
      </el-form-item>
      <p v-if="presetsError" class="import-note">
        {{ presetsError }}
        <el-button text size="small" :disabled="busy || presetsLoading" @click="loadPresets">{{ t('uploads.retry') }}</el-button>
      </p>
      <p class="import-note">{{ t('navigation.import.limits') }}</p>
    </el-form>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" />
    <p v-if="disabled" class="import-note">{{ t('navigation.import.locked') }}</p>
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
