// Browser-only preferences of the Settings page (General section). They live in localStorage, apply at once and
// follow changes made in another window of the console. Language and the light/dark choice keep their own keys
// (i18n.ts, theme.ts); "follow system" is kept here and drives setTheme().
import { reactive, watch } from 'vue'
import { setTheme } from '@/theme'

export type Density = 'compact' | 'comfortable'
export type Motion = 'full' | 'reduced'
export const ZOOMS = [90, 100, 110, 125] as const
export type Zoom = (typeof ZOOMS)[number]
/** 'last' = the page open when the console was last used; otherwise a route path */
export const START_PAGES = ['last', '/monitor', '/mapping', '/playback', '/maps', '/navigation', '/evaluation'] as const
export type StartPage = (typeof START_PAGES)[number]

export interface UiPrefs {
  density: Density
  zoom: Zoom
  motion: Motion
  followSystem: boolean
  showPluginGroup: boolean
  showNumbers: boolean
  showClock: boolean
  startPage: StartPage
  notifyTasks: boolean
}

export const PREFS_KEY = 'robot_console.ui_prefs'
const LAST_PAGE_KEY = 'robot_console.last_page'
const DEFAULTS: UiPrefs = {
  density: 'comfortable', zoom: 100, motion: 'full', followSystem: false,
  showPluginGroup: true, showNumbers: true, showClock: true, startPage: '/monitor', notifyTasks: false,
}

const oneOf = <T>(list: readonly T[], v: unknown, fallback: T): T => (list.includes(v as T) ? v as T : fallback)
const flag = (v: unknown, fallback: boolean) => (typeof v === 'boolean' ? v : fallback)

/** Keep only known keys with valid values (old or hand-edited storage must not break the page) */
export function sanitize(raw: unknown): UiPrefs {
  const r = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {}
  return {
    density: oneOf(['compact', 'comfortable'] as const, r.density, DEFAULTS.density),
    zoom: oneOf(ZOOMS, r.zoom, DEFAULTS.zoom),
    motion: oneOf(['full', 'reduced'] as const, r.motion, DEFAULTS.motion),
    followSystem: flag(r.followSystem, DEFAULTS.followSystem),
    showPluginGroup: flag(r.showPluginGroup, DEFAULTS.showPluginGroup),
    showNumbers: flag(r.showNumbers, DEFAULTS.showNumbers),
    showClock: flag(r.showClock, DEFAULTS.showClock),
    startPage: oneOf(START_PAGES, r.startPage, DEFAULTS.startPage),
    notifyTasks: flag(r.notifyTasks, DEFAULTS.notifyTasks),
  }
}

function read(): UiPrefs {
  try {
    return sanitize(JSON.parse(localStorage.getItem(PREFS_KEY) || '{}'))
  } catch {
    return { ...DEFAULTS }
  }
}

export const prefs = reactive<UiPrefs>(read())

const systemDark = () => window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false

/** Bring <html> in line with the preferences: density and motion classes, zoom */
export function applyPrefs() {
  const root = document.documentElement
  root.classList.toggle('density-compact', prefs.density === 'compact')
  root.classList.toggle('reduce-motion', prefs.motion === 'reduced')
  root.style.setProperty('--ui-zoom', String(prefs.zoom / 100))
  root.classList.toggle('ui-zoomed', prefs.zoom !== 100)
  if (prefs.followSystem) void setTheme(systemDark() ? 'dark' : 'light')
}

let started = false
export function initPrefs() {
  if (started) return
  started = true
  applyPrefs()
  watch(prefs, () => {
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)) } catch { /* private mode */ }
    applyPrefs()
  }, { deep: true })
  window.addEventListener('storage', (e) => {
    if (e.key === PREFS_KEY) Object.assign(prefs, read())
  })
  window.matchMedia?.('(prefers-color-scheme: dark)').addEventListener?.('change', () => {
    if (prefs.followSystem) void setTheme(systemDark() ? 'dark' : 'light')
  })
}

/** A manual theme choice (side menu or Settings) ends "follow system" */
export function pickThemeManually(id: string, origin?: { x: number; y: number }) {
  prefs.followSystem = false
  void setTheme(id, origin)
}

export function followSystemTheme() {
  prefs.followSystem = true
}

export function rememberPage(path: string) {
  try { localStorage.setItem(LAST_PAGE_KEY, path) } catch { /* ignore */ }
}

/** Where '/' leads: the chosen start page, or the last page when that is a known console page */
export function startPath(known: (path: string) => boolean): string {
  if (prefs.startPage !== 'last') return prefs.startPage
  try {
    const last = localStorage.getItem(LAST_PAGE_KEY)
    if (last && known(last)) return last
  } catch { /* ignore */ }
  return DEFAULTS.startPage
}

/** Browser part of a settings export: these localStorage entries, as stored */
export const BROWSER_KEYS = [PREFS_KEY, 'robot_console.theme', 'robot_console.lang'] as const

export function exportBrowserPrefs(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const k of BROWSER_KEYS) {
    try {
      const v = localStorage.getItem(k)
      if (v !== null) out[k] = v
    } catch { /* ignore */ }
  }
  return out
}

/** Write the browser part of an import; unknown keys are ignored. The page reloads afterwards to apply them. */
export function importBrowserPrefs(data: unknown): number {
  if (!data || typeof data !== 'object') return 0
  let n = 0
  for (const k of BROWSER_KEYS) {
    const v = (data as Record<string, unknown>)[k]
    if (typeof v !== 'string' || v.length > 4096) continue
    const value = k === PREFS_KEY ? JSON.stringify(sanitize(safeJson(v))) : v
    try { localStorage.setItem(k, value); n++ } catch { /* ignore */ }
  }
  return n
}

function safeJson(text: string): unknown {
  try { return JSON.parse(text) } catch { return {} }
}
