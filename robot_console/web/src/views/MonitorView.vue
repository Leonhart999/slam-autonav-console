<!-- 系统监控页：顶部指标卡，下面是进程启停与日志、话题频率、TF、节点列表 -->
<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import { fmtDuration, shortType } from '@/format'
import { pick } from '@/i18n'
import { errorText, resultText } from '@/messages'
import { useStatusStore } from '@/stores/status'
import type { StackInfo, StackState, TfEdge, TopicInfo } from '@/types'
import GlassPanel from '@/components/GlassPanel.vue'
import KpiTile from '@/components/KpiTile.vue'
import RadarMark from '@/components/RadarMark.vue'

// 状态胶囊的颜色类名（样式在 style.css 的 .chip）
type TagType = 'success' | 'warning' | 'danger' | 'info' | 'primary'

const { t } = useI18n()
// Texts for the last-stop column, kept local so the shared locale files are not touched
const { t: tl } = useI18n({
  useScope: 'local',
  messages: {
    'zh-CN': { stop: { normal: '已停止 · {sec} s', forced: '强制结束（{sig}）· {sec} s', gone: '已退出 · {sec} s',
      title: '最近一次从控制台停止：用时 {sec} s（含停止前的服务调用），结束于 {stage}，退出码 {code}。{hint}',
      hintNormal: '收到 SIGINT 后正常退出；退出码 -2 或 1 在这种情况下是正常的。',
      hintForced: 'SIGINT 后 10 s 内没有退出，被 SIGTERM / SIGKILL 强制结束；数据可能没有保存完，请看日志。',
      hintGone: '停止前进程已经自己退出了。' } },
    en: { stop: { normal: 'Stopped · {sec} s', forced: 'Forced ({sig}) · {sec} s', gone: 'Already exited · {sec} s',
      title: 'Last stop from the console: {sec} s (including the pre-stop service call), ended at {stage}, exit code {code}. {hint}',
      hintNormal: 'Exited normally after SIGINT; exit codes such as -2 or 1 are expected here.',
      hintForced: 'Did not exit within 10 s of SIGINT and was forced with SIGTERM / SIGKILL; data may not have been saved, check the log.',
      hintGone: 'The process had already exited before the stop.' } },
  },
})
const store = useStatusStore()
const s = computed(() => store.status)

// ---------------- 进程 ----------------

interface LastStop { duration: number; stage: 'sigint' | 'sigterm' | 'sigkill' | null; exit_code: number | null; at: number }
function lastStop(row: StackInfo): LastStop | null {
  return row.state === 'exited' ? ((row as StackInfo & { last_stop?: LastStop | null }).last_stop ?? null) : null
}
function stopText(row: StackInfo) {
  const ls = lastStop(row)!
  const sec = ls.duration.toFixed(1)
  if (ls.stage === 'sigterm' || ls.stage === 'sigkill') return tl('stop.forced', { sig: ls.stage.toUpperCase(), sec })
  return tl(ls.stage ? 'stop.normal' : 'stop.gone', { sec })
}
function stopTitle(row: StackInfo) {
  const ls = lastStop(row)!
  const hint = ls.stage === 'sigint' ? 'stop.hintNormal' : ls.stage ? 'stop.hintForced' : 'stop.hintGone'
  return tl('stop.title', { sec: ls.duration.toFixed(1), stage: ls.stage?.toUpperCase() ?? '—',
    code: ls.exit_code ?? '—', hint: tl(hint) })
}
const STATE_TAG: Record<StackState, TagType> = {
  idle: 'info',
  running: 'success',
  stopping: 'warning',
  exited: 'info',
}
const busy = ref<Record<string, boolean>>({}) // 正在等接口返回的进程，按钮显示加载中

async function start(st: StackInfo) {
  busy.value[st.name] = true
  try {
    await api.startStack(st.name)
    ElMessage.success(t('monitor.started', { name: pick(st.label) }))
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    busy.value[st.name] = false
  }
}

async function stop(st: StackInfo) {
  busy.value[st.name] = true
  try {
    await api.stopStack(st.name)
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    busy.value[st.name] = false
  }
}

// 日志抽屉：打开后每 2 s 刷新一次
const logOpen = ref(false)
const logStack = ref<StackInfo | null>(null)
const logText = ref('')
const logBox = ref<HTMLElement | null>(null)
let logTimer: number | undefined

async function refreshLog() {
  if (!logStack.value) return
  try {
    const r = await api.stackLog(logStack.value.name)
    const box = logBox.value
    const atBottom = !box || box.scrollTop + box.clientHeight >= box.scrollHeight - 20
    logText.value = r.text || t('monitor.logEmpty')
    if (atBottom) {
      await nextTick()
      logBox.value?.scrollTo({ top: logBox.value.scrollHeight })
    }
  } catch (e) {
    logText.value = t('monitor.logFailed', { reason: errorText(e) })
  }
}
function openLog(st: StackInfo) {
  logStack.value = st
  logText.value = ''
  logOpen.value = true
  refreshLog()
  window.clearInterval(logTimer)
  logTimer = window.setInterval(refreshLog, 2000)
}
function closeLog() {
  window.clearInterval(logTimer)
}
onUnmounted(closeLog)

// ---------------- 话题 ----------------

function topicTag(tp: TopicInfo): [string, TagType] {
  const k = (key: string) => t(`monitor.topicState.${key}`)
  if (tp.publishers === 0) return [k('noPublisher'), 'info']
  if (tp.expected_hz == null) return tp.age != null && tp.age < 5 ? [k('hasData'), 'success'] : [k('waiting'), 'info']
  if (tp.hz === 0) return [k('noData'), 'danger']
  if (tp.hz < 0.8 * tp.expected_hz) return [k('low'), 'warning']
  // 明显偏高通常是同一个话题有两个发布者（例如开了两个仿真）
  if (tp.hz > 1.5 * tp.expected_hz) return [k('high'), 'warning']
  return [k('ok'), 'success']
}

// 频率条：实际频率占期望频率的比例（最多画满）
function rateWidth(tp: TopicInfo) {
  return `${Math.min(1, tp.hz / (tp.expected_hz || 1)) * 100}%`
}

// ---------------- TF ----------------

const tfRows = computed(() => {
  if (!s.value) return []
  return [
    ...s.value.tf.expected.map((e) => ({ ...e, expected: true })),
    ...s.value.tf.other.map((e) => ({ ...e, expected: false })),
  ]
})

// 系统里有没有在跑的东西：控制台自己之外还有节点，或者有进程在运行
const active = computed(
  () => !!s.value && (s.value.nodes.length > 1 || s.value.stacks.some((x) => x.state === 'running')),
)

function tfTag(e: TfEdge): [string, TagType] {
  // 什么都没启动时 TF 缺失是正常的，显示灰色，不报红
  if (!e.present) return [t('monitor.tfState.missing'), active.value ? 'danger' : 'info']
  if (e.static) return [t('monitor.tfState.static'), 'success']
  if ((e.age ?? 0) > 1) return [t('monitor.tfState.stale', { age: e.age }), 'warning']
  return [t('monitor.tfState.rate', { hz: e.hz }), 'success']
}

// ---------------- 顶部指标卡 ----------------

type Tone = 'ok' | 'warn' | 'bad' | 'idle' | 'accent'
// 全部正常为绿；一个都不正常（多半是还没启动）为灰；部分正常为黄
function healthTone(good: number, total: number): Tone {
  if (total === 0 || good === 0) return 'idle'
  return good === total ? 'ok' : 'warn'
}

const kpis = computed(() => {
  const v = s.value
  if (!v) return []
  const running = v.stacks.filter((x) => x.state === 'running').length
  const published = v.topics.filter((x) => x.publishers > 0)
  const topicsOk = published.filter((x) => topicTag(x)[1] === 'success').length
  const tfOk = v.tf.expected.filter((e) => tfTag(e)[1] === 'success').length
  return [
    {
      key: 'proc', label: t('monitor.kpi.processes'), sub: t('monitor.kpi.processesSub'),
      value: running, total: v.stacks.length, tone: (running ? 'accent' : 'idle') as Tone,
    },
    {
      key: 'topic', label: t('monitor.kpi.topics'), sub: t('monitor.kpi.topicsSub'),
      value: topicsOk, total: published.length, tone: healthTone(topicsOk, published.length),
    },
    {
      key: 'tf', label: t('monitor.kpi.tf'), sub: t('monitor.kpi.tfSub'),
      value: tfOk, total: v.tf.expected.length, tone: healthTone(tfOk, v.tf.expected.length),
    },
    {
      key: 'node', label: t('monitor.kpi.nodes'), sub: t('monitor.kpi.nodesSub'),
      value: v.nodes.length, total: null, tone: 'accent' as Tone,
    },
  ]
})
</script>

<template>
  <div class="monitor">
    <div v-if="!s" class="empty glass">
      <RadarMark :size="132" />
      <p>{{ store.connected ? t('monitor.waiting') : t('monitor.offline') }}</p>
    </div>

    <template v-else>
      <div class="kpis">
        <KpiTile
          v-for="k in kpis" :key="k.key" :label="k.label" :sub="k.sub"
          :value="k.value" :total="k.total" :tone="k.tone"
        />
      </div>

      <!-- 进程 -->
      <GlassPanel :title="t('monitor.processes')" flush>
        <el-table :data="s.stacks" size="small">
          <el-table-column :label="t('monitor.col.name')" width="180">
            <template #default="{ row }">
              <el-tooltip :content="row.cmd" placement="top-start">
                <div class="proc">
                  <span class="proc-label">{{ pick(row.label) }}</span>
                  <span class="proc-id mono">{{ row.name }}</span>
                </div>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column :label="t('monitor.col.description')" min-width="220">
            <template #default="{ row }"><span class="desc">{{ pick(row.description) }}</span></template>
          </el-table-column>
          <el-table-column :label="t('monitor.col.state')" width="110">
            <template #default="{ row }">
              <span class="chip" :class="[STATE_TAG[row.state as StackState], { live: row.state === 'running' }]">
                {{ t(`monitor.state.${row.state}`) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column :label="t('monitor.col.uptime')" width="96">
            <template #default="{ row }">
              <span class="mono">{{ fmtDuration(row.uptime) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('monitor.col.result')" min-width="130">
            <template #default="{ row }">
              <template v-if="lastStop(row)">
                <!-- A stop from the console that ended at SIGINT is normal, whatever the exit code (-2, 1, ...) -->
                <span
                  class="mono" :class="{ forced: lastStop(row)!.stage === 'sigterm' || lastStop(row)!.stage === 'sigkill' }"
                  :title="stopTitle(row)"
                >{{ stopText(row) }}</span>
              </template>
              <span v-else-if="row.exit_code != null" class="mono">{{ t('monitor.exitCode', { code: row.exit_code }) }}</span>
              <span v-for="(m, i) in row.messages" :key="i" class="msg">{{ resultText(m) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('monitor.col.actions')" width="210">
            <template #default="{ row }">
              <el-button
                size="small" type="primary" :loading="busy[row.name]"
                :disabled="row.state === 'running' || row.state === 'stopping' || row.page_only" @click="start(row)"
              >{{ t('monitor.start') }}</el-button>
              <el-button
                size="small" :loading="row.state === 'stopping'"
                :disabled="row.state !== 'running'" @click="stop(row)"
              >{{ t('monitor.stop') }}</el-button>
              <el-button size="small" link type="primary" :disabled="row.state === 'idle'" @click="openLog(row)">
                {{ t('monitor.log') }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </GlassPanel>

      <div class="grid2">
        <!-- 话题 -->
        <GlassPanel :title="t('monitor.topics')" flush>
          <template #extra><span class="count mono">{{ s.topics.length }}</span></template>
          <el-table :data="s.topics" size="small">
            <el-table-column :label="t('monitor.col.topic')" min-width="150">
              <template #default="{ row }"><span class="mono topic">{{ row.name }}</span></template>
            </el-table-column>
            <el-table-column :label="t('monitor.col.type')" min-width="120" show-overflow-tooltip>
              <template #default="{ row }"><span class="mono type">{{ shortType(row.type) }}</span></template>
            </el-table-column>
            <el-table-column prop="publishers" :label="t('monitor.col.publishers')" width="64" align="center" />
            <el-table-column :label="t('monitor.col.hzExpected')" width="120" align="right">
              <template #default="{ row }">
                <div class="rate mono">
                  <b>{{ row.hz.toFixed(1) }}</b><span class="dim"> / {{ row.expected_hz ?? '—' }}</span>
                </div>
                <div v-if="row.expected_hz" class="meter" :class="topicTag(row)[1]">
                  <i :style="{ width: rateWidth(row) }" />
                </div>
              </template>
            </el-table-column>
            <el-table-column :label="t('monitor.col.state')" width="124">
              <template #default="{ row }">
                <span class="chip" :class="topicTag(row)[1]">{{ topicTag(row)[0] }}</span>
              </template>
            </el-table-column>
          </el-table>
        </GlassPanel>

        <!-- TF -->
        <GlassPanel :title="t('monitor.tf')" flush>
          <template #extra><span class="count mono">{{ tfRows.length }}</span></template>
          <el-table :data="tfRows" size="small">
            <el-table-column :label="t('monitor.col.edge')" min-width="190">
              <template #default="{ row }">
                <span class="mono edge">{{ row.parent }}<i class="arrow">→</i>{{ row.child }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="t('monitor.col.source')" width="84">
              <template #default="{ row }">
                <span :class="{ dim: !row.expected }">
                  {{ row.expected ? t('monitor.tfSource.config') : t('monitor.tfSource.other') }}
                </span>
              </template>
            </el-table-column>
            <el-table-column :label="t('monitor.col.state')" width="120">
              <template #default="{ row }">
                <span class="chip" :class="tfTag(row)[1]">{{ tfTag(row)[0] }}</span>
              </template>
            </el-table-column>
          </el-table>
        </GlassPanel>
      </div>

      <!-- 节点 -->
      <GlassPanel :title="t('monitor.nodes', { n: s.nodes.length })">
        <div class="nodes">
          <span v-for="n in s.nodes" :key="n" class="node mono">{{ n }}</span>
        </div>
      </GlassPanel>
    </template>

    <el-drawer
      v-model="logOpen" :title="t('monitor.logTitle', { name: logStack ? pick(logStack.label) : '' })"
      size="60%" @closed="closeLog"
    >
      <pre ref="logBox" class="log mono">{{ logText }}</pre>
    </el-drawer>
  </div>
</template>

<style scoped>
.monitor {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 18px;
  padding: 56px 24px;
  border-radius: var(--radius);
  color: var(--ink-2);
  text-align: center;
}
.empty p {
  margin: 0;
  max-width: 560px;
  line-height: 1.6;
}
.kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}
.grid2 {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  gap: 14px;
  align-items: start;
}
@media (max-width: 1360px) {
  .grid2 {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 900px) {
  .kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.count {
  min-width: 22px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11.5px;
  text-align: center;
  color: var(--accent);
  background: rgba(8, 145, 178, 0.08);
}
.proc {
  display: flex;
  flex-direction: column;
  line-height: 1.35;
}
.proc-label {
  font-weight: 600;
}
.proc-id {
  font-size: 11px;
  color: var(--ink-3);
}
.desc {
  color: var(--ink-2);
}
.forced {
  color: var(--warn);
}
.msg {
  margin-left: 6px;
  color: var(--ink-2);
  font-size: 12px;
}
.topic {
  color: var(--ink);
}
.type {
  color: var(--ink-3);
  font-size: 12px;
}
.dim {
  color: var(--ink-3);
}
.rate b {
  font-weight: 600;
}
/* 频率条：颜色跟状态胶囊一致 */
.meter {
  --c: var(--idle);
  height: 3px;
  margin: 4px 0 2px auto;
  width: 84px;
  border-radius: 3px;
  background: var(--hairline);
  overflow: hidden;
}
.meter.success { --c: var(--ok); }
.meter.warning { --c: var(--warn); }
.meter.danger { --c: var(--bad); }
.meter i {
  display: block;
  height: 100%;
  margin-left: auto;
  border-radius: 3px;
  background: var(--c);
  transition: width 0.5s ease;
}
.edge .arrow {
  margin: 0 8px;
  font-style: normal;
  color: var(--accent);
}
.nodes {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.node {
  padding: 4px 10px;
  border-radius: 8px;
  font-size: 12px;
  color: var(--ink-2);
  background: rgba(255, 255, 255, 0.7);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.log {
  height: calc(100vh - 120px);
  margin: 0;
  padding: 14px 16px;
  box-sizing: border-box;
  overflow: auto;
  border-radius: 12px;
  background:
    linear-gradient(rgba(6, 182, 212, 0.05) 1px, transparent 1px) 0 0 / 100% 22px,
    #0b1220;
  color: #cbd5e1;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  box-shadow: inset 0 0 0 1px rgba(148, 163, 184, 0.15);
}
</style>
