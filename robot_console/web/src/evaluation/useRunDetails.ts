import { onScopeDispose, ref, type Ref } from 'vue'
import { ApiError, isCancelled, isReadOnlyPreview, latestRequest, validResponse } from '@/http'
import { tasksApi, waitTask, type ConsoleTask } from '@/tasks'
import { errorText } from '@/messages'
import { evalApi, type RunDetail, type RunEntry } from './api'

/** Per-run requests can run independently; leaving the page cancels observation of every task. */
export function useRunDetails(runs: Ref<RunEntry[]>) {
  const details = ref<Record<string, RunDetail>>({}), errors = ref<Record<string, string>>({})
  const busy = ref<Set<string>>(new Set())
  const scopes = new Map<string, ReturnType<typeof latestRequest>>()
  const recoveredTask = ref<ConsoleTask<RunDetail> | null>(null)
  let disposed = false
  onScopeDispose(() => { disposed = true; for (const scope of scopes.values()) scope.cancel() })
  function absorb(d: RunDetail) {
    details.value = { ...details.value, [d.id]: d }
    const index = runs.value.findIndex(r => r.id === d.id)
    if (index >= 0) {
      const { info: _info, eval: _eval, ref_eval: _ref, amcl: _amcl, ...entry } = d
      runs.value.splice(index, 1, entry)
    }
  }
  async function ensure(id: string, mode: 'auto' | 'evaluate' | 'force' = 'auto') {
    if (!id || busy.value.has(id) || disposed) return
    const entry = runs.value.find(r => r.id === id), loaded = details.value[id]
    const withRef = entry?.playback?.gt_kind === 'ref' && !entry.playback.ref_error
    if (mode === 'auto' && loaded && (loaded.eval || (withRef ? loaded.ref_eval : !entry?.evaluable)
      || (loaded.type === 'navigation' && !entry?.recording))) return
    const scope = scopes.get(id) ?? latestRequest()
    scopes.set(id, scope)
    busy.value = new Set([...busy.value, id])
    try {
      const auto = !isReadOnlyPreview() && (entry?.evaluable || withRef) && !entry?.recording && !entry?.eval_error
      const d = await scope.run(signal => mode === 'force' ? evalApi.evaluate(id, true, signal)
        : mode === 'evaluate' || auto ? evalApi.evaluate(id, false, signal) : evalApi.detail(id, signal))
      if (disposed || scopes.get(id) !== scope) return
      absorb(d)
      const { [id]: _old, ...rest } = errors.value
      errors.value = rest
    } catch (e) {
      if (disposed || scopes.get(id) !== scope || isCancelled(e)) return
      errors.value = { ...errors.value, [id]: errorText(e) }
      try {
        const d = await scope.run(signal => evalApi.detail(id, signal))
        if (!disposed && scopes.get(id) === scope) absorb(d)
      } catch { /* The run may have been deleted. */ }
    } finally {
      if (!disposed && scopes.get(id) === scope) { const next = new Set(busy.value); next.delete(id); busy.value = next }
    }
  }
  async function recover(id: string, taskId: string) {
    scopes.get(id)?.cancel()
    const scope = latestRequest()
    scopes.set(id, scope)
    busy.value = new Set([...busy.value, id])
    try {
      const detail = await scope.run(async signal => {
        const task = await tasksApi.get<RunDetail>(taskId, signal)
        if (task.kind !== 'evaluate' || task.target !== id) throw new ApiError({ code: 'response_invalid', params: { path: 'evaluation task' } })
        return waitTask(task, signal, current => { if (!signal.aborted) recoveredTask.value = current })
      })
      if (!validResponse(`/api/evaluation/runs/${encodeURIComponent(id)}`, detail) || detail.id !== id) {
        throw new ApiError({ code: 'response_invalid', params: { path: 'evaluation result' } })
      }
      absorb(detail)
      const { [id]: _old, ...rest } = errors.value
      errors.value = rest
    } catch (e) {
      if (!disposed && scopes.get(id) === scope && !isCancelled(e)) errors.value = { ...errors.value, [id]: errorText(e) }
    } finally {
      if (!disposed && scopes.get(id) === scope) { const next = new Set(busy.value); next.delete(id); busy.value = next }
    }
  }
  return { details, errors, busy, absorb, ensure, recover, recoveredTask }
}
