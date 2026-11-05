// Evaluation page API (backend: robot_console/evaluation.py). Errors are ApiError like the rest of api.ts.
import { ApiError } from '@/api'
import type { CodedMsg } from '@/types'

export type RunType = 'mapping' | 'navigation'

/** Navigation run metrics (robot_console/nav_eval.py nav_metrics) */
export interface NavMetrics {
  variant: string | null
  map: string | null
  state: string | null
  goals: number
  finished: number
  succeeded: number
  success_rate: number | null
  mean_time_s: number | null
  mean_path_m: number | null
  mean_goal_error_m: number | null
  mean_loc_error_m: number | null
  total_time_s: number
  total_path_m: number
  recoveries: number
  min_clearance_m: number | null
  amcl_samples: number | null
  amcl_rmse_m: number | null
  amcl_mean_m: number | null
  amcl_max_m: number | null
  has_gt: boolean
}

export interface NavGoalRecord {
  index: number
  x: number
  y: number
  yaw: number
  result: 'succeeded' | 'aborted' | 'canceled' | 'rejected' | 'timeout' | string | null
  time_s?: number | null
  path_m?: number | null
  goal_error_m?: number | null
  goal_yaw_error_deg?: number | null
  loc_error_m?: number | null
  recoveries?: number | null
  min_clearance_m?: number | null
  speed_scale?: number | null
}

export interface RunEntry {
  id: string
  type: RunType
  preset: string | null
  started: string | null
  ended: string | null
  duration: number | null
  length: number | null
  maps: string[]
  recording: boolean
  evaluable: boolean
  ape_rmse: number | null
  rpe_rmse: number | null
  evaluated: string | null
  eval_error: string | null
  variant: string | null
  nav: NavMetrics | null
  figures: string[]
}

export interface ErrorStats {
  rmse: number
  mean: number
  median: number
  std: number
  min: number
  max: number
}

export interface RunEval {
  evo: string
  created: string
  params: { max_diff: number; rpe_delta: number; align: string; pose_relation: string }
  matched_poses: number
  duration: number
  length: number
  ref_length: number
  ape: ErrorStats
  rpe: (ErrorStats & { pairs: number }) | null
  rpe_error?: string
  series: {
    traj_est: [number, number][]
    traj_gt: [number, number][]
    ape: [number[], number[]]
    rpe: [number[], number[]] | null
  }
}

export interface RunDetail extends RunEntry {
  info: Record<string, unknown> & { goals?: NavGoalRecord[] }
  eval: RunEval | null
  /** Navigation only: AMCL error over time; goals = [goal index, first time] */
  amcl: { t: number[]; error: number[]; goals: [number, number][] } | null
}

export interface ExportResult {
  dir: string
  files: string[]
  runs: string[]
  skipped: string[]
}

/** Why a run was not moved to the trash (robot_console/run_trash.py) */
export interface RunTrashRefusal {
  id: string
  reason: 'not_found' | 'recording' | 'used_by_map' | 'failed'
  maps?: string[]
  detail?: string
}

/** One folder in <runs_dir>/.trash: a deleted run, or a group of runs moved there by an earlier clean-up */
export interface RunTrashEntry {
  entry: string
  run: string | null
  type: RunType | 'group'
  preset: string | null
  started: string | null
  deleted: string
  runs: number
  size: number
  restorable: boolean
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

const enc = encodeURIComponent

export const evalApi = {
  runs: () => request<{ runs: RunEntry[]; params: { max_diff: number; rpe_delta: number } }>('GET', '/api/evaluation/runs'),
  detail: (id: string) => request<RunDetail>('GET', `/api/evaluation/runs/${enc(id)}`),
  /** Runs evo when there is no valid cached result (force: always). About 0.2–1 s per run */
  evaluate: (id: string, force = false) =>
    request<RunDetail>('POST', `/api/evaluation/runs/${enc(id)}/evaluate${force ? '?force=true' : ''}`),
  figureUrl: (id: string, name: string) => `/api/evaluation/runs/${enc(id)}/files/${enc(name)}`,
  exportRuns: (ids: string[]) => request<ExportResult>('POST', '/api/evaluation/export', { ids }),
  /** Moves runs to <runs_dir>/.trash (restorable); runs in use or referenced by a map are refused */
  trashRuns: (ids: string[]) =>
    request<{ moved: string[]; refused: RunTrashRefusal[] }>('POST', '/api/run-trash/move', { ids }),
  trashList: () => request<{ entries: RunTrashEntry[]; size: number }>('GET', '/api/run-trash'),
  trashRestore: (entry: string) => request<{ restored: string }>('POST', `/api/run-trash/${enc(entry)}/restore`),
  trashPurge: (entry: string) => request<{ deleted: string }>('DELETE', `/api/run-trash/${enc(entry)}`),
  trashEmpty: () => request<{ deleted: number }>('DELETE', '/api/run-trash'),
}
