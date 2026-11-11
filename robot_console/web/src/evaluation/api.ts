// Evaluation page API (backend: robot_console/evaluation.py). Errors are ApiError like the rest of api.ts.
import { ApiError } from '@/api'
import type { Bilingual, CodedMsg } from '@/types'

export type RunType = 'mapping' | 'navigation' | 'playback'

/** Numbers of an estimated trajectory that need no reference (evaluation.py trajectory_stats); heights relative
 *  to the first pose */
export interface TrajStats {
  poses: number
  duration: number
  length: number
  start_end: number
  start_end_xy: number
  z_end: number
  z_max_abs: number
  z_max_at: number
}

/** Playback run: where the data came from and what it can be compared with (evaluation.py _entry) */
export interface PlaybackInfo {
  bag: string
  bag_name: string
  range: [number, number] | null
  rate: number | null
  sensor: string | null
  sensor_label: Bilingual | null
  /** gt: the bag had ground truth (ATE/RPE); ref: reference odometry only; none: intrinsic numbers only */
  gt_kind: 'gt' | 'ref' | 'none'
  /** gt.tum came from a simulation running at the same time (older runs): ignored */
  gt_suspect: boolean
  ref_source: string | null
  ref_label: Bilingual | string | null
  /** x/y APE RMSE against the reference odometry (not accuracy) */
  ref_ape_rmse: number | null
  ref_error: string | null
  /** the bag part starts and ends at the same place (marked by the user): start_end = loop closure error */
  loop: boolean
  lag: { mean: number; max: number; samples: number } | null
  stats: TrajStats | null
}

/** Downsampled estimate of a run (GET /trajectory): t from the first pose, z relative to it */
export interface RunTrajectory {
  id: string
  t: number[]
  x: number[]
  y: number[]
  z: number[]
  t0: number
  lag: { t: number[]; lag: number[] } | null
}

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
  algorithm: string | null
  playback: PlaybackInfo | null
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
  params: { max_diff: number; rpe_delta: number; align: string; pose_relation: string; planar?: boolean }
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
  /** Playback with reference odometry: evo result against ref.tum (x/y only) */
  ref_eval: RunEval | null
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

/** One folder in <runs_dir>/.trash: a deleted run or batch record, or a group of runs moved there by an earlier
 *  clean-up. `run` is the run / batch id. */
export interface RunTrashEntry {
  entry: string
  run: string | null
  type: RunType | 'group' | 'batch'
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
  trajectory: (id: string) => request<RunTrajectory>('GET', `/api/evaluation/runs/${enc(id)}/trajectory`),
  /** Marks the bag part of a playback run as a loop (applies to every run of that part) */
  setLoop: (id: string, loop: boolean) => request<RunDetail>('POST', `/api/evaluation/runs/${enc(id)}/loop`, { loop }),
  /** evo APE of each run's estimate against the baseline run's estimate (same bag part) */
  relative: (ids: string[], baseline: string) =>
    request<{ baseline: string; rows: { id: string; eval: RunEval | null; error: string | null }[] }>(
      'POST', '/api/evaluation/relative', { ids, baseline }),
  exportRuns: (ids: string[]) => request<ExportResult>('POST', '/api/evaluation/export', { ids }),
  /** Moves runs or batch records (batch_*) to <runs_dir>/.trash (restorable); runs in use or referenced by a
   *  map, and a batch still running, are refused */
  trashRuns: (ids: string[]) =>
    request<{ moved: string[]; refused: RunTrashRefusal[] }>('POST', '/api/run-trash/move', { ids }),
  trashList: () => request<{ entries: RunTrashEntry[]; size: number }>('GET', '/api/run-trash'),
  trashRestore: (entry: string) => request<{ restored: string }>('POST', `/api/run-trash/${enc(entry)}/restore`),
  trashPurge: (entry: string) => request<{ deleted: string }>('DELETE', `/api/run-trash/${enc(entry)}`),
  /** kind 'batch' empties only batch records, 'runs' everything else (the two pages' recycle bins) */
  trashEmpty: (kind: 'runs' | 'batch') => request<{ deleted: number }>('DELETE', `/api/run-trash?kind=${kind}`),
}
