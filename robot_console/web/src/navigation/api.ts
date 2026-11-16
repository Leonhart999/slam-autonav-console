import { fetchAsset, request, xyz } from '@/http'
import type { NavigationMap, NavigationStatus } from './types'

export const navigationApi = {
  maps: (signal: AbortSignal) => request<NavigationMap[]>('GET', '/api/navigation/maps', undefined, { signal }),
  status: (signal: AbortSignal) => request<NavigationStatus>('GET', '/api/navigation/status', undefined, { signal }),
  grid: async (name: string, signal: AbortSignal) =>
    (await fetchAsset(`/api/navigation/maps/${encodeURIComponent(name)}/grid.png`, signal)).blob(),
  cloud: async (name: string, signal: AbortSignal) =>
    xyz(await (await fetchAsset(`/api/navigation/maps/${encodeURIComponent(name)}/cloud`, signal)).arrayBuffer()),
  start: (name: string, signal: AbortSignal) =>
    request('POST', `/api/navigation/start?name=${encodeURIComponent(name)}`, undefined, { signal }),
  stop: (signal: AbortSignal) => request('POST', '/api/navigation/stop', undefined, { signal }),
}
