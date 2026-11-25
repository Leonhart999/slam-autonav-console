// Appearance & UI themes. Built-in looks are light and dark; plugins can register extra theme modes via
// RobotConsole.register({ themes: [...] }) (plugin interface v1). The choice is kept in localStorage;
// index.html applies light/dark before the first paint, and custom plugin themes apply once their script loads.
// CSS reads the tokens of style.css (`html.dark` or `html[data-ui-theme="<id>"]` overrides them);
// canvases and WebGL scenes, which cannot read CSS directly, take their colors from `scenePalette`.
import { computed, nextTick, ref, watch } from 'vue'

export type BuiltinTheme = 'light' | 'dark'
export type Theme = string
export const THEMES: BuiltinTheme[] = ['light', 'dark']

const STORAGE_KEY = 'robot_console.theme'   // same key as the inline script in index.html
const BASE_COLOR: Record<BuiltinTheme, string> = { light: '#eef2f8', dark: '#080d18' }
const REVEAL_MS = 520

type RGB = [number, number, number]
export interface ScenePalette {
  /** height ramp of point clouds, low to high (linear 0..1 components) */
  ramp: [RGB, RGB, RGB, RGB]
  gridMajor: number
  gridMinor: number
  /** estimated trajectory on the mapping stage */
  estLine: number
  /** current laser scan points on the 3D mapping stage */
  scanPoint: number
  /** robot outline/arrow & pulse ring in 2D/3D views */
  robotAccent: string
  robotRing: number
  /** multiplied onto the occupancy grid used as the 3D floor */
  floorTint: number
  /** drawn over the 2D occupancy grid to take the glare off a white map; null = leave the map as it is */
  mapShade: string | null
  /** letterbox around the grid in the map editor, and the outline of the grid */
  canvasBg: string
  canvasEdge: string
}

export interface ChartPalette {
  series: string[]
  axis: {
    line: string
    split: string
    label: string
    ink: string
    tipBg: string
    tipInk: string
    paper: string
  }
}

export interface ThemeDef {
  id: string
  pluginId: string
  label: { zh: string; en: string }
  /** Base built-in appearance ("light" or "dark"); controls whether `html.dark` is active */
  base: BuiltinTheme
  /** Browser theme-color meta value */
  pageColor?: string
  /** CSS custom properties applied onto <html> (and cleaned up when switching away) */
  tokens?: Record<string, string>
  /** Optional partial overrides for 2D/3D WebGL & canvas palettes */
  scene?: Partial<ScenePalette>
  /** Optional partial overrides for ECharts evaluation palettes */
  charts?: { series?: string[]; axis?: Partial<ChartPalette['axis']> }
}

const LIGHT_SCENE: ScenePalette = {
  ramp: [[0.6, 0.86, 0.93], [0.02, 0.64, 0.8], [0.15, 0.39, 0.92], [0.33, 0.25, 0.85]],
  gridMajor: 0x9db4cc,
  gridMinor: 0xd5dfea,
  estLine: 0x2563eb,
  scanPoint: 0x00b4d8,
  robotAccent: '#0891b2',
  robotRing: 0x06b6d4,
  floorTint: 0xffffff,
  mapShade: null,
  canvasBg: '#eef2f8',
  canvasEdge: 'rgba(15,30,60,0.25)',
}
// Same hues in the same order, lifted so the high (indigo) end stays readable on a dark stage
const DARK_SCENE: ScenePalette = {
  ramp: [[0.72, 0.93, 0.98], [0.13, 0.78, 0.93], [0.36, 0.6, 1.0], [0.62, 0.55, 1.0]],
  gridMajor: 0x3b4a68,
  gridMinor: 0x202b42,
  estLine: 0x60a5fa,
  scanPoint: 0x22d3ee,
  robotAccent: '#22d3ee',
  robotRing: 0x22d3ee,
  floorTint: 0xb4bfd0,
  mapShade: 'rgba(8, 13, 24, 0.3)',
  canvasBg: '#0d1424',
  canvasEdge: 'rgba(150,180,230,0.3)',
}

const LIGHT_CHARTS: ChartPalette = {
  series: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'],
  axis: { line: '#cbd3df', split: '#e6ebf2', label: '#7a879d', ink: '#4a5872',
    tipBg: 'rgba(255,255,255,0.96)', tipInk: '#0b1526', paper: '#fff' },
}
const DARK_CHARTS: ChartPalette = {
  series: ['#5b9df0', '#f08a5d', '#34c99a', '#f2b632', '#f08fb6', '#4cc04c', '#9084f2', '#f06b6b'],
  axis: { line: '#3a4762', split: '#232e45', label: '#8795ae', ink: '#b4c0d4',
    tipBg: 'rgba(22,31,51,0.96)', tipInk: '#e8eef8', paper: '#131b2d' },
}

/** Extra themes registered by active UI extension plugins */
export const customThemes = ref<ThemeDef[]>([])
const appliedTokenKeys = new Set<string>()

function savedTheme(): Theme {
  try {
    return localStorage.getItem(STORAGE_KEY) || 'light'
  } catch {
    return 'light'
  }
}

export const theme = ref<Theme>(savedTheme())
export const activeThemeDef = computed(() => customThemes.value.find((t) => t.id === theme.value) ?? null)
/** Effective appearance ('light' or 'dark'); custom themes inherit from their `base` */
export const effectiveBase = computed<BuiltinTheme>(() => {
  if (theme.value === 'dark') return 'dark'
  if (theme.value === 'light') return 'light'
  return activeThemeDef.value?.base ?? 'light'
})
export const isDark = computed(() => effectiveBase.value === 'dark')

export const themeChoices = computed(() => [
  { id: 'light', builtin: true as const, base: 'light' as BuiltinTheme, label: { zh: '浅色', en: 'Light' } },
  { id: 'dark', builtin: true as const, base: 'dark' as BuiltinTheme, label: { zh: '深色', en: 'Dark' } },
  ...customThemes.value.map((t) => ({ id: t.id, builtin: false as const, base: t.base, label: t.label, pluginId: t.pluginId })),
])

function clearCustomTokens() {
  const root = document.documentElement
  for (const k of appliedTokenKeys) root.style?.removeProperty?.(k)
  appliedTokenKeys.clear()
}

function apply(next: Theme) {
  theme.value = next
  const root = document.documentElement
  const custom = customThemes.value.find((t) => t.id === next) ?? null
  const base: BuiltinTheme = next === 'dark' ? 'dark' : next === 'light' ? 'light' : (custom?.base ?? 'light')
  root.classList.toggle('dark', base === 'dark')
  if (custom) root.setAttribute?.('data-ui-theme', custom.id)
  else root.removeAttribute?.('data-ui-theme')

  clearCustomTokens()
  if (custom?.tokens && root.style?.setProperty) {
    for (const [k, v] of Object.entries(custom.tokens)) {
      if (!k.startsWith('--')) continue
      root.style.setProperty(k, v)
      appliedTokenKeys.add(k)
    }
  }
  const pageColor = custom?.pageColor ?? BASE_COLOR[base]
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', pageColor)
}

/** Register themes contributed by a UI plugin and re-apply if the saved choice matches one of them */
export function registerPluginThemes(defs: ThemeDef[]) {
  if (!defs.length) return
  const next = [...customThemes.value]
  for (const d of defs) {
    if (!next.some((x) => x.id === d.id)) next.push(d)
  }
  customThemes.value = next
  if (defs.some((d) => d.id === theme.value)) apply(theme.value)
}

/** Remove all themes belonging to a plugin (e.g. if its header/UI crashed) */
export function unregisterPluginThemes(pluginId: string) {
  const hadActive = activeThemeDef.value?.pluginId === pluginId
  customThemes.value = customThemes.value.filter((t) => t.pluginId !== pluginId)
  if (hadActive) apply('light')
}

/** Bring <html> in line with the saved choice and follow changes made in another window of the console */
export function initTheme() {
  apply(theme.value)
  window.addEventListener('storage', (e) => {
    if (e.key === STORAGE_KEY && savedTheme() !== theme.value) apply(savedTheme())
  })
}

let running: ViewTransition | null = null
let fadeTimer = 0

/** Switch the appearance. With `origin` (viewport px) the new look spreads from that point as a growing circle;
    browsers without view transitions cross-fade the colors, and "reduce motion" switches at once. */
export async function setTheme(next: Theme, origin?: { x: number; y: number }) {
  if (next === theme.value) return
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch { /* private mode: the choice lasts until the page is closed */ }

  const root = document.documentElement
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    apply(next)
    return
  }
  if (typeof document.startViewTransition !== 'function') {
    root.classList.add('theme-fade')
    apply(next)
    window.clearTimeout(fadeTimer)
    fadeTimer = window.setTimeout(() => root.classList.remove('theme-fade'), 400)
    return
  }

  running?.skipTransition()
  // `theme-capturing` freezes CSS transitions only while the browser snapshots the new look, so sliding
  // thumbs and tinted controls appear in their final state instead of mid-transition; removed as soon as ready.
  root.classList.add('theme-reveal', 'theme-capturing')
  const transition = document.startViewTransition(async () => {
    apply(next)
    await nextTick()   // Vue-rendered colors, charts, and sync canvas/WebGL redraws belong to the new snapshot
  })
  running = transition
  const x = origin?.x ?? window.innerWidth / 2
  const y = origin?.y ?? window.innerHeight / 2
  const radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y))
  try {
    await transition.ready
    root.classList.remove('theme-capturing')
    const anim = root.animate(
      { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
      { duration: REVEAL_MS, easing: 'cubic-bezier(0.4, 0, 0.2, 1)', pseudoElement: '::view-transition-new(root)' },
    )
    await Promise.allSettled([anim.finished, transition.finished])
  } catch { /* skipped (a second click, hidden tab): the new look is already applied */ } finally {
    if (running === transition) {
      running = null
      root.classList.remove('theme-reveal', 'theme-capturing')
    }
  }
}

watch(activeThemeDef, () => {
  if (activeThemeDef.value) apply(theme.value)
})

export const scenePalette = computed<ScenePalette>(() => {
  const base = isDark.value ? DARK_SCENE : LIGHT_SCENE
  const over = activeThemeDef.value?.scene
  if (!over) return base
  return { ...base, ...over }
})

export const chartPalette = computed<ChartPalette>(() => {
  const base = isDark.value ? DARK_CHARTS : LIGHT_CHARTS
  const over = activeThemeDef.value?.charts
  if (!over) return base
  return {
    series: over.series?.length ? over.series : base.series,
    axis: over.axis ? { ...base.axis, ...over.axis } : base.axis,
  }
})
