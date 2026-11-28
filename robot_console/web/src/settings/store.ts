// Shared state of the console-config part of the Settings page: one draft across all sections, so the save bar
// counts every unsaved change and switching sections keeps them.
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ApiError } from '@/http'
import { errorText } from '@/messages'
import { settingsApi, type SettingsPayload, type SettingsSaved, type SettingValue } from './api'

export interface FieldError { reason: string; min?: number; max?: number }

export const payload = ref<SettingsPayload | null>(null)
export const draft = reactive<Record<string, SettingValue>>({})
export const fieldErrors = reactive<Record<string, FieldError>>({})
export const loadError = ref('')
export const loading = ref(false)
export const saving = ref(false)

const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

function adopt(p: SettingsPayload) {
  payload.value = p
  for (const k of Object.keys(draft)) delete draft[k]
  for (const [k, f] of Object.entries(p.fields)) draft[k] = clone(f.value)
  for (const k of Object.keys(fieldErrors)) delete fieldErrors[k]
}

export async function loadSettings(force = false) {
  if (payload.value && !force) return
  loading.value = true
  loadError.value = ''
  try {
    adopt(await settingsApi.get())
  } catch (e) {
    loadError.value = errorText(e)
  } finally {
    loading.value = false
  }
}

export const dirtyKeys = computed(() => {
  const p = payload.value
  if (!p) return []
  return Object.keys(p.fields).filter((k) => !same(draft[k], p.fields[k].value))
})
export const writable = computed(() => !!payload.value?.writable)
export const pendingRestart = computed(() => !!payload.value?.pending_restart)

export function discard() {
  if (payload.value) adopt(payload.value)
}

/** Ask the status bar to restart the console (it shows progress and reloads the page when the backend is back) */
export function requestRestart() {
  window.dispatchEvent(new Event('console-restart-request'))
}

export function adoptSaved(r: SettingsSaved) {
  adopt(r)
}

/** onInvalid: called with the key the backend rejected (the row shows the reason); other errors become a toast */
export async function saveSettings(restart = false, onInvalid?: (key: string) => void): Promise<boolean> {
  const keys = dirtyKeys.value
  if (!keys.length || saving.value) return false
  saving.value = true
  for (const k of Object.keys(fieldErrors)) delete fieldErrors[k]
  try {
    const set: Record<string, SettingValue> = {}
    for (const k of keys) set[k] = clone(draft[k])
    adopt(await settingsApi.save(set))
    if (restart) requestRestart()
    return true
  } catch (e) {
    if (e instanceof ApiError && (e.coded.code === 'setting_invalid' || e.coded.code === 'setting_unknown')) {
      const p = e.coded.params as Record<string, unknown>
      const key = String(p.key ?? '')
      if (key && key in (payload.value?.fields ?? {})) {
        fieldErrors[key] = {
          reason: e.coded.code === 'setting_unknown' ? 'option' : String(p.reason ?? 'format'),
          min: typeof p.min === 'number' ? p.min : undefined,
          max: typeof p.max === 'number' ? p.max : undefined,
        }
        if (onInvalid) { onInvalid(key); return false }
      }
    }
    ElMessage.error(errorText(e))
    return false
  } finally {
    saving.value = false
  }
}

/** Keys of each section, in display order (also used to count unsaved changes per section) */
export const SECTION_KEYS = {
  storage: ['paths.data_root', 'mapping.maps_dir', 'mapping.runs_dir', 'playback.bag_dirs', 'evaluation.results_dir',
    'server.log_dir'],
  experiment: ['evaluation.rpe_delta', 'evaluation.max_diff', 'mapping.gt_topic', 'batch.slam_stacks', 'batch.run_timeout',
    'batch.ready_timeout', 'batch.max_runs', 'batch.speeds', 'navigation.default_map', 'navigation.goal_timeout',
    'navigation.speed_scale_range', 'navigation.max_w_cap', 'playback.rates'],
  safety: ['teleop.max_v', 'teleop.recommended_v', 'teleop.max_w', 'teleop.timeout', 'teleop.topic',
    'estop.zero_duration'],
  network: ['server.port', 'server.status_rate', 'plugins.pip.proxy', 'plugins.pip.index_url', 'plugins.market.proxy',
    'plugins.market.index_urls'],
} as const

/** Settings section a console key is edited in */
export function sectionOf(key: string): keyof typeof SECTION_KEYS | null {
  for (const [sec, keys] of Object.entries(SECTION_KEYS)) {
    if ((keys as readonly string[]).includes(key)) return sec as keyof typeof SECTION_KEYS
  }
  return null
}
