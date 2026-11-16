// Shared transport. Cancelling observation never implies cancelling a server-side operation.
import type { CodedMsg } from './types'

export class ApiError extends Error {
  constructor(readonly coded: CodedMsg, readonly status?: number) { super(coded.code) }
}
export class RequestCancelled extends Error {
  constructor() { super('Request cancelled'); this.name = 'AbortError' }
}
export const isCancelled = (e: unknown) => e instanceof RequestCancelled
  || (e instanceof Error && e.name === 'AbortError')

export const TIMEOUTS = { read: 15000, control: 60000, asset: 60000, long: 660000, upload: 1800000 } as const
export interface RequestOptions<T = unknown> {
  signal?: AbortSignal
  category?: keyof typeof TIMEOUTS
  timeout?: number
  validate?: (value: unknown) => value is T
  headers?: HeadersInit
  raw?: BodyInit
  cache?: RequestCache
}

export const isRecord = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const string = (v: unknown) => typeof v === 'string'
const array = Array.isArray
const number = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const rows = (v: unknown, check: (row: Record<string, unknown>) => boolean) =>
  array(v) && v.every(row => isRecord(row) && check(row))
const strings = (v: unknown) => array(v) && v.every(string)
const grid = (v: unknown) => isRecord(v) && number(v.width) && v.width > 0 && number(v.height) && v.height > 0
  && number(v.resolution) && v.resolution > 0 && array(v.origin) && v.origin.length === 3 && v.origin.every(number)
const stats = (v: unknown) => isRecord(v) && ['rmse', 'mean', 'median', 'std', 'min', 'max'].every(k => number(v[k]))
const evaluation = (v: unknown) => v === null || (isRecord(v) && stats(v.ape) && (v.rpe === null || stats(v.rpe))
  && isRecord(v.params) && isRecord(v.series) && array(v.series.traj_est) && array(v.series.traj_gt)
  && array(v.series.ape) && number(v.matched_poses) && number(v.duration) && number(v.length))

/** Core response contracts checked before data enters page state. Additional endpoints can supply a validator. */
export function validResponse(path: string, v: unknown, method = 'GET'): boolean {
  const p = path.split('?')[0]
  if (p === '/api/tasks') return isRecord(v) && rows(v.tasks, validTask)
  if (/^\/api\/tasks\//.test(p) || path.includes('background=true')) return isRecord(v) && validTask(v)
  if (p === '/api/navigation/maps') return rows(v, row => string(row.name) && typeof row.has_cloud === 'boolean' && grid(row.grid))
  if (p === '/api/maps') return rows(v, row => string(row.name) && typeof row.has_cloud === 'boolean' && array(row.issues)
    && (row.grid === null || grid(row.grid)))
  if (method === 'GET' && /^\/api\/maps\/[^/]+$/.test(p)) return isRecord(v) && string(v.name) && array(v.files)
    && array(v.issues) && (v.grid === null || grid(v.grid))
  if (/^\/api\/maps\/[^/]+\/regrid$/.test(p)) return isRecord(v) && string(v.token) && string(v.parent)
    && grid(v.grid) && isRecord(v.stats) && number(v.stats.occupied) && number(v.stats.free)
    && strings(v.warnings) && number(v.elapsed) && string(v.info)
  if (p === '/api/navigation/status') return isRecord(v) && v.type === 'navigation' && string(v.state) && array(v.scan)
  if (/^\/api\/slam_config\//.test(p)) return isRecord(v) && string(v.stack) && string(v.algorithm)
    && rows(v.params, row => string(row.key) && 'value' in row && 'auto' in row)
  if (p === '/api/evaluation/runs') return isRecord(v) && rows(v.runs, row => string(row.id) && string(row.type))
    && isRecord(v.params) && number(v.params.max_diff) && number(v.params.rpe_delta)
  if (/^\/api\/evaluation\/runs\/[^/]+(?:\/evaluate|\/loop)?$/.test(p)) return isRecord(v)
    && string(v.id) && string(v.type) && isRecord(v.info) && evaluation(v.eval) && evaluation(v.ref_eval)
    && strings(v.maps) && strings(v.figures)
  if (p === '/api/evaluation/relative') return isRecord(v) && string(v.baseline)
    && rows(v.rows, row => string(row.id) && evaluation(row.eval) && (row.error === null || string(row.error)))
  if (p === '/api/evaluation/export') return isRecord(v) && string(v.dir) && strings(v.files) && strings(v.runs) && strings(v.skipped)
  if (/\/solid$/.test(p)) return isRecord(v) && array(v.primitives) && array(v.meshes)
  return isRecord(v) || array(v)
}

function validTask(v: Record<string, unknown>) {
  return typeof v.id === 'string' && /^[a-f0-9]{32}$/.test(v.id) && string(v.kind) && string(v.target)
    && ['queued', 'running', 'succeeded', 'failed', 'interrupted'].includes(String(v.state)) && string(v.phase)
    && isRecord(v.input) && number(v.created) && number(v.updated) && ('result' in v)
    && (v.error === null || (isRecord(v.error) && string(v.error.code) && isRecord(v.error.params)))
}

export function responseError(body: unknown, status: number, statusText = ''): ApiError {
  const d = isRecord(body) ? body.detail : undefined
  if (isRecord(d) && typeof d.code === 'string') {
    return new ApiError({ code: d.code, params: isRecord(d.params) ? d.params as CodedMsg['params'] : {} }, status)
  }
  return new ApiError({ code: 'unknown', params: { reason: String(d ?? `${status} ${statusText}`) } }, status)
}

async function transport<T>(method: string, path: string, body: unknown, options: RequestOptions,
  consume: (res: Response) => Promise<T>): Promise<T> {
  const controller = new AbortController()
  let timedOut = false
  const cancel = () => controller.abort()
  if (options.signal?.aborted) throw new RequestCancelled()
  options.signal?.addEventListener('abort', cancel, { once: true })
  const category = options.category ?? (method === 'GET' ? 'read' : 'control')
  const timer = setTimeout(() => { timedOut = true; controller.abort() }, options.timeout ?? TIMEOUTS[category])
  try {
    const headers = new Headers(options.headers)
    if (body !== undefined && options.raw === undefined) headers.set('Content-Type', 'application/json')
    const res = await fetch(path, { method, headers, signal: controller.signal, cache: options.cache,
      body: options.raw ?? (body === undefined ? undefined : JSON.stringify(body)) })
    if (!res.ok) throw responseError(await res.json().catch(() => null), res.status, res.statusText)
    const result = await consume(res)
    if (controller.signal.aborted) throw new RequestCancelled()
    return result
  } catch (e) {
    if (timedOut) throw new ApiError({ code: 'request_timeout', params: { seconds: (options.timeout ?? TIMEOUTS[category]) / 1000 } })
    if (controller.signal.aborted || isCancelled(e)) throw new RequestCancelled()
    if (e instanceof ApiError) throw e
    throw new ApiError({ code: 'request_network', params: { reason: e instanceof Error ? e.message : String(e) } })
  } finally {
    clearTimeout(timer)
    options.signal?.removeEventListener('abort', cancel)
  }
}

export function request<T>(method: string, path: string, json?: unknown, options: RequestOptions<T> = {}): Promise<T> {
  return transport(method, path, json, options, async res => {
    let value: unknown
    try { value = await res.json() } catch { throw new ApiError({ code: 'response_invalid', params: { path } }) }
    if (!(options.validate ? options.validate(value) : validResponse(path, value, method))) {
      throw new ApiError({ code: 'response_invalid', params: { path } })
    }
    return value as T
  })
}

/** Buffer the complete asset inside the timeout, then expose its original headers to existing callers. */
export function fetchAsset(path: string, signal?: AbortSignal): Promise<Response> {
  return transport('GET', path, undefined, { signal, category: 'asset' }, async res =>
    new Response(await res.arrayBuffer(), { status: res.status, statusText: res.statusText, headers: res.headers }))
}

export function xyz(buffer: ArrayBuffer): Float32Array {
  if (buffer.byteLength % 12 !== 0) throw new ApiError({ code: 'response_invalid', params: { path: 'XYZ cloud' } })
  const points = new Float32Array(buffer)
  if (!points.every(Number.isFinite)) throw new ApiError({ code: 'response_invalid', params: { path: 'XYZ cloud' } })
  return points
}

/** One independently replaceable query. It also rejects stale responses from transports ignoring abort. */
export function latestRequest() {
  let sequence = 0
  let controller: AbortController | undefined
  return {
    cancel() { ++sequence; controller?.abort() },
    async run<T>(work: (signal: AbortSignal) => Promise<T>): Promise<T> {
      const mine = ++sequence
      controller?.abort()
      const current = controller = new AbortController()
      try {
        const result = await work(current.signal)
        if (mine !== sequence || current.signal.aborted) throw new RequestCancelled()
        return result
      } catch (e) {
        if (mine !== sequence || current.signal.aborted) throw new RequestCancelled()
        throw e
      }
    },
  }
}
