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
  | { type: 'done'; usage: { input: number; output: number }; model: string; rounds: number }
  | { type: 'error'; code: string; params: Record<string, unknown> }

/** What happened to a run tool call: blocked by the access level, waiting, approved / denied by the user, run
 *  without asking (auto), or the answer was stopped while it waited */
export type Decision = 'blocked' | 'pending' | 'approved' | 'denied' | 'auto' | 'cancelled'

export type Item =
  | { kind: 'user'; text: string }
  // live: arrived while this page was open (only those get the typing reveal; never sent by the backend)
  | { kind: 'assistant'; text: string; final: boolean; live?: boolean }
  | { kind: 'tool'; id: string; name: string; input: Record<string, unknown>; state: 'running' | 'ok' | 'error'; error?: string
      tool?: 'read' | 'run'; decision?: Decision }
  | { kind: 'note'; text: string; tone: 'info' | 'error' }

export const getStatus = () => RC().request<Status>('GET', `${BASE}/status`)
export const testConnection = () => RC().request<TestResult>('POST', `${BASE}/test`, {}, undefined, { timeout: 200000 })
export const getSession = (id: string) =>
  RC().request<{ id: string; title: string; items: Item[] }>('GET', `${BASE}/session/${encodeURIComponent(id)}`)

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
