// Jobs and Python environment of a plugin page, refreshed every 1.5 s while something runs (jobs or install)
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ACTIVE, type Job, type Kit, type PythonStatus } from './kit'

export function useJobs(kit: Kit) {
  const jobs = ref<Job[]>([])
  const python = ref<PythonStatus | null>(null)
  const error = ref('')
  let timer: ReturnType<typeof setTimeout> | null = null
  let alive = true
  let seq = 0
  const listeners: ((job: Job) => void)[] = []
  const busy = computed(() => jobs.value.some((j) => ACTIVE.includes(j.state)) || python.value?.state === 'installing')
  const ready = computed(() => !python.value || python.value.state === 'ready')

  async function refresh() {
    if (timer) clearTimeout(timer)
    timer = null
    const id = ++seq
    const before = new Map(jobs.value.map((j) => [j.id, j.state]))
    try {
      const out = await kit.jobs()
      if (!alive || id !== seq) return
      python.value = out.python
      jobs.value = out.jobs
      error.value = ''
      for (const j of out.jobs) {
        const old = before.get(j.id)
        if (old && ACTIVE.includes(old) && !ACTIVE.includes(j.state)) listeners.forEach((fn) => fn(j))
      }
    } catch (e) {
      if (!alive || id !== seq) return
      error.value = kit.errorText(e)
    }
    if (alive && id === seq && busy.value) timer = setTimeout(refresh, 1500)
  }

  /** Called once for every job that finishes while the page watches it */
  const onFinished = (fn: (job: Job) => void) => listeners.push(fn)

  onMounted(refresh)
  onUnmounted(() => {
    alive = false
    if (timer) clearTimeout(timer)
  })
  return { jobs, python, error, busy, ready, refresh, onFinished }
}
