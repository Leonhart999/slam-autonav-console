// Per-plugin helpers shared by the pages of the plugins in this repository: translation, error texts, the
// generic job routes of plugin interface v1 (/api/ext/<id>/jobs...) and the Python environment state.
import { inject, provide, type InjectionKey } from 'vue'
import { RC, type Coded } from './sdk'

export type JobState = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted'
export type PythonState = 'missing' | 'installing' | 'ready' | 'outdated' | 'failed'

export interface Job<R = Record<string, unknown>> {
  id: string
  kind: string
  title: string
  target: string
  state: JobState
  phase: string
  progress: number
  created: number
  started: number | null
  finished: number | null
  error: Coded | null
  params: Record<string, unknown>
  detail?: Record<string, unknown>
  result?: R | null
}

export interface PythonStatus {
  state: PythonState
  requirements: string[]
  packages: string[]
  error: string | null
  env: string
}

export interface Kit {
  id: string
  base: string
  t: (key: string, params?: Record<string, unknown>) => string
  errorText: (e: unknown) => string
  codedText: (m: Coded) => string
  request: <T = unknown>(method: string, path: string, body?: unknown, timeout?: number) => Promise<T>
  jobs: () => Promise<{ python: PythonStatus | null; jobs: Job[] }>
  job: <R>(id: string) => Promise<Job<R>>
  cancel: (id: string) => Promise<Job>
  remove: (id: string) => Promise<unknown>
  log: (id: string) => Promise<{ text: string }>
  fileUrl: (id: string, name: string) => string
  installPython: () => Promise<unknown>
}

export const ACTIVE: JobState[] = ['queued', 'running']

export function makeKit(id: string): Kit {
  const base = `/api/ext/${id}`
  const t = (key: string, params?: Record<string, unknown>) => RC().i18n.useT(id)(key, params)
  const codedText = (m: Coded) => {
    const sdk = RC()
    if (sdk.codedText) return sdk.codedText(m)
    const own = new RegExp(`^plugin\\.${id}\\.(\\w+)$`).exec(m.code)
    if (own) return t(`errors.${own[1]}`, m.params)
    return `${m.code} ${JSON.stringify(m.params)}`
  }
  const request = <T>(method: string, path: string, body?: unknown, timeout?: number) =>
    RC().request<T>(method, path.startsWith('/api/') ? path : base + path, body, undefined,
      timeout ? { timeout } : undefined)
  const enc = encodeURIComponent
  return {
    id, base, t, codedText, request,
    errorText: (e) => RC().errorText(e),
    jobs: () => request('GET', '/jobs'),
    job: (jid) => request('GET', `/jobs/${enc(jid)}`),
    cancel: (jid) => request('POST', `/jobs/${enc(jid)}/cancel`),
    remove: (jid) => request('DELETE', `/jobs/${enc(jid)}`),
    log: (jid) => request('GET', `/jobs/${enc(jid)}/log`),
    fileUrl: (jid, name) => `${base}/jobs/${enc(jid)}/files/${name.split('/').map(enc).join('/')}`,
    installPython: () => request('POST', `/api/plugins/${enc(id)}/python/install`),
  }
}

const KEY: InjectionKey<Kit> = Symbol('plugin-kit')
export const provideKit = (kit: Kit) => provide(KEY, kit)
export function useKit(): Kit {
  const kit = inject(KEY)
  if (!kit) throw new Error('plugin kit not provided')
  return kit
}

export const fmtSize = (bytes: number) => {
  if (!Number.isFinite(bytes)) return '–'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = bytes
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(i && v < 10 ? 1 : 0)} ${units[i]}`
}

export const fmtTime = (s: number | null | undefined) => {
  if (!s) return '–'
  const d = new Date(s * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export const fmtDuration = (s: number | null | undefined) => {
  if (s == null || !Number.isFinite(s)) return '–'
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`
  const m = Math.floor(s / 60)
  return `${m} min ${Math.round(s - m * 60)} s`
}
