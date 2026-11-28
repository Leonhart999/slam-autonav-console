// Browser notification when a background task (task center) finishes while the console tab is not in front.
// Settings → General turns it on (and asks for the permission). While on, the task list is polled every 5 s.
import { watch } from 'vue'
import { i18n } from '@/i18n'
import { tasksApi, type ConsoleTask } from '@/tasks'
import { settingsMessages } from './messages'
import { prefs } from './prefs'

const POLL_MS = 5000
const ACTIVE = new Set(['queued', 'running'])
let timer: number | undefined
let known = new Map<string, ConsoleTask['state']>()
let seeded = false
let lastPoll = 0                       // s, epoch: tasks that finished after it are new even if never seen running

function text(key: string, params: Record<string, string>) {
  const lang = i18n.global.locale.value === 'en' ? 'en' : 'zh-CN'
  let s = key.split('.').reduce<unknown>((o, k) => (o as Record<string, unknown> | undefined)?.[k], settingsMessages[lang])
  if (typeof s !== 'string') return ''
  for (const [k, v] of Object.entries(params)) s = (s as string).replace(`{${k}}`, v)
  return s as string
}

function taskName(task: ConsoleTask) {
  const key = `tasks.kind.${task.kind}`
  const kind = i18n.global.te(key) ? i18n.global.t(key) : task.kind
  return task.target ? `${kind} · ${task.target}` : kind
}

async function poll() {
  const now = Date.now() / 1000          // before the request, so nothing finishing meanwhile is skipped
  let tasks: ConsoleTask[]
  try {
    tasks = (await tasksApi.list()).tasks
  } catch {
    return
  }
  const next = new Map(tasks.map((x) => [x.id, x.state] as const))
  if (seeded && document.visibilityState !== 'visible' && Notification.permission === 'granted') {
    for (const task of tasks) {
      const before = known.get(task.id)
      if (ACTIVE.has(task.state)) continue
      const wasActive = before !== undefined && ACTIVE.has(before)
      // started and finished between two polls: first seen already finished
      const newlyFinished = before === undefined && (task.finished ?? task.updated) > lastPoll
      if (!wasActive && !newlyFinished) continue
      const key = task.state === 'succeeded' ? 'settings.general.notify.succeeded'
        : task.state === 'failed' ? 'settings.general.notify.failed' : 'settings.general.notify.interrupted'
      try {
        new Notification(text(key, { task: taskName(task) }), { tag: `robot_console-task-${task.id}` })
      } catch { /* some browsers only allow notifications from a service worker */ }
    }
  }
  known = next
  seeded = true
  lastPoll = now
}

function stop() {
  if (timer !== undefined) window.clearInterval(timer)
  timer = undefined
  known = new Map()
  seeded = false
  lastPoll = 0
}

let started = false
export function startTaskNotifier() {
  if (started || typeof Notification === 'undefined') return
  started = true
  watch(() => prefs.notifyTasks, (on) => {
    stop()
    if (!on) return
    void poll()
    timer = window.setInterval(() => { void poll() }, POLL_MS)
  }, { immediate: true })
}
