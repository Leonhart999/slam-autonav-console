<!-- Evaluation page, comparison of ticked playback runs. Runs of the same bag part saw exactly the same input, so
     they can be overlaid (top view, height over time) and compared with each other: relative deviation = evo APE of
     each estimate against a baseline run's estimate (matched by bag time). Runs with the same algorithm and rate
     give a repeatability line (mean ± std). Different bag parts only share the table. -->
<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import type { EChartsCoreOption } from 'echarts/core'
import { errorText } from '@/messages'
import { evalApi, type RunEntry, type RunEval, type RunTrajectory } from '@/evaluation/api'
import { evalMessages } from '@/evaluation/messages'
import { SERIES } from '@/evaluation/colors'
import { partKey, partText, timeSeries, topView, zipXY } from '@/evaluation/charts'
import EvalChart from '@/components/EvalChart.vue'

const props = defineProps<{ entries: RunEntry[]; colorOf: (id: string) => string }>()
const { t } = useI18n({ messages: evalMessages })

const MAX_RELATIVE = 8
const m3 = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(3))
const f1 = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(1))

interface Group { key: string; bag: string; label: string; runs: RunEntry[] }
const groups = computed<Group[]>(() => {
  const map = new Map<string, Group>()
  for (const r of props.entries) {
    const p = r.playback!
    const key = partKey(p.bag, p.range)
    if (!map.has(key)) {
      map.set(key, { key, bag: p.bag, runs: [],
        label: t('evalPage.pb.compare.group', { bag: p.bag_name, part: partText(p.range, t('evalPage.pb.whole')) }) })
    }
    map.get(key)!.runs.push(r)
  }
  return [...map.values()]
})
const comparable = computed(() => groups.value.filter((g) => g.runs.length >= 2))

// ---------------- trajectories of the runs in comparable groups ----------------
const trajs = reactive<Record<string, RunTrajectory>>({})
watch(comparable, async (gs) => {
  for (const g of gs) {
    for (const r of g.runs.slice(0, SERIES.length)) {
      if (trajs[r.id]) continue
      try {
        trajs[r.id] = await evalApi.trajectory(r.id)
      } catch {
        // no estimate: the run is only in the table
      }
    }
  }
}, { immediate: true })

// ---------------- relative deviation per group ----------------
const baselines = reactive<Record<string, string>>({})
const relative = reactive<Record<string, { baseline: string; rows: { id: string; eval: RunEval | null; error: string | null }[] }>>({})
const relBusy = ref('')
watch(comparable, (gs) => {
  for (const g of gs) {
    if (!g.runs.some((r) => r.id === baselines[g.key])) baselines[g.key] = g.runs[0].id
    const rel = relative[g.key]
    // forget a result whose runs are no longer all ticked
    if (rel && (![rel.baseline, ...rel.rows.map((x) => x.id)].every((id) => g.runs.some((r) => r.id === id))
      || rel.rows.length + 1 !== g.runs.length)) delete relative[g.key]
  }
}, { immediate: true })
async function computeRelative(g: Group) {
  relBusy.value = g.key
  try {
    relative[g.key] = await evalApi.relative(g.runs.slice(0, MAX_RELATIVE).map((r) => r.id), baselines[g.key])
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    relBusy.value = ''
  }
}

const labels = computed(() => ({ start: t('evalPage.pb.chart.start'), end: t('evalPage.pb.chart.end') }))
function overlayOption(g: Group): EChartsCoreOption | null {
  const rel = relative[g.key]
  if (rel) {
    const base = rel.rows.find((x) => x.eval)?.eval
    if (base) {
      return topView([
        { name: rel.baseline, points: base.series.traj_gt, color: props.colorOf(rel.baseline) },
        ...rel.rows.filter((x) => x.eval).map((x) => ({ name: x.id, points: x.eval!.series.traj_est, color: props.colorOf(x.id) })),
      ], labels.value)
    }
  }
  const series = g.runs.slice(0, SERIES.length).filter((r) => trajs[r.id])
    .map((r) => ({ name: r.id, points: zipXY(trajs[r.id].x, trajs[r.id].y), color: props.colorOf(r.id) }))
  return series.length >= 2 ? topView(series, labels.value) : null
}
function zOption(g: Group) {
  const series = g.runs.slice(0, SERIES.length).filter((r) => trajs[r.id])
    .map((r) => ({ name: r.id, points: zipXY(trajs[r.id].t, trajs[r.id].z), color: props.colorOf(r.id) }))
  return series.length >= 2
    ? timeSeries(series, { x: t('evalPage.chart.time'), y: t('evalPage.pb.chart.zAxis'), unit: 'm' }, { zero: true }) : null
}

// ---------------- repeatability: same algorithm and rate inside a group ----------------
function meanStd(vs: number[]) {
  const n = vs.length
  const mean = vs.reduce((a, v) => a + v, 0) / n
  const std = n > 1 ? Math.sqrt(vs.reduce((a, v) => a + (v - mean) ** 2, 0) / (n - 1)) : 0
  return `${mean.toFixed(3)} ± ${std.toFixed(3)}`
}
function repeats(g: Group) {
  const by = new Map<string, RunEntry[]>()
  for (const r of g.runs) {
    const k = `${r.algorithm ?? '—'} · ${r.playback!.rate ?? '—'}x`
    by.set(k, [...(by.get(k) ?? []), r])
  }
  return [...by.entries()].filter(([, rs]) => rs.length >= 2 && rs.every((r) => r.playback!.stats)).map(([k, rs]) => {
    const s = rs.map((r) => r.playback!.stats!)
    const lags = rs.map((r) => r.playback!.lag?.max).filter((v): v is number => v != null)
    return { key: k, n: rs.length, startEnd: meanStd(s.map((x) => x.start_end)), zEnd: meanStd(s.map((x) => x.z_end)),
      length: meanStd(s.map((x) => x.length)), lag: lags.length === rs.length ? meanStd(lags) : '—' }
  })
}
const versus = (r: RunEntry) => {
  if (r.ape_rmse != null) return `ATE ${m3(r.ape_rmse)}`
  return r.playback!.ref_ape_rmse != null ? `${m3(r.playback!.ref_ape_rmse)}` : '—'
}
</script>

<template>
  <div class="pb-compare">
    <h4 class="sub-title">{{ t('evalPage.pb.compare.title') }}</h4>
    <p v-if="groups.length > 1" class="note warn">{{ t('evalPage.pb.compare.mixed', { n: groups.length }) }}</p>
    <el-table :data="entries" size="small" :row-key="(r: RunEntry) => r.id">
      <el-table-column :label="t('evalPage.col.id')" min-width="200">
        <template #default="{ row }">
          <span class="swatch" :style="{ background: colorOf(row.id) }" /><span class="name mono">{{ row.id }}</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('evalPage.col.bag')" min-width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ row.playback.bag_name }} · {{ partText(row.playback.range, t('evalPage.pb.whole')) }}</template>
      </el-table-column>
      <el-table-column :label="t('evalPage.pb.slam')" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">{{ row.algorithm ?? '—' }}</template>
      </el-table-column>
      <el-table-column :label="t('evalPage.pb.rate')" width="64" align="right">
        <template #default="{ row }"><span class="num">{{ row.playback.rate ?? '—' }}x</span></template>
      </el-table-column>
      <el-table-column :label="t('evalPage.col.length')" width="90" align="right">
        <template #default="{ row }"><span class="num">{{ f1(row.playback.stats?.length) }} m</span></template>
      </el-table-column>
      <el-table-column :label="t('evalPage.col.startEnd')" width="100" align="right">
        <template #default="{ row }"><span class="num strong">{{ m3(row.playback.stats?.start_end) }}</span></template>
      </el-table-column>
      <el-table-column :label="t('evalPage.col.zEnd')" width="90" align="right">
        <template #default="{ row }"><span class="num">{{ m3(row.playback.stats?.z_end) }}</span></template>
      </el-table-column>
      <el-table-column :label="t('evalPage.pb.compare.zMax')" width="90" align="right">
        <template #default="{ row }"><span class="num">{{ m3(row.playback.stats?.z_max_abs) }}</span></template>
      </el-table-column>
      <el-table-column :label="t('evalPage.pb.compare.lagMax')" width="90" align="right">
        <template #default="{ row }"><span class="num">{{ row.playback.lag ? `${f1(row.playback.lag.max)} s` : '—' }}</span></template>
      </el-table-column>
      <el-table-column :label="t('evalPage.col.versus')" width="110" align="right">
        <template #default="{ row }"><span class="num">{{ versus(row) }}</span></template>
      </el-table-column>
    </el-table>
    <p v-if="!comparable.length" class="hint">{{ t('evalPage.pb.compare.hint') }}</p>

    <section v-for="g in comparable" :key="g.key" class="group">
      <h4 v-if="groups.length > 1 || comparable.length > 1" class="sub-title mono">{{ g.label }}</h4>
      <div class="charts">
        <figure>
          <figcaption>{{ relative[g.key] ? t('evalPage.pb.compare.overlayAligned', { id: relative[g.key].baseline }) : t('evalPage.pb.compare.overlay') }}</figcaption>
          <EvalChart v-if="overlayOption(g)" :option="overlayOption(g)!" square />
        </figure>
        <figure>
          <figcaption>{{ t('evalPage.pb.compare.z') }}</figcaption>
          <EvalChart v-if="zOption(g)" :option="zOption(g)!" height="300px" />
        </figure>
      </div>

      <div class="relative-bar">
        <span class="k">{{ t('evalPage.pb.compare.baseline') }}</span>
        <el-select v-model="baselines[g.key]" size="small" class="base-select">
          <el-option v-for="r in g.runs.slice(0, MAX_RELATIVE)" :key="r.id" :value="r.id" :label="r.id" />
        </el-select>
        <el-button size="small" type="primary" plain :loading="relBusy === g.key" @click="computeRelative(g)">
          {{ t('evalPage.pb.compare.relative') }}
        </el-button>
        <span v-if="g.runs.length > MAX_RELATIVE" class="dim">{{ t('evalPage.pb.compare.relativeCap', { n: MAX_RELATIVE }) }}</span>
      </div>
      <template v-if="relative[g.key]">
        <h4 class="sub-title">{{ t('evalPage.pb.compare.relativeTitle', { id: relative[g.key].baseline }) }}</h4>
        <el-table :data="relative[g.key].rows" size="small" :row-key="(r: { id: string }) => r.id">
          <el-table-column :label="t('evalPage.col.id')" min-width="200">
            <template #default="{ row }">
              <span class="swatch" :style="{ background: colorOf(row.id) }" /><span class="name mono">{{ row.id }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('evalPage.pb.compare.rmse')" width="120" align="right">
            <template #default="{ row }"><span class="num strong">{{ row.eval ? m3(row.eval.ape.rmse) : '—' }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.pb.compare.max')" width="100" align="right">
            <template #default="{ row }"><span class="num">{{ row.eval ? m3(row.eval.ape.max) : '—' }}</span></template>
          </el-table-column>
          <el-table-column :label="t('evalPage.pb.compare.matched')" width="100" align="right">
            <template #default="{ row }"><span class="num">{{ row.eval?.matched_poses ?? '—' }}</span></template>
          </el-table-column>
          <el-table-column min-width="160">
            <template #default="{ row }"><span v-if="row.error" class="bad">{{ row.error }}</span></template>
          </el-table-column>
        </el-table>
        <p class="method">{{ t('evalPage.pb.compare.relativeNote') }}</p>
      </template>

      <template v-for="rp in repeats(g)" :key="rp.key">
        <h4 class="sub-title">{{ t('evalPage.pb.compare.repeat', { n: rp.n }) }} · <span class="mono">{{ rp.key }}</span></h4>
        <dl class="facts">
          <dt>{{ t('evalPage.col.startEnd') }}</dt><dd class="num">{{ rp.startEnd }} m</dd>
          <dt>{{ t('evalPage.col.zEnd') }}</dt><dd class="num">{{ rp.zEnd }} m</dd>
          <dt>{{ t('evalPage.col.length') }}</dt><dd class="num">{{ rp.length }} m</dd>
          <dt>{{ t('evalPage.pb.compare.lagMax') }}</dt><dd class="num">{{ rp.lag }}{{ rp.lag === '—' ? '' : ' s' }}</dd>
        </dl>
      </template>
    </section>
  </div>
</template>

<style scoped>
.sub-title {
  margin: 16px 0 6px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-2);
}
.note {
  margin: 6px 0;
  font-size: 13px;
  color: var(--ink-2);
}
.warn {
  color: var(--warn);
}
.bad {
  color: var(--bad);
  font-size: 12px;
}
.hint {
  margin: 8px 0 0;
  font-size: 13px;
  color: var(--ink-3);
}
.name {
  font-weight: 600;
  color: var(--ink);
}
.num {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
.strong {
  font-weight: 600;
  color: var(--ink);
}
.dim {
  font-size: 12px;
  color: var(--ink-3);
}
.swatch {
  display: inline-block;
  width: 10px;
  height: 3px;
  margin-right: 6px;
  border-radius: 2px;
  vertical-align: middle;
}
.group {
  margin-top: 8px;
}
.charts {
  display: grid;
  grid-template-columns: minmax(280px, 460px) minmax(0, 1fr);
  gap: 16px;
  margin-top: 10px;
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
.relative-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}
.relative-bar .k {
  font-size: 12.5px;
  color: var(--ink-2);
}
.base-select {
  width: 300px;
}
.method {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-3);
}
.facts {
  display: grid;
  grid-template-columns: max-content max-content;
  gap: 2px 14px;
  margin: 0;
  font-size: 12.5px;
}
.facts dt {
  color: var(--ink-3);
}
.facts dd {
  margin: 0;
}
@media (max-width: 1100px) {
  .charts {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
