<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { tasksApi, waitTask, type ConsoleTask } from '@/tasks'
import type { RelativeResult } from '@/evaluation/api'
import { taskMessages } from '@/taskMessages'
import { useQuery } from '@/composables/useQuery'
import { errorText } from '@/messages'
import { openFolder } from '@/openFolder'
import { ApiError } from '@/http'

const { t } = useI18n({ messages: taskMessages })
const router = useRouter()
const open = ref(false), tasks = ref<ConsoleTask[]>([]), retrying = ref('')
const query = useQuery()
const resultQuery = useQuery()
const resultOpen = ref(false)
const relative = ref<RelativeResult | null>(null)
const resultTask = ref<ConsoleTask<RelativeResult> | null>(null)
function closeResult() { resultQuery.cancel(); relative.value = null; resultTask.value = null }
const active = computed(() => tasks.value.filter(task => ['queued', 'running'].includes(task.state)).length)
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
  await router.push(task.kind === 'regrid'
    ? { path: '/maps', query: { map: task.saved_map ?? task.target, task: task.saved_map ? undefined : task.id } }
    : { path: '/evaluation', query: { run: task.target, task: task.id } })
}
</script>

<template>
  <el-button size="small" :type="active ? 'primary' : 'default'" @click="open = true; refresh()">
    {{ t('tasks.title') }}<span v-if="active"> · {{ t('tasks.active', { n: active }) }}</span>
  </el-button>
  <el-drawer v-model="open" :title="t('tasks.title')" size="min(760px, 100vw)">
    <p>{{ t('tasks.background') }}</p>
    <el-button :loading="query.loading.value" @click="refresh">{{ t('tasks.refresh') }}</el-button>
    <el-alert v-if="query.error.value" :title="query.error.value" type="error" :closable="false" />
    <p v-if="!tasks.length">{{ t('tasks.empty') }}</p>
    <article v-for="task in tasks" :key="task.id" class="task">
      <div class="task-heading">
        <strong>{{ t(`tasks.kind.${task.kind}`) }} · {{ task.target }}</strong>
        <el-tag :type="task.state === 'failed' || task.state === 'interrupted' ? 'danger' : task.state === 'succeeded' ? 'success' : 'info'">
          {{ t(`tasks.states.${task.state}`) }}
        </el-tag>
      </div>
      <p class="mono">{{ t('tasks.id') }}: {{ task.id }}</p>
      <p role="status">{{ t('tasks.phase') }}: {{ t(`tasks.phases.${task.phase}`) }}</p>
      <el-alert v-if="task.error" :title="errorText(new ApiError(task.error))" type="error" :closable="false" />
      <el-button v-if="task.state !== 'failed' && task.state !== 'interrupted' && (task.kind !== 'eval_export' || task.state === 'succeeded')" @click="show(task)">{{ t('tasks.open') }}</el-button>
      <el-button v-if="task.state === 'failed' || task.state === 'interrupted'" :loading="retrying === task.id" @click="retry(task)">{{ t('tasks.retry') }}</el-button>
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
.mono { font-size: 12px; color: var(--ink-3); }
</style>
