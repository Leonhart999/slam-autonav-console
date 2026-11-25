// Theme definition, live CSS token overrides, and viewport blue-light attenuation filter for eye_care_ui.
import { computed, ref, watch } from 'vue'
import { RC, THEME_ID } from './sdk'

export interface EyeCarePrefs {
  warmth: number         // 1..5
  strength: number       // 0..80 (%)
  softContrast: boolean
  overlayEnabled: boolean
}

const PREFS_KEY = 'robot_console.eye_care_ui.prefs'
const OVERLAY_ID = 'eye-care-blue-filter-overlay'

const DEFAULT_PREFS: EyeCarePrefs = {
  warmth: 3,
  strength: 40,
  softContrast: true,
  overlayEnabled: true,
}

function loadPrefs(): EyeCarePrefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY)
    if (!raw) return { ...DEFAULT_PREFS }
    const p = JSON.parse(raw) as Partial<EyeCarePrefs>
    return {
      warmth: Math.min(5, Math.max(1, Number(p.warmth ?? DEFAULT_PREFS.warmth) || 3)),
      strength: Math.min(80, Math.max(0, Number(p.strength ?? DEFAULT_PREFS.strength))),
      softContrast: p.softContrast !== undefined ? Boolean(p.softContrast) : DEFAULT_PREFS.softContrast,
      overlayEnabled: p.overlayEnabled !== undefined ? Boolean(p.overlayEnabled) : DEFAULT_PREFS.overlayEnabled,
    }
  } catch {
    return { ...DEFAULT_PREFS }
  }
}

export const prefs = ref<EyeCarePrefs>(loadPrefs())

interface WarmLevelSpec {
  page: string
  paper: string
  paperRgb: string
  cardSolid: string
  ink: string
  ink2: string
  ink3: string
  glass: string
  glassStrong: string
  glassEdge: string
  hairline: string
  drawerBg: string
  canvasBg: string
  bgGlow: string
  bgGrid: string
  kelvin: number
}

// 5 calibrated warm paper levels with WCAG >= 4.5:1 ink contrast and attenuated short-wavelength blue output
const LEVELS: Record<number, WarmLevelSpec> = {
  1: {
    page: '#f7f3ea',
    paper: '#fcf9f2',
    paperRgb: '252, 249, 242',
    cardSolid: '#faf6ec',
    ink: '#2b2418',
    ink2: '#5c4f3c',
    ink3: '#756650',
    glass: 'rgba(253, 249, 240, 0.72)',
    glassStrong: 'rgba(255, 251, 244, 0.90)',
    glassEdge: 'rgba(255, 250, 238, 0.95)',
    hairline: 'rgba(92, 68, 32, 0.11)',
    drawerBg: 'rgba(250, 245, 234, 0.90)',
    canvasBg: '#f4efe4',
    bgGlow:
      'radial-gradient(900px 620px at 8% -10%, rgba(234, 179, 8, 0.16), transparent 60%),' +
      'radial-gradient(820px 620px at 105% 112%, rgba(101, 163, 13, 0.12), transparent 60%),' +
      'linear-gradient(180deg, #faf6ed, #f2ecdf)',
    bgGrid: 'rgba(110, 82, 40, 0.055)',
    kelvin: 5200,
  },
  2: {
    page: '#f4eedf',
    paper: '#faf5e8',
    paperRgb: '250, 245, 232',
    cardSolid: '#f7f1e2',
    ink: '#292114',
    ink2: '#594b36',
    ink3: '#726148',
    glass: 'rgba(250, 244, 229, 0.72)',
    glassStrong: 'rgba(253, 248, 236, 0.90)',
    glassEdge: 'rgba(255, 248, 230, 0.92)',
    hairline: 'rgba(94, 68, 28, 0.12)',
    drawerBg: 'rgba(247, 240, 224, 0.92)',
    canvasBg: '#efe8d6',
    bgGlow:
      'radial-gradient(900px 620px at 8% -10%, rgba(217, 155, 38, 0.18), transparent 60%),' +
      'radial-gradient(820px 620px at 105% 112%, rgba(132, 169, 44, 0.14), transparent 60%),' +
      'linear-gradient(180deg, #f7f1e3, #ede4cf)',
    bgGrid: 'rgba(112, 80, 32, 0.06)',
    kelvin: 4600,
  },
  3: {
    page: '#efe6d1',
    paper: '#f6efe0',
    paperRgb: '246, 239, 224',
    cardSolid: '#f3ebd8',
    ink: '#261e10',
    ink2: '#55462f',
    ink3: '#6c5a3f',
    glass: 'rgba(247, 239, 220, 0.74)',
    glassStrong: 'rgba(250, 244, 228, 0.92)',
    glassEdge: 'rgba(255, 246, 222, 0.88)',
    hairline: 'rgba(96, 66, 22, 0.13)',
    drawerBg: 'rgba(244, 235, 214, 0.92)',
    canvasBg: '#ebe1ca',
    bgGlow:
      'radial-gradient(900px 620px at 8% -10%, rgba(217, 119, 6, 0.18), transparent 60%),' +
      'radial-gradient(820px 620px at 105% 112%, rgba(101, 163, 13, 0.14), transparent 60%),' +
      'linear-gradient(180deg, #f4ecda, #e9dec4)',
    bgGrid: 'rgba(105, 72, 24, 0.06)',
    kelvin: 4100,
  },
  4: {
    page: '#eae0c8',
    paper: '#f2e8d4',
    paperRgb: '242, 232, 212',
    cardSolid: '#eee3cb',
    ink: '#231a0d',
    ink2: '#504029',
    ink3: '#675439',
    glass: 'rgba(242, 232, 208, 0.76)',
    glassStrong: 'rgba(247, 238, 218, 0.92)',
    glassEdge: 'rgba(255, 242, 210, 0.84)',
    hairline: 'rgba(96, 64, 18, 0.14)',
    drawerBg: 'rgba(239, 229, 204, 0.94)',
    canvasBg: '#e4d9bf',
    bgGlow:
      'radial-gradient(900px 620px at 8% -10%, rgba(194, 102, 12, 0.20), transparent 60%),' +
      'radial-gradient(820px 620px at 105% 112%, rgba(92, 140, 22, 0.14), transparent 60%),' +
      'linear-gradient(180deg, #efe5ce, #e3d6b8)',
    bgGrid: 'rgba(98, 66, 20, 0.065)',
    kelvin: 3600,
  },
  5: {
    page: '#e5d8bc',
    paper: '#ede0c6',
    paperRgb: '237, 224, 198',
    cardSolid: '#e9dcc0',
    ink: '#20170a',
    ink2: '#4b3b23',
    ink3: '#604e32',
    glass: 'rgba(237, 224, 196, 0.78)',
    glassStrong: 'rgba(243, 232, 208, 0.93)',
    glassEdge: 'rgba(252, 237, 200, 0.80)',
    hairline: 'rgba(94, 60, 14, 0.16)',
    drawerBg: 'rgba(234, 221, 192, 0.95)',
    canvasBg: '#ded0b2',
    bgGlow:
      'radial-gradient(900px 620px at 8% -10%, rgba(180, 83, 9, 0.22), transparent 60%),' +
      'radial-gradient(820px 620px at 105% 112%, rgba(77, 124, 15, 0.15), transparent 60%),' +
      'linear-gradient(180deg, #eadec3, #dcccaa)',
    bgGrid: 'rgba(92, 60, 16, 0.07)',
    kelvin: 3200,
  },
}

export const isEyeCareActive = computed(() => RC().theme.current.value === THEME_ID)
export const activeSpec = computed(() => LEVELS[prefs.value.warmth] ?? LEVELS[3])
export const estimatedBlueCut = computed(() => {
  const baseCut = (prefs.value.warmth - 1) * 7 + 12
  const filterCut = prefs.value.overlayEnabled ? prefs.value.strength * 0.55 : 0
  return Math.min(82, Math.round(baseCut + filterCut))
})
export const estimatedKelvin = computed(() => {
  const spec = activeSpec.value
  const shift = prefs.value.overlayEnabled ? Math.round(prefs.value.strength * 6) : 0
  return Math.max(2700, spec.kelvin - shift)
})

export const EYE_CARE_TOKENS: Record<string, string> = {
  '--ink': '#261e10',
  '--ink-2': '#55462f',
  '--ink-3': '#6c5a3f',
  '--accent': '#a16207',
  '--accent-2': '#4d7c0f',
  '--accent-grad': 'linear-gradient(135deg, #c2780a 0%, #7c5c10 100%)',
  '--ok': '#2f7d4d',
  '--warn': '#b45309',
  '--bad': '#b91c1c',
  '--idle': '#786b57',
  '--glass': 'rgba(247, 239, 220, 0.74)',
  '--glass-strong': 'rgba(250, 244, 228, 0.92)',
  '--glass-edge': 'rgba(255, 246, 222, 0.88)',
  '--hairline': 'rgba(96, 66, 22, 0.13)',
  '--shadow': '0 1px 0 rgba(255, 250, 235, 0.7) inset, 0 0 0 1px rgba(96, 66, 22, 0.08), 0 10px 30px -14px rgba(82, 54, 16, 0.20)',
  '--page': '#efe6d1',
  '--paper': '#f6efe0',
  '--card-solid': '#f3ebd8',
  '--paper-rgb': '246, 239, 224',
  '--tint-rgb': '96, 66, 22',
  '--shade-rgb': '82, 54, 16',
  '--accent-rgb': '161, 98, 7',
  '--deepen': '#1e1609',
  '--overlay': 'rgba(42, 30, 12, 0.22)',
  '--drawer-bg': 'rgba(244, 235, 214, 0.92)',
  '--stage-glow': 'radial-gradient(ellipse at 50% 35%, rgba(252, 247, 235, 0.90), rgba(244, 235, 214, 0.55) 70%)',
  '--canvas-bg': '#ebe1ca',
  '--ramp': '#d9b976, #b58428, #6b8e23, #2f6f4e',
  '--line-est': '#9a5b13',
  '--grid-line': '#b8a88a',
  '--note-ink': '#78350f',
  '--note-ink-2': '#92400e',
  '--note-bg': 'rgba(254, 243, 199, 0.90)',
  '--note-edge': 'rgba(180, 83, 9, 0.35)',
  '--offline-bg': 'rgba(254, 226, 226, 0.78)',
  '--offline-edge': 'rgba(220, 38, 38, 0.45)',
  '--bg-glow': LEVELS[3].bgGlow,
  '--bg-grid': 'rgba(105, 72, 24, 0.06)',
  '--el-color-primary': '#9a5b13',
  '--el-color-primary-light-3': '#b57e3c',
  '--el-color-primary-light-5': '#cca26c',
  '--el-color-primary-light-7': '#e0c59f',
  '--el-color-primary-light-8': '#ebd6b9',
  '--el-color-primary-light-9': '#f6ead9',
  '--el-color-primary-dark-2': '#78450b',
}

export const EYE_CARE_SCENE = {
  ramp: [[0.85, 0.73, 0.46], [0.71, 0.52, 0.16], [0.42, 0.56, 0.14], [0.18, 0.44, 0.31]] as [
    [number, number, number], [number, number, number], [number, number, number], [number, number, number],
  ],
  gridMajor: 0xb09f80,
  gridMinor: 0xd9cdb3,
  estLine: 0x9a5b13,
  scanPoint: 0xb45309,
  robotAccent: '#9a5b13',
  robotRing: 0xc2780a,
  floorTint: 0xf7efe0,
  mapShade: 'rgba(140, 102, 42, 0.08)',
  canvasBg: '#ebe1ca',
  canvasEdge: 'rgba(96, 66, 22, 0.30)',
}

export const EYE_CARE_CHARTS = {
  series: ['#9a5b13', '#3f7d4c', '#b45309', '#6d5e3e', '#a1483b', '#4d7c0f', '#7c5c10', '#8c3b2b'],
  axis: {
    line: '#c5b79c',
    split: '#dfd4bc',
    label: '#6c5a3f',
    ink: '#4b3b23',
    tipBg: 'rgba(250, 244, 228, 0.96)',
    tipInk: '#261e10',
    paper: '#f6efe0',
  },
}

function ensureOverlay(): HTMLDivElement {
  let el = document.getElementById(OVERLAY_ID) as HTMLDivElement | null
  if (!el) {
    el = document.createElement('div')
    el.id = OVERLAY_ID
    el.setAttribute('aria-hidden', 'true')
    Object.assign(el.style, {
      position: 'fixed',
      inset: '0',
      zIndex: '99999',
      pointerEvents: 'none',
      transition: 'background-color 0.25s ease, backdrop-filter 0.25s ease',
      mixBlendMode: 'multiply',
    })
    document.body.appendChild(el)
  }
  return el
}

const LIVE_TOKEN_KEYS = [
  '--page', '--paper', '--paper-rgb', '--card-solid', '--ink', '--ink-2', '--ink-3',
  '--glass', '--glass-strong', '--glass-edge', '--hairline', '--drawer-bg',
  '--canvas-bg', '--bg-glow', '--bg-grid', '--stage-glow',
]

export function syncEyeCareEffects() {
  const root = document.documentElement
  const active = isEyeCareActive.value
  const spec = activeSpec.value

  if (!active) {
    const ov = document.getElementById(OVERLAY_ID)
    if (ov) {
      ov.style.backgroundColor = 'transparent'
      ov.style.backdropFilter = 'none'
    }
    for (const k of LIVE_TOKEN_KEYS) root.style.removeProperty(k)
    return
  }

  root.style.setProperty('--page', spec.page)
  root.style.setProperty('--paper', spec.paper)
  root.style.setProperty('--paper-rgb', spec.paperRgb)
  root.style.setProperty('--card-solid', spec.cardSolid)
  root.style.setProperty('--ink', spec.ink)
  root.style.setProperty('--ink-2', spec.ink2)
  root.style.setProperty('--ink-3', spec.ink3)
  root.style.setProperty('--glass', spec.glass)
  root.style.setProperty('--glass-strong', spec.glassStrong)
  root.style.setProperty('--glass-edge', spec.glassEdge)
  root.style.setProperty('--hairline', spec.hairline)
  root.style.setProperty('--drawer-bg', spec.drawerBg)
  root.style.setProperty('--canvas-bg', spec.canvasBg)
  root.style.setProperty('--bg-glow', spec.bgGlow)
  root.style.setProperty('--bg-grid', spec.bgGrid)
  root.style.setProperty('--stage-glow', prefs.value.softContrast
    ? 'radial-gradient(ellipse at 50% 35%, rgba(247, 239, 220, 0.88), rgba(235, 225, 202, 0.56) 70%)'
    : 'radial-gradient(ellipse at 50% 35%, rgba(255, 251, 240, 0.94), rgba(244, 235, 214, 0.58) 70%)')

  const ov = ensureOverlay()
  if (prefs.value.overlayEnabled && prefs.value.strength > 0) {
    // Warm amber multiply filter attenuates short-wavelength blue proportionally to strength
    const alpha = Math.min(0.28, (prefs.value.strength / 100) * 0.34)
    const contrast = prefs.value.softContrast ? 0.95 : 1.0
    const sepia = Math.min(0.22, (prefs.value.strength / 100) * 0.24)
    ov.style.backgroundColor = `rgba(255, 198, 92, ${alpha.toFixed(3)})`
    ov.style.backdropFilter = `sepia(${sepia.toFixed(3)}) contrast(${contrast.toFixed(2)})`
  } else {
    ov.style.backgroundColor = 'transparent'
    ov.style.backdropFilter = prefs.value.softContrast ? 'contrast(0.96)' : 'none'
  }
}

export function savePrefs(next: EyeCarePrefs) {
  prefs.value = { ...next }
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs.value))
  } catch { /* ignore storage failure */ }
  syncEyeCareEffects()
}

export function resetPrefs() {
  savePrefs({ ...DEFAULT_PREFS })
}

let synced = false
let configLoaded = false
export function initEyeCareRuntime() {
  if (!synced) {
    synced = true
    watch([isEyeCareActive, prefs], syncEyeCareEffects, { deep: true, immediate: true })
  }
  if (!configLoaded) {
    configLoaded = true
    void applyBackendDefaults()
  }
}

async function applyBackendDefaults() {
  try {
    const cfg = await RC().request<{
      default_filter_strength?: number
      default_warmth?: number
      auto_apply_on_load?: boolean
    }>('GET', '/api/ext/eye_care_ui/config')
    let hasSaved = false
    try { hasSaved = !!localStorage.getItem(PREFS_KEY) } catch {}
    if (!hasSaved) {
      savePrefs({
        warmth: Math.min(5, Math.max(1, Number(cfg.default_warmth ?? DEFAULT_PREFS.warmth) || 3)),
        strength: Math.min(80, Math.max(0, Number(cfg.default_filter_strength ?? DEFAULT_PREFS.strength))),
        softContrast: DEFAULT_PREFS.softContrast,
        overlayEnabled: DEFAULT_PREFS.overlayEnabled,
      })
    }
    if (cfg.auto_apply_on_load && RC().theme.current.value !== THEME_ID) {
      await RC().theme.set(THEME_ID)
    }
  } catch { /* offline or backend unavailable during isolated frontend check */ }
}
