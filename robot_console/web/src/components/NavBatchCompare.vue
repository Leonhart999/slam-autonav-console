<!-- Side-by-side comparison of ticked navigation batch experiments (mean ± std per metric). -->
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { NavBatchHistoryEntry } from '@/evaluation/navBatch'
import type { MeanStd } from '@/evaluation/batch'

export interface NavBatchCompareItem {
  entry: NavBatchHistoryEntry
  color: string
  scene: string
}

const props = defineProps<{ items: NavBatchCompareItem[] }>()
const { t } = useI18n()

const METRICS = [
  'pooled_success_rate', 'success_rate', 'total_time_s', 'mean_time_s',
  'total_path_m', 'mean_path_m', 'recoveries', 'goal_error_m', 'loc_error_m',
  'amcl_rmse_m', 'min_clearance_m', 'wall_s',
] as const
type Metric = typeof METRICS[number]
const LOWER_IS_BETTER = new Set<Metric>([
  'total_time_s', 'mean_time_s', 'total_path_m', 'mean_path_m',
  'recoveries', 'goal_error_m', 'loc_error_m', 'amcl_rmse_m', 'wall_s',
])
const HIGHER_IS_BETTER = new Set<Metric>(['pooled_success_rate', 'success_rate', 'min_clearance_m'])
const BAR_METRICS = new Set<Metric>(['pooled_success_rate', 'success_rate', 'total_time_s', 'total_path_m', 'recoveries', 'loc_error_m'])

const mixedMaps = computed(() => {
  const s = [...new Set(props.items.map((b) => `${b.entry.map} (${b.scene})`))]
  return s.length > 1 ? s.join(' / ') : ''
})

interface Cell { val: number; std: number | null; n: number | null }

function cellOf(b: NavBatchHistoryEntry, k: Metric): Cell | null {
  const s = b.summary
  if (!s) return null
  if (k === 'pooled_success_rate') {
    return s.pooled_success_rate == null ? null : { val: s.pooled_success_rate, std: null, n: s.total_goals }
  }
  const m = s[k] as MeanStd | null
  return m ? { val: m.mean, std: m.std, n: m.n } : null
}

const rows = computed(() => METRICS.map((key) => {
  const cells = props.items.map((b) => cellOf(b.entry, key))
  const vals = cells.flatMap((c) => (c ? [c.val] : []))
  let best: number | null = null
  if (vals.length > 1) {
    if (LOWER_IS_BETTER.has(key)) best = Math.min(...vals)
    else if (HIGHER_IS_BETTER.has(key)) best = Math.max(...vals)
  }
  const scale = key === 'pooled_success_rate' || key === 'success_rate'
    ? 1
    : Math.max(0, ...cells.map((c) => (c ? c.val + (c.std ?? 0) : 0))) || 1
  return { key, cells, best, scale }
}))

function fmt(k: Metric, v: number): string {
  if (k === 'pooled_success_rate' || k === 'success_rate') return `${(v * 100).toFixed(1)}%`
  if (k === 'total_time_s' || k === 'mean_time_s' || k === 'wall_s') return v.toFixed(1)
  if (k === 'total_path_m' || k === 'mean_path_m' || k === 'recoveries') return v.toFixed(2)
  return v.toFixed(3)
}

const pct = (v: number, scale: number) => Math.max(0, Math.min(100, (v / scale) * 100))
</script>

<template>
  <h4 class="sub">{{ t('navBatch.cmp.compareTitle') }}</h4>
  <p v-if="mixedMaps" class="warn">{{ t('navBatch.cmp.mixed', { maps: mixedMaps }) }}</p>
  <el-table :data="rows" size="small" :row-key="(r: { key: string }) => r.key">
    <el-table-column :label="t('navBatch.metric')" min-width="160" fixed>
      <template #default="{ row }">{{ t(`navBatch.m.${row.key}`) }}</template>
    </el-table-column>
    <el-table-column v-for="(b, i) in items" :key="b.entry.id" min-width="190" align="right">
      <template #header>
        <div class="head">
          <span class="mono"><span class="swatch" :style="{ background: b.color }" />{{ b.entry.id.replace(/^nav_batch_/, '') }}</span>
          <span class="dim">{{ b.entry.variant }} · {{ b.entry.localizer === 'gt' ? 'GT' : 'AMCL' }}</span>
        </div>
      </template>
      <template #default="{ row }">
        <template v-if="row.cells[i]">
          <span class="mono strong" :class="{ best: row.best != null && row.cells[i].val === row.best }">
            {{ fmt(row.key, row.cells[i].val) }}
          </span>
          <span v-if="row.cells[i].std != null" class="mono dim"> ± {{ fmt(row.key, row.cells[i].std) }}</span>
          <span v-if="row.cells[i].n != null" class="mono dim nval">
            {{ row.key === 'pooled_success_rate' ? `g=${row.cells[i].n}` : `n=${row.cells[i].n}` }}
          </span>
          <div v-if="BAR_METRICS.has(row.key)" class="bar">
            <i :style="{ width: `${pct(row.cells[i].val, row.scale)}%`, background: b.color }" />
            <em v-if="row.cells[i].std != null"
              :style="{ left: `${pct(row.cells[i].val - row.cells[i].std, row.scale)}%`,
                        width: `${pct(row.cells[i].val + row.cells[i].std, row.scale) - pct(row.cells[i].val - row.cells[i].std, row.scale)}%` }" />
          </div>
        </template>
        <span v-else class="mono dim">—</span>
      </template>
    </el-table-column>
  </el-table>
  <p class="legend">{{ t('navBatch.cmp.best') }}</p>
</template>

<style scoped>
.sub { margin: 16px 0 6px; font-size: 12.5px; font-weight: 600; color: var(--ink-2); }
.warn { margin: 6px 0; font-size: 12.5px; color: var(--warn); }
.mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.strong { font-weight: 600; color: var(--ink); }
.dim { color: var(--ink-3); font-size: 12px; }
.best { color: var(--ok); }
.nval { margin-left: 8px; font-size: 11px; }
.swatch { display: inline-block; width: 8px; height: 8px; margin-right: 6px; border-radius: 2px; vertical-align: 1px; }
.head { display: inline-flex; flex-direction: column; align-items: flex-end; line-height: 1.35; text-transform: none; letter-spacing: 0; }
.head .dim { font-weight: 400; font-size: 11.5px; white-space: nowrap; }
.bar { position: relative; height: 6px; margin: 5px 0 1px auto; max-width: 160px; border-radius: 3px; background: var(--hairline); }
.bar i { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 3px; opacity: 0.75; }
.bar em { position: absolute; top: 2px; height: 2px; background: var(--ink-2); }
.legend { margin: 8px 0 0; font-size: 12px; color: var(--ink-3); }
</style>
