// Settings page REST calls (backend robot_console/settings.py). All console settings are raw console.yaml values
// (unexpanded ~ and ${data_root}); the backend writes them into the user override file and they apply after a
// console restart.
import { isRecord, request } from '@/http'
import type { Bilingual } from '@/types'

export type SettingValue = string | number | boolean | null | SettingValue[]
export interface SettingField { value: SettingValue; default: SettingValue; overridden: boolean }
export interface SettingsPayload {
  user_file: string | null
  writable: boolean
  reason: null | 'no_user_config'
  fields: Record<string, SettingField>
  options: { 'batch.slam_stacks'?: { name: string; label: Bilingual }[] }
  read_only: Record<string, unknown>
  pending_restart: boolean
}
export interface SettingsSaved extends SettingsPayload {
  changed: string[]
  restart_required: boolean
  ignored?: string[]
}
export interface StorageFolder { key: string; path: string; exists: boolean; bytes: number; items: number; partial: boolean }
export interface StorageDisk { path: string; total: number; used: number; free: number }
export interface StorageInfo { folders: StorageFolder[]; disks: StorageDisk[] }
export interface AboutInfo {
  version: string
  repo: string
  ros_distro: string | null
  rmw: string
  domain_id: string
  python: string
  os: string
  wsl: boolean
  kernel: string
  venv: string | null
  evo_python: string | null
  evo_ok: boolean
  plugins_enabled: number | null
  plugins_total: number | null
  config_files: string[]
  user_file: string | null
  host: string
  port: number
  data_root: string
}
export interface UpdateInfo {
  current: string
  latest: string | null
  newer: boolean
  url: string | null
  published_at: string | null
  checked_at: number | string | null
  error: string | null
}
export interface NetTestResult { target: 'market' | 'pip' | 'github'; url: string; ok: boolean; ms: number | null; status: number | null; error: string | null }
export interface SettingsExport { format: 'robot_console.settings'; version: 1; exported_at: string; console_yaml: string }

const validPayload = (v: unknown): v is SettingsPayload => isRecord(v) && isRecord(v.fields)
  && typeof v.writable === 'boolean' && isRecord(v.options) && isRecord(v.read_only)
  && Object.values(v.fields).every((f) => isRecord(f) && 'value' in f && 'default' in f && typeof f.overridden === 'boolean')
const validSaved = (v: unknown): v is SettingsSaved => validPayload(v) && isRecord(v) && Array.isArray(v.changed)

export const settingsApi = {
  get: (signal?: AbortSignal) => request<SettingsPayload>('GET', '/api/settings', undefined, { signal, validate: validPayload }),
  save: (set: Record<string, SettingValue>, reset: string[] = []) =>
    request<SettingsSaved>('PUT', '/api/settings', { set, reset }, { validate: validSaved }),
  resetAll: () => request<SettingsSaved>('POST', '/api/settings/reset_all', {}, { validate: validSaved }),
  export: () => request<SettingsExport>('GET', '/api/settings/export', undefined, {
    validate: (v): v is SettingsExport => isRecord(v) && v.format === 'robot_console.settings' && typeof v.console_yaml === 'string',
  }),
  import: (consoleYaml: string) =>
    request<SettingsSaved>('POST', '/api/settings/import', { console_yaml: consoleYaml }, { validate: validSaved }),
  storage: (signal?: AbortSignal) => request<StorageInfo>('GET', '/api/settings/storage', undefined, {
    signal, validate: (v): v is StorageInfo => isRecord(v) && Array.isArray(v.folders) && Array.isArray(v.disks),
  }),
  about: (signal?: AbortSignal) => request<AboutInfo>('GET', '/api/settings/about', undefined, {
    signal, validate: (v): v is AboutInfo => isRecord(v) && typeof v.version === 'string',
  }),
  update: (force = false, signal?: AbortSignal) =>
    request<UpdateInfo>('GET', `/api/settings/update?force=${force}`, undefined, {
      signal, validate: (v): v is UpdateInfo => isRecord(v) && typeof v.current === 'string' && typeof v.newer === 'boolean',
    }),
  networkTest: (body: { proxy?: string; pip_proxy?: string; pip_index_url?: string; market_index_url?: string }) =>
    request<{ results: NetTestResult[] }>('POST', '/api/settings/network_test', body, {
      timeout: 30000, validate: (v): v is { results: NetTestResult[] } => isRecord(v) && Array.isArray(v.results),
    }),
}
