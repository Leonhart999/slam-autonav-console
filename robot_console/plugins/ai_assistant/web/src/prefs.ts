// Preferences of the assistant panel, kept in this browser: the skin (classic or glass), how a question is sent,
// the typing reveal, keyboard shortcuts, and the look of the glass. The glass model, its presets and the tint maths
// come from ClipDeck (by the same authors): a white point and a black point say how bright the page under the
// glass shows through, and give a grey tint with an alpha; the text colour follows the tint unless it is set.
// The window glass is kept per console theme (light / dark), so each theme keeps its own tuning.
import { computed, reactive, ref, watch } from 'vue'

export type Skin = 'classic' | 'glass'
export type TextTone = 'auto' | 'light' | 'dark'
export type LiquidMode = 'standard' | 'polar' | 'prominent' | 'shader'
export type SendKey = 'enter' | 'ctrlEnter'
export type ShortcutId = 'togglePanel' | 'toggleHistory' | 'newChat'

/** Window glass: one set per console theme */
export interface WindowGlass {
  whitePoint: number
  blackPoint: number
  backdropBlur: number
  backdropSaturation: number
  rimLight: number
  edgeHighlight: number
  textTone: TextTone
  textHalo: number
  secondaryTextOpacity: number
}

export interface LiquidGlassPrefs {
  liquidEnabled: boolean
  liquidMode: LiquidMode
  displacementScale: number
  aberrationIntensity: number
  elasticity: number
  frost: number
  liquidSaturation: number
  liquidShade: number
}

export interface Prefs {
  skin: Skin
  sendKey: SendKey
  reveal: boolean
  shortcuts: Record<ShortcutId, string>
  cornerRadius: number
  animations: boolean
  glass: { light: WindowGlass; dark: WindowGlass }
  liquid: LiquidGlassPrefs
}

export interface Preset<T> { id: string; values: Partial<T> }

export const GLASS_PRESETS: Preset<WindowGlass>[] = [
  { id: 'smoke', values: { whitePoint: 164, blackPoint: 13, backdropBlur: 10, backdropSaturation: 100, rimLight: 0,
    edgeHighlight: 0, textTone: 'auto', textHalo: 0, secondaryTextOpacity: 56 } },
  { id: 'clear', values: { whitePoint: 255, blackPoint: 82, backdropBlur: 16, backdropSaturation: 160, rimLight: 100,
    edgeHighlight: 0, textTone: 'auto', textHalo: 0, secondaryTextOpacity: 46 } },
]

export const LIQUID_PRESETS: Preset<LiquidGlassPrefs>[] = [
  { id: 'subtle', values: { liquidMode: 'standard', displacementScale: 30, aberrationIntensity: 1, elasticity: 0.08,
    frost: 0.15, liquidSaturation: 120, liquidShade: 0 } },
  { id: 'standard', values: { liquidMode: 'standard', displacementScale: 70, aberrationIntensity: 2, elasticity: 0.15,
    frost: 0.25, liquidSaturation: 140, liquidShade: 0 } },
  { id: 'strong', values: { liquidMode: 'prominent', displacementScale: 120, aberrationIntensity: 3, elasticity: 0.3,
    frost: 0.3, liquidSaturation: 170, liquidShade: 0 } },
  { id: 'calm', values: { liquidMode: 'standard', displacementScale: 35, aberrationIntensity: 2, elasticity: 0.15,
    frost: 0.5, liquidSaturation: 140, liquidShade: 20 } },
]

export const DEFAULT_SHORTCUTS: Record<ShortcutId, string> = {
  togglePanel: 'Ctrl + /',
  toggleHistory: 'Alt + H',
  newChat: 'Alt + N',
}

const DEFAULT_WINDOW_DARK = GLASS_PRESETS[0].values as WindowGlass
const DEFAULT_WINDOW_LIGHT = GLASS_PRESETS[1].values as WindowGlass

export function defaultPrefs(): Prefs {
  return {
    skin: 'glass',
    sendKey: 'enter',
    reveal: true,
    shortcuts: { ...DEFAULT_SHORTCUTS },
    cornerRadius: 24,
    animations: true,
    glass: { light: { ...DEFAULT_WINDOW_LIGHT }, dark: { ...DEFAULT_WINDOW_DARK } },
    liquid: { ...(LIQUID_PRESETS[1].values as LiquidGlassPrefs), liquidEnabled: true },
  }
}

export const BLUR_MAX = 20
const GAP = 4
const MODES: LiquidMode[] = ['standard', 'polar', 'prominent', 'shader']
const TONES: TextTone[] = ['auto', 'light', 'dark']

function num(v: unknown, lo: number, hi: number, d: number) {
  return typeof v !== 'number' || Number.isNaN(v) ? d : Math.min(hi, Math.max(lo, v))
}

function normalizeWindow(raw: Partial<WindowGlass> | undefined, d: WindowGlass): WindowGlass {
  const a = raw ?? {}
  const whitePoint = Math.round(num(a.whitePoint, GAP, 255, d.whitePoint))
  const top = whitePoint - GAP
  return {
    whitePoint,
    blackPoint: Math.round(num(a.blackPoint, 0, top, Math.min(d.blackPoint, top))),
    backdropBlur: Math.round(num(a.backdropBlur, 0, BLUR_MAX, d.backdropBlur)),
    backdropSaturation: Math.round(num(a.backdropSaturation, 50, 250, d.backdropSaturation)),
    rimLight: Math.round(num(a.rimLight, 0, 100, d.rimLight)),
    edgeHighlight: Math.round(num(a.edgeHighlight, 0, 100, d.edgeHighlight)),
    textTone: TONES.includes(a.textTone as TextTone) ? (a.textTone as TextTone) : d.textTone,
    textHalo: Math.round(num(a.textHalo, 0, 100, d.textHalo)),
    secondaryTextOpacity: Math.round(num(a.secondaryTextOpacity, 30, 100, d.secondaryTextOpacity)),
  }
}

export function normalizePrefs(raw: unknown): Prefs {
  const d = defaultPrefs()
  const a = (raw && typeof raw === 'object' ? raw : {}) as Partial<Prefs>
  const l = (a.liquid ?? {}) as Partial<LiquidGlassPrefs>
  const sc = (a.shortcuts ?? {}) as Partial<Record<ShortcutId, string>>
  return {
    skin: a.skin === 'classic' ? 'classic' : a.skin === 'glass' ? 'glass' : d.skin,
    sendKey: a.sendKey === 'ctrlEnter' ? 'ctrlEnter' : 'enter',
    reveal: typeof a.reveal === 'boolean' ? a.reveal : d.reveal,
    shortcuts: {
      togglePanel: typeof sc.togglePanel === 'string' ? sc.togglePanel : d.shortcuts.togglePanel,
      toggleHistory: typeof sc.toggleHistory === 'string' ? sc.toggleHistory : d.shortcuts.toggleHistory,
      newChat: typeof sc.newChat === 'string' ? sc.newChat : d.shortcuts.newChat,
    },
    cornerRadius: Math.round(num(a.cornerRadius, 8, 40, d.cornerRadius)),
    animations: typeof a.animations === 'boolean' ? a.animations : d.animations,
    glass: {
      light: normalizeWindow(a.glass?.light, d.glass.light),
      dark: normalizeWindow(a.glass?.dark, d.glass.dark),
    },
    liquid: {
      liquidEnabled: typeof l.liquidEnabled === 'boolean' ? l.liquidEnabled : d.liquid.liquidEnabled,
      liquidMode: MODES.includes(l.liquidMode as LiquidMode) ? (l.liquidMode as LiquidMode) : d.liquid.liquidMode,
      displacementScale: Math.round(num(l.displacementScale, 0, 150, d.liquid.displacementScale)),
      aberrationIntensity: Math.round(num(l.aberrationIntensity, 0, 10, d.liquid.aberrationIntensity) * 10) / 10,
      elasticity: Math.round(num(l.elasticity, 0, 0.5, d.liquid.elasticity) * 100) / 100,
      frost: Math.round(num(l.frost, 0, 1, d.liquid.frost) * 100) / 100,
      liquidSaturation: Math.round(num(l.liquidSaturation, 50, 250, d.liquid.liquidSaturation)),
      liquidShade: Math.round(num(l.liquidShade, 0, 50, d.liquid.liquidShade)),
    },
  }
}

const STORE = 'robot_console.ai_assistant.prefs'
const OLD_SKIN_STORE = 'robot_console.ai_assistant.skin'

function load(): Prefs {
  try {
    const raw = localStorage.getItem(STORE)
    if (raw) return normalizePrefs(JSON.parse(raw))
  } catch { /* fall through to the defaults */ }
  const p = defaultPrefs()
  const old = localStorage.getItem(OLD_SKIN_STORE)
  if (old === 'classic' || old === 'glass') p.skin = old
  return p
}

/** The saved preferences */
export const prefs = reactive<Prefs>(load())
/** While the settings page is open: its unsaved copy, shown live in the panel */
export const draft = ref<Prefs | null>(null)
/** What the panel shows: the draft while settings are open, else the saved preferences */
export const shown = computed<Prefs>(() => draft.value ?? prefs)

export function savePrefs(next: Prefs) {
  Object.assign(prefs, normalizePrefs(JSON.parse(JSON.stringify(next))))
}
watch(prefs, (p) => localStorage.setItem(STORE, JSON.stringify(p)), { deep: true })

/** The console theme base (light / dark): the <html> element carries .dark in the dark look */
export const consoleDark = ref(document.documentElement.classList.contains('dark'))
new MutationObserver(() => {
  consoleDark.value = document.documentElement.classList.contains('dark')
}).observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })

export const skin = computed<Skin>(() => shown.value.skin)
export const isGlass = computed(() => skin.value === 'glass')
export const windowGlass = computed<WindowGlass>(() => shown.value.glass[consoleDark.value ? 'dark' : 'light'])
export const liquid = computed(() => ({ ...shown.value.liquid, animations: shown.value.animations }))

// <html data-aia-skin> lets the plugin's pages (outside the panel) follow the skin as well
watch(skin, (s) => { document.documentElement.dataset.aiaSkin = s }, { immediate: true })

export function matchPreset<T>(presets: Preset<T>[], values: T): Preset<T> | null {
  return presets.find((p) => Object.entries(p.values).every(([k, v]) => (values as Record<string, unknown>)[k] === v)) ?? null
}

// ---- tint maths (ClipDeck) ----
function srgbToLinear(c: number) {
  const a = c / 255
  return a <= 0.03928 ? a / 12.92 : ((a + 0.055) / 1.055) ** 2.4
}

/** The grey and alpha that map the page's white to whitePoint and its black to blackPoint */
export function tintOf(g: WindowGlass) {
  const alpha = 1 - (g.whitePoint - g.blackPoint) / 255
  return { gray: alpha > 0 ? g.blackPoint / alpha : 0, alpha }
}

/** 'light' text (on dark glass) or 'dark' text, from the mid-grey seen through the glass */
export function textToneOf(g: WindowGlass): 'light' | 'dark' {
  if (g.textTone !== 'auto') return g.textTone
  const lum = srgbToLinear(g.blackPoint + (128 / 255) * (g.whitePoint - g.blackPoint))
  return 1.05 / (lum + 0.05) >= (lum + 0.05) / 0.05 ? 'light' : 'dark'
}

/** CSS variables and the tone attribute for the glass panel */
export function windowStyle(g: WindowGlass, radius: number) {
  const tone = textToneOf(g)
  const t = tintOf(g)
  const halo = g.textHalo / 100
  const rgb = tone === 'light' ? '0, 0, 0' : '255, 255, 255'
  return {
    tone: tone === 'light' ? 'dark' : 'light', // the glass is dark when its text is light
    style: {
      '--tint-gray': String(Number(t.gray.toFixed(2))),
      '--tint-alpha': String(Number(t.alpha.toFixed(4))),
      '--window-radius': `${radius}px`,
      '--backdrop-blur': `${g.backdropBlur}px`,
      '--backdrop-saturation': `${g.backdropSaturation}%`,
      '--rim': String(g.rimLight / 100),
      '--edge-highlight': String(g.edgeHighlight / 100),
      '--text-secondary-alpha': String(g.secondaryTextOpacity / 100),
      '--text-halo': halo > 0 ? `0 0 2px rgba(${rgb}, ${(0.6 * halo).toFixed(3)}), 0 0 8px rgba(${rgb}, ${(0.45 * halo).toFixed(3)})` : 'none',
    } as Record<string, string>,
  }
}

// ---- shortcuts (ClipDeck's matcher): "Ctrl + Shift + K", "Alt + H", "Ctrl + /" ----
function keyName(k: string) {
  const a = k.trim().toLowerCase()
  return a === ' ' || a === 'spacebar' ? 'space' : a === 'esc' ? 'escape' : a === 'return' ? 'enter' : a
}

export function matchShortcut(e: KeyboardEvent, combo: string): boolean {
  const parts = combo.split('+').map(keyName).filter(Boolean)
  // "Ctrl + +" splits into an empty last part: treat a trailing "+" as the key
  if (/\+\s*\+\s*$/.test(combo)) parts.push('+')
  if (!parts.length) return false
  let alt = false, ctrl = false, meta = false, shift = false
  let key: string | null = null
  for (const p of parts) {
    if (p === 'alt') alt = true
    else if (p === 'ctrl' || p === 'control') ctrl = true
    else if (p === 'meta' || p === 'win' || p === 'cmd') meta = true
    else if (p === 'shift') shift = true
    else key = p
  }
  if (!key) return false
  // letters: compare the physical key so Alt+H still matches when Alt changes e.key (e.g. on macOS)
  const pressed = /^[a-z]$/.test(key) && e.code?.startsWith('Key') ? e.code.slice(3).toLowerCase() : keyName(e.key)
  // Ctrl also matches Cmd on a Mac (unless the combo names Meta itself)
  const ctrlOk = ctrl ? e.ctrlKey || (!meta && e.metaKey) : !e.ctrlKey
  const metaOk = meta ? e.metaKey : ctrl || !e.metaKey
  return pressed === key && e.altKey === alt && ctrlOk && metaOk && e.shiftKey === shift
}

/** The combo of a key event, written the same way ("Ctrl + Shift + K"); null for a lone modifier */
export function comboOf(e: KeyboardEvent): string | null {
  if (['Control', 'Alt', 'Shift', 'Meta'].includes(e.key)) return null
  const parts: string[] = []
  if (e.ctrlKey) parts.push('Ctrl')
  if (e.altKey) parts.push('Alt')
  if (e.shiftKey) parts.push('Shift')
  if (e.metaKey) parts.push('Meta')
  let k = e.code?.startsWith('Key') ? e.code.slice(3) : e.code?.startsWith('Digit') ? e.code.slice(5) : e.key
  if (k === ' ') k = 'Space'
  parts.push(k.length === 1 ? k.toUpperCase() : k)
  return parts.join(' + ')
}
