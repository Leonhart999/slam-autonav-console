import { ApiError, isRecord, request, RequestCancelled, validResponse } from './http'
import type { CodedMsg } from './types'

export interface ConsoleTask<T = unknown> {
  id: string
  kind: string
  target: string
  state: 'queued' | 'running' | 'succeeded' | 'failed' | 'interrupted'
  phase: string
  created: number
  updated: number
  finished: number | null
  result: T | null
  error: CodedMsg | null
  retry_of: string | null
  input: Record<string, unknown>
  saved_map?: string
}
export const tasksApi = {
  list: (signal?: AbortSignal, kind?: string, target?: string) => {
    const q = new URLSearchParams()
    if (kind) q.set('kind', kind)
    if (target !== undefined) q.set('target', target)
    return request<{ tasks: ConsoleTask[] }>('GET', `/api/tasks?${q}`, undefined, { signal })
  },
  get: <T = unknown>(id: string, signal?: AbortSignal) =>
    request<ConsoleTask<T>>('GET', `/api/tasks/${encodeURIComponent(id)}`, undefined, { signal }),
  retry: (id: string) => request<ConsoleTask>('POST', `/api/tasks/${encodeURIComponent(id)}/retry`),
}

export function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new RequestCancelled()); return }
    const cancel = () => { clearTimeout(timer); signal?.removeEventListener('abort', cancel); reject(new RequestCancelled()) }
    const timer = setTimeout(() => { signal?.removeEventListener('abort', cancel); resolve() }, ms)
    signal?.addEventListener('abort', cancel, { once: true })
  })
}

export async function waitTask<T>(initial: ConsoleTask<T>, signal?: AbortSignal,
  onTask?: (task: ConsoleTask<T>) => void): Promise<T> {
  let task = initial
  while (true) {
    if (signal?.aborted) throw new RequestCancelled()
    onTask?.(task)
    if (task.state === 'succeeded') {
      // Submission/list responses intentionally omit the potentially large result.
      if (task.result === null) task = await tasksApi.get<T>(task.id, signal)
      if (task.result === null) throw new ApiError({ code: 'response_invalid', params: { path: 'task result' } })
      return task.result
    }
    if (task.state === 'failed' || task.state === 'interrupted') {
      throw new ApiError(task.error ?? { code: 'task_failed', params: { reason: task.phase } })
    }
    await delay(700, signal)
    task = await tasksApi.get<T>(task.id, signal)
  }
}

export async function taskResult<T>(path: string, body?: unknown, signal?: AbortSignal,
  onTask?: (task: ConsoleTask<T>) => void): Promise<T> {
  const task = await request<ConsoleTask<T>>('POST', `${path}${path.includes('?') ? '&' : '?'}background=true`, body, { signal })
  if (typeof window !== 'undefined') window.dispatchEvent(new Event('console-tasks-changed'))
  const result = await waitTask(task, signal, onTask)
  if (!validResponse(path, result) || !(isRecord(result) || Array.isArray(result))) {
    throw new ApiError({ code: 'response_invalid', params: { path } })
  }
  return result
}
