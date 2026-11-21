// Plugin management API (robot_console/plugins.py register_plugin_routes)
import { isRecord, request } from '@/http'
import type { MarketResult, MarketView, PluginInfo, PluginList, PluginPython, PluginSettings } from './types'

const STATES = ['active', 'disabled', 'failed', 'invalid', 'duplicate', 'incompatible']

function bilingual(v: unknown) {
  return isRecord(v) && typeof v.zh === 'string' && typeof v.en === 'string'
}

export function validPlugin(v: unknown): v is PluginInfo {
  if (!isRecord(v)) return false
  const m = v.manifest
  return typeof v.id === 'string' && typeof v.dir === 'string' && typeof v.path === 'string'
    && STATES.includes(String(v.state)) && typeof v.enabled === 'boolean' && typeof v.restart_needed === 'boolean'
    && (v.issue === null || (isRecord(v.issue) && typeof v.issue.code === 'string' && isRecord(v.issue.params)))
    && (m === null || (isRecord(m) && typeof m.version === 'string' && bilingual(m.name) && bilingual(m.description)))
    && (v.frontend === null || (isRecord(v.frontend) && typeof v.frontend.script === 'string'))
    && Array.isArray(v.settings) && v.settings.every((s) => isRecord(s) && typeof s.key === 'string'
      && typeof s.type === 'string' && bilingual(s.label))
    && (v.python === undefined || v.python === null || validPython(v.python))
}

const PY_STATES = ['missing', 'installing', 'ready', 'outdated', 'failed']

function validPython(v: unknown): v is PluginPython {
  return isRecord(v) && PY_STATES.includes(String(v.state)) && Array.isArray(v.requirements)
    && typeof v.env === 'string' && Array.isArray(v.packages)
}

export function validPluginList(v: unknown): v is PluginList {
  return isRecord(v) && typeof v.api_version === 'number' && Array.isArray(v.dirs) && Array.isArray(v.paths)
    && typeof v.disabled_by_env === 'boolean' && Array.isArray(v.plugins) && v.plugins.every(validPlugin)
}

function validSettings(v: unknown): v is PluginSettings {
  return isRecord(v) && isRecord(v.values) && isRecord(v.secrets)
}

const MARKET_STATUS = ['available', 'installed', 'update', 'newer', 'incompatible']

function validEntry(v: unknown): boolean {
  return isRecord(v) && typeof v.id === 'string' && typeof v.version === 'string' && typeof v.api === 'number'
    && bilingual(v.name) && bilingual(v.description) && typeof v.url === 'string' && typeof v.sha256 === 'string'
    && typeof v.size === 'number' && Array.isArray(v.requirements) && MARKET_STATUS.includes(String(v.status))
    && (v.installed === null || (isRecord(v.installed) && typeof v.installed.version === 'string'))
}

export function validMarket(v: unknown): v is MarketView {
  return isRecord(v) && Array.isArray(v.index_urls) && typeof v.stale === 'boolean' && typeof v.busy === 'boolean'
    && (v.error === null || (isRecord(v.error) && typeof v.error.code === 'string'))
    && Array.isArray(v.plugins) && v.plugins.every(validEntry)
}

function validMarketResult(v: unknown): v is MarketResult {
  return isRecord(v) && (v.plugin === null || validPlugin(v.plugin)) && validMarket(v.market)
}

const id = (pid: string) => encodeURIComponent(pid)

export const pluginApi = {
  list: (signal?: AbortSignal) => request<PluginList>('GET', '/api/plugins', undefined, { signal, validate: validPluginList }),
  rescan: () => request<PluginList>('POST', '/api/plugins/rescan', undefined, { validate: validPluginList }),
  setEnabled: (pid: string, enabled: boolean) =>
    request<PluginInfo>('PUT', `/api/plugins/${id(pid)}/enabled`, { enabled }, { validate: validPlugin }),
  settings: (pid: string) => request<PluginSettings>('GET', `/api/plugins/${id(pid)}/settings`, undefined, { validate: validSettings }),
  saveSettings: (pid: string, values: PluginSettings['values'], clear: string[]) =>
    request<PluginSettings>('PUT', `/api/plugins/${id(pid)}/settings`, { values, clear }, { validate: validSettings }),
  installPython: (pid: string) =>
    request<PluginInfo>('POST', `/api/plugins/${id(pid)}/python/install`, undefined, { validate: validPlugin }),
  removePython: (pid: string) =>
    request<PluginInfo>('DELETE', `/api/plugins/${id(pid)}/python`, undefined, { validate: validPlugin }),
  pythonLog: (pid: string) => request<{ text: string }>('GET', `/api/plugins/${id(pid)}/python/log`, undefined,
    { validate: (v: unknown): v is { text: string } => isRecord(v) && typeof v.text === 'string' }),
  market: (refresh = false) => request<MarketView>('GET', `/api/plugins/market${refresh ? '?refresh=true' : ''}`,
    undefined, { validate: validMarket, category: 'control' }),
  marketInstall: (pid: string) => request<MarketResult>('POST', `/api/plugins/market/${id(pid)}/install`, undefined,
    { validate: validMarketResult, category: 'long' }),
  marketUpload: (file: File) => request<MarketResult>('POST',
    `/api/plugins/market/upload?name=${encodeURIComponent(file.name)}`, undefined,
    { raw: file, headers: { 'Content-Type': 'application/zip' }, category: 'upload', validate: validMarketResult }),
  uninstall: (pid: string, purge: boolean) => request<MarketResult>('DELETE',
    `/api/plugins/${id(pid)}${purge ? '?purge=true' : ''}`, undefined, { validate: validMarketResult }),
}
