<!-- Header button + chat panel of the AI assistant. The panel floats on the right (not modal), so the user can keep
     it open while moving between pages; the current page is sent with every question. The header moves the panel and
     its edges and corners resize it, always inside the area between the header clock and the E-STOP bar. -->
<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElButton, ElInput, ElTooltip } from 'element-plus'
import { renderMarkdown } from './markdown'
import { ACCESS_LEVELS, ChatError, confirmCall, getSession, getStatus, streamChat, testConnection, type Access,
  type ChatEvent, type Item, type Status, type Tokens } from './api'
import { compact } from './format'
import HistoryPane from './HistoryPane.vue'
import { RC, codeText, t } from './sdk'
import { DRAG_SPRING, createSpring } from './spring'
import { finishReveal, vReveal } from './reveal'
import Icon from './Icon.vue'
import CopyButton from './CopyButton.vue'
import LikeButton from './LikeButton.vue'
import ModelMenu from './ModelMenu.vue'
import AccessMenu from './AccessMenu.vue'
import { vSpot } from './skin'
import { draft, isGlass, matchShortcut, prefs, shown, windowGlass, windowStyle } from './prefs'
import LiquidGlass from './glass/LiquidGlass.vue'
import EdgeGlow from './glass/EdgeGlow.vue'
import OverlayScrollbar from './glass/OverlayScrollbar.vue'
import SettingsView from './SettingsView.vue'
import { useHoverLens } from './glass/lens'
import { ripple } from './glass/ripple'

const STORE = 'robot_console.ai_assistant.session'
const OFFSET_STORE = 'robot_console.ai_assistant.offset'
const GEOMETRY_STORE = 'robot_console.ai_assistant.geometry'
const LIKES_STORE = 'robot_console.ai_assistant.likes'
const ACCESS_STORE = 'robot_console.ai_assistant.access'
const DOCK_STORE = 'robot_console.ai_assistant.history.docked'
const DOCK_WIDTH = 720
const SUGGESTIONS = ['latest', 'batch', 'zdrift', 'params', 'status', 'runBatch']
// round glass avatars of the suggestions in the glass skin: icon and tint
const SUGGESTION_ICONS: Record<string, [string, number]> = {
  latest: ['gauge', 0], batch: ['sliders', 1], zdrift: ['search', 2], params: ['wrench', 1], status: ['power', 3], runBatch: ['bolt', 2],
}

const open = ref(false)
const status = ref<Status | null>(null)
const statusError = ref('')
const items = ref<Item[]>([])
const input = ref('')
const busy = ref(false)
const switching = ref(false)
const busyText = ref('')
const usage = ref('')
const testing = ref(false)
const sessionId = ref(localStorage.getItem(STORE) || '')
const list = ref<HTMLElement | null>(null)
const panel = ref<HTMLElement | null>(null)
const expanded = ref<Set<string>>(new Set())
const startedAt = ref(0)
const now = ref(0)
let controller: AbortController | null = null
let loadController: AbortController | null = null
let loadSeq = 0
let sessionEpoch = 0
let loaded = false
let ticker = 0
// set when a new answer is taller than the list: keep its first line in view instead of jumping to the bottom
let holdScroll = false

const ready = computed(() => !!status.value && !status.value.problem)
const service = computed(() => {
  const s = status.value
  if (!s || !s.active) return ''
  const base = s.base_url || (s.provider === 'anthropic' ? 'api.anthropic.com' : 'api.openai.com')
  return `${base.replace(/^https?:\/\//, '').replace(/\/.*$/, '')} · ${s.model || '?'}`
})

// ---- what the assistant may do (access menu under the input), kept in this browser ----
const storedAccess = localStorage.getItem(ACCESS_STORE) as Access | null
const access = ref<Access>(storedAccess && ACCESS_LEVELS.includes(storedAccess) ? storedAccess : 'read')
watch(access, (a) => localStorage.setItem(ACCESS_STORE, a))

// ---- the settings page inside the panel; the glass look ----
const view = ref<'chat' | 'settings'>('chat')
function openPanelSettings() {
  draft.value = JSON.parse(JSON.stringify(prefs))
  view.value = 'settings'
}
function closePanelSettings() {
  draft.value = null
  view.value = 'chat'
}
const glassLook = computed(() => windowStyle(windowGlass.value, shown.value.cornerRadius))
const headGroup = ref<HTMLElement | null>(null)
const headLens = ref<InstanceType<typeof LiquidGlass> | null>(null)
const headLensEl = computed(() => headLens.value?.root ?? null)
useHoverLens(headGroup, headLensEl, isGlass)
const mainEl = ref<HTMLElement | null>(null)
let stopRipple: (() => void) | null = null

/** New chat from the header: in the glass look a ring of liquid runs out from the button over the conversation */
function newChatFromHeader(e: MouseEvent) {
  if (busy.value || !items.value.length) return
  newChat()
  const host = mainEl.value
  if (!isGlass.value || !host || !shown.value.animations) return
  const b = (e.currentTarget as HTMLElement).getBoundingClientRect()
  stopRipple?.()
  stopRipple = ripple(host, b.left + b.width / 2, b.top + b.height / 2)
}

function onMenuError(text: string) {
  items.value.push({ kind: 'note', text, tone: 'error' })
  scrollDown()
}
const elapsed = computed(() => Math.max(0, Math.floor((now.value - startedAt.value) / 1000)))

async function refreshStatus() {
  try {
    status.value = await getStatus()
    statusError.value = ''
  } catch (e) {
    statusError.value = RC().errorText(e)
  }
}

async function restore() {
  if (loaded || !sessionId.value) return
  loaded = true
  await loadSession(sessionId.value, true)
}

/** Show a stored conversation (from the history list, the usage page or the last one of this browser) */
async function loadSession(id: string, quiet = false) {
  loadController?.abort()
  const ac = loadController = new AbortController()
  const seq = ++loadSeq
  switching.value = true
  try {
    const s = await getSession(id, ac.signal)
    if (seq !== loadSeq || ac.signal.aborted) return false
    sessionEpoch++
    items.value = s.items
    sessionId.value = s.id
    const u = s.usage
    usage.value = u && (u.input || u.output || u.cache_read || u.cache_write) ? sessionUsageText(u) : ''
    holdScroll = false
    expanded.value = new Set()
    scrollDown()
    return true
  } catch (e) {
    if (seq !== loadSeq || ac.signal.aborted) return false
    if (id === sessionId.value) {
      sessionId.value = ''
      localStorage.removeItem(STORE)
    }
    if (!quiet) onMenuError(RC().errorText(e))
    return false
  } finally {
    if (seq === loadSeq) {
      switching.value = false
      if (loadController === ac) loadController = null
    }
  }
}

function sessionUsageText(u: Partial<Tokens>) {
  return t('sessionUsage', {
    input: compact(u.input), output: compact(u.output), cache: compact((u.cache_read || 0) + (u.cache_write || 0)),
  })
}

/** "in 1.2k · out 860 · cache 900" under an answer; cache parts only when present */
function tokenText(u: Tokens) {
  const parts = [t('turn.in', { n: compact(u.input) }), t('turn.out', { n: compact(u.output) })]
  if (u.cache_read) parts.push(t('turn.cache', { n: compact(u.cache_read) }))
  if (u.cache_write) parts.push(t('turn.cacheWrite', { n: compact(u.cache_write) }))
  return parts.join(' · ')
}

// ---- history: an overlay on narrow panels, a docked column on wide ones ----
const historyOpen = ref(false)
const panelWidth = ref(0)
const dockPref = ref(localStorage.getItem(DOCK_STORE) === '1')
const wide = computed(() => panelWidth.value >= DOCK_WIDTH)
const docked = computed(() => wide.value && dockPref.value)
const showHistory = computed(() => docked.value || historyOpen.value)
const historyReload = ref(0)
const pendingOpen = ref<string | null>(null)       // conversation to open once the running answer is stopped

function toggleHistory() {
  if (wide.value) {
    dockPref.value = !dockPref.value
    localStorage.setItem(DOCK_STORE, dockPref.value ? '1' : '0')
    historyOpen.value = false
  } else {
    historyOpen.value = !historyOpen.value
  }
}

async function openFromHistory(id: string) {
  if (id === sessionId.value && items.value.length) {
    // back to the conversation already on screen: a switch to another one still loading is cancelled, so the last
    // click wins (A, then B, then A again stays on A)
    if (switching.value) {
      loadController?.abort()
      loadController = null
      loadSeq++
      switching.value = false
    }
    if (!docked.value) historyOpen.value = false
    return
  }
  if (busy.value) {
    pendingOpen.value = id
    return
  }
  if (await loadSession(id) && !docked.value) historyOpen.value = false
}

async function stopAndOpen() {
  const id = pendingOpen.value
  if (!id) return
  stop()
  // wait until the stream has ended (the server releases the conversation with it)
  for (let i = 0; i < 100 && busy.value; i++) await new Promise((r) => window.setTimeout(r, 50))
  pendingOpen.value = null
  if (await loadSession(id) && !docked.value) historyOpen.value = false
}

function onHistoryNew() {
  newChat()
  if (!docked.value) historyOpen.value = false
}

function onHistoryDeleted(id: string) {
  if (id === sessionId.value) newChat()
}

/** Another page (the usage page) asks to show a conversation */
function onOpenSession(e: Event) {
  const id = (e as CustomEvent<string>).detail
  if (typeof id !== 'string' || !id) return
  open.value = true
  loaded = true
  void openFromHistory(id)
}

watch(open, async (o) => {
  if (!o) {
    if (view.value === 'settings') closePanelSettings()
    return
  }
  await Promise.all([refreshStatus(), restore()])
  scrollDown()
})
watch(busy, (b) => { if (!b) historyReload.value++ })
watch(sessionId, (id) => (id ? localStorage.setItem(STORE, id) : localStorage.removeItem(STORE)))

function scrollDown() {
  if (holdScroll) return
  void nextTick(() => { if (list.value) list.value.scrollTop = list.value.scrollHeight })
}

/** Scroll for a new answer: to the bottom when it fits, else so that its first line is at the top */
function showAnswer() {
  void nextTick(() => {
    const box = list.value
    const all = box?.querySelectorAll<HTMLElement>('.aia-answer-wrap')
    const last = all?.[all.length - 1]
    if (!box || !last) return
    if (last.offsetHeight > box.clientHeight - 24) {
      box.scrollTop = last.offsetTop - 12
      holdScroll = true
    } else {
      box.scrollTop = box.scrollHeight
    }
  })
}

function newChat() {
  if (busy.value) return
  loadController?.abort()
  loadController = null
  loadSeq++
  switching.value = false
  sessionEpoch++
  pendingOpen.value = null
  items.value = []
  usage.value = ''
  sessionId.value = ''
  holdScroll = false
}

function toolLabel(it: Extract<Item, { kind: 'tool' }>) {
  const key = `tools.${it.name}`
  const text = t(key, { ...it.input })
  return text === `plugins.ai_assistant.${key}` ? t('tools.other', { name: it.name }) : text
}

function toggleTool(id: string) {
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}

// "Helpful" marks, per conversation and answer position, kept in this browser only
function loadLikes(): Set<string> {
  try {
    const raw = JSON.parse(localStorage.getItem(LIKES_STORE) || '[]') as unknown
    return new Set(Array.isArray(raw) ? raw.filter((x): x is string => typeof x === 'string') : [])
  } catch {
    return new Set()
  }
}
const likes = ref<Set<string>>(loadLikes())
const likeKey = (i: number) => `${sessionId.value}:${i}`
function toggleLike(i: number) {
  const next = new Set(likes.value)
  const k = likeKey(i)
  if (next.has(k)) next.delete(k)
  else next.add(k)
  likes.value = next
  localStorage.setItem(LIKES_STORE, JSON.stringify([...next].slice(-500)))
}

function toolItem(id: string) {
  // the newest call with this id: some services number their tool calls per answer (call_0, call_1, ...)
  for (let i = items.value.length - 1; i >= 0; i--) {
    const it = items.value[i]
    if (it.kind === 'tool' && it.id === id) return it
  }
  return null
}
let lastRound = 1
let turnTokens: Tokens | null = null

// ---- run tools waiting for the user's approval (access "ask") ----
const answering = ref<Set<string>>(new Set())
async function decide(id: string, approve: boolean) {
  const it = toolItem(id)
  if (!it || it.decision !== 'pending' || answering.value.has(id)) return
  answering.value = new Set([...answering.value, id])
  try {
    await confirmCall(sessionId.value, id, approve)
    it.decision = approve ? 'approved' : 'denied'
  } catch (e) {
    onMenuError(RC().errorText(e))
  } finally {
    const next = new Set(answering.value)
    next.delete(id)
    answering.value = next
  }
}

/** Arguments of a run tool call as label / value pairs for its approval card */
function argRows(input: Record<string, unknown>) {
  return Object.entries(input).map(([k, v]) => {
    let value: string
    if (Array.isArray(v)) value = t('confirm.items', { n: v.length })
    else if (typeof v === 'boolean') value = t(v ? 'confirm.yes' : 'confirm.no')
    else value = String(v)
    const key = `argNames.${k}`
    const label = t(key)
    return { key: k, label: label === `plugins.ai_assistant.${key}` ? k : label, value }
  })
}

function onEvent(e: ChatEvent) {
  switch (e.type) {
    case 'session':
      if (sessionId.value && e.id !== sessionId.value) items.value.splice(items.value.length - 1, 0, { kind: 'note', text: t('expired'), tone: 'info' })
      sessionId.value = e.id
      break
    case 'status':
      lastRound = e.round
      busyText.value = e.state === 'confirm' ? t('confirm.waiting') : t('thinking', { round: e.round })
      break
    case 'text':
      if (e.text) {
        items.value.push({ kind: 'assistant', text: e.text, final: e.final, live: true })
        showAnswer()
      }
      if (e.truncated) items.value.push({ kind: 'note', text: t('truncated'), tone: 'info' })
      break
    case 'tool':
      items.value.push({ kind: 'tool', id: e.id, name: e.name, input: e.input, state: 'running', tool: e.kind ?? 'read' })
      break
    case 'confirm': {
      const it = toolItem(e.id)
      if (it) it.decision = 'pending'
      break
    }
    case 'tool_result': {
      const it = toolItem(e.id)
      if (it) {
        it.state = e.ok ? 'ok' : 'error'
        if (e.error) it.error = e.error
        if (e.decision) it.decision = e.decision
        else if (it.decision === 'pending') it.decision = 'approved'
      }
      if (busy.value) busyText.value = t('thinking', { round: lastRound })
      break
    }
    case 'usage': {
      const u = e.usage
      const prev = turnTokens ?? { input: 0, output: 0, cache_read: 0, cache_write: 0, reasoning: 0 }
      turnTokens = {
        input: prev.input + (u.input || 0), output: prev.output + (u.output || 0), cache_read: prev.cache_read + (u.cache_read || 0),
        cache_write: prev.cache_write + (u.cache_write || 0), reasoning: prev.reasoning + (u.reasoning || 0),
      }
      break
    }
    case 'done': {
      usage.value = t('turnUsage', { model: e.model, input: compact(e.usage.input), output: compact(e.usage.output), rounds: e.rounds })
      // the final answer shows the sums of the whole turn
      const last = [...items.value].reverse().find((x) => x.kind === 'assistant')
      if (last && last.kind === 'assistant' && last.final) last.tokens = { ...(turnTokens ?? {}), ...e.usage } as Tokens
      break
    }
    case 'error':
      items.value.push({ kind: 'note', text: e.code === 'cancelled' ? t('stopped') : codeText(e.code, e.params), tone: e.code === 'cancelled' ? 'info' : 'error' })
      break
  }
  if (e.type !== 'text') scrollDown()
}

async function send(text?: string) {
  const message = (text ?? input.value).trim()
  if (!message || busy.value || switching.value) return
  const epoch = sessionEpoch
  input.value = ''
  usage.value = ''
  holdScroll = false
  items.value.push({ kind: 'user', text: message })
  turnTokens = null
  busy.value = true
  busyText.value = t('thinking', { round: 1 })
  startedAt.value = now.value = Date.now()
  ticker = window.setInterval(() => (now.value = Date.now()), 1000)
  controller = new AbortController()
  scrollDown()
  try {
    await streamChat({ message, session: sessionId.value || undefined, page: location.hash.replace(/^#/, ''),
      access: access.value }, (e) => { if (epoch === sessionEpoch) onEvent(e) }, controller.signal)
  } catch (e) {
    if (epoch === sessionEpoch) {
      const err = e instanceof ChatError ? e : new ChatError('unknown', { reason: String(e) })
      onEvent({ type: 'error', code: err.code, params: err.params })
      if (err.code !== 'cancelled') void refreshStatus()
    }
  } finally {
    if (epoch === sessionEpoch) {
      for (const it of items.value) if (it.kind === 'tool' && it.decision === 'pending') it.decision = 'cancelled'
      scrollDown()
    }
    busy.value = false
    controller = null
    window.clearInterval(ticker)
  }
}

function stop() {
  controller?.abort()
}

function onKey(ev: Event | KeyboardEvent) {
  const e = ev as KeyboardEvent
  if (e.key !== 'Enter' || e.isComposing) return
  const sends = prefs.sendKey === 'ctrlEnter' ? (e.ctrlKey || e.metaKey) && !e.shiftKey : !e.shiftKey && !e.ctrlKey && !e.metaKey
  if (sends) {
    e.preventDefault()
    void send()
  }
}

async function test() {
  testing.value = true
  try {
    const r = await testConnection()
    items.value.push({ kind: 'note', text: t('testOk', { model: r.model, seconds: r.seconds }), tone: 'info' })
  } catch (e) {
    items.value.push({ kind: 'note', text: RC().errorText(e), tone: 'error' })
  } finally {
    testing.value = false
    scrollDown()
  }
}

/** The model services page of the console */
function openSettings() {
  location.hash = '#/ext/ai_assistant/providers'
}

function skipReveal(e: MouseEvent) {
  finishReveal(e.currentTarget as HTMLElement)
}

// Rendered answers, so that re-rendering the panel (a new status after a model or effort change, a resize) does not
// parse every answer again while something is animating
const mdCache = new Map<string, string>()
function markdown(text: string) {
  let html = mdCache.get(text)
  if (html === undefined) {
    html = renderMarkdown(text)
    if (mdCache.size >= 300) mdCache.delete(mdCache.keys().next().value as string)
    mdCache.set(text, html)
  }
  return html
}

// ---- where the panel sits and how big it is ----
// The panel can be moved and resized anywhere inside the window, 14 px from each edge. By default it is docked
// below the header clock (DOCK_TOP) and above the status bar (DOCK_BOTTOM), so the E-STOP button stays visible until
// the user moves the panel over it. Its right edge is docked at the window's right side; x (<= 0) moves it left from
// there, y is its top edge. Dragging the header moves it on springs (1200 / 80: follows the pointer, never
// overshoots); dragging an edge or a corner resizes it directly. Height `null` = the docked height (DOCK_TOP to
// DOCK_BOTTOM), which follows the window. Kept in this browser.
const DOCK_TOP = 64
const DOCK_BOTTOM = 84
const SIDE = 14
const DEFAULT_W = 460
const MIN_W = 340
const MIN_H = 360
type Dir = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw'
const RESIZE_DIRS: Dir[] = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw']
interface Geometry { x: number; y: number; w: number; h: number | null }

function loadGeometry(): Geometry {
  try {
    const g = JSON.parse(localStorage.getItem(GEOMETRY_STORE) || 'null') as Partial<Geometry> | null
    if (g && typeof g === 'object') {
      const num = (v: unknown, d: number) => (typeof v === 'number' && Number.isFinite(v) ? v : d)
      return { x: num(g.x, 0), y: num(g.y, DOCK_TOP), w: num(g.w, DEFAULT_W), h: typeof g.h === 'number' ? num(g.h, 0) || null : null }
    }
  } catch { /* fall through to the default */ }
  // the sideways offset of plugin versions before the resizable panel
  return { x: Math.min(0, Number(localStorage.getItem(OFFSET_STORE)) || 0), y: DOCK_TOP, w: DEFAULT_W, h: null }
}

const geo = loadGeometry()
const maxH = () => Math.max(200, window.innerHeight - 2 * SIDE)
const dockH = () => Math.min(maxH(), Math.max(200, window.innerHeight - DOCK_TOP - DOCK_BOTTOM))
const maxW = () => Math.max(240, window.innerWidth - 2 * SIDE)
const minW = () => Math.min(MIN_W, maxW())
const minH = () => Math.min(MIN_H, maxH())
const panelH = () => (geo.h === null ? dockH() : Math.max(minH(), Math.min(maxH(), geo.h)))
const panelW = () => Math.max(minW(), Math.min(maxW(), geo.w))
const clampX = (x: number) => Math.max(Math.min(0, -(window.innerWidth - 2 * SIDE - panelW())), Math.min(0, x))
const clampY = (y: number) => Math.max(SIDE, Math.min(window.innerHeight - SIDE - panelH(), y))

const dragging = ref(false)
const resizing = ref<Dir | null>(null)
let springX = 0
let springY = DOCK_TOP
function paintTranslate() {
  if (panel.value) panel.value.style.translate = `${springX.toFixed(2)}px ${springY.toFixed(2)}px`
}
const moveX = createSpring(geo.x, DRAG_SPRING, (x) => { springX = x; paintTranslate() })
const moveY = createSpring(geo.y, DRAG_SPRING, (y) => { springY = y; paintTranslate() })

/** Put the panel where `geo` says, clamped to the window, without animation */
function applyGeometry() {
  const el = panel.value
  if (!el) return
  geo.w = panelW()
  if (geo.h !== null) geo.h = panelH()
  el.style.width = `${geo.w}px`
  el.style.height = `${panelH()}px`
  panelWidth.value = geo.w
  geo.x = clampX(moveX.target)
  geo.y = clampY(moveY.target)
  moveX.jump(geo.x)
  moveY.jump(geo.y)
}

function saveGeometry() {
  localStorage.setItem(GEOMETRY_STORE, JSON.stringify({ x: Math.round(geo.x), y: Math.round(geo.y), w: Math.round(geo.w), h: geo.h === null ? null : Math.round(geo.h) }))
  localStorage.removeItem(OFFSET_STORE)
}

// moving by the header
let dragStart = { px: 0, py: 0, x: 0, y: 0 }
function onHeadDown(e: PointerEvent) {
  const target = e.target as HTMLElement
  if (e.button !== 0 || target.closest('button, a, input, textarea, .aia-menu') || !target.closest('.aia-head, .aia-set-header')) return
  dragging.value = true
  dragStart = { px: e.clientX, py: e.clientY, x: moveX.target, y: moveY.target }
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  e.preventDefault()
}

function onHeadMove(e: PointerEvent) {
  if (!dragging.value) return
  geo.x = clampX(dragStart.x + e.clientX - dragStart.px)
  geo.y = clampY(dragStart.y + e.clientY - dragStart.py)
  moveX.to(geo.x)
  moveY.to(geo.y)
}

function onHeadUp() {
  if (!dragging.value) return
  dragging.value = false
  saveGeometry()
}

/** Double-click on the header: back to the right side, at the top of the safe area */
function dock() {
  geo.x = 0
  geo.y = DOCK_TOP
  moveX.to(0)
  moveY.to(DOCK_TOP)
  saveGeometry()
}

// resizing by an edge or a corner: the opposite edges stay where they are
let resizeStart = { px: 0, py: 0, left: 0, right: 0, top: 0, bottom: 0 }
function onResizeDown(e: PointerEvent, dir: Dir) {
  if (e.button !== 0 || !panel.value) return
  const right = window.innerWidth - SIDE + moveX.target
  const top = moveY.target
  resizeStart = { px: e.clientX, py: e.clientY, left: right - panelW(), right, top, bottom: top + panelH() }
  moveX.jump(moveX.target)
  moveY.jump(moveY.target)
  resizing.value = dir
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  e.preventDefault()
}

function onResizeMove(e: PointerEvent) {
  const dir = resizing.value
  const el = panel.value
  if (!dir || !el) return
  const s = resizeStart
  const dx = e.clientX - s.px
  const dy = e.clientY - s.py
  let { left, right, top, bottom } = s
  const winRight = window.innerWidth - SIDE
  const winBottom = window.innerHeight - SIDE
  if (dir.includes('w')) left = Math.max(SIDE, Math.min(right - minW(), left + dx))
  if (dir.includes('e')) right = Math.min(winRight, Math.max(left + minW(), right + dx))
  if (dir.includes('n')) top = Math.max(SIDE, Math.min(bottom - minH(), top + dy))
  if (dir.includes('s')) bottom = Math.min(winBottom, Math.max(top + minH(), bottom + dy))
  geo.w = right - left
  geo.x = right - winRight
  geo.y = top
  geo.h = bottom - top
  el.style.width = `${geo.w}px`
  el.style.height = `${bottom - top}px`
  panelWidth.value = geo.w
  moveX.jump(geo.x)
  moveY.jump(geo.y)
}

function onResizeUp() {
  if (!resizing.value) return
  resizing.value = null
  saveGeometry()
}

/** Double-click on an edge: that size back to its default (width 460 px, full height), with a short glide */
function resetSize(dir: Dir) {
  const el = panel.value
  if (!el) return
  if (dir.includes('e') || dir.includes('w')) geo.w = DEFAULT_W
  if (dir.includes('n') || dir.includes('s')) { geo.h = null; geo.y = DOCK_TOP }
  el.classList.add('aia-gliding')
  panelWidth.value = panelW()
  el.style.width = `${panelW()}px`
  el.style.height = `${panelH()}px`
  geo.x = clampX(geo.x)
  moveX.to(geo.x)
  moveY.to(clampY(geo.y))
  geo.y = clampY(geo.y)
  window.setTimeout(() => el.classList.remove('aia-gliding'), 360)
  saveGeometry()
}

watch(panel, (el) => { if (el) applyGeometry() }, { flush: 'post' })
function onResize() {
  applyGeometry()
}

function onProvidersChanged() {
  if (open.value) void refreshStatus()
}

onMounted(() => {
  window.addEventListener('keydown', onGlobalKey)
  window.addEventListener('resize', onResize)
  window.addEventListener('aia:providers-changed', onProvidersChanged)
  window.addEventListener('aia:open-session', onOpenSession)
})
onUnmounted(() => {
  window.removeEventListener('keydown', onGlobalKey)
  window.removeEventListener('resize', onResize)
  window.removeEventListener('aia:providers-changed', onProvidersChanged)
  window.removeEventListener('aia:open-session', onOpenSession)
  window.clearInterval(ticker)
  moveX.stop()
  moveY.stop()
})
// Ctrl+/ toggles the panel from anywhere
function onGlobalKey(e: KeyboardEvent) {
  // the settings page is recording a shortcut
  if ((e.target as HTMLElement | null)?.classList?.contains('aia-field-input')) return
  const sc = prefs.shortcuts
  if (sc.togglePanel && matchShortcut(e, sc.togglePanel)) { e.preventDefault(); open.value = !open.value }
  else if (open.value && view.value === 'chat' && sc.toggleHistory && matchShortcut(e, sc.toggleHistory)) { e.preventDefault(); toggleHistory() }
  else if (open.value && view.value === 'chat' && sc.newChat && matchShortcut(e, sc.newChat)) { e.preventDefault(); newChat() }
}
</script>

<template>
  <el-button size="small" :type="open ? 'primary' : 'default'" class="aia-trigger" title="Ctrl+/" @click="open = !open">
    <Icon name="spark" />{{ t('button') }}
  </el-button>
  <Teleport to="body">
    <transition name="aia-slide">
      <div v-if="open" ref="panel" class="aia-frame" :class="{ dragging, resizing: !!resizing }">
      <aside
        v-spot class="aia-panel" role="dialog" :aria-label="t('title')"
        :class="{ 'is-busy': busy, 'aia-no-motion': !shown.animations }"
        :data-tone="isGlass ? glassLook.tone : undefined" :style="isGlass ? glassLook.style : undefined"
        @pointerdown="onHeadDown" @pointermove="onHeadMove" @pointerup="onHeadUp" @pointercancel="onHeadUp"
      >
        <EdgeGlow v-if="isGlass" :active="busy && shown.animations" />
        <transition name="aia-view" mode="out-in">
        <SettingsView v-if="view === 'settings'" key="settings" @close="closePanelSettings" />
        <div v-else key="chat" class="aia-view-wrap">
        <header class="aia-head aia-magnet-group" :title="t('dragHint')" @dblclick="dock">
          <div class="aia-title">
            <span class="aia-tick" />{{ t('title') }}
          </div>
          <span class="aia-grow" />
          <!-- glass: the buttons sit in one liquid capsule and a lens glides to the one under the pointer -->
          <LiquidGlass class="aia-head-glass is-raised" magnetic>
            <div ref="headGroup" class="aia-lensgroup">
              <button type="button" class="aia-ibtn" data-lens :class="{ on: showHistory }" :aria-pressed="showHistory"
                      :aria-label="t('history.title')" :title="`${t('history.title')} (${prefs.shortcuts.toggleHistory})`" @click="toggleHistory">
                <Icon name="history" />
              </button>
              <button type="button" class="aia-ibtn" data-lens :disabled="busy || !items.length"
                      :aria-label="t('newChat')" :title="`${t('newChat')} (${prefs.shortcuts.newChat})`" @click="newChatFromHeader">
                <Icon name="compose" />
              </button>
              <button type="button" class="aia-ibtn" data-lens :aria-label="t('settings.title')" :title="t('settings.title')"
                      @click="openPanelSettings">
                <Icon name="gear" />
              </button>
              <button type="button" class="aia-ibtn" data-lens :aria-label="t('close')" :title="t('close')" @click="open = false">
                <Icon name="close" />
              </button>
              <LiquidGlass ref="headLens" class="aia-button-lens" variant="lens" />
            </div>
          </LiquidGlass>
        </header>

        <div class="aia-body" :class="{ docked }">
        <transition name="aia-hist">
          <HistoryPane
            v-if="showHistory" :current="sessionId" :busy="busy" :docked="docked" :reload="historyReload"
            @open="openFromHistory" @new-chat="onHistoryNew" @close="historyOpen = false" @deleted="onHistoryDeleted"
          />
        </transition>
        <div ref="mainEl" class="aia-main">
        <div v-if="pendingOpen" class="aia-busybar" role="alert">
          <span>{{ t('history.busy') }}</span>
          <span class="aia-grow" />
          <el-button size="small" @click="pendingOpen = null">{{ t('history.busyCancel') }}</el-button>
          <el-button size="small" type="warning" @click="stopAndOpen">{{ t('history.busyStop') }}</el-button>
        </div>
        <div class="aia-scroll-area">
        <div ref="list" class="aia-list">
          <div v-if="statusError" class="aia-note error">{{ statusError }}</div>
          <div v-else-if="status && status.problem" class="aia-setup">
            <div class="aia-setup-title">{{ t('setup.title') }}</div>
            <p>{{ codeText(status.problem.code, status.problem.params) }}</p>
            <p class="aia-dim">{{ t('setup.text') }}</p>
            <div class="aia-row">
              <el-button size="small" type="primary" @click="openSettings">{{ t('setup.open') }}</el-button>
              <el-button size="small" @click="refreshStatus">{{ t('setup.recheck') }}</el-button>
            </div>
          </div>

          <template v-if="!items.length && ready">
            <p class="aia-intro">{{ t('intro') }}</p>
            <p class="aia-section-label">{{ t('suggestTitle') }}</p>
            <div class="aia-suggest">
              <button v-for="k in SUGGESTIONS" :key="k" type="button" class="aia-chip" @click="send(t(`suggestions.${k}`))">
                <span class="aia-card-hl" aria-hidden="true"><span class="aia-card-edge" /></span>
                <span class="aia-chip-ico" :class="`t${SUGGESTION_ICONS[k][1]}`" aria-hidden="true"><Icon :name="(SUGGESTION_ICONS[k][0] as any)" /></span>
                <span>{{ t(`suggestions.${k}`) }}</span>
              </button>
            </div>
          </template>

          <template v-for="(it, i) in items" :key="i">
            <div v-if="it.kind === 'user'" class="aia-user">{{ it.text }}</div>
            <div v-else-if="it.kind === 'assistant'" class="aia-answer-wrap">
              <div v-reveal="!!it.live && shown.reveal" class="aia-answer" :class="{ interim: !it.final }" @click="skipReveal"
                   v-html="markdown(it.text)" />
              <div v-if="it.final" class="aia-acts">
                <CopyButton :text="it.text" />
                <LikeButton :liked="likes.has(likeKey(i))" @toggle="toggleLike(i)" />
                <span v-if="it.tokens" class="aia-tokens" :title="t('turn.title')">{{ tokenText(it.tokens) }}</span>
              </div>
            </div>
            <div v-else-if="it.kind === 'tool' && it.tool === 'run'" class="aia-run"
                 :class="[it.state, `dec-${it.decision || 'none'}`]">
              <div class="aia-run-head">
                <Icon name="bolt" class="aia-run-icon" /><span class="aia-run-title">{{ toolLabel(it) }}</span>
                <span v-if="it.decision && it.decision !== 'pending'" class="aia-run-state">{{ t(`confirm.state.${it.decision}`) }}</span>
              </div>
              <dl v-if="Object.keys(it.input).length" class="aia-run-args">
                <template v-for="r in argRows(it.input)" :key="r.key">
                  <dt>{{ r.label }}</dt><dd class="mono">{{ r.value }}</dd>
                </template>
              </dl>
              <div v-if="it.decision === 'pending'" class="aia-run-ask">
                <span class="aia-run-q">{{ t('confirm.question') }}</span>
                <el-button size="small" :disabled="answering.has(it.id)" @click="decide(it.id, false)">{{ t('confirm.deny') }}</el-button>
                <el-button size="small" type="primary" :loading="answering.has(it.id)" @click="decide(it.id, true)">{{ t('confirm.allow') }}</el-button>
              </div>
              <div v-else-if="it.error && it.decision !== 'blocked' && it.decision !== 'denied'" class="aia-tool-error">{{ it.error }}</div>
            </div>
            <div v-else-if="it.kind === 'tool'" class="aia-tool" :class="[it.state, { open: expanded.has(it.id) }]">
              <button type="button" class="aia-tool-head" :aria-expanded="expanded.has(it.id)" @click="toggleTool(it.id)">
                <Icon name="chevron" class="aia-caret" /><span class="aia-dot" />{{ toolLabel(it) }}
              </button>
              <div class="aia-fold" :aria-hidden="!expanded.has(it.id)">
                <div class="aia-fold-inner">
                  <div class="aia-tool-body mono">
                    <div>{{ it.name }}({{ JSON.stringify(it.input) }})</div>
                    <div v-if="it.error" class="aia-tool-error">{{ it.error }}</div>
                  </div>
                </div>
              </div>
            </div>
            <div v-else class="aia-note" :class="it.tone">{{ it.text }}</div>
          </template>
          <div v-if="busy" class="aia-busy"><span class="aia-pulse" />{{ busyText }}<span class="aia-elapsed">{{ elapsed }} s</span></div>
          <div v-else-if="usage" class="aia-usage mono">{{ usage }}</div>
        </div>
        <OverlayScrollbar :scroller="list" />
        </div>

        <footer class="aia-foot">
          <LiquidGlass class="aia-input-glass" :radius="20">
            <el-input
              v-model="input" type="textarea" :autosize="{ minRows: 2, maxRows: 7 }" resize="none" :maxlength="8000"
              :placeholder="t(prefs.sendKey === 'ctrlEnter' ? 'placeholderCtrl' : 'placeholder')" :disabled="!ready || switching" @keydown="onKey"
            />
          </LiquidGlass>
          <div class="aia-row aia-magnet-group">
            <ModelMenu :status="status" :busy="busy" :testing="testing" @changed="refreshStatus" @test="test"
                       @manage="openSettings" @error="onMenuError" />
            <AccessMenu v-model="access" />
            <span class="aia-grow" />
            <el-tooltip :content="t('privacy', { service })" placement="top" :show-after="300">
              <button type="button" class="aia-ibtn aia-privacy" :aria-label="t('privacy', { service })"><Icon name="info" /></button>
            </el-tooltip>
            <el-button v-if="busy" size="small" type="warning" @click="stop">{{ t('stop') }}</el-button>
            <el-button v-else size="small" type="primary" :disabled="!ready || switching || !input.trim()" @click="send()">{{ t('send') }}</el-button>
          </div>
        </footer>
        </div>
        </div>
        </div>
        </transition>
      </aside>
      <!-- edges and corners resize the panel; double-click one to get that size's default back -->
      <div v-for="d in RESIZE_DIRS" :key="d" class="aia-rz" :class="[`aia-rz-${d}`, { on: resizing === d }]"
           aria-hidden="true"
           @pointerdown="onResizeDown($event, d)" @pointermove="onResizeMove" @pointerup="onResizeUp"
           @pointercancel="onResizeUp" @dblclick="resetSize(d)" />
      </div>
    </transition>
  </Teleport>
</template>

<style>
/* Not scoped: the panel is teleported to <body>. Every class starts with aia- (ai assistant).
   Surfaces: no outlines and no pure black anywhere. Four background levels, each 1.5-2.5 % lighter or darker than
   the next, separate the parts: l0 conversation, l1 header / footer, l2 cards and the input, l3 code and table heads. */

/* ---- icons: 1em, in the text line, lowered by 0.125em ---- */
.aia-i { display: inline-block; width: 1em; height: 1em; vertical-align: -0.125em; flex: none; overflow: visible; }
.aia-i-line { fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.aia-trigger > span { display: inline-block; }
.aia-trigger .aia-i { margin-right: 5px; }
.dark .aia-trigger { font-weight: 460; }

/* The frame carries the place and size (set from the script: width, height, translate = x and top edge); the panel
   fills it and clips its content, the resize handles sit on the frame's edges, half outside the panel. */
.aia-frame {
  position: fixed;
  z-index: 2000;
  top: 0;
  right: 14px;
  width: min(460px, calc(100vw - 28px));
  /* below the header clock and above the status bar: the E-STOP button must stay visible and clickable */
  height: calc(100vh - 148px);
  translate: 0 64px;
}
.aia-frame.aia-gliding { transition: width 0.34s cubic-bezier(0.22, 1, 0.36, 1), height 0.34s cubic-bezier(0.22, 1, 0.36, 1); }
.aia-frame.resizing, .aia-frame.resizing * { user-select: none; }
.aia-panel {
  --aia-l0: hsl(220 33% 98.5%);
  --aia-l1: hsl(220 30% 97%);
  --aia-l2: hsl(220 27% 95%);
  --aia-l3: hsl(220 24% 93%);
  --aia-shade: 220 45% 22%;
  --aia-ring: 220 40% 30%;
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  border-radius: var(--radius, 16px);
  overflow: hidden;
  color: var(--ink);
  background: var(--aia-l0);
  font-variant-numeric: tabular-nums;
  box-shadow: 0 24px 56px -12px hsl(var(--aia-shade) / 0.22), 0 6px 16px -6px hsl(var(--aia-shade) / 0.1);
}
.dark .aia-panel {
  --aia-l0: hsl(222 32% 10%);
  --aia-l1: hsl(222 30% 12%);
  --aia-l2: hsl(222 28% 14.5%);
  --aia-l3: hsl(222 26% 17%);
  --aia-shade: 224 60% 3%;
  --aia-ring: 215 60% 75%;
  /* light text on a dark ground looks heavier: every weight is 40 lower than in the light look */
  font-weight: 360;
  box-shadow: 0 24px 56px -12px hsl(var(--aia-shade) / 0.7), 0 6px 16px -6px hsl(var(--aia-shade) / 0.5);
}
.dark .aia-panel .el-button { font-weight: 460; }
/* Element Plus buttons draw a 1 px border; here they are told apart by fill only (their height is fixed, so nothing moves) */
.aia-panel .el-button { border: 0; }
.aia-panel .el-button:not(.is-text):not(.el-button--primary):not(.el-button--warning) { background: var(--aia-l2); }
.aia-panel .el-button:not(.is-text):not(.el-button--primary):not(.el-button--warning):hover { background: var(--aia-l3); }

.aia-head {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 12px 10px 10px 16px;
  background: var(--aia-l1);
  cursor: grab;
  touch-action: none;
  user-select: none;
}
.aia-frame.dragging .aia-head { cursor: grabbing; }

/* ---- resize handles: 10 px strips across the edges (7 px outside, 3 px inside), 18 px squares on the corners.
   A short pill shows on the edge under the pointer and while dragging it. ---- */
.aia-rz { position: absolute; z-index: 3; touch-action: none; }
.aia-rz-n, .aia-rz-s { left: 18px; right: 18px; height: 10px; cursor: ns-resize; }
.aia-rz-e, .aia-rz-w { top: 18px; bottom: 18px; width: 10px; cursor: ew-resize; }
.aia-rz-n { top: -7px; }
.aia-rz-s { bottom: -7px; }
.aia-rz-w { left: -7px; }
.aia-rz-e { right: -7px; }
.aia-rz-ne, .aia-rz-nw, .aia-rz-se, .aia-rz-sw { width: 18px; height: 18px; }
.aia-rz-nw { left: -7px; top: -7px; cursor: nwse-resize; }
.aia-rz-se { right: -7px; bottom: -7px; cursor: nwse-resize; }
.aia-rz-ne { right: -7px; top: -7px; cursor: nesw-resize; }
.aia-rz-sw { left: -7px; bottom: -7px; cursor: nesw-resize; }
.aia-rz-n::after, .aia-rz-s::after, .aia-rz-e::after, .aia-rz-w::after {
  content: '';
  position: absolute;
  border-radius: 2px;
  background: var(--accent);
  opacity: 0;
  transition: opacity 0.18s ease, scale 0.28s cubic-bezier(0.22, 1, 0.36, 1);
  pointer-events: none;
}
.aia-rz-n::after, .aia-rz-s::after { left: calc(50% - 22px); width: 44px; height: 3px; top: 3.5px; scale: 0.5 1; }
.aia-rz-e::after, .aia-rz-w::after { top: calc(50% - 22px); height: 44px; width: 3px; left: 3.5px; scale: 1 0.5; }
.aia-rz:hover::after { opacity: 0.45; scale: 1; }
.aia-rz.on::after { opacity: 0.8; scale: 1.6 1; }
.aia-rz-e.on::after, .aia-rz-w.on::after { scale: 1 1.6; }
/* while resizing, the whole window shows the resize cursor and nothing under the pointer reacts */
.aia-frame.resizing .aia-panel { pointer-events: none; }
.aia-title {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
}
.dark .aia-title { font-weight: 560; }

/* ---- composer pickers (ModelMenu.vue, AccessMenu.vue): chips under the input, menus open upwards ---- */
.aia-pick { position: relative; min-width: 0; display: flex; }
.aia-chipbtn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  max-width: 100%;
  height: 26px;
  padding: 0 9px 0 5px;
  border: 0;
  border-radius: 9px;
  background: var(--aia-l2);
  color: var(--ink-2);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.aia-chipbtn:hover, .aia-chipbtn.open { background: var(--aia-l3); color: var(--ink); }
.aia-chipbtn:disabled { cursor: default; opacity: 0.6; }
.aia-chipbtn:focus-visible { outline: none; box-shadow: 0 0 0 2px var(--aia-l1), 0 0 0 4px rgba(var(--accent-rgb), 0.45); }
.aia-chipbtn > .aia-i { margin-left: 3px; }
.aia-chip-text { min-width: 0; max-width: 150px; }
.aia-chip-sub { flex: none; margin-left: -6px; color: var(--ink-3); font-size: 11px; }
.aia-chip-sub .aia-swapt-in:not(.empty) { padding-left: 6px; }
.aia-chipbtn.acc-ask { color: var(--accent); }
.aia-chipbtn.acc-auto { color: var(--warn); background: color-mix(in srgb, var(--warn) 12%, var(--aia-l2)); }
.aia-chipbtn.acc-auto:hover, .aia-chipbtn.acc-auto.open { background: color-mix(in srgb, var(--warn) 18%, var(--aia-l2)); }
/* menus above the chips; popover.ts sets width, left, max-height and transform-origin so they stay in the panel */
.aia-menu.up { top: auto; bottom: calc(100% + 8px); transform-origin: 12px 100%; }
.aia-menu-cap { flex: none; padding: 4px 8px 6px; font-size: 11px; color: var(--ink-3); }
.aia-menu-scroll { flex: 0 1 auto; min-height: 64px; overflow-y: auto; overscroll-behavior: contain; margin: 0 -2px; padding: 0 2px; }
.aia-menu-foot2 { flex: none; display: flex; gap: 4px; margin-top: 6px; }
.aia-menu-foot2 .aia-menu-foot { margin-top: 0; flex: 1; justify-content: center; }
.aia-menu-foot:disabled { opacity: 0.6; cursor: default; }
.aia-dotav.sm { width: 16px; height: 16px; border-radius: 5px; font-size: 9.5px; }

/* reasoning effort: dots on a track; a knob on a spring (ModelMenu.vue sets --k, the fractional level) with the
   track filled up to it. Press and slide along the track, click a dot, or use the arrow keys. */
.aia-effort { flex: none; margin-top: 6px; padding: 8px 8px 6px; border-radius: 9px; background: var(--aia-l2); }
.aia-effort-head { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--ink-2); }
.aia-effort-val { margin-left: auto; color: var(--ink); font-weight: 600; }
.dark .aia-effort-val { font-weight: 560; }
.aia-effort-track {
  --k: 0;
  --step: calc((100% - 22px) / max(1, var(--n) - 1));
  position: relative;
  display: flex;
  justify-content: space-between;
  align-items: center;
  height: 22px;
  margin: 8px 2px 4px;
  padding: 0 4px;
  border-radius: 11px;
  background: var(--aia-l3);
  cursor: pointer;
  touch-action: none;
}
.aia-effort-fill {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  /* from the left end to the right edge of the knob */
  width: calc(22px + var(--step) * var(--k));
  border-radius: 11px;
  background: rgba(var(--accent-rgb), 0.18);
  pointer-events: none;
}
.aia-effort-knob {
  position: absolute;
  top: 4px;
  /* % in --step is the track's width here (left), not the knob's own (as it would be in translate) */
  left: calc(4px + var(--step) * var(--k));
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 1px 4px rgba(var(--accent-rgb), 0.45), 0 0 0 0 rgba(var(--accent-rgb), 0.16);
  transition: scale 0.24s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.2s ease;
  pointer-events: none;
}
.aia-effort-track:hover .aia-effort-knob { box-shadow: 0 1px 4px rgba(var(--accent-rgb), 0.45), 0 0 0 4px rgba(var(--accent-rgb), 0.14); }
.aia-effort-track.pressing .aia-effort-knob {
  scale: 1.18;
  box-shadow: 0 2px 8px rgba(var(--accent-rgb), 0.5), 0 0 0 5px rgba(var(--accent-rgb), 0.16);
}
.aia-effort-dot {
  position: relative;
  width: 14px;
  height: 14px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  cursor: pointer;
}
.aia-effort-dot:focus-visible { outline: none; box-shadow: 0 0 0 2px var(--aia-l2), 0 0 0 4px rgba(var(--accent-rgb), 0.55); }
.aia-effort-dot::after {
  content: '';
  position: absolute;
  inset: 4.5px;
  border-radius: 50%;
  background: var(--ink-3);
  opacity: 0.55;
  transition: background-color 0.22s ease, opacity 0.22s ease, scale 0.22s ease;
}
.aia-effort-dot.past::after { background: var(--accent); opacity: 0.7; }
.aia-effort-dot:hover::after { opacity: 1; scale: 1.3; }
/* no effort set (the model sends none): no knob and no fill until a level is picked; beats the glass skin's rules */
.aia-panel .aia-effort-track.unset :is(.aia-effort-knob, .aia-effort-fill) { opacity: 0; }
.aia-effort-help { font-size: 11px; color: var(--ink-3); line-height: 1.45; }

/* text that rolls to its new value (SwapText.vue) */
.aia-swapt { position: relative; display: inline-block; overflow: hidden; white-space: nowrap; vertical-align: bottom; }
.aia-swapt-in { display: block; overflow: hidden; text-overflow: ellipsis; }
.aia-swapt.gliding .aia-swapt-in { text-overflow: clip; }
.aia-swapt-in.empty { width: 0; }
.aia-swapt-enter-active, .aia-swapt-leave-active {
  transition: transform 0.34s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.26s ease, filter 0.26s ease;
}
.aia-swapt-leave-active { position: absolute; left: 0; top: 0; }
.aia-swapt-enter-from { opacity: 0; filter: blur(2px); transform: translateY(calc(var(--aia-dir, 1) * 75%)); }
.aia-swapt-leave-to { opacity: 0; filter: blur(2px); transform: translateY(calc(var(--aia-dir, 1) * -75%)); }

/* access menu */
.aia-access-menu { width: 300px; }
.aia-acc {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  width: 100%;
  padding: 7px 8px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--ink);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.12s ease;
}
.aia-acc:hover { background: var(--aia-l3); }
.aia-acc.on { background: rgba(var(--accent-rgb), 0.08); }
.aia-acc-icon { margin-top: 3px; color: var(--ink-3); font-size: 14px; }
.aia-acc.on .aia-acc-icon { color: var(--accent); }
.aia-acc.acc-auto .aia-acc-icon { color: var(--warn); }
.aia-acc-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.aia-acc-name { font-size: 12.5px; font-weight: 600; }
.dark .aia-acc-name { font-weight: 560; }
.aia-acc-desc { font-size: 11.5px; line-height: 1.45; color: var(--ink-2); text-wrap: pretty; }
.aia-acc .aia-menu-check { margin-top: 3px; }

/* run tool calls: a card with the arguments, and buttons while it waits for approval */
.aia-run {
  padding: 9px 11px;
  border-radius: 11px;
  font-size: 12px;
  background: var(--aia-l1);
  box-shadow: inset 3px 0 0 var(--aia-run-c, var(--accent));
}
.aia-run.dec-pending { --aia-run-c: var(--warn); background: color-mix(in srgb, var(--warn) 8%, var(--aia-l1)); }
.aia-run.dec-denied, .aia-run.dec-blocked, .aia-run.dec-cancelled { --aia-run-c: var(--ink-3); }
.aia-run.error:not(.dec-denied):not(.dec-blocked):not(.dec-cancelled) { --aia-run-c: var(--bad); }
.aia-run.ok { --aia-run-c: var(--ok); }
.aia-run-head { display: flex; align-items: center; gap: 7px; }
.aia-run-icon { color: var(--aia-run-c, var(--accent)); }
.aia-run-title { flex: 1; min-width: 0; font-weight: 600; color: var(--ink); overflow-wrap: anywhere; }
.dark .aia-run-title { font-weight: 560; }
.aia-run-state { flex: none; color: var(--ink-3); font-size: 11.5px; }
.aia-run-args {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 2px 12px;
  margin: 7px 0 0 21px;
  font-size: 11.5px;
}
.aia-run-args dt { color: var(--ink-3); }
.aia-run-args dd { margin: 0; color: var(--ink); overflow-wrap: anywhere; }
.aia-run-ask { display: flex; align-items: center; gap: 6px; margin: 9px 0 0 21px; }
.aia-run-ask .el-button + .el-button { margin-left: 0; }
.aia-run-q { flex: 1; color: var(--ink-2); }
.aia-run .aia-tool-error { margin: 6px 0 0 21px; font-size: 11.5px; }
.aia-dotav {
  flex: none;
  display: inline-grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 6px;
  color: hsl(0 0% 100%);
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
}
.aia-menu {
  position: absolute;
  z-index: 5;
  top: calc(100% + 6px);
  left: 0;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  width: 280px;
  max-width: calc(100vw - 32px);
  max-height: min(60vh, 460px);
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 6px;
  border-radius: 12px;
  background: var(--aia-l1);
  box-shadow: 0 18px 40px -10px hsl(var(--aia-shade) / 0.28), 0 4px 12px -4px hsl(var(--aia-shade) / 0.14);
  cursor: default;
}
.aia-menu > * { flex-shrink: 0; }
.aia-menu > .aia-menu-scroll { flex-shrink: 1; }
.aia-menu-group + .aia-menu-group { margin-top: 4px; padding-top: 4px; }
.aia-menu-head, .aia-menu-item, .aia-menu-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--ink);
  font: inherit;
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.12s ease;
}
.aia-menu-head { padding: 5px 6px; font-weight: 600; }
.dark .aia-menu-head { font-weight: 560; }
.aia-menu-item { padding: 5px 8px 5px 34px; color: var(--ink-2); }
.aia-menu-head:hover, .aia-menu-item:hover, .aia-menu-foot:hover { background: var(--aia-l3); }
.aia-menu-item.on { color: var(--accent); background: rgba(var(--accent-rgb), 0.08); }
.aia-menu-name, .aia-menu-label { flex: 1; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.aia-menu-check { color: var(--accent); }
.aia-menu-foot { margin-top: 6px; padding: 7px 8px; color: var(--ink-2); background: var(--aia-l2); }
.aia-menu-foot .aia-i { margin-right: 2px; }
/* menus grow out of their chip (expo-out) and shrink back faster */
.aia-pop-enter-active { transition: opacity 0.18s ease-out, transform 0.3s cubic-bezier(0.16, 1, 0.3, 1); }
.aia-pop-leave-active { transition: opacity 0.13s ease-in, transform 0.13s ease-in; pointer-events: none; }
.aia-pop-enter-from, .aia-pop-leave-to { opacity: 0; transform: translateY(-4px) scale(0.97); }
.aia-menu.up.aia-pop-enter-from, .aia-menu.up.aia-pop-leave-to { transform: translateY(6px) scale(0.96); }
.aia-tick { width: 3px; height: 14px; border-radius: 2px; background: var(--accent-grad); }
.aia-service {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  font-weight: 400;
  color: var(--ink-3);
}
.dark .aia-service { font-weight: 360; }

/* icon-only buttons: close, copy, like, privacy */
.aia-ibtn {
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--ink-3);
  font: inherit;
  font-size: 15px;
  line-height: 28px;
  text-align: center;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.aia-ibtn:hover { background: var(--aia-l2); color: var(--ink); }
.aia-ibtn:active { background: var(--aia-l3); }
.aia-ibtn.on { background: var(--aia-l3); color: var(--ink); }
.aia-ibtn:disabled { opacity: 0.45; cursor: default; background: transparent; }

/* ---- body: history (overlay or docked column) + conversation ---- */
.aia-body { position: relative; flex: 1; min-height: 0; display: flex; }
.aia-main { position: relative; flex: 1; min-width: 0; display: flex; flex-direction: column; }
.aia-busybar {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 10px 12px 0;
  padding: 8px 10px;
  border-radius: 10px;
  font-size: 12.5px;
  color: var(--ink-2);
  background: color-mix(in srgb, var(--warn) 9%, var(--aia-l2));
}
.aia-busybar .el-button + .el-button { margin-left: 0; }

.aia-hist {
  position: absolute;
  inset: 0;
  z-index: 4;
  display: flex;
  flex-direction: column;
  background: var(--aia-l0);
}
.aia-hist.docked {
  position: relative;
  inset: auto;
  z-index: auto;
  flex: none;
  width: 230px;
  background: var(--aia-l1);
}
.aia-hist-enter-active, .aia-hist-leave-active { transition: opacity 0.2s cubic-bezier(0.2, 0.8, 0.2, 1), translate 0.2s cubic-bezier(0.2, 0.8, 0.2, 1), width 0.2s cubic-bezier(0.2, 0.8, 0.2, 1); }
.aia-hist-enter-from, .aia-hist-leave-to { opacity: 0; translate: -16px 0; }
.aia-hist.docked.aia-hist-enter-from, .aia-hist.docked.aia-hist-leave-to { width: 0; translate: 0 0; }
.aia-hist-top { flex: none; display: flex; align-items: center; gap: 6px; padding: 10px 10px 6px 12px; }
.aia-hist-search {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 10px;
  border-radius: 9px;
  background: var(--aia-l2);
  transition: box-shadow 0.15s ease;
}
.aia-hist-search:focus-within { box-shadow: 0 0 0 2px var(--aia-l1), 0 0 0 4px rgba(var(--accent-rgb), 0.35); }
.aia-hist-sicon { color: var(--ink-3); font-size: 13px; }
.aia-hist-search input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--ink);
  font: inherit;
  font-size: 12.5px;
}
.aia-hist-search input::placeholder { color: var(--ink-3); }
.aia-hist-search input::-webkit-search-cancel-button { display: none; }
.aia-hist-list { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: 2px 8px 10px; }
.aia-hist-group { padding: 12px 8px 4px; font-size: 11px; letter-spacing: 0.04em; color: var(--ink-3); }
.aia-hist-note { padding: 18px 10px; font-size: 12.5px; color: var(--ink-3); text-wrap: pretty; }
.aia-hist-note.error { color: var(--bad); }
.aia-hist-sub { margin-top: 4px; font-size: 11.5px; color: var(--ink-3); overflow-wrap: anywhere; }
.aia-hist-retry { border: 0; padding: 0; background: none; color: var(--accent); font: inherit; cursor: pointer; }
.aia-hist-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 7px 10px 6px;
  border-radius: 10px;
  cursor: pointer;
  transition: background-color 0.12s ease;
}
.aia-hist-item:hover, .aia-hist-item:focus-within { background: var(--aia-l2); }
.aia-hist-item.on { background: var(--aia-l3); box-shadow: inset 2px 0 0 var(--accent); }
.aia-hist-item:focus-visible { outline: none; box-shadow: inset 0 0 0 2px rgba(var(--accent-rgb), 0.4); }
.aia-hist-title {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.dark .aia-hist-title { font-weight: 560; }
.aia-hist-pin { flex: none; color: var(--accent); font-size: 11px; }
.aia-hist-snippet {
  font-size: 11.5px;
  line-height: 1.45;
  color: var(--ink-2);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.aia-hist-snippet mark { padding: 0 1px; border-radius: 3px; color: inherit; background: rgba(var(--accent-rgb), 0.18); }
.aia-hist-rename {
  width: 100%;
  height: 24px;
  padding: 0 6px;
  border: 0;
  border-radius: 6px;
  outline: none;
  color: var(--ink);
  background: var(--aia-l0);
  font: inherit;
  font-size: 13px;
  box-shadow: 0 0 0 2px rgba(var(--accent-rgb), 0.35);
}
.aia-hist-meta { position: relative; display: flex; align-items: center; gap: 4px; min-height: 20px; }
.aia-hist-metatext { flex: 1; min-width: 0; font-size: 11.5px; color: var(--ink-3); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.aia-hist-acts {
  position: absolute;
  right: -4px;
  top: 50%;
  translate: 0 -50%;
  display: flex;
  gap: 1px;
  padding-left: 14px;
  opacity: 0;
  pointer-events: none;
  background: linear-gradient(to right, transparent, var(--aia-l2) 14px);
  transition: opacity 0.12s ease;
}
.aia-hist-item.on .aia-hist-acts { background: linear-gradient(to right, transparent, var(--aia-l3) 14px); }
.aia-hist-item:hover .aia-hist-acts, .aia-hist-item:focus-within .aia-hist-acts, .aia-hist-item.confirming .aia-hist-acts { opacity: 1; pointer-events: auto; }
.aia-hist-act {
  display: inline-grid;
  place-items: center;
  min-width: 20px;
  height: 20px;
  padding: 0 3px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--ink-3);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  transition: background-color 0.12s ease, color 0.12s ease;
}
.aia-hist-act:hover { background: var(--aia-l3); color: var(--ink); }
.aia-hist-act.del:hover, .aia-hist-act.del.ask { color: var(--bad); }
.aia-hist-act.del.ask { padding: 0 6px; font-size: 11.5px; background: color-mix(in srgb, var(--bad) 12%, transparent); }
.aia-hist-act:disabled { opacity: 0.4; cursor: default; }
.aia-hist-foot { flex: none; display: flex; align-items: center; gap: 6px; padding: 6px 12px 10px; font-size: 11px; color: var(--ink-3); }
.aia-hist-more { border: 0; padding: 0; background: none; color: var(--accent); font: inherit; cursor: pointer; }
.aia-hist-more:hover { text-decoration: underline; }
.aia-hist-more:disabled { opacity: 0.5; cursor: default; text-decoration: none; }
@media (prefers-reduced-motion: reduce) {
  .aia-hist-enter-active, .aia-hist-leave-active { transition: none; }
}

.aia-list {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  font-size: 13px;
  line-height: 1.65;
}
.aia-intro, .aia-dim { color: var(--ink-2); margin: 0; }
.aia-intro, .aia-dim, .aia-note, .aia-user, .aia-setup p,
.aia-answer p, .aia-answer li, .aia-answer blockquote { text-wrap: pretty; }
.aia-suggest { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
.aia-chip {
  border: 0;
  background: var(--aia-l2);
  color: var(--ink);
  border-radius: 10px;
  padding: 6px 11px;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.aia-chip:hover { background: var(--aia-l3); color: var(--accent); }
.aia-user {
  align-self: flex-end;
  max-width: 88%;
  padding: 8px 12px;
  border-radius: 12px 12px 4px 12px;
  background: color-mix(in srgb, var(--accent) 12%, var(--aia-l1));
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

/* ---- answers ---- */
.aia-answer { overflow-wrap: anywhere; }
.aia-answer.interim { color: var(--ink-2); }
.aia-answer > :first-child { margin-top: 0; }
.aia-answer > :last-child { margin-bottom: 0; }
.aia-answer p { margin: 0 0 8px; }
.aia-answer h3, .aia-answer h4, .aia-answer h5, .aia-answer h6 { margin: 10px 0 6px; font-size: 13.5px; font-weight: 600; }
.aia-answer strong { font-weight: 700; }
.dark .aia-answer h3, .dark .aia-answer h4, .dark .aia-answer h5, .dark .aia-answer h6 { font-weight: 560; }
.dark .aia-answer strong { font-weight: 660; }
.aia-answer ul, .aia-answer ol { margin: 0 0 8px; padding-left: 20px; }
.aia-answer code {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  padding: 1px 5px;
  border-radius: 5px;
  background: var(--aia-l2);
}
.aia-answer pre {
  margin: 0 0 8px;
  padding: 9px 11px;
  border-radius: 9px;
  overflow-x: auto;
  background: var(--aia-l2);
}
.aia-answer pre code { padding: 0; background: none; }
.aia-answer blockquote {
  margin: 0 0 8px;
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--aia-l1);
  color: var(--ink-2);
}
.aia-answer a { color: var(--accent); }
.aia-answer .md-table { overflow-x: auto; margin: 0 0 8px; border-radius: 9px; background: var(--aia-l1); }
.aia-answer table { border-collapse: collapse; font-size: 12px; }
.aia-answer th, .aia-answer td { padding: 4px 9px; text-align: left; vertical-align: top; min-width: 56px; }
.aia-answer th { white-space: nowrap; color: var(--ink-2); font-weight: 600; background: var(--aia-l3); }
.dark .aia-answer th { font-weight: 560; }
.aia-answer tbody tr:nth-child(even) td { background: var(--aia-l2); }

/* typing reveal (reveal.ts): each character 120 ms from blur(6px), started 30 ms after the previous one */
.aia-revealing { cursor: default; }
.aia-ch { animation: aia-ch-in 120ms ease-out both; }
@keyframes aia-ch-in {
  from { opacity: 0; filter: blur(6px); }
  to { opacity: 1; filter: blur(0); }
}

.aia-acts { display: flex; align-items: center; gap: 2px; margin: 4px 0 0 -6px; }
.aia-acts .aia-ibtn { width: 26px; height: 26px; line-height: 26px; font-size: 14px; }
/* tokens of the answer: quiet until the pointer is on the answer */
.aia-tokens {
  margin-left: auto;
  padding-left: 8px;
  font-size: 11px;
  color: var(--ink-3);
  opacity: 0.7;
  white-space: nowrap;
  transition: opacity 0.15s ease;
}
.aia-answer-wrap:hover .aia-tokens { opacity: 1; }
/* copy: icon and check mark share one cell and cross-fade */
.aia-swap { display: inline-grid; vertical-align: -0.125em; }
.aia-swap > .aia-i { grid-area: 1 / 1; vertical-align: 0; transition: opacity 0.16s ease, scale 0.16s ease; }
.aia-swap-b { opacity: 0; scale: 0.6; }
.aia-copy.done { color: var(--ok); }
.aia-copy.done .aia-swap-a { opacity: 0; scale: 0.6; }
.aia-copy.done .aia-swap-b { opacity: 1; scale: 1; }
.aia-like-icon { display: inline-block; line-height: 1; transform-origin: 50% 60%; }
.aia-like.on { color: var(--accent); }

/* ---- tool calls: the details fold open with a grid track going from 0fr to 1fr ---- */
.aia-tool { font-size: 12px; color: var(--ink-2); }
.aia-tool-head {
  display: block;
  width: 100%;
  padding: 2px 0;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  cursor: pointer;
  text-align: left;
  overflow-wrap: anywhere;
}
.aia-tool-head:hover { color: var(--ink); }
.aia-caret { margin-right: 6px; color: var(--ink-3); transition: rotate 0.24s cubic-bezier(0.2, 0.8, 0.2, 1); }
.aia-tool.open .aia-caret { rotate: 90deg; }
.aia-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-right: 8px;
  border-radius: 50%;
  vertical-align: 0.06em;
  background: var(--accent);
}
.aia-tool.running .aia-dot { animation: aia-pulse 1.2s ease-in-out infinite; }
.aia-tool.ok .aia-dot { background: var(--ok); }
.aia-tool.error .aia-dot { background: var(--bad); }
.aia-fold {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows 0.26s cubic-bezier(0.2, 0.8, 0.2, 1);
}
.aia-tool.open .aia-fold { grid-template-rows: 1fr; }
.aia-fold-inner { min-height: 0; overflow: hidden; }
.aia-tool-body {
  margin: 4px 0 2px 21px;
  padding: 7px 9px;
  border-radius: 8px;
  font-size: 11px;
  overflow-wrap: anywhere;
  background: var(--aia-l2);
}
.aia-tool-error { margin-top: 4px; color: var(--bad); }

.aia-note { font-size: 12px; color: var(--ink-2); }
.aia-note.error { color: var(--bad); }
.aia-setup {
  padding: 12px 14px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--warn) 9%, var(--aia-l1));
}
.aia-setup p { margin: 6px 0; }
.aia-setup-title { font-weight: 600; }
.dark .aia-setup-title { font-weight: 560; }
.aia-busy { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--ink-2); }
.aia-elapsed { color: var(--ink-3); }
.aia-pulse { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); animation: aia-pulse 1.2s ease-in-out infinite; }
.aia-usage { font-size: 11px; color: var(--ink-3); }

/* ---- input: no outline or border; a double ring of shadow, deeper while focused ---- */
.aia-foot {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 12px 12px;
  background: var(--aia-l1);
}
.aia-panel .el-textarea__inner {
  border: 0;
  outline: none;
  border-radius: 12px;
  padding: 9px 12px;
  color: var(--ink);
  background: var(--aia-l2);
  box-shadow: 0 0 0 1px hsl(var(--aia-ring) / 0.05), 0 0 0 4px hsl(var(--aia-ring) / 0.025);
  transition: box-shadow 0.18s ease, background-color 0.18s ease;
}
.dark .aia-panel .el-textarea__inner { font-weight: 360; }
.aia-panel .el-textarea__inner:hover { box-shadow: 0 0 0 1px hsl(var(--aia-ring) / 0.08), 0 0 0 4px hsl(var(--aia-ring) / 0.04); }
.aia-panel .el-textarea__inner:focus,
.aia-panel .el-textarea__inner:focus-visible {
  outline: none;
  background: var(--aia-l0);
  box-shadow: 0 0 0 3px rgba(var(--accent-rgb), 0.2), 0 0 0 7px rgba(var(--accent-rgb), 0.08),
    0 4px 14px -4px hsl(var(--aia-shade) / 0.16);
}
.aia-panel .el-textarea.is-disabled .el-textarea__inner { background: var(--aia-l1); box-shadow: none; }
.aia-row { display: flex; align-items: center; gap: 6px; }
.aia-row .el-button + .el-button { margin-left: 0; }
.aia-grow { flex: 1; }
.aia-privacy { cursor: help; font-size: 14px; }

.aia-slide-enter-active { transition: transform 0.36s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.24s ease-out; }
.aia-slide-leave-active { transition: transform 0.18s ease-in, opacity 0.18s ease-in; }
.aia-slide-enter-from, .aia-slide-leave-to { transform: translateX(24px) scale(0.985); opacity: 0; }
@keyframes aia-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
@media (prefers-reduced-motion: reduce) {
  .aia-fold, .aia-caret, .aia-swap > .aia-i, .aia-frame.aia-gliding, .aia-effort-knob, .aia-rz::after,
  .aia-pop-enter-active, .aia-pop-leave-active, .aia-swapt-enter-active, .aia-swapt-leave-active { transition: none; }
}
</style>
