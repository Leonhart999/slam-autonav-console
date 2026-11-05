// Batch mapping experiments API (backend: robot_console/batch.py)
import type { ExportResult } from '@/evaluation/api'
import type { Bilingual, CodedMsg } from '@/types'
import { ApiError } from '@/api'

export interface MeanStd { mean: number; std: number; min: number; max: number; n: number }

export interface BatchRun {
  index: number
  run_id: string | null
  ok: boolean
  code: string | null
  params: Record<string, string | number>
  map: string | null
  started: string
  ended: string | null
  ape_rmse?: number | null
  rpe_rmse?: number | null
  length?: number
  duration?: number
  rtf?: number
  drive_sim_s?: number
  drive_wall_s?: number
  wall_s?: number
  eval_error?: string
}

export type BatchPhase = 'starting' | 'start_sim' | 'wait_sim' | 'start_slam' | 'wait_slam' | 'drive' | 'save_map'
  | 'stopping' | 'evaluate' | 'done'

export interface BatchStatus {
  state: 'idle' | 'running' | 'finished' | 'canceled' | 'failed'
  id?: string
  created?: string
  ended?: string | null
  preset?: string
  slam?: string
  n?: number
  save_maps?: boolean
  speed?: number            // requested sim speed-up (Gazebo real_time_factor); the reached RTF is per run
  run_timeout?: number
  runs?: BatchRun[]
  reason?: string | null
  summary?: {
    runs: number
    succeeded: number
    ape_rmse: MeanStd | null
    rpe_rmse: MeanStd | null
    length: MeanStd | null
    duration: MeanStd | null
    rtf?: MeanStd | null
    wall_s?: MeanStd | null
  } | null
  // live fields (only while this console runs the batch)
  index?: number
  phase?: BatchPhase
  progress?: { done: number; total: number | null } | null
  run_started?: number | null
  elapsed?: number
}

export interface BatchOptions {
  presets: { name: string; label: Bilingual; description: Bilingual }[]
  slam: { name: string; label: Bilingual }[]
  max_runs: number
  run_timeout: number
  speeds: number[]
}

export interface BatchHistoryEntry {
  id: string
  created: string
  preset: string
  slam: string
  n: number
  state: BatchStatus['state']
  summary: BatchStatus['summary']
  speed: number
  run_ids: string[]
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const d = data.detail
    if (d && typeof d === 'object' && 'code' in d) throw new ApiError(d as CodedMsg)
    throw new ApiError({ code: 'unknown', params: { reason: String(d ?? `${res.status} ${res.statusText}`) } })
  }
  return data as T
}

export const batchApi = {
  options: () => request<BatchOptions>('GET', '/api/batch/options'),
  status: () => request<BatchStatus>('GET', '/api/batch/status'),
  history: () => request<BatchHistoryEntry[]>('GET', '/api/batch/history'),
  get: (id: string) => request<BatchStatus>('GET', `/api/batch/${encodeURIComponent(id)}`),
  start: (p: { preset: string; slam: string; n: number; save_maps: boolean; speed: number }) =>
    request<BatchStatus>('POST', '/api/batch/start', p),
  cancel: () => request<BatchStatus>('POST', '/api/batch/cancel'),
  /** Same export as the run list, plus batch_summary.csv / batch_runs.csv of this batch */
  exportBatch: (b: BatchStatus): Promise<ExportResult> =>
    request<ExportResult>('POST', '/api/evaluation/export', {
      ids: (b.runs ?? []).filter((r) => r.ok && r.run_id).map((r) => r.run_id), batch: b.id,
    }),
}
