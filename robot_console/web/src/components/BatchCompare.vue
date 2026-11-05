<!-- Side-by-side comparison of ticked batch experiments (mean ± std per metric), shown in the evaluation page's
     comparison panel. The batches are ticked in BatchPanel; summaries come from /api/batch/history. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { BatchHistoryEntry, MeanStd } from '@/evaluation/batch'

export interface BatchCompareItem { entry: BatchHistoryEntry; color: string; scene: string; slam: string }

const props = defineProps<{ items: BatchCompareItem[] }>()

const { t } = useI18n({ messages: {
  'zh-CN': { bcmp: {
    title: '批量实验对比',
    metric: '指标',
    mixed: '所选批次的场景不同（{scenes}），指标不能直接横向比较。',
    best: '加粗绿色 = 所选批次中 ATE / RPE 平均值最小；条形为平均值，细线为 ± 1 个标准差，n 为成功次数。',
    m: { ape_rmse: 'ATE RMSE (m)', rpe_rmse: 'RPE RMSE (m)', length: '轨迹长度 (m)', duration: '时长 (s)', rtf: '行驶时实时率', wall_s: '单次墙钟用时 (s)' },
  } },
  en: { bcmp: {
    title: 'Batch experiments',
    metric: 'Metric',
    mixed: 'The ticked batches ran in different scenes ({scenes}); their numbers are not directly comparable.',
    best: 'Bold green = lowest ATE / RPE mean among the ticked batches; the bar is the mean, the thin line ± 1 std, n the successful runs.',
    m: { ape_rmse: 'ATE RMSE (m)', rpe_rmse: 'RPE RMSE (m)', length: 'Length (m)', duration: 'Duration (s)', rtf: 'RTF while driving', wall_s: 'Wall time per run (s)' },
  } },
} })

const METRICS = ['ape_rmse', 'rpe_rmse', 'length', 'duration', 'rtf', 'wall_s'] as const
type Metric = typeof METRICS[number]
const LOWER_IS_BETTER = new Set<Metric>(['ape_rmse', 'rpe_rmse'])   // accuracy; the others are run facts

const mixedScenes = computed(() => {
  const s = [...new Set(props.items.map((b) => b.scene))]
  return s.length > 1 ? s.join(' / ') : ''
})
const rows = computed(() => METRICS.map((key) => {
  const cells = props.items.map((b) => (b.entry.summary?.[key] ?? null) as MeanStd | null)
  const means = cells.flatMap((c) => (c ? [c.mean] : []))
  const best = LOWER_IS_BETTER.has(key) && means.length > 1 ? Math.min(...means) : null
  // Bars share one scale per metric: the largest mean + std of the ticked batches
  const scale = Math.max(0, ...cells.map((c) => (c ? c.mean + c.std : 0))) || 1
  return { key, cells, best, scale }
}))
const fmt = (k: Metric, v: number) =>
  k === 'duration' || k === 'wall_s' ? v.toFixed(1) : k === 'length' || k === 'rtf' ? v.toFixed(2) : v.toFixed(4)
const pct = (v: number, scale: number) => Math.max(0, Math.min(100, (v / scale) * 100))
</script>

<template>
  <h4 class="sub">{{ t('bcmp.title') }}</h4>
  <p v-if="mixedScenes" class="warn">{{ t('bcmp.mixed', { scenes: mixedScenes }) }}</p>
  <el-table :data="rows" size="small" :row-key="(r: { key: string }) => r.key">
    <el-table-column :label="t('bcmp.metric')" min-width="150" fixed>
      <template #default="{ row }">{{ t(`bcmp.m.${row.key}`) }}</template>
    </el-table-column>
    <el-table-column v-for="(b, i) in items" :key="b.entry.id" min-width="190" align="right">
      <template #header>
        <div class="head">
          <span class="mono"><span class="swatch" :style="{ background: b.color }" />{{ b.entry.id.replace(/^batch_/, '') }}</span>
          <span class="dim">{{ b.slam }} · {{ b.scene }}</span>
        </div>
      </template>
      <template #default="{ row }">
        <template v-if="row.cells[i]">
          <span class="mono strong" :class="{ best: row.best != null && row.cells[i].mean === row.best }">{{ fmt(row.key, row.cells[i].mean) }}</span>
          <span class="mono dim"> ± {{ fmt(row.key, row.cells[i].std) }}</span>
          <span class="mono dim nval">n={{ row.cells[i].n }}</span>
          <div v-if="LOWER_IS_BETTER.has(row.key)" class="bar">
            <i :style="{ width: `${pct(row.cells[i].mean, row.scale)}%`, background: b.color }" />
            <em :style="{ left: `${pct(row.cells[i].mean - row.cells[i].std, row.scale)}%`,
                          width: `${pct(row.cells[i].mean + row.cells[i].std, row.scale) - pct(row.cells[i].mean - row.cells[i].std, row.scale)}%` }" />
          </div>
        </template>
        <span v-else class="mono dim">—</span>
      </template>
    </el-table-column>
  </el-table>
  <p class="legend">{{ t('bcmp.best') }}</p>
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
