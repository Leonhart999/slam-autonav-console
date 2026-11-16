// 2D grid editor API (backend: robot_console/map_edit.py)
import { fetchAsset, request } from '@/http'

export const OCCUPIED = 0
export const UNKNOWN = 205
export const FREE = 254

export interface EditGrid {
  width: number
  height: number
  resolution: number
  /** Cells Nav2 reads as free only because of an old free_thresh (pcd2grid would call them unknown) */
  legacyFree: number
  /** free_thresh of the source map.yaml */
  freeThresh: number
  /** map.pgm row order: row 0 is the top of the image (largest y) */
  cells: Uint8Array
}

export interface EditSaved {
  name: string
  parent: string
  changed: { free: number; occupied: number; unknown: number }
  width: number
  height: number
  legacy_free_cells: number
  legacy_unknown: boolean
  restored_unknown: number
}

const enc = encodeURIComponent

export const editApi = {
  grid: async (name: string, signal: AbortSignal): Promise<EditGrid> => {
    const res = await fetchAsset(`/api/maps/${enc(name)}/edit/grid`, signal)
    const width = Number(res.headers.get('X-Grid-Width'))
    const height = Number(res.headers.get('X-Grid-Height'))
    const resolution = Number(res.headers.get('X-Grid-Resolution'))
    const legacyFree = Number(res.headers.get('X-Grid-Legacy-Free') ?? 0)
    const freeThresh = Number(res.headers.get('X-Grid-Free-Thresh') ?? 0.25)
    return { width, height, resolution, legacyFree, freeThresh, cells: new Uint8Array(await res.arrayBuffer()) }
  },
  save: async (name: string, newName: string, cells: Uint8Array, legacyUnknown = false): Promise<EditSaved> => {
    const q = `new_name=${enc(newName)}${legacyUnknown ? '&legacy_unknown=true' : ''}`
    return request<EditSaved>('POST', `/api/maps/${enc(name)}/edit/save?${q}`, undefined, {
      raw: cells.slice().buffer as ArrayBuffer, headers: { 'Content-Type': 'application/octet-stream' }, category: 'long',
    })
  },
}
