import { ApiError } from '@/api'
import type { NavigationMap, NavigationStatus } from './types'

async function response(path: string, signal: AbortSignal, method = 'GET') {
  const res = await fetch(`/api/navigation${path}`, { method, signal })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const detail = body?.detail
    if (detail && typeof detail === 'object' && typeof detail.code === 'string') {
      throw new ApiError({ code: detail.code, params: detail.params ?? {} })
    }
    throw new ApiError({ code: 'unknown', params: { reason: String(detail ?? res.status) } })
  }
  return res
}

export const navigationApi = {
  maps: async (signal: AbortSignal): Promise<NavigationMap[]> =>
    (await response('/maps', signal)).json(),
  status: async (signal: AbortSignal): Promise<NavigationStatus> =>
    (await response('/status', signal)).json(),
  grid: async (name: string, signal: AbortSignal) =>
    (await response(`/maps/${encodeURIComponent(name)}/grid.png`, signal)).blob(),
  cloud: async (name: string, signal: AbortSignal) => {
    const buffer = await (await response(`/maps/${encodeURIComponent(name)}/cloud`, signal)).arrayBuffer()
    if (buffer.byteLength % 12 !== 0) throw new Error('Invalid XYZ point cloud payload')
    const data = new Float32Array(buffer)
    if (!data.every(Number.isFinite)) throw new Error('Non-finite XYZ point cloud coordinates')
    return data
  },
  start: (name: string, signal: AbortSignal) =>
    response(`/start?name=${encodeURIComponent(name)}`, signal, 'POST'),
  stop: (signal: AbortSignal) => response('/stop', signal, 'POST'),
}
