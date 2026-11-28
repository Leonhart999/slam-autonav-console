// Talking to the plugin backend: status, connection test, and the NDJSON chat stream
import { RC } from './sdk'

const BASE = '/api/ext/ai_assistant'

export interface ProfileBrief {
  id: string
  name: string
  color: string
  format: 'openai' | 'anthropic'
  model: string
  models: { id: string; label: string; effort: Effort; efforts: EffortValue[] }[]
}

/** What the assistant may do for a question: chat only, read data, run experiments after asking, or freely */
export type Access = 'chat' | 'read' | 'ask' | 'auto'
export const ACCESS_LEVELS: Access[] = ['chat', 'read', 'ask', 'auto']

export interface Status {
  provider: string
  model: string
  base_url: string
  key_set: boolean
  name: string
  active: string
  effort: Effort
  profiles: ProfileBrief[]
  tools: { name: string; kind: 'read' | 'run' }[]
  problem: { code: string; params: Record<string, unknown> } | null
}

export type EffortValue = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max' | 'ultra'
export type Effort = '' | EffortValue
export type Currency = 'USD' | 'CNY'
export interface ModelEntry { id: string; label: string; effort: Effort; efforts: EffortValue[] }

/** A model service profile as the backend shows it (the key itself never comes back) */
export interface Profile {
  id: string
  name: string
  note: string
  website: string
  color: string
  format: 'openai' | 'anthropic'
  base_url: string
  endpoints: string[]
  full_url: boolean
  models: ModelEntry[]
  model: string
  proxy: string
  user_agent: string
  headers: Record<string, string>
  body: Record<string, unknown>
  key_set: boolean
  key_hint: string
  managed?: boolean
}

/** What the editor sends: a profile plus the key field (empty = keep) and clear_key */
export type ProfileInput = Omit<Profile, 'id' | 'key_set' | 'key_hint'> & { api_key?: string; clear_key?: boolean }

export interface Listing { active: string; profiles: Profile[] }
export interface RemoteModel { id: string; name: string; context: number | null }
export interface Latency { url: string; ms: number | null; status: number | null; error: string | null }
export interface TestResult { model: string; seconds: number; reply: string }

export const getProviders = () => RC().request<Listing>('GET', `${BASE}/providers`)
export const createProvider = (profile: ProfileInput) => RC().request<Profile>('POST', `${BASE}/providers`, { profile })
export const updateProvider = (id: string, profile: ProfileInput) =>
  RC().request<Profile>('PUT', `${BASE}/providers/${encodeURIComponent(id)}`, { profile })
export const deleteProvider = (id: string) => RC().request<Listing>('DELETE', `${BASE}/providers/${encodeURIComponent(id)}`)
export const orderProviders = (ids: string[]) => RC().request<Listing>('POST', `${BASE}/providers/order`, { ids })
export const activateProvider = (id: string, model?: string, effort?: Effort) =>
  RC().request<Profile>('POST', `${BASE}/providers/${encodeURIComponent(id)}/activate`,
    { ...(model ? { model } : {}), ...(effort !== undefined ? { effort } : {}) })
export const probeModels = (id: string | null, profile: ProfileInput) =>
  RC().request<{ models: RemoteModel[] }>('POST', `${BASE}/probe/models`, { id, profile }, undefined, { timeout: 75000 })
export const probeLatency = (id: string | null, profile: ProfileInput, urls: string[]) =>
  RC().request<{ results: Latency[] }>('POST', `${BASE}/probe/latency`, { id, profile, urls }, undefined, { timeout: 30000 })
export const testProfile = (id: string | null, profile: ProfileInput) =>
  RC().request<TestResult>('POST', `${BASE}/test`, { id, profile }, undefined, { timeout: 200000 })

export type ChatEvent =
  | { type: 'session'; id: string }
  | { type: 'status'; state: string; round: number }
  | { type: 'text'; text: string; final: boolean; truncated?: boolean }
  | { type: 'tool'; id: string; name: string; input: Record<string, unknown>; kind?: 'read' | 'run' }
  | { type: 'confirm'; id: string; name: string; input: Record<string, unknown> }
  | { type: 'tool_result'; id: string; ok: boolean; chars: number; error: string | null; decision?: Decision | null }
  | { type: 'usage'; round: number; usage: Tokens; model: string }
  | { type: 'done'; usage: Tokens; model: string; rounds: number }
  | { type: 'error'; code: string; params: Record<string, unknown> }

/** What happened to a run tool call: blocked by the access level, waiting, approved / denied by the user, run
 *  without asking (auto), or the answer was stopped while it waited */
export type Decision = 'blocked' | 'pending' | 'approved' | 'denied' | 'auto' | 'cancelled'

export type Item =
  | { kind: 'user'; text: string }
  // live: arrived while this page was open (only those get the typing reveal; never sent by the backend)
  | { kind: 'assistant'; text: string; final: boolean; live?: boolean; tokens?: Tokens }
  | { kind: 'tool'; id: string; name: string; input: Record<string, unknown>; state: 'running' | 'ok' | 'error'; error?: string
      tool?: 'read' | 'run'; decision?: Decision }
  | { kind: 'note'; text: string; tone: 'info' | 'error' }

export const getStatus = () => RC().request<Status>('GET', `${BASE}/status`)
export const testConnection = () => RC().request<TestResult>('POST', `${BASE}/test`, {}, undefined, { timeout: 200000 })
export const getSession = (id: string, signal?: AbortSignal) =>
  RC().request<{ id: string; title: string; items: Item[]; pinned?: boolean; usage?: Partial<Tokens> }>(
    'GET', `${BASE}/session/${encodeURIComponent(id)}`, undefined, signal)

// ---- tokens and usage statistics ----
export interface Tokens { input: number; output: number; cache_read: number; cache_write: number; reasoning: number }
export interface Money { currency: Currency; amount: number }

// ---- conversation history ----
export interface SessionBrief {
  id: string
  title: string
  created: number
  updated: number
  pinned: boolean
  turns: number
  model: string
  profile_name: string
  tokens: { input: number; output: number; cache: number }
  preview: string
  snippet?: string
}
export interface SessionPage { total: number; items: SessionBrief[] }

const sid = (id: string) => `${BASE}/sessions/${encodeURIComponent(id)}`
export const listSessions = (q = '', limit = 200, signal?: AbortSignal, offset = 0) =>
  RC().request<SessionPage>('GET', `${BASE}/sessions?${new URLSearchParams({ q, limit: String(limit), offset: String(offset) })}`, undefined, signal)
export const patchSession = (id: string, change: { title?: string; pinned?: boolean }) =>
  RC().request<SessionBrief>('PATCH', sid(id), change)
export const deleteSession = (id: string) => RC().request<{ ok: boolean }>('DELETE', sid(id))
export const exportSessionUrl = (id: string, format: 'md' | 'json' = 'md') => `${sid(id)}/export?format=${format}`

export interface UsageQuery { from?: number; to?: number; profile?: string; model?: string }
const qs = (o: Record<string, string | number | undefined | null>) => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== null && v !== '') p.set(k, String(v))
  return p.toString()
}
export interface UsageSummary {
  calls: number; sessions: number; input: number; output: number; cache_read: number; cache_write: number
  reasoning: number; ms_avg: number | null; ms_p90: number | null; cost: Money[]; unpriced_models: string[]; errors: number
}
export interface UsagePoint {
  t: number; calls: number; input: number; output: number; cache_read: number; cache_write: number; reasoning: number; cost: Money[]
}
export interface UsageRow {
  key: string; label: string; profile_name?: string; calls: number; input: number; output: number; cache_read: number
  cache_write: number; reasoning: number; cost: Money[]; ms_avg: number | null; last: number; session_exists?: boolean
  title?: string
}
export type UsageBy = 'model' | 'profile' | 'session'
export const usageSummary = (q: UsageQuery) => RC().request<UsageSummary>('GET', `${BASE}/usage/summary?${qs({ ...q })}`)
export const usageSeries = (q: UsageQuery, bucket: 'hour' | 'day') => {
  let tzName = ''
  try {
    tzName = Intl.DateTimeFormat().resolvedOptions().timeZone || ''
  } catch { /* fallback to offset */ }
  return RC().request<{ bucket: string; points: UsagePoint[] }>('GET',
    `${BASE}/usage/series?${qs({ ...q, bucket, tz: new Date().getTimezoneOffset(), tz_name: tzName })}`)
}
export const usageBreakdown = (q: UsageQuery, by: UsageBy) =>
  RC().request<{ rows: UsageRow[] }>('GET', `${BASE}/usage/breakdown?${qs({ ...q, by })}`)
export const usageFilters = () => RC().request<{ profiles: { id: string; name: string }[]; models: string[] }>('GET', `${BASE}/usage/filters`)
export const usageExportUrl = (q: UsageQuery) => `${BASE}/usage/export?${qs({ ...q, format: 'csv' })}`
export const usagePrunePreview = (before: number | null) =>
  RC().request<{ rows: number }>('GET', `${BASE}/usage/prune/preview?${qs({ before })}`)
export const usagePrune = (before: number | null) => RC().request<{ deleted: number }>('POST', `${BASE}/usage/prune`, { before })

// ---- prices for the usage estimate (pricing.py): one table by model id, synced from models.dev ----
export const PRICE_FIELDS = ['input', 'output', 'cache_read', 'cache_write'] as const
export type PriceField = typeof PRICE_FIELDS[number]
export type PriceRates = Record<PriceField, number | null>
export interface PriceEntry extends PriceRates {
  id: string; name: string; currency: Currency; source: 'manual' | 'sync' | 'import'; provider?: string; updated: number
}
export interface PricingSync {
  enabled: boolean; providers: string[]; provider_names: Record<string, string>; last: number | null
  attempt: number | null; error: string | null; running: boolean
}
export interface Pricing {
  entries: PriceEntry[]; hidden: number; sync: PricingSync; catalog: { fetched: number | null; providers: number }
}
export interface CatalogModel extends PriceRates { provider: string; provider_name: string; id: string; name: string; added: boolean }
export interface Catalog { fetched: number | null; providers: { id: string; name: string; count: number }[]; models: CatalogModel[] }
export const getPricing = () => RC().request<Pricing>('GET', `${BASE}/pricing`)
export const savePrice = (entry: Partial<PriceRates> & { id: string; name?: string; currency: Currency }, oldId?: string) =>
  RC().request<PriceEntry>('POST', `${BASE}/pricing/entries`, { entry, old_id: oldId ?? null })
export const deletePrices = (ids: string[]) => RC().request<{ deleted: number }>('POST', `${BASE}/pricing/entries/delete`, { ids })
export const setPricingSync = (change: { enabled?: boolean; providers?: string[]; unhide?: boolean }) =>
  RC().request<Pricing>('PATCH', `${BASE}/pricing/sync`, change)
export const syncPricing = () => RC().request<Pricing>('POST', `${BASE}/pricing/sync`)
export const searchCatalog = (q: string, provider = '', limit = 200) =>
  RC().request<Catalog>('GET', `${BASE}/pricing/catalog?${qs({ q, provider, limit })}`)
export const importPrices = (models: { provider: string; id: string }[]) =>
  RC().request<Pricing & { imported: number }>('POST', `${BASE}/pricing/import`, { models })

// ---- embedded CPA engine and OAuth sign-in ----
export type EngineState = 'missing' | 'installing' | 'stopped' | 'starting' | 'ready' | 'error'
export interface EngineStatus {
  state: EngineState
  version: string
  progress?: { done: number; total: number } | null
  pid?: number | null
  uptime_s?: number | null
  proxy_url: string
  autostart: boolean
  error?: { code: string; detail?: string } | null
  log_tail?: string[]
  callback_ports?: { provider: string; port: number }[] | Record<string, number>
}
export type OAuthProvider = 'kimi' | 'kimi-ai' | 'copilot' | 'meta' | 'codex' | 'claude' | 'antigravity' | 'xai' | 'devin'
export interface OAuthStart { url: string; state: string; flow?: 'browser' | 'device'; user_code?: string; expires_in?: number }
export interface Credential {
  name: string
  id?: string
  auth_index?: string
  provider?: string
  type?: string
  label?: string
  email?: string
  account?: string
  status?: string
  status_message?: string
  disabled?: boolean
  unavailable?: boolean
  siwc_bind_supported?: boolean
  use_siwc?: boolean
  siwc_status?: 'off' | 'valid' | 'invalid'
  siwc_bound?: boolean
  requires_sign_in?: boolean
  renewal_state?: string
  next_action?: string
  [k: string]: unknown
}

const CPA = `${BASE}/cpa`
const LONG = { timeout: 60000 }
export const cpaStatus = () => RC().request<EngineStatus>('GET', `${CPA}/status`)
export const cpaInstall = (proxy_url?: string) =>
  RC().request<EngineStatus>('POST', `${CPA}/install`, proxy_url ? { proxy_url } : {}, undefined, LONG)
export const cpaAction = (action: 'start' | 'stop' | 'restart') =>
  RC().request<EngineStatus>('POST', `${CPA}/${action}`, {}, undefined, LONG)
export const cpaLogs = (lines = 200) => RC().request<{ lines: string[] }>('GET', `${CPA}/logs?lines=${lines}`)
export const cpaSettings = (change: { proxy_url?: string; autostart?: boolean }) =>
  RC().request<EngineStatus>('PUT', `${CPA}/settings`, change, undefined, LONG)
export const oauthStart = (provider: OAuthProvider, body: { proxy_url?: string; name?: string } = {}) =>
  RC().request<OAuthStart>('POST', `${CPA}/oauth/start`, { provider, ...body }, undefined, LONG)
export const oauthStatus = (state: string) =>
  RC().request<{ status: 'ok' | 'wait' | 'error'; error?: string }>('GET', `${CPA}/oauth/status?state=${encodeURIComponent(state)}`)
export const oauthCancel = (state: string) =>
  RC().request<{ cancelled: boolean }>('DELETE', `${CPA}/oauth/session?state=${encodeURIComponent(state)}`)
export const oauthCallback = (provider: OAuthProvider, redirect_url: string) =>
  RC().request<{ ok: boolean }>('POST', `${CPA}/oauth/callback`, { provider, redirect_url })
export const oauthSiwc = (name: string) => RC().request<OAuthStart>('POST', `${CPA}/oauth/siwc`, { name }, undefined, LONG)
export const vertexImport = (filename: string, content: string, prefix?: string) =>
  RC().request<{ ok: boolean; name?: string }>('POST', `${CPA}/vertex`, { filename, content, ...(prefix ? { prefix } : {}) }, undefined, LONG)
export const listCredentials = () => RC().request<{ items: Credential[] }>('GET', `${CPA}/credentials`)
export const setCredentialDisabled = (name: string, disabled: boolean) =>
  RC().request<{ ok: boolean }>('PATCH', `${CPA}/credentials/status`, { name, disabled })
export const refreshCredential = (name: string) =>
  RC().request<{ ok: boolean }>('POST', `${CPA}/credentials/refresh`, { name }, undefined, LONG)
export const verifyCredential = (name: string, model?: string) =>
  RC().request<Record<string, unknown>>('POST', `${CPA}/credentials/verify`, { name, ...(model ? { model } : {}) }, undefined, { timeout: 120000 })
export const removeCredential = (name: string) =>
  RC().request<{ ok: boolean }>('DELETE', `${CPA}/credentials?name=${encodeURIComponent(name)}`)
export const useCpa = (activate = true) =>
  RC().request<{ profile: Profile | null }>('POST', `${CPA}/use`, { activate }, undefined, LONG)

/** Download a file from a plugin GET route (keeps the browser's own download handling) */
export function download(url: string) {
  const a = document.createElement('a')
  a.href = url
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
}

/** A coded error from a non-2xx answer of /chat (it fails before the stream starts) */
export class ChatError extends Error {
  constructor(readonly code: string, readonly params: Record<string, unknown> = {}) { super(code) }
}

export const confirmCall = (session: string, id: string, approve: boolean) =>
  RC().request<{ ok: boolean }>('POST', `${BASE}/chat/confirm`, { session, id, approve })

export async function streamChat(body: { message: string; session?: string; page?: string; access?: Access },
  onEvent: (e: ChatEvent) => void, signal: AbortSignal) {
  let res: Response
  try {
    res = await fetch(`${BASE}/chat`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
    })
  } catch (e) {
    if (signal.aborted) throw new ChatError('cancelled')
    throw new ChatError('network', { reason: e instanceof Error ? e.message : String(e) })
  }
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => null) as { detail?: { code?: string; params?: Record<string, unknown> } } | null
    const d = data?.detail
    throw new ChatError(d?.code ?? 'unknown', d?.params ?? { reason: `${res.status} ${res.statusText}` })
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let nl: number
      while ((nl = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, nl).trim()
        buffer = buffer.slice(nl + 1)
        if (line) onEvent(JSON.parse(line) as ChatEvent)
      }
    }
  } catch (e) {
    if (signal.aborted) throw new ChatError('cancelled')
    throw new ChatError('network', { reason: e instanceof Error ? e.message : String(e) })
  }
}
