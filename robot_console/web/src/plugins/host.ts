// Plugin frontends: the registry of what plugin scripts registered, and the loader of those scripts.
// The SDK object the scripts see (window.RobotConsole) is built in sdk.ts.
import { markRaw, shallowReactive } from 'vue'
import type { Component } from 'vue'
import { customThemes, registerPluginThemes, type ThemeDef } from '@/theme'
import type { Bilingual } from '@/types'
import type { PluginDef, PluginInfo } from './types'

export interface PluginPage {
  pluginId: string
  path: string
  title: Bilingual
  icon: Component | null
  component: Component
}

/** A component a plugin shows in the page header (for example a button that opens its own drawer) */
export interface PluginHeaderItem {
  pluginId: string
  key: string
  component: Component
}

export type LoadIssueCode = 'script_failed' | 'timeout' | 'not_registered' | 'register_invalid' | 'crashed'
export interface LoadIssue { code: LoadIssueCode; params: Record<string, string> }

/** Shallow: components must not become reactive proxies, so the arrays and maps are always replaced */
export const registry = shallowReactive({
  loading: true,
  expected: [] as string[],        // active plugins with a frontend, in load order
  registered: [] as string[],
  pages: [] as PluginPage[],
  header: [] as PluginHeaderItem[],
  issues: {} as Record<string, LoadIssue>,
})

export const pagePath = (p: Pick<PluginPage, 'pluginId' | 'path'>) => `/ext/${p.pluginId}/${p.path}`

type MessageMerger = (pluginId: string, messages: NonNullable<PluginDef['messages']>) => void
let mergeMessages: MessageMerger = () => {}
export function setMessageMerger(fn: MessageMerger) { mergeMessages = fn }

const PAGE_RE = /^[a-z0-9][a-z0-9_-]{0,39}$/
const THEME_RE = /^[a-z0-9][a-z0-9_-]{1,39}$/
const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const isComponent = (v: unknown) => (!!v && typeof v === 'object') || typeof v === 'function'

function setIssue(id: string, code: LoadIssueCode, params: Record<string, string> = {}) {
  registry.issues = { ...registry.issues, [id]: { code, params } }
}

/** A header item of a plugin threw while rendering: it is hidden and the Plugins page shows why */
export function reportCrash(id: string, error: string) {
  registry.header = registry.header.filter((h) => h.pluginId !== id)
  setIssue(id, 'crashed', { error: error.slice(0, 300) })
}

function title(v: unknown): Bilingual | null {
  if (typeof v === 'string' && v) return { zh: v, en: v }
  if (isObj(v) && (typeof v.zh === 'string' || typeof v.en === 'string')) {
    const zh = typeof v.zh === 'string' ? v.zh : ''
    const en = typeof v.en === 'string' ? v.en : ''
    if (zh || en) return { zh: zh || en, en: en || zh }
  }
  return null
}

/** window.RobotConsole.register(): checks the whole definition before anything is applied. Throws on a bad
 *  definition (the plugin author sees it in the browser console) and records it for the Plugins page. */
export function register(def: unknown): void {
  const id = isObj(def) && typeof def.id === 'string' ? def.id : ''
  if (!registry.expected.includes(id)) {
    throw new Error(`RobotConsole.register: "${id}" is not an active plugin with a frontend`)
  }
  const fail = (reason: string): never => {
    setIssue(id, 'register_invalid', { reason })
    throw new Error(`RobotConsole.register(${id}): ${reason}`)
  }
  if (registry.registered.includes(id)) fail('registered twice')
  const d = def as Record<string, unknown>
  const messages = d.messages
  if (messages !== undefined && (!isObj(messages)
    || Object.entries(messages).some(([lang, m]) => !['zh-CN', 'en'].includes(lang) || !isObj(m)))) {
    fail('messages must be { "zh-CN"?: {...}, en?: {...} }')
  }
  const pages: PluginPage[] = []
  if (d.pages !== undefined && !Array.isArray(d.pages)) fail('pages must be an array')
  for (const [i, p] of ((d.pages as unknown[]) ?? []).entries()) {
    if (!isObj(p)) fail(`pages[${i}] is not an object`)
    const page = p as Record<string, unknown>
    if (typeof page.path !== 'string' || !PAGE_RE.test(page.path)) fail(`pages[${i}].path must match ${PAGE_RE}`)
    if (pages.some((q) => q.path === page.path)) fail(`pages[${i}].path "${page.path}" is used twice`)
    const t = title(page.title)
    if (!t) fail(`pages[${i}].title is missing`)
    if (!isComponent(page.component)) fail(`pages[${i}].component is not a component`)
    if (page.icon !== undefined && !isComponent(page.icon)) fail(`pages[${i}].icon is not a component`)
    pages.push({
      pluginId: id, path: page.path as string, title: t as Bilingual,
      icon: page.icon ? markRaw(page.icon as object) as Component : null,
      component: markRaw(page.component as object) as Component,
    })
  }
  if (d.header !== undefined && (!Array.isArray(d.header) || !d.header.every(isComponent))) {
    fail('header must be an array of components')
  }
  const header = ((d.header as unknown[]) ?? []).map((c, i) => ({
    pluginId: id, key: `${id}:${i}`, component: markRaw(c as object) as Component,
  }))
  const themes: ThemeDef[] = []
  if (d.themes !== undefined && !Array.isArray(d.themes)) fail('themes must be an array')
  for (const [i, item] of ((d.themes as unknown[]) ?? []).entries()) {
    if (!isObj(item)) fail(`themes[${i}] is not an object`)
    const raw = item as Record<string, unknown>
    if (typeof raw.id !== 'string' || !THEME_RE.test(raw.id) || raw.id === 'light' || raw.id === 'dark') {
      fail(`themes[${i}].id must match ${THEME_RE} and not be light/dark`)
    }
    if (themes.some((q) => q.id === raw.id) || customThemes.value.some((q) => q.id === raw.id)) {
      fail(`themes[${i}].id "${raw.id}" is already registered`)
    }
    const lbl = title(raw.label)
    if (!lbl) fail(`themes[${i}].label is missing`)
    if (raw.base !== 'light' && raw.base !== 'dark') fail(`themes[${i}].base must be "light" or "dark"`)
    if (raw.pageColor !== undefined && typeof raw.pageColor !== 'string') fail(`themes[${i}].pageColor must be a string`)
    const tokens: Record<string, string> = {}
    if (raw.tokens !== undefined) {
      if (!isObj(raw.tokens)) fail(`themes[${i}].tokens must be an object`)
      for (const [k, v] of Object.entries(raw.tokens as Record<string, unknown>)) {
        if (!k.startsWith('--') || typeof v !== 'string') fail(`themes[${i}].tokens["${k}"] must map --var to string`)
        tokens[k] = v as string
      }
    }
    if (raw.scene !== undefined && !isObj(raw.scene)) fail(`themes[${i}].scene must be an object`)
    if (raw.charts !== undefined && !isObj(raw.charts)) fail(`themes[${i}].charts must be an object`)
    themes.push({
      id: raw.id as string,
      pluginId: id,
      label: lbl!,
      base: raw.base as 'light' | 'dark',
      pageColor: raw.pageColor as string | undefined,
      tokens: raw.tokens ? tokens : undefined,
      scene: raw.scene as ThemeDef['scene'],
      charts: raw.charts as ThemeDef['charts'],
    })
  }
  if (messages) mergeMessages(id, messages as NonNullable<PluginDef['messages']>)
  registry.pages = [...registry.pages, ...pages]
  registry.header = [...registry.header, ...header]
  registerPluginThemes(themes)
  registry.registered = [...registry.registered, id]
}

type ScriptResult = 'loaded' | 'failed' | 'timeout'

function loadScript(src: string, timeoutMs: number): Promise<ScriptResult> {
  return new Promise((resolve) => {
    const el = document.createElement('script')
    const timer = window.setTimeout(() => resolve('timeout'), timeoutMs)
    el.src = src
    el.async = false
    el.onload = () => { window.clearTimeout(timer); resolve('loaded') }
    el.onerror = () => { window.clearTimeout(timer); resolve('failed') }
    document.head.appendChild(el)
  })
}

function addStyle(href: string) {
  const el = document.createElement('link')
  el.rel = 'stylesheet'
  el.href = href
  document.head.appendChild(el)
}

/** Load the scripts of every active plugin with a frontend, one after the other (in the order of the list). */
export async function loadPluginFrontends(plugins: PluginInfo[], timeoutMs = 15000) {
  const withUi = plugins.filter((p) => p.state === 'active' && p.frontend)
  registry.expected = withUi.map((p) => p.id)
  for (const p of withUi) {
    const fe = p.frontend!
    if (fe.style) addStyle(fe.style)
    const result = await loadScript(fe.script, timeoutMs)
    if (result === 'failed') setIssue(p.id, 'script_failed', { script: fe.script })
    else if (result === 'timeout') setIssue(p.id, 'timeout', { script: fe.script })
    else if (!registry.registered.includes(p.id) && !registry.issues[p.id]) setIssue(p.id, 'not_registered')
  }
  registry.loading = false
}
