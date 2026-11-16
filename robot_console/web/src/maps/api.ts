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
}

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
