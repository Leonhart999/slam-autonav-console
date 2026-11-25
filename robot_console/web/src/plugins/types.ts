// Plugins: data from /api/plugins (robot_console/plugins.py) and the definition a plugin script registers
import type { Component } from 'vue'
import type { Bilingual, CodedMsg } from '@/types'

export type PluginState = 'active' | 'disabled' | 'failed' | 'invalid' | 'duplicate' | 'incompatible'
export type SettingType = 'string' | 'text' | 'number' | 'integer' | 'boolean' | 'select' | 'secret'
export type SettingValue = string | number | boolean

export interface SettingSpec {
  key: string
  type: SettingType
  label: Bilingual
  help: Bilingual
  default?: SettingValue
  min?: number
  max?: number
  step?: number
  max_length?: number
  options?: { value: string | number; label: Bilingual }[]
}

export interface PluginInfo {
  id: string
  dir: string          // for display (~ for the home folder)
  path: string         // absolute, for "Open folder"
  state: PluginState
  enabled: boolean
  restart_needed: boolean
  issue: CodedMsg | null
  manifest: {
    api: number
    version: string
    name: Bilingual
    description: Bilingual
    author: string
    license: string
    homepage: string
    /** the upstream project a plugin is built on (optional) */
    upstream?: string
  } | null
  has_backend: boolean
  has_frontend: boolean
  frontend: { script: string; style: string | null } | null
  settings: SettingSpec[]
  /** version now on disk of a loaded plugin that was updated since the start ('' = removed); applies at the restart */
  pending_version?: string | null
  /** 'market' / 'file' when the plugin market installed the folder (it can be uninstalled there) */
  origin?: 'market' | 'file' | null
  /** Python packages of the manifest (python.requirements) and their venv; null when it lists none */
  python?: PluginPython | null
}

export type PythonState = 'missing' | 'installing' | 'ready' | 'outdated' | 'failed'

export interface PluginPython {
  state: PythonState
  requirements: string[]
  env: string
  packages: string[]
  python: string | null
  error: string | null
  finished: number | null
}

export interface PluginList {
  api_version: number
  dirs: string[]
  paths: string[]
  disabled_by_env: boolean
  plugins: PluginInfo[]
}

export interface PluginSettings {
  values: Record<string, SettingValue>
  secrets: Record<string, boolean>
}

/** A page a plugin adds: shown at /#/ext/<id>/<path> and in the side menu */
export interface PluginPageDef {
  path: string
  title: Bilingual | string
  icon?: Component
  component: Component
}

/** A UI theme a plugin registers: appears in the console appearance switcher alongside Light / Dark */
export interface PluginThemeDef {
  /** Unique theme id (lowercase letters, digits, underscores or hyphens, 2-40 chars) */
  id: string
  label: Bilingual | string
  /** Built-in base appearance this theme builds on ('light' or 'dark') */
  base: 'light' | 'dark'
  /** Browser theme-color meta tag value */
  pageColor?: string
  /** CSS custom properties (--*) applied to :root when this theme is active */
  tokens?: Record<string, string>
  /** Optional partial overrides for 2D/3D canvas and WebGL scene palettes */
  scene?: {
    ramp?: [[number, number, number], [number, number, number], [number, number, number], [number, number, number]]
    gridMajor?: number
    gridMinor?: number
    estLine?: number
    scanPoint?: number
    robotAccent?: string
    robotRing?: number
    floorTint?: number
    mapShade?: string | null
    canvasBg?: string
    canvasEdge?: string
  }
  /** Optional partial overrides for ECharts evaluation palettes */
  charts?: {
    series?: string[]
    axis?: Partial<{
      line: string
      split: string
      label: string
      ink: string
      tipBg: string
      tipInk: string
      paper: string
    }>
  }
}

/** What a plugin script passes to window.RobotConsole.register() (plugin interface v1, docs/plugins.md) */
export interface PluginDef {
  id: string
  messages?: { 'zh-CN'?: Record<string, unknown>; en?: Record<string, unknown> }
  pages?: PluginPageDef[]
  /** components shown in the page header, before the background-task button */
  header?: Component[]
  /** UI themes (appearance modes) contributed by this plugin */
  themes?: PluginThemeDef[]
}

/** Plugin market (robot_console/plugin_market.py): one plugin of the index and what is installed under its id */
export type MarketStatus = 'available' | 'installed' | 'update' | 'newer' | 'incompatible'

export interface MarketEntry {
  id: string
  version: string
  api: number
  name: Bilingual
  description: Bilingual
  author: string
  license: string
  homepage: string
  source: string
  upstream: string
  published: string
  requirements: string[]
  url: string
  sha256: string
  size: number
  status: MarketStatus
  installed: {
    version: string
    dir: string
    origin: 'market' | 'file' | 'manual' | 'bundled'
    state: PluginState
    enabled: boolean
    active: boolean
  } | null
}

export interface MarketView {
  index_urls: string[]
  source: string | null
  fetched: number | null
  name: string
  updated: string
  stale: boolean
  error: CodedMsg | null
  skipped: CodedMsg[]
  install_dir: string | null
  api_version: number
  supported_apis: number[]
  busy: boolean
  plugins: MarketEntry[]
}

export interface MarketResult {
  plugin: PluginInfo | null
  market: MarketView
  sha256?: string
  verified?: boolean
}
