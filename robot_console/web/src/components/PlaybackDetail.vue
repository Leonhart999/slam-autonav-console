<!-- Evaluation page, details of a playback run (SLAM fed from a rosbag): where the data came from, the numbers that
     need no ground truth (start-to-end distance / loop closure error, height drift, SLAM lag), the deviation from
     the bag's reference odometry, and links back to the Playback page. Ground-truth ATE / RPE (bags recorded in
     the simulation) are shown by the page itself below this block. -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { EChartsCoreOption } from 'echarts/core'
import { fmtDuration } from '@/format'
import { pick } from '@/i18n'
import { errorText } from '@/messages'
import { openFolder } from '@/openFolder'
import type { Bilingual } from '@/types'
import { evalApi, type RunDetail, type RunEntry, type RunTrajectory } from '@/evaluation/api'
import { evalMessages } from '@/evaluation/messages'
import { REFERENCE, SERIES } from '@/evaluation/colors'
import { partText, timeSeries, topView, zipXY } from '@/evaluation/charts'
import KpiTile from '@/components/KpiTile.vue'
import EvalChart from '@/components/EvalChart.vue'
import InfoTip from '@/components/InfoTip.vue'

const props = defineProps<{ entry: RunEntry; detail: RunDetail | null }>()
const emit = defineEmits<{ (e: 'filter-bag', bag: string): void; (e: 'changed', d: RunDetail): void }>()

const { t } = useI18n({ messages: evalMessages })
const router = useRouter()

const pb = computed(() => props.entry.playback!)
const stats = computed(() => pb.value.stats)
const info = computed(() => (props.detail?.info ?? {}) as Record<string, unknown>)
const label = (v: Bilingual | string | null | undefined) => (v == null ? '' : typeof v === 'string' ? v : pick(v))
const refName = computed(() => label(pb.value.ref_label) || pb.value.ref_source || '')
const m3 = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(3))
const m2 = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(2))
const num = (v: number | null | undefined, d = 3) => (v == null ? 0 : Number(v.toFixed(d)))

// ---------------- trajectory (no evo needed) ----------------
const traj = ref<RunTrajectory | null>(null)
const trajError = ref('')
async function loadTraj(id: string) {
  traj.value = null
  trajError.value = ''
  try {
    const r = await evalApi.trajectory(id)
    if (props.entry.id === id) traj.value = r
  } catch (e) {
    if (props.entry.id === id) trajError.value = errorText(e)
  }
}
// reload when another run is shown or the estimate grew (still recording)
watch(() => [props.entry.id, props.entry.length] as const, ([id]) => loadTraj(id), { immediate: true })

const refEval = computed(() => props.detail?.ref_eval ?? null)
const topOption = computed<EChartsCoreOption | null>(() => {
  const labels = { start: t('evalPage.pb.chart.start'), end: t('evalPage.pb.chart.end') }
  const ev = refEval.value
  if (ev) {
    return topView([
      { name: t('evalPage.pb.chart.ref'), points: ev.series.traj_gt, color: REFERENCE, dashed: true },
      { name: t('evalPage.pb.chart.est'), points: ev.series.traj_est, color: SERIES[0], ends: true },
    ], labels)
  }
  const tr = traj.value
  return tr ? topView([{ name: t('evalPage.pb.chart.est'), points: zipXY(tr.x, tr.y), color: SERIES[0], ends: true }], labels) : null
})
const zOption = computed(() => {
  const tr = traj.value
  return tr ? timeSeries([{ name: 'z', points: zipXY(tr.t, tr.z), color: SERIES[0] }],
    { x: t('evalPage.chart.time'), y: t('evalPage.pb.chart.zAxis'), unit: 'm' }, { zero: true }) : null
})
const lagOption = computed(() => {
  const l = traj.value?.lag
  return l ? timeSeries([{ name: 'lag', points: zipXY(l.t, l.lag), color: SERIES[1] }],
    { x: t('evalPage.chart.time'), y: t('evalPage.pb.chart.lagAxis'), unit: 's' }, { min: 0 }) : null
})

// ---------------- loop mark ----------------
const loopBusy = ref(false)
async function setLoop(v: string | number | boolean) {
  loopBusy.value = true
  try {
    emit('changed', await evalApi.setLoop(props.entry.id, !!v))
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    loopBusy.value = false
  }
}

// ---------------- links ----------------
function replay() {
  const r = pb.value.range
  const slam = typeof info.value.slam === 'string' ? info.value.slam : undefined
  router.push({ path: '/playback', query: {
    bag: pb.value.bag, slam, rate: pb.value.rate != null ? String(pb.value.rate) : undefined,
    begin: r ? String(r[0]) : undefined, end: r ? String(r[1]) : undefined, from: props.entry.id,
  } })
}
const bagFolder = computed(() => pb.value.bag)
const estSource = computed(() => (typeof info.value.est_source === 'string' ? info.value.est_source : ''))
const slamCmd = computed(() => (typeof info.value.slam_cmd === 'string' ? info.value.slam_cmd : ''))
</script>

<template>
  <div class="pb-detail">
    <p v-if="pb.gt_suspect" class="note warn">{{ t('evalPage.pb.suspectNote') }}</p>
    <p v-else-if="pb.gt_kind === 'none'" class="note">{{ t('evalPage.pb.noGtNote') }}</p>
    <p v-if="pb.gt_kind !== 'none' && pb.ref_source" class="note">{{ t('evalPage.pb.refNote', { ref: refName }) }}</p>
    <p v-if="pb.ref_error" class="note bad">{{ t('evalPage.pb.refFailed', { reason: pb.ref_error }) }}</p>

    <!-- Where the data came from -->
    <dl class="facts">
      <dt>{{ t('evalPage.pb.bag') }}</dt>
      <dd><span class="mono" :title="pb.bag">{{ pb.bag_name }}</span></dd>
      <dt>{{ t('evalPage.pb.part') }}</dt>
      <dd class="num">{{ partText(pb.range, t('evalPage.pb.whole')) }}</dd>
      <dt>{{ t('evalPage.pb.rate') }}</dt>
      <dd class="num">{{ pb.rate != null ? `${pb.rate}x` : '—' }}</dd>
      <dt>{{ t('evalPage.pb.sensor') }}</dt>
      <dd>{{ label(pb.sensor_label) || pb.sensor || '—' }}</dd>
      <dt>{{ t('evalPage.pb.slam') }}</dt>
      <dd>{{ entry.algorithm ?? '—' }}<span v-if="estSource" class="dim"> · {{ t('evalPage.pb.estSource') }} {{ estSource }}</span></dd>
      <dt>{{ t('evalPage.pb.reference') }}</dt>
      <dd>{{ refName || t('evalPage.pb.referenceNone') }}</dd>
      <template v-if="slamCmd">
        <dt>{{ t('evalPage.pb.cmd') }}</dt>
        <dd class="mono cmd">{{ slamCmd }}</dd>
      </template>
    </dl>
    <div class="actions">
      <el-button size="small" type="primary" plain :title="t('evalPage.pb.replayHint')" @click="replay">{{ t('evalPage.pb.replay') }}</el-button>
      <el-button size="small" @click="emit('filter-bag', pb.bag)">{{ t('evalPage.pb.sameBag') }}</el-button>
      <el-button size="small" @click="openFolder(bagFolder)">{{ t('evalPage.pb.openBag') }}</el-button>
      <span class="loop">
        <el-switch :model-value="pb.loop" size="small" :loading="loopBusy" @update:model-value="setLoop" />
        {{ t('evalPage.pb.loop') }}<InfoTip :content="t('evalPage.pb.loopTip')" />
      </span>
    </div>

    <!-- Numbers that need no reference -->
    <div v-if="stats" class="kpis">
      <KpiTile :label="pb.loop ? t('evalPage.pb.kpi.closure') : t('evalPage.pb.kpi.startEnd')" :value="num(stats.start_end)"
        :tone="pb.loop ? 'accent' : 'idle'" :sub="t('evalPage.pb.kpi.startEndSub', { xy: m3(stats.start_end_xy) })" />
      <KpiTile :label="t('evalPage.pb.kpi.zEnd')" :value="num(stats.z_end)" tone="accent"
        :sub="t('evalPage.pb.kpi.zEndSub', { max: m3(stats.z_max_abs), at: stats.z_max_at })" />
      <KpiTile v-if="pb.ref_ape_rmse != null" :label="t('evalPage.pb.kpi.ref')" :value="num(pb.ref_ape_rmse)"
        :sub="t('evalPage.pb.kpi.refSub', { label: refName })" />
      <KpiTile v-else-if="pb.lag" :label="t('evalPage.pb.kpi.lag')" :value="num(pb.lag.max, 1)"
        :sub="t('evalPage.pb.kpi.lagSub', { mean: m2(pb.lag.mean) })" />
      <KpiTile v-else :label="t('evalPage.pb.kpi.length')" :value="num(stats.length, 1)" tone="idle"
        :sub="t('evalPage.pb.kpi.lengthSub', { t: fmtDuration(stats.duration) })" />
    </div>
    <p v-else class="hint">{{ trajError || t('evalPage.pb.noTraj') }}</p>

    <div v-if="topOption" class="charts">
      <figure>
        <figcaption>{{ refEval ? t('evalPage.pb.chart.trajRef') : t('evalPage.pb.chart.traj') }}</figcaption>
        <EvalChart :option="topOption" square />
      </figure>
      <div class="stack">
        <figure v-if="zOption">
          <figcaption>{{ t('evalPage.pb.chart.z') }}</figcaption>
          <EvalChart :option="zOption" height="210px" />
        </figure>
        <figure v-if="lagOption">
          <figcaption>{{ t('evalPage.pb.chart.lag') }}</figcaption>
          <EvalChart :option="lagOption" height="210px" />
        </figure>
        <p v-else-if="!pb.lag" class="hint">{{ t('evalPage.pb.kpi.lag') }}: {{ t('evalPage.pb.kpi.lagNone') }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pb-detail {
  margin-top: 10px;
}
.note {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--ink-2);
}
.warn {
  color: var(--warn);
}
.bad {
  color: var(--bad);
}
.hint {
  margin: 4px 0;
  font-size: 13px;
  color: var(--ink-3);
}
.facts {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 4px 14px;
  margin: 12px 0 0;
  font-size: 12.5px;
}
.facts dt {
  color: var(--ink-3);
}
.facts dd {
  margin: 0;
  min-width: 0;
  color: var(--ink);
  overflow-wrap: anywhere;
}
.cmd {
  font-size: 11.5px;
  color: var(--ink-2);
}
.num {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
.dim {
  color: var(--ink-3);
}
.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}
.actions .el-button + .el-button {
  margin-left: 0;
}
.loop {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  font-size: 12.5px;
  color: var(--ink-2);
}
.kpis {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
}
.charts {
  display: grid;
  grid-template-columns: minmax(280px, 460px) minmax(0, 1fr);
  gap: 16px;
  margin-top: 16px;
}
.stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
figure {
  margin: 0;
  min-width: 0;
}
figcaption {
  margin-bottom: 4px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-2);
}
@media (max-width: 1100px) {
  .charts {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 720px) {
  .kpis {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
