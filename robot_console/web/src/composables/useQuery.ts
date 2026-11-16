import { onScopeDispose, ref } from 'vue'
import { isCancelled, latestRequest } from '@/http'
import { errorText } from '@/messages'

/** Loaders share cancellation, stale-result rejection, errors and loading state. */
export function useQuery() {
  const scope = latestRequest()
  const loading = ref(false), error = ref('')
  let sequence = 0
  function cancel() { ++sequence; scope.cancel(); loading.value = false }
  onScopeDispose(cancel)
  async function run<T>(work: (signal: AbortSignal) => Promise<T>, apply: (value: T) => void) {
    const mine = ++sequence
    loading.value = true
    error.value = ''
    try { const value = await scope.run(work); if (mine === sequence) apply(value) }
    catch (e) { if (mine === sequence && !isCancelled(e)) error.value = errorText(e) }
    finally { if (mine === sequence) loading.value = false }
  }
  return { loading, error, run, cancel }
}
