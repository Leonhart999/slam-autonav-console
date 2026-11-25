// Map catalog, projection previews and recycle bin API.
import { fetchAsset, request, xyz, type RequestOptions } from '@/http'
import { taskResult, type ConsoleTask } from '@/tasks'

export type IssueLevel = 'error' | 'warn' | 'info'

export interface MapIssue {
  code: string
  level: IssueLevel
  params: Record<string, string | number>
}

export interface MapGrid {
  resolution: number
  width: number
  height: number
  origin: [number, number, number]
  size_m: [number, number]
  image: string
}

export interface MapEntry {
  name: string
  kind: 'folder' | 'flat'
  created: string
  source: string | null
  /** name of the bag the map was built from (bag playback), else null */
  bag: string | null
  algorithm: string | null
  preset: string | null
  points: number | null
  voxels: number | null
  has_grid: boolean
  has_cloud: boolean
  has_solid: boolean
  navigable: boolean
  grid: MapGrid | null
  extent: number[] | null
  issues: MapIssue[]
}

export interface MapFile {
  name: string
  dir: boolean
  size: number
  count: number | null
  role: string | null
}

export interface MapDetail extends MapEntry {
  frame: string | null
  run: string | null
  duration: number | null
  length: number | null
  voxel_size: number | null
  sim_cmd: string | null
  slam_cmd: string | null
  location: string
  location_win: string
  yaml_path: string | null
  files: MapFile[]
  total_size: number
  /** the scans the map was built from are still on disk (2D can be rebuilt from scan evidence) */
  has_scans?: boolean
  /** loop optimization: reason it is unavailable (null = available), ground-constrained FAST-LIO2 variant */
  loop?: { reason: 'no_scans' | 'already_optimized' | null; ground: boolean }
}

export interface LoopParams {
  loop_radius: number
  loop_min_gap: number
  map_range: number
  map_res: number
  scan_stride: number
  accept_overlap: number
  accept_rmse: number
  max_loop_shift: number
  max_loop_yaw_deg: number
}
export interface LoopWall { points: number; median_m: number; p90_m: number; rmse_m: number }
export interface LoopRow {
  s_i: number; s_j: number; dist: number
  overlap_before: number; overlap_after: number; rmse_before: number; rmse_after: number
  shift_xy: number; yaw_deg: number; ok: boolean; reasons: string[]
}
export interface LoopResult {
  token: string
  parent: string
  params: LoopParams
  ground: boolean
  elapsed: number
  scans: number
  keyframes: number
  path_m: number
  duration_s: number
  accepted: number
  revisits: number
  end_correction: { xy_m: number; z_m: number; yaw_deg: number } | null
  max_correction: { xy_m: number; yaw_deg: number } | null
  loops: LoopRow[]
  junctions: { s_i: number; s_j: number; wall_before: LoopWall | null; wall_after: LoopWall | null }[]
  figures: string[]
}
export interface LoopEnv { installed: boolean; python: string; reason: string | null; install: string }

export interface RegridPreview {
  token: string
  parent: string
  grid: MapGrid
  info: string
  warnings: string[]
  elapsed: number
  input_points: number
  stats: { occupied: number; free: number }
}
export interface TrashEntry { id: string; name: string; deleted: string; size: number }

function getJson<T>(path: string, method = 'GET', data?: unknown, options: RequestOptions<T> = {}): Promise<T> {
  return request<T>(method, path, data, options)
}

const enc = encodeURIComponent

export const mapsApi = {
  discardPreview: (token: string) => getJson<unknown>(`/api/maps/regrid/${enc(token)}`, 'DELETE'),
  preview: (name: string, params: unknown, signal?: AbortSignal, onTask?: (task: ConsoleTask<RegridPreview>) => void) =>
    taskResult<RegridPreview>(`/api/maps/${enc(name)}/regrid`, params, signal, onTask),
  savePreview: (token: string, name: string) => getJson<MapDetail>(`/api/maps/regrid/${enc(token)}/save`, 'POST', { name }),
  loopEnv: (signal?: AbortSignal) => getJson<LoopEnv>('/api/maps/loop/env', 'GET', undefined, { signal }),
  loop: (name: string, params: LoopParams, signal?: AbortSignal, onTask?: (task: ConsoleTask<LoopResult>) => void) =>
    taskResult<LoopResult>(`/api/maps/${enc(name)}/loop`, params, signal, onTask),
  loopFigure: async (token: string, figure: string, signal: AbortSignal) =>
    (await fetchAsset(`/api/maps/loop/${enc(token)}/figures/${enc(figure)}.png`, signal)).blob(),
  saveLoop: (token: string, name: string) =>
    getJson<MapDetail & { loop_run: string }>(`/api/maps/loop/${enc(token)}/save`, 'POST', { name }),
  discardLoop: (token: string) => getJson<unknown>(`/api/maps/loop/${enc(token)}`, 'DELETE'),
  trash: () => getJson<TrashEntry[]>('/api/map-trash'),
  remove: (name: string) => getJson<unknown>(`/api/maps/${enc(name)}`, 'DELETE'),
  restore: (id: string) => getJson<unknown>(`/api/map-trash/${enc(id)}/restore`, 'POST'),
  purge: (id?: string) => getJson<unknown>(`/api/map-trash${id ? '/' + enc(id) : ''}`, 'DELETE'),
  list: (signal?: AbortSignal) => getJson<MapEntry[]>('/api/maps', 'GET', undefined, { signal }),
  detail: (name: string, signal?: AbortSignal) => getJson<MapDetail>(`/api/maps/${enc(name)}`, 'GET', undefined, { signal }),
  /** 2D grid image (same rendering the navigation page uses) */
  grid: async (name: string, signal: AbortSignal) =>
    createImageBitmap(await (await fetchAsset(`/api/navigation/maps/${enc(name)}/grid.png`, signal)).blob()),
  /** Down-sampled xyz cloud; works for maps without a 2D grid too */
  cloud: async (name: string, signal: AbortSignal) => {
    const buf = await (await fetchAsset(`/api/maps/${enc(name)}/cloud`, signal)).arrayBuffer()
    return xyz(buf)
  },
}

/** 1536 → "1.5 KB" */
export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB']
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`
}
