// Batch navigation experiments API (backend: robot_console/nav_batch.py)
import type { ExportResult } from '@/evaluation/api'
import type { NavMission, PickedPose } from '@/types'
import type { MeanStd } from '@/evaluation/batch'
import { request } from '@/http'
import { taskResult } from '@/tasks'

export type NavLocalizer = 'amcl' | 'gt'

export type NavBatchPhase = 'starting' | 'start_sim' | 'wait_sim' | 'start_nav' | 'wait_nav'
  | 'resetting' | 'drive' | 'stopping' | 'done'

export interface NavBatchRun {
  index: number
  run_id: string | null
  ok: boolean
  all_succeeded: boolean
  code: string | null
  params: Record<string, string | number>
  reset_kind: 'initial' | 'fast' | 'restart'
  started: string
  ended: string | null
  goals?: number
  finished?: number
  succeeded?: number
  success_rate?: number | null
  total_time_s?: number | null
  mean_time_s?: number | null
  total_path_m?: number | null
  mean_path_m?: number | null
  mean_goal_error_m?: number | null
  mean_loc_error_m?: number | null
  amcl_rmse_m?: number | null
  recoveries?: number
  min_clearance_m?: number | null
  wall_s?: number
}

export interface NavBatchSummary {
  runs: number
  completed: number
  all_goals_runs: number
  total_goals: number
  total_succeeded: number
  pooled_success_rate: number | null
  success_rate: MeanStd | null
  total_time_s: MeanStd | null
  mean_time_s: MeanStd | null
  total_path_m: MeanStd | null
  mean_path_m: MeanStd | null
  recoveries: MeanStd | null
  total_recoveries: number
  goal_error_m: MeanStd | null
  loc_error_m: MeanStd | null
  amcl_rmse_m: MeanStd | null
  min_clearance_m: MeanStd | null
  wall_s: MeanStd | null
}

export interface NavBatchStatus {
  state: 'idle' | 'running' | 'finished' | 'canceled' | 'failed'
  id?: string
  created?: string
  ended?: string | null
  map?: string
  preset?: string
  variant?: string
  localizer?: NavLocalizer
  speed_scale?: number
  n?: number
  goals?: PickedPose[]
  runs?: NavBatchRun[]
  reason?: string | null
  summary?: NavBatchSummary | null
  index?: number
  phase?: NavBatchPhase
  mission?: NavMission | null
  run_started?: number | null
  elapsed?: number
}

export interface NavBatchOptions {
  variants: string[]
  default_variant: string
  localizers: NavLocalizer[]
  max_runs: number
  goal_timeout: number
  speed_scale_range: [number, number]
}

export interface NavBatchHistoryEntry {
  id: string
  created: string
  map: string
  preset: string
  variant: string
  localizer: NavLocalizer
  speed_scale: number
  n: number
  goals_count: number
  state: NavBatchStatus['state']
  summary: NavBatchSummary | null
  run_ids: string[]
}

export const navBatchApi = {
  options: () => request<NavBatchOptions>('GET', '/api/nav-batch/options'),
  status: () => request<NavBatchStatus>('GET', '/api/nav-batch/status'),
  history: () => request<NavBatchHistoryEntry[]>('GET', '/api/nav-batch/history'),
  get: (id: string) => request<NavBatchStatus>('GET', `/api/nav-batch/${encodeURIComponent(id)}`),
  start: (p: {
    map: string
    variant: string
    localizer: NavLocalizer
    goals: PickedPose[]
    n: number
    speed_scale: number
  }) => request<NavBatchStatus>('POST', '/api/nav-batch/start', p),
  cancel: () => request<NavBatchStatus>('POST', '/api/nav-batch/cancel'),
  exportBatch: (b: NavBatchStatus, signal?: AbortSignal): Promise<ExportResult> =>
    taskResult<ExportResult>('/api/evaluation/export', {
      ids: (b.runs ?? []).filter((r) => r.ok && r.run_id).map((r) => r.run_id),
      batch: b.id,
    }, signal),
}
