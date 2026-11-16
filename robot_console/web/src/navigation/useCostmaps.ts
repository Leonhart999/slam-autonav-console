import { onScopeDispose, shallowReactive, watch, type ComputedRef, type Ref } from 'vue'
import { fetchAsset, latestRequest } from '@/http'
import type { CostmapLayer, NavStatus } from '@/types'

/** Costmap bitmaps belong to a navigation session and are released when it changes or unmounts. */
export function useCostmaps(nav: Ref<NavStatus | null>, shown: { global: boolean; local: boolean },
  running: ComputedRef<boolean>, map: ComputedRef<string>) {
  const layers = shallowReactive<{ global: CostmapLayer | null; local: CostmapLayer | null }>({ global: null, local: null })
  const scopes = { global: latestRequest(), local: latestRequest() }
  const loading = { global: false, local: false }
  const versions = { global: -1, local: -1 }
  let generation = 0
  function clear(key: 'global' | 'local') {
    scopes[key].cancel(); layers[key]?.image.close(); layers[key] = null; versions[key] = -1
  }
  watch([map, running], () => { ++generation; clear('global'); clear('local') }, { flush: 'sync' })
  async function load(key: 'global' | 'local', version: number) {
    if (loading[key]) return
    loading[key] = true
    const mine = generation
    const disposable: { bitmap: ImageBitmap | null } = { bitmap: null }
    try {
      const layer = await scopes[key].run(async signal => {
        const res = await fetchAsset(`/api/navigation/costmap/${key}.png`, signal)
        const numbers = (h: string) => (res.headers.get(h) ?? '').split(',').map(Number)
        const [x, y, yaw] = numbers('X-Origin'), [width, height] = numbers('X-Size')
        const resolution = numbers('X-Resolution')[0]
        if (![x, y, yaw, width, height, resolution].every(Number.isFinite) || width <= 0 || height <= 0 || resolution <= 0) {
          throw new Error('Invalid costmap metadata')
        }
        disposable.bitmap = await createImageBitmap(await res.blob())
        return { image: disposable.bitmap, origin: [x, y, yaw] as [number, number, number], width, height, resolution }
      })
      if (mine !== generation || !shown[key] || !running.value) return
      layers[key]?.image.close(); layers[key] = layer; disposable.bitmap = null; versions[key] = version
    } catch { /* A later status poll retries transient failures. */ }
    finally { disposable.bitmap?.close(); loading[key] = false }
  }
  watch(() => [nav.value?.costmaps, shown.global, shown.local, running.value, map.value] as const, () => {
    for (const key of ['global', 'local'] as const) {
      const version = nav.value?.costmaps?.[key] ?? null
      if (!running.value || !shown[key] || version === null) clear(key)
      else if (version !== versions[key]) void load(key, version)
    }
  })
  onScopeDispose(() => { ++generation; clear('global'); clear('local') })
  return layers
}
