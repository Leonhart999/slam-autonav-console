<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { tasksApi, waitTask, type ConsoleTask, type TaskRemoval } from '@/tasks'
import type { RelativeResult } from '@/evaluation/api'
import { taskMessages } from '@/taskMessages'
import { useQuery } from '@/composables/useQuery'
import { errorText } from '@/messages'
import { openFolder } from '@/openFolder'
import { ApiError } from '@/http'
import { fmtDuration } from '@/format'

const { t } = useI18n({ messages: taskMessages })
const router = useRouter()
const open = ref(false), tasks = ref<ConsoleTask[]>([]), retrying = ref('')
const query = useQuery()
const resultQuery = useQuery()
const resultOpen = ref(false)
const relative = ref<RelativeResult | null>(null)
const resultTask = ref<ConsoleTask<RelativeResult> | null>(null)
function closeResult() { resultQuery.cancel(); relative.value = null; resultTask.value = null }
const isActive = (task: ConsoleTask) => task.state === 'queued' || task.state === 'running'
const active = computed(() => tasks.value.filter(isActive).length)
const finishedCount = computed(() => tasks.value.length - active.value)
const failedCount = computed(() => tasks.value.filter(task => task.state === 'failed' || task.state === 'interrupted').length)
const removing = ref('') // task id being removed, '*' while clearing
const pad = (n: number) => String(n).padStart(2, '0')
function submitted(task: ConsoleTask) {
  const d = new Date(task.created * 1000)
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
// Running tasks count up with each 1 s refresh; finished ones show their final duration.
function took(task: ConsoleTask) {
  const sec = Math.max(0, (task.finished ?? Date.now() / 1000) - task.created)
  return sec < 60 ? `${sec.toFixed(1)} s` : fmtDuration(sec)
}
let timer: ReturnType<typeof setTimeout> | undefined
let alive = true
let refreshSequence = 0
async function refresh() {
  const mine = ++refreshSequence
  clearTimeout(timer)
  await query.run(signal => tasksApi.list(signal), data => { tasks.value = data.tasks })
  if (alive && mine === refreshSequence) timer = setTimeout(refresh, active.value ? 1000 : 5000)
}
onMounted(() => { void refresh(); window.addEventListener('console-tasks-changed', refresh) })
onBeforeUnmount(() => { alive = false; clearTimeout(timer); window.removeEventListener('console-tasks-changed', refresh) })
async function retry(task: ConsoleTask) {
  retrying.value = task.id
  try { await tasksApi.retry(task.id); ElMessage.success(t('tasks.retrySent')); await refresh() }
  catch (e) { ElMessage.error(errorText(e)) }
  finally { retrying.value = '' }
}
function absorbRemoval(r: TaskRemoval) {
  tasks.value = r.tasks
  if (r.failed) ElMessage.warning(t('tasks.clearPartial', { n: r.removed, failed: r.failed }))
}
async function remove(task: ConsoleTask) {
  removing.value = task.id
  try { absorbRemoval(await tasksApi.remove(task.id)) }
  catch (e) { ElMessage.error(errorText(e)); void refresh() }
  finally { removing.value = '' }
}
async function clearAll() {
  if (!finishedCount.value) return
  try {
    const ask = [t('tasks.clearAsk', { n: finishedCount.value }),
      failedCount.value ? t('tasks.clearAskFailed', { n: failedCount.value }) : '',
      active.value ? t('tasks.clearAskActive', { n: active.value }) : ''].filter(Boolean).join(' ')
    await ElMessageBox.confirm(ask,
      t('tasks.clear'), { type: 'warning', confirmButtonText: t('tasks.clearConfirm'), cancelButtonText: t('tasks.cancel') })
  } catch { return }
  removing.value = '*'
  try {
    const r = await tasksApi.clear()
    absorbRemoval(r)
    if (!r.failed) ElMessage.success(t('tasks.cleared', { n: r.removed }))
  } catch (e) { ElMessage.error(errorText(e)); void refresh() }
  finally { removing.value = '' }
}
async function show(task: ConsoleTask) {
  if (task.kind === 'eval_relative') {
    resultOpen.value = true
    relative.value = null
    await resultQuery.run(async signal => waitTask(await tasksApi.get<RelativeResult>(task.id, signal), signal,
      current => { if (!signal.aborted) resultTask.value = current }), result => { relative.value = result })
    return
  }
  if (task.kind === 'eval_export') {
    try {
      const detail = await tasksApi.get<{ dir: string }>(task.id)
      if (detail.result?.dir) await openFolder(detail.result.dir)
    } catch (e) { ElMessage.error(errorText(e)) }
    return
  }
  open.value = false
  await router.push(task.kind === 'regrid' || task.kind === 'loop'
    ? { path: '/maps', query: { map: task.saved_map ?? task.target, task: task.saved_map ? undefined : task.id,
        dialog: task.kind === 'loop' && !task.saved_map ? 'loop' : undefined } }
    : { path: '/evaluation', query: { run: task.target, task: task.id } })
}
</script>

<template>
  <el-button size="small" :type="active ? 'primary' : 'default'" @click="open = true; refresh()">
    {{ t('tasks.title') }}<span v-if="active"> · {{ t('tasks.active', { n: active }) }}</span>
  </el-button>
  <el-drawer v-model="open" :title="t('tasks.title')" size="min(760px, 100vw)">
    <p>{{ t('tasks.background') }}</p>
    <div class="toolbar">
      <el-button :loading="query.loading.value" @click="refresh">{{ t('tasks.refresh') }}</el-button>
      <el-button type="danger" plain :disabled="!finishedCount || !!removing" :loading="removing === '*'" @click="clearAll">
        {{ t('tasks.clear') }}<span v-if="finishedCount" class="mono-n">&nbsp;· {{ finishedCount }}</span>
      </el-button>
      <span class="hint">{{ t('tasks.clearHint') }}</span>
    </div>
    <el-alert v-if="query.error.value" :title="query.error.value" type="error" :closable="false" />
    <p v-if="!tasks.length">{{ t('tasks.empty') }}</p>
    <article v-for="task in tasks" :key="task.id" class="task">
      <div class="task-heading">
        <strong>{{ t(`tasks.kind.${task.kind}`) }} · {{ task.target }}</strong>
        <el-tag :type="task.state === 'failed' || task.state === 'interrupted' ? 'danger' : task.state === 'succeeded' ? 'success' : 'info'">
          {{ t(`tasks.states.${task.state}`) }}
        </el-tag>
      </div>
      <p class="mono">{{ t('tasks.submitted') }} {{ submitted(task) }} · {{ t(task.finished ? 'tasks.took' : 'tasks.elapsed') }} {{ took(task) }} · {{ t('tasks.id') }}: {{ task.id }}</p>
      <p role="status">{{ t('tasks.phase') }}: {{ t(`tasks.phases.${task.phase}`) }}</p>
      <el-alert v-if="task.error" :title="errorText(new ApiError(task.error))" type="error" :closable="false" />
      <el-button v-if="task.state !== 'failed' && task.state !== 'interrupted' && (task.kind !== 'eval_export' || task.state === 'succeeded')" @click="show(task)">{{ t('tasks.open') }}</el-button>
      <el-button v-if="task.state === 'failed' || task.state === 'interrupted'" :loading="retrying === task.id" @click="retry(task)">{{ t('tasks.retry') }}</el-button>
      <el-button v-if="!isActive(task)" text :disabled="!!removing" :loading="removing === task.id" @click="remove(task)">{{ t('tasks.remove') }}</el-button>
    </article>
  </el-drawer>
  <el-dialog v-model="resultOpen" :title="t('tasks.kind.eval_relative')" width="min(800px, 96vw)" @closed="closeResult">
    <p v-if="resultTask" role="status">{{ t('tasks.id') }}: {{ resultTask.id }} · {{ t(`tasks.phases.${resultTask.phase}`) }}</p>
    <el-alert v-if="resultQuery.error.value" :title="resultQuery.error.value" type="error" :closable="false" />
    <template v-if="relative">
      <p>{{ t('tasks.baseline') }}: {{ relative.baseline }}</p>
      <el-table :data="relative.rows" size="small">
        <el-table-column prop="id" :label="t('tasks.target')" min-width="190" />
        <el-table-column label="RMSE (m)" width="110"><template #default="{ row }">{{ row.eval?.ape.rmse.toFixed(3) ?? '—' }}</template></el-table-column>
        <el-table-column :label="t('tasks.mean')" width="110"><template #default="{ row }">{{ row.eval?.ape.mean.toFixed(3) ?? '—' }}</template></el-table-column>
        <el-table-column :label="t('tasks.max')" width="110"><template #default="{ row }">{{ row.eval?.ape.max.toFixed(3) ?? '—' }}</template></el-table-column>
        <el-table-column prop="error" :label="t('tasks.error')" min-width="160" />
      </el-table>
    </template>
  </el-dialog>
</template>

<style scoped>
.task { padding: 18px 0; border-bottom: 1px solid var(--hairline); overflow-wrap: anywhere; }
.task-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.task p { margin: 8px 0; }
.toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin-bottom: 4px; }
.toolbar .el-button + .el-button { margin-left: 0; }
.hint { font-size: 12px; color: var(--ink-3); }
.mono-n { font-variant-numeric: tabular-nums; }
.mono { font-size: 12px; color: var(--ink-3); }
</style>
