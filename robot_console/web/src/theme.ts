// Light / dark appearance. The choice is kept in localStorage; index.html applies it before the first paint.
// CSS reads the tokens of style.css (`html.dark` overrides them); canvases and WebGL scenes, which cannot read
// CSS, take their colors from `scenePalette`.
import { computed, nextTick, ref } from 'vue'

export type Theme = 'light' | 'dark'
export const THEMES: Theme[] = ['light', 'dark']

const STORAGE_KEY = 'robot_console.theme'   // same key as the inline script in index.html
const PAGE_COLOR: Record<Theme, string> = { light: '#eef2f8', dark: '#080d18' }
const REVEAL_MS = 520

function savedTheme(): Theme {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

export const theme = ref<Theme>(savedTheme())
export const isDark = computed(() => theme.value === 'dark')

function apply(next: Theme) {
  theme.value = next
  document.documentElement.classList.toggle('dark', next === 'dark')
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', PAGE_COLOR[next])
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

// ---------------- Colors for canvases and WebGL ----------------

type RGB = [number, number, number]
export interface ScenePalette {
  /** height ramp of point clouds, low to high (linear 0..1 components) */
  ramp: [RGB, RGB, RGB, RGB]
  gridMajor: number
  gridMinor: number
  /** estimated trajectory on the mapping stage */
  estLine: number
  /** multiplied onto the occupancy grid used as the 3D floor */
  floorTint: number
  /** drawn over the 2D occupancy grid to take the glare off a white map; null = leave the map as it is */
  mapShade: string | null
  /** letterbox around the grid in the map editor, and the outline of the grid */
  canvasBg: string
  canvasEdge: string
}

const LIGHT_SCENE: ScenePalette = {
  ramp: [[0.6, 0.86, 0.93], [0.02, 0.64, 0.8], [0.15, 0.39, 0.92], [0.33, 0.25, 0.85]],
  gridMajor: 0x9db4cc,
  gridMinor: 0xd5dfea,
  estLine: 0x2563eb,
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
  floorTint: 0xb4bfd0,
  mapShade: 'rgba(8, 13, 24, 0.3)',
  canvasBg: '#0d1424',
  canvasEdge: 'rgba(150,180,230,0.3)',
}

export const scenePalette = computed(() => (isDark.value ? DARK_SCENE : LIGHT_SCENE))
