// 上传表单共用的校验与状态；完整的内容和安全检查仍由后端负责。
import { computed, onBeforeUnmount, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ApiError, type UploadProgressHandler } from './api'
import { i18n } from './i18n'
import { errorText } from './messages'

const MIB = 1024 * 1024
export const IMPORT_NAME_RE = /^[A-Za-z0-9_-]{1,48}$/
export const IMPORT_LIMITS = { scene: 256 * MIB, sdf: 8 * MIB, yaml: 64 * 1024, image: 64 * MIB, cloud: 256 * MIB, total: 384 * MIB }

export function validateImportName(name: string) {
  if (!IMPORT_NAME_RE.test(name)) throw new ApiError({ code: 'import_name_invalid', params: { name } })
}

export function validateImportFile(file: File | null, extensions: string[], limit: number): asserts file is File {
  if (!file) throw new Error(i18n.global.t('uploads.requiredFiles'))
  if (!extensions.some((extension) => file.name.toLowerCase().endsWith(extension))) {
    throw new Error(i18n.global.t('uploads.fileType', { name: file.name, extensions: extensions.join(', ') }))
  }
  if (file.size > limit) throw new ApiError({ code: 'import_too_large', params: { limit: limit / MIB } })
  if (!file.size) throw new Error(i18n.global.t('uploads.emptyFile', { name: file.name }))
}

export function validateImportTotal(files: File[]) {
  if (files.reduce((size, file) => size + file.size, 0) > IMPORT_LIMITS.total) {
    throw new ApiError({ code: 'import_too_large', params: { limit: IMPORT_LIMITS.total / MIB } })
  }
}

export async function validateMapImage(yaml: File, image: File) {
  // ROS 地图通常使用顶层 image 标量；兼容引号、注释和包含目录的路径。
  // 复杂 YAML 语法交给后端解析，不把前端的轻量检查当成 YAML 解析器。
  const text = (await yaml.text()).replace(/^﻿/, '')
  const match = text.match(/^image[ \t]*:[ \t]*(.+)$/m)
  if (!match) return
  const scalar = match[1].trim()
  let path: string
  if (scalar.startsWith('"')) {
    const quoted = scalar.match(/^"(?:[^"\\]|\\.)*"/)
    if (!quoted) return
    try { path = JSON.parse(quoted[0]) as string } catch { return }
  } else if (scalar.startsWith("'")) {
    const quoted = scalar.match(/^'((?:[^']|'')*)'/)
    if (!quoted) return
    path = quoted[1].replace(/''/g, "'")
  } else {
    if (/^[!&*>{\[|]/.test(scalar)) return
    path = scalar.replace(/[ \t]+#.*$/, '').trim()
  }
  const basename = path.replace(/\\/g, '/').split('/').pop()
  if (basename && basename !== image.name) {
    throw new Error(i18n.global.t('uploads.imageMismatch', { expected: basename, actual: image.name }))
  }
}

export async function validatePcd(file: File) {
  // 只读取头部，避免把整个二进制点云加载进浏览器内存。
  const header = await file.slice(0, 64 * 1024).text()
  const encoding = header.match(/^DATA[ \t]+(\S+)[ \t]*\r?$/im)?.[1].toLowerCase()
  if (encoding !== 'ascii' && encoding !== 'binary') throw new Error(i18n.global.t('uploads.pcdEncoding'))
}

export function useImportUpload() {
  const busy = ref(false)
  const error = ref('')
  const phase = ref<'checking' | 'uploading' | 'importing'>('checking')
  const percentage = ref<number | null>(null)
  const loaded = ref(0)
  const loadedMiB = computed(() => (loaded.value / MIB).toFixed(1))
  const onProgress: UploadProgressHandler = (progress) => {
    loaded.value = progress.loaded
    phase.value = progress.sent ? 'importing' : 'uploading'
    percentage.value = progress.total ? Math.min(100, Math.floor(100 * progress.loaded / progress.total)) : null
  }

  async function run<T>(action: (progress: UploadProgressHandler) => Promise<T>): Promise<T | undefined> {
    if (busy.value) return
    busy.value = true
    error.value = ''
    phase.value = 'checking'
    percentage.value = null
    loaded.value = 0
    try {
      return await action(onProgress)
    } catch (e) {
      error.value = errorText(e)
    } finally {
      busy.value = false
    }
  }

  // 上传或导入期间禁止页内离开；刷新、关闭浏览器时使用浏览器自带的离开确认。
  onBeforeRouteLeave(() => !busy.value)
  const beforeUnload = (event: BeforeUnloadEvent) => {
    if (!busy.value) return
    event.preventDefault()
    event.returnValue = ''
  }
  window.addEventListener('beforeunload', beforeUnload)
  onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))

  return { busy, error, phase, percentage, loadedMiB, run }
}
