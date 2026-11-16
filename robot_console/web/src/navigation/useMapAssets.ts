import { computed, onScopeDispose, shallowRef, watch, type ComputedRef } from 'vue'
import { api } from '@/api'
import { fetchAsset, RequestCancelled, xyz } from '@/http'
import { useQuery } from '@/composables/useQuery'
import { errorText } from '@/messages'
import type { NavMap, NavSolid } from '@/types'

/** Owns bitmap disposal and independent map resources; switching maps invalidates the whole load. */
export function useMapAssets(map: ComputedRef<NavMap | null>) {
  const query = useQuery()
  const image = shallowRef<ImageBitmap | null>(null)
  const cloud = shallowRef<Float32Array | null>(null)
  const solid = shallowRef<NavSolid | null>(null)
  const partialError = shallowRef('')
  function clear() { image.value?.close(); image.value = null; cloud.value = null; solid.value = null; partialError.value = '' }
  watch(() => map.value?.name, () => {
    query.cancel()
    clear()
    const m = map.value
    if (!m) return
    void query.run(async signal => {
      const base = `/api/navigation/maps/${encodeURIComponent(m.name)}`
      const results = await Promise.allSettled([
        fetchAsset(`${base}/grid.png`, signal).then(res => res.blob()).then(blob => createImageBitmap(blob)),
        m.has_cloud ? fetchAsset(`${base}/cloud`, signal).then(res => res.arrayBuffer()).then(xyz) : Promise.resolve(null),
        m.has_solid ? api.navigationSolid(m.name, signal) : Promise.resolve(null),
      ] as const)
      const bitmap = results[0].status === 'fulfilled' ? results[0].value : null
      if (signal.aborted) { bitmap?.close(); throw new RequestCancelled() }
      return { bitmap, cloud: results[1].status === 'fulfilled' ? results[1].value : null,
        solid: results[2].status === 'fulfilled' ? results[2].value : null,
        errors: results.flatMap(r => r.status === 'rejected' ? [errorText(r.reason)] : []) }
    }, result => {
      image.value = result.bitmap
      cloud.value = result.cloud
      solid.value = result.solid
      partialError.value = result.errors.join('\n')
    })
  }, { immediate: true })
  onScopeDispose(clear)
  return { image, cloud, solid, loadingAssets: query.loading,
    assetError: computed(() => query.error.value || partialError.value) }
}
