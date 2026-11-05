// Map catalog, projection previews and recycle bin API.
import { ApiError, fetchAsset } from '@/api'
import type { CodedMsg } from '@/types'

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

async function getJson<T>(path: string, method = 'GET', data?: unknown): Promise<T> {
  const res = await fetch(path, { method, headers: data ? { 'Content-Type': 'application/json' } : undefined,
    body: data ? JSON.stringify(data) : undefined })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const d = body.detail
    if (d && typeof d === 'object' && 'code' in d) throw new ApiError(d as CodedMsg)
    throw new ApiError({ code: 'unknown', params: { reason: `${res.status} ${res.statusText}` } })
  }
  return body as T
}

const enc = encodeURIComponent

export const mapsApi = {
  discardPreview: (token: string) => getJson<unknown>(`/api/maps/regrid/${enc(token)}`, 'DELETE'),
  preview: (name: string, params: unknown) => getJson<RegridPreview>(`/api/maps/${enc(name)}/regrid`, 'POST', params),
  savePreview: (token: string, name: string) => getJson<MapDetail>(`/api/maps/regrid/${enc(token)}/save`, 'POST', { name }),
  trash: () => getJson<TrashEntry[]>('/api/map-trash'),
  remove: (name: string) => getJson<unknown>(`/api/maps/${enc(name)}`, 'DELETE'),
  restore: (id: string) => getJson<unknown>(`/api/map-trash/${enc(id)}/restore`, 'POST'),
  purge: (id?: string) => getJson<unknown>(`/api/map-trash${id ? '/' + enc(id) : ''}`, 'DELETE'),
  list: () => getJson<MapEntry[]>('/api/maps'),
  detail: (name: string) => getJson<MapDetail>(`/api/maps/${enc(name)}`),
  /** 2D grid image (same rendering the navigation page uses) */
  grid: async (name: string, signal: AbortSignal) =>
    createImageBitmap(await (await fetchAsset(`/api/navigation/maps/${enc(name)}/grid.png`, signal)).blob()),
  /** Down-sampled xyz cloud; works for maps without a 2D grid too */
  cloud: async (name: string, signal: AbortSignal) => {
    const buf = await (await fetchAsset(`/api/maps/${enc(name)}/cloud`, signal)).arrayBuffer()
    return new Float32Array(buf, 0, Math.floor(buf.byteLength / 12) * 3)
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
