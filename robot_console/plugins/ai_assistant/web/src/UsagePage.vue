<!-- Usage tab: what the assistant's model calls used, from the plugin's own records (one per model call, also of
     answers that were stopped or failed). Range and filters, four totals, tokens over time, share per model or
     service, a table by model / service / conversation, and the pricing panel. Cost is an estimate from the price
     table (PricingDialog.vue, synced from models.dev); calls without a price are counted but not priced. -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElButton, ElDialog, ElOption, ElRadio, ElRadioButton, ElRadioGroup, ElSelect, ElTable, ElTableColumn } from 'element-plus'
import {
  download, getPricing, usageBreakdown, usageExportUrl, usageFilters, usagePrune, usagePrunePreview, usageSeries, usageSummary,
  type UsageBy, type UsagePoint, type UsageQuery, type UsageRow, type UsageSummary,
} from './api'
import { compact, grouped, moneyList, relative, seconds, shortModel } from './format'
import { RC, t } from './sdk'
import Icon from './Icon.vue'
import StackedBars from './StackedBars.vue'
import ShareBars from './ShareBars.vue'
import PricingDialog from './PricingDialog.vue'

const { GlassPanel } = RC().ui
type Range = 'today' | '7d' | '30d' | 'all'
const RANGES: Range[] = ['today', '7d', '30d', 'all']

const range = ref<Range>('7d')
const profile = ref('')
const model = ref('')
const by = ref<UsageBy>('model')
const shareBy = ref<'model' | 'profile'>('model')
const filters = ref<{ profiles: { id: string; name: string }[]; models: string[] }>({ profiles: [], models: [] })
const summary = ref<UsageSummary | null>(null)
const points = ref<UsagePoint[]>([])
const rows = ref<UsageRow[]>([])
const shareRows = ref<UsageRow[]>([])
const loading = ref(true)
const error = ref('')
const pruneOpen = ref(false)
const pruneKeep = ref<'7' | '30' | '90' | 'all'>('30')
const prunePreview = ref<number | null>(null)
const pruning = ref(false)
const pruneArmed = ref(false)
const pricingOpen = ref(false)
const priced = ref<number | null>(null)
let seq = 0
let armTimer = 0

async function loadPriced() {
  try {
    priced.value = (await getPricing()).entries.length
  } catch { /* the panel shows no count */ }
}
function onPricingChanged() {
  void loadPriced()
  void load()
}

function query(): UsageQuery {
  const now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  if (range.value === '7d') start.setDate(start.getDate() - 6)
  else if (range.value === '30d') start.setDate(start.getDate() - 29)
  const from = range.value === 'all' ? 0 : Math.floor(start.getTime() / 1000)
  return { from, to: Math.ceil(now.getTime() / 1000), profile: profile.value || undefined, model: model.value || undefined }
}
const bucket = computed<'hour' | 'day'>(() => (range.value === 'today' ? 'hour' : 'day'))

async function load() {
  const n = ++seq
  loading.value = true
  const q = query()
  try {
    const [s, se, b, sh] = await Promise.all([
      usageSummary(q), usageSeries(q, bucket.value), usageBreakdown(q, by.value),
      usageBreakdown(q, shareBy.value),
    ])
    if (n !== seq) return
    summary.value = s
    points.value = se.points
    rows.value = b.rows
    shareRows.value = sh.rows
    error.value = ''
  } catch (e) {
    if (n === seq) error.value = RC().errorText(e)
  } finally {
    if (n === seq) loading.value = false
  }
}

async function loadFilters() {
  try {
    filters.value = await usageFilters()
  } catch { /* the selects stay empty */ }
}

// every change goes through load(): its sequence number drops answers that a newer request has overtaken, so a slow
// answer for an older filter or grouping never replaces the newer one (and KPIs, chart and table always match)
watch([range, profile, model, by, shareBy], () => void load())
onMounted(() => {
  void loadFilters()
  void load()
  void loadPriced()
})

const tokensTotal = computed(() => {
  const s = summary.value
  return s ? s.input + s.output + s.cache_read + s.cache_write : 0
})
const costText = computed(() => {
  const s = summary.value
  if (!s || !s.cost.length) return ''
  return moneyList(s.cost)
})

function pickShare(key: string) {
  if (shareBy.value === 'model') model.value = key
  else profile.value = key
}

function rowTokens(r: UsageRow) {
  return r.input + r.output + r.cache_read + r.cache_write
}
function openSession(r: UsageRow) {
  if (by.value !== 'session') return
  if (r.session_exists === false) {
    RC().ui.ElMessage.error(t('usage.table.sessionGone'))
    return
  }
  window.dispatchEvent(new CustomEvent('aia:open-session', { detail: r.key }))
}
const rowClass = ({ row }: { row: UsageRow }) => (by.value === 'session' ? (row.session_exists === false ? 'gone' : 'clickable') : '')

function exportCsv() {
  download(usageExportUrl(query()))
}

const cutoffFor = (keep: typeof pruneKeep.value) => (keep === 'all' ? null : Math.floor(Date.now() / 1000 - Number(keep) * 86400))
// the cutoff the preview on screen was counted for: deleting uses exactly this one, never the current radio value
let previewCutoff: number | null | undefined
let previewSeq = 0
async function openPrune() {
  pruneOpen.value = true
  pruneArmed.value = false
  await refreshPreview()
}
async function refreshPreview() {
  const n = ++previewSeq
  const keep = pruneKeep.value
  const cutoff = cutoffFor(keep)
  prunePreview.value = null
  previewCutoff = undefined
  pruneArmed.value = false
  window.clearTimeout(armTimer)
  let rows: number | null = null
  try {
    rows = (await usagePrunePreview(cutoff)).rows
  } catch {
    rows = null
  }
  if (n !== previewSeq || !pruneOpen.value) return          // a newer choice was made meanwhile
  prunePreview.value = rows
  previewCutoff = rows === null ? undefined : cutoff
  // deleting everything needs a 2 s pause first
  if (keep !== 'all') pruneArmed.value = true
  else armTimer = window.setTimeout(() => { if (n === previewSeq) pruneArmed.value = true }, 2000)
}
watch(pruneKeep, () => { if (pruneOpen.value) void refreshPreview() })
watch(pruneOpen, (o) => { if (!o) { previewSeq++; window.clearTimeout(armTimer) } })
async function prune() {
  if (previewCutoff === undefined || !pruneArmed.value) return
  pruning.value = true
  try {
    const r = await usagePrune(previewCutoff)
    RC().ui.ElMessage.success(t('usage.prune.done', { n: grouped(r.deleted) }))
    pruneOpen.value = false
    void loadFilters()
    void load()
  } catch (e) {
    RC().ui.ElMessage.error(RC().errorText(e))
  } finally {
    pruning.value = false
  }
}
</script>

<template>
  <div class="aiu">
    <div class="aiu-filters">
      <el-radio-group v-model="range" size="small">
        <el-radio-button v-for="r in RANGES" :key="r" :value="r">{{ t(`usage.range.${r}`) }}</el-radio-button>
      </el-radio-group>
      <el-select v-model="profile" size="small" clearable :placeholder="t('usage.filter.profiles')" class="aiu-sel">
        <el-option v-for="p in filters.profiles" :key="p.id" :value="p.id" :label="p.name" />
      </el-select>
      <el-select v-model="model" size="small" clearable filterable :placeholder="t('usage.filter.models')" class="aiu-sel">
        <el-option v-for="m in filters.models" :key="m" :value="m" :label="m" />
      </el-select>
      <span class="aip-grow" />
      <el-button size="small" @click="exportCsv"><Icon name="download" />{{ t('usage.export') }}</el-button>
      <el-button size="small" text @click="openPrune"><Icon name="trash" />{{ t('usage.prune.open') }}</el-button>
    </div>

    <div v-if="error" class="aip-error">{{ error }}</div>

    <div class="aiu-kpis">
      <div class="aiu-kpi">
        <span class="aiu-kpi-label">{{ t('usage.kpi.tokens') }}</span>
        <span class="aiu-kpi-value">{{ summary ? compact(tokensTotal) : '—' }}</span>
        <span class="aiu-kpi-sub">
          <template v-if="summary">
            {{ t('usage.kpi.split', { input: compact(summary.input), output: compact(summary.output), cache: compact(summary.cache_read) }) }}<template v-if="summary.cache_write"> · {{ t('usage.kpi.cacheWrite', { n: compact(summary.cache_write) }) }}</template><template v-if="summary.reasoning"> · {{ t('usage.kpi.reasoning', { n: compact(summary.reasoning) }) }}</template>
          </template>
        </span>
      </div>
      <div class="aiu-kpi">
        <span class="aiu-kpi-label">{{ t('usage.kpi.calls') }}</span>
        <span class="aiu-kpi-value">{{ summary ? grouped(summary.calls) : '—' }}</span>
        <span class="aiu-kpi-sub">
          <template v-if="summary">{{ t('usage.kpi.sessions', { n: grouped(summary.sessions) }) }}<template v-if="summary.errors"> · {{ t('usage.kpi.errors', { n: grouped(summary.errors) }) }}</template></template>
        </span>
      </div>
      <div class="aiu-kpi">
        <span class="aiu-kpi-label">{{ t('usage.kpi.cost') }}<span class="aiu-est" :title="t('usage.kpi.costHelp')">{{ t('usage.kpi.estimate') }}</span></span>
        <span v-if="costText" class="aiu-kpi-value">{{ costText }}</span>
        <span v-else class="aiu-kpi-value none">{{ summary && summary.calls ? t('usage.kpi.noPrice') : '—' }}</span>
        <span class="aiu-kpi-sub">
          <button v-if="summary && summary.unpriced_models.length" type="button" class="aip-link aiu-kpi-link"
                  :title="summary.unpriced_models.join(', ')" @click="pricingOpen = true">{{ t('usage.kpi.unpriced', { n: summary.unpriced_models.length }) }}</button>
          <template v-else>{{ t('usage.kpi.costHint') }}</template>
        </span>
      </div>
      <div class="aiu-kpi">
        <span class="aiu-kpi-label">{{ t('usage.kpi.latency') }}</span>
        <span class="aiu-kpi-value">{{ summary ? seconds(summary.ms_avg) : '—' }}</span>
        <span class="aiu-kpi-sub">{{ summary && summary.ms_p90 !== null ? t('usage.kpi.p90', { v: seconds(summary.ms_p90) }) : '' }}</span>
      </div>
    </div>

    <div class="aiu-charts">
      <GlassPanel :title="t(range === 'today' ? 'usage.chart.byHour' : 'usage.chart.byDay')">
        <StackedBars :points="points" :bucket="bucket" :loading="loading && !points.length" />
      </GlassPanel>
      <GlassPanel :title="t('usage.share.title')">
        <template #extra>
          <el-radio-group v-model="shareBy" size="small">
            <el-radio-button value="model">{{ t('usage.by.model') }}</el-radio-button>
            <el-radio-button value="profile">{{ t('usage.by.profile') }}</el-radio-button>
          </el-radio-group>
        </template>
        <ShareBars :rows="shareRows" :by="shareBy" pickable @pick="pickShare" />
      </GlassPanel>
    </div>

    <GlassPanel :title="t('usage.table.title')">
      <div class="aiu-table-head">
        <el-radio-group v-model="by" size="small">
          <el-radio-button value="model">{{ t('usage.by.model') }}</el-radio-button>
          <el-radio-button value="profile">{{ t('usage.by.profile') }}</el-radio-button>
          <el-radio-button value="session">{{ t('usage.by.session') }}</el-radio-button>
        </el-radio-group>
        <span v-if="by === 'session'" class="aip-help">{{ t('usage.table.sessionHelp') }}</span>
      </div>
      <el-table :data="rows" class="aiu-table" :row-class-name="rowClass" :empty-text="t('usage.table.empty')"
                :default-sort="{ prop: 'tokens', order: 'descending' }" @row-click="openSession">
        <el-table-column v-if="by === 'session'" :label="t('usage.table.session')" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">{{ row.title || (row.session_exists === false ? t('usage.table.deleted') : t('history.untitled')) }}</template>
        </el-table-column>
        <el-table-column v-else :label="t(by === 'model' ? 'usage.table.model' : 'usage.table.profile')" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="mono">{{ by === 'model' ? shortModel(row.label || row.key) : (row.label || row.key) }}</span>
            <span v-if="by === 'model' && row.profile_name" class="aiu-cell-sub">{{ row.profile_name }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('usage.table.calls')" prop="calls" align="right" min-width="80" sortable>
          <template #default="{ row }">{{ grouped(row.calls) }}</template>
        </el-table-column>
        <el-table-column :label="t('usage.series.input')" prop="input" align="right" min-width="84" sortable>
          <template #default="{ row }">{{ compact(row.input) }}</template>
        </el-table-column>
        <el-table-column :label="t('usage.series.output')" prop="output" align="right" min-width="92" sortable>
          <template #default="{ row }">{{ compact(row.output) }}</template>
        </el-table-column>
        <el-table-column :label="t('usage.series.cache_read')" prop="cache_read" align="right" min-width="108" sortable>
          <template #default="{ row }">{{ compact(row.cache_read) }}</template>
        </el-table-column>
        <el-table-column :label="t('usage.series.cache_write')" prop="cache_write" align="right" min-width="128" sortable>
          <template #default="{ row }">{{ compact(row.cache_write) }}</template>
        </el-table-column>
        <el-table-column :label="t('usage.table.tokens')" prop="tokens" align="right" min-width="86" sortable
                         :sort-method="(a: UsageRow, b: UsageRow) => rowTokens(a) - rowTokens(b)">
          <template #default="{ row }">{{ compact(rowTokens(row)) }}</template>
        </el-table-column>
        <el-table-column :label="t('usage.table.cost')" align="right" min-width="116">
          <template #default="{ row }">
            <span v-if="row.cost?.length">{{ moneyList(row.cost) }}</span>
            <span v-else class="aiu-cell-none" :title="t('usage.kpi.noPrice')">—</span>
          </template>
        </el-table-column>
        <el-table-column v-if="by !== 'session'" :label="t('usage.table.latency')" align="right" min-width="106">
          <template #default="{ row }">{{ seconds(row.ms_avg) }}</template>
        </el-table-column>
        <el-table-column :label="t('usage.table.last')" align="right" min-width="88">
          <template #default="{ row }">{{ row.last ? relative(row.last) : '' }}</template>
        </el-table-column>
      </el-table>
    </GlassPanel>

    <GlassPanel :title="t('pricing.title')">
      <div class="aiu-pricing">
        <div class="aiu-pricing-text">
          <span>{{ t('pricing.panel.text') }}</span>
          <span v-if="priced !== null" class="aiu-cell-sub">{{ t('pricing.panel.count', { n: priced }) }}</span>
        </div>
        <el-button @click="pricingOpen = true">{{ t('pricing.panel.manage') }}</el-button>
      </div>
    </GlassPanel>
    <PricingDialog v-model="pricingOpen" @changed="onPricingChanged" />

    <el-dialog v-model="pruneOpen" :title="t('usage.prune.title')" width="min(420px, 94vw)" append-to-body class="aip-dialog">
      <div class="aiu-prune">
        <p class="aip-help">{{ t('usage.prune.help') }}</p>
        <el-radio-group v-model="pruneKeep" class="aiu-prune-opts">
          <el-radio value="7">{{ t('usage.prune.keep', { n: 7 }) }}</el-radio>
          <el-radio value="30">{{ t('usage.prune.keep', { n: 30 }) }}</el-radio>
          <el-radio value="90">{{ t('usage.prune.keep', { n: 90 }) }}</el-radio>
          <el-radio value="all">{{ t('usage.prune.all') }}</el-radio>
        </el-radio-group>
        <p class="aiu-prune-preview">{{ prunePreview === null ? '…' : t('usage.prune.preview', { n: grouped(prunePreview) }) }}</p>
        <div class="aip-row"><span class="aip-grow" />
          <el-button text @click="pruneOpen = false">{{ t('editor.cancel') }}</el-button>
          <el-button type="danger" :disabled="!prunePreview || !pruneArmed" :loading="pruning" @click="prune">{{ t('usage.prune.confirm') }}</el-button>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<style>
/* Usage tab (aiu-) */
.aiu { --aip-l1: rgba(var(--tint-rgb), 0.028); --aip-l2: rgba(var(--tint-rgb), 0.05); --aip-l3: rgba(var(--tint-rgb), 0.08);
  --c-in: var(--accent); --c-out: color-mix(in srgb, var(--warn) 85%, var(--ink));
  --c-cache: color-mix(in srgb, var(--ink-3) 70%, var(--accent)); --c-cache-w: color-mix(in srgb, var(--ok) 68%, var(--ink-2));
  display: flex; flex-direction: column; gap: 14px; font-variant-numeric: tabular-nums; }
.dark .aiu { font-weight: 360; }
.dark .aiu .el-button { font-weight: 460; }
.aiu .el-button { border: 0; }
.aiu .el-button:not(.is-text):not(.is-link):not(.el-button--primary):not(.el-button--danger) { background: var(--aip-l2); }
.aiu .aia-i { margin-right: 5px; }
.aiu-filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.aiu-sel { width: 170px; }
.aiu .el-radio-group { padding: 3px; border-radius: 10px; background: var(--aip-l2); gap: 2px; }
.aiu .el-radio-button__inner { border: 0 !important; border-radius: 8px !important; background: transparent; box-shadow: none !important; color: var(--ink-2); }
.aiu .el-radio-button.is-active .el-radio-button__inner { color: var(--ink); background: var(--card-solid); box-shadow: 0 1px 3px rgba(var(--shade-rgb), 0.12) !important; }
.aiu .el-select__wrapper { box-shadow: none; background: var(--aip-l2); }

.aiu-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
@media (max-width: 1100px) { .aiu-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.aiu-kpi { display: flex; flex-direction: column; gap: 4px; min-width: 0; padding: 14px 16px; border-radius: 14px; background: var(--card-solid); box-shadow: inset 0 0 0 100vmax var(--aip-l1); }
.aiu-kpi-label { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--ink-3); }
.aiu-kpi-value { font-size: 26px; font-weight: 600; line-height: 1.2; color: var(--ink); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.dark .aiu-kpi-value { font-weight: 560; }
.aiu-kpi-value.none { font-size: 16px; font-weight: 400; color: var(--ink-3); padding: 6px 0 5px; }
.aiu-kpi-sub { min-height: 1.4em; font-size: 11.5px; color: var(--ink-3); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.aiu-est { padding: 0 6px; border-radius: 999px; font-size: 10.5px; color: var(--ink-2); background: var(--aip-l2); cursor: help; }

.aiu-charts { display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(0, 1fr); gap: 14px; align-items: start; }
@media (max-width: 1100px) { .aiu-charts { grid-template-columns: minmax(0, 1fr); } }
.aiu-chart { position: relative; padding-top: 28px; }
.aiu-svg { display: block; overflow: visible; }
.aiu-grid line { stroke: rgba(var(--tint-rgb), 0.09); stroke-width: 1; }
.aiu-grid line.base { stroke: rgba(var(--tint-rgb), 0.22); }
.aiu-grid text, .aiu-xlab text { fill: var(--ink-3); font-size: 11px; font-variant-numeric: tabular-nums; }
.aiu-band { fill: rgba(var(--tint-rgb), 0.05); }
.aiu-bars g { transform-box: fill-box; transform-origin: 50% 100%; transform: scaleY(0); transition: transform 0.22s cubic-bezier(0.2, 0.7, 0.2, 1), opacity 0.15s ease; }
.aiu-svg.played .aiu-bars g { transform: scaleY(1); }
.aiu-bars.hovering g:not(.on) { opacity: 0.55; }
.aiu-bars rect { transition: y 0.18s ease, height 0.18s ease; }
.c-input { fill: var(--c-in); background: var(--c-in); }
.c-output { fill: var(--c-out); background: var(--c-out); }
.c-cache_read { fill: var(--c-cache); background: var(--c-cache); }
.c-cache_write { fill: var(--c-cache-w); background: var(--c-cache-w); }
.aiu-skel rect { fill: var(--aip-l2); animation: aiu-breathe 1.2s ease-in-out infinite; }
@keyframes aiu-breathe { 50% { opacity: 0.5; } }
.aiu-hit { fill: transparent; }
.aiu-empty { fill: var(--ink-3); font-size: 12.5px; }
.aiu-legend { position: absolute; top: 0; right: 0; display: flex; gap: 4px; }
.aiu-leg { display: inline-flex; align-items: center; gap: 5px; height: 22px; padding: 0 7px; border: 0; border-radius: 6px; background: transparent; color: var(--ink-2); font: inherit; font-size: 12px; cursor: pointer; }
.aiu-leg:hover { background: var(--aip-l2); }
.aiu-leg.off { opacity: 0.4; }
.aiu-leg i, .aiu-tip-row i { width: 7px; height: 7px; border-radius: 50%; }
.aiu-tip {
  position: absolute;
  top: 36px;
  z-index: 3;
  min-width: 168px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--card-solid);
  box-shadow: 0 4px 16px rgba(var(--shade-rgb), 0.12), 0 0 0 100vmax transparent;
  font-size: 12px;
  color: var(--ink);
  pointer-events: none;
}
.dark .aiu-tip { box-shadow: 0 4px 16px rgba(var(--shade-rgb), 0.4); }
.aiu-tip-head { display: flex; gap: 8px; margin-bottom: 4px; font-weight: 600; }
.dark .aiu-tip-head { font-weight: 560; }
.aiu-tip-row { display: flex; align-items: center; gap: 6px; line-height: 1.6; }
.aiu-tip-row.sub { color: var(--ink-3); }
.aiu-grow { flex: 1; }
.aiu-share { display: flex; flex-direction: column; gap: 2px; }
.aiu-share-empty { padding: 30px 0; text-align: center; font-size: 12.5px; color: var(--ink-3); }
.aiu-share-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.6fr) auto;
  align-items: center;
  gap: 10px;
  height: 30px;
  padding: 0 8px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--ink);
  font: inherit;
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
}
.aiu-share-row:disabled { cursor: default; }
.aiu-share-row:not(:disabled):hover { background: var(--aip-l1); }
.aiu-share-name { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.aiu-share-track { height: 6px; border-radius: 3px; background: var(--aip-l2); overflow: hidden; }
.aiu-share-track i { display: block; height: 100%; border-radius: 3px; background: var(--ink-3); width: 0; transition: width 0.24s cubic-bezier(0.2, 0.7, 0.2, 1); }
.aiu-share:not(.played) .aiu-share-track i { width: 0 !important; }
.aiu-share-track i.first { background: var(--accent); }
.aiu-share-val { font-size: 12px; color: var(--ink-3); white-space: nowrap; }
.aiu-share-val b { font-weight: 400; color: var(--ink); }

.aiu-table-head { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; flex-wrap: wrap; }
.aiu-table-head .aip-help { margin: 0; }
.aiu-table.el-table {
  --el-table-border-color: transparent;
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: transparent;
  --el-table-row-hover-bg-color: var(--aip-l1);
  --el-table-text-color: var(--ink);
  --el-table-header-text-color: var(--ink-3);
  font-size: 13px;
  background: transparent;
}
.aiu-table .el-table__inner-wrapper::before { display: none; }
.aiu-table th.el-table__cell, .aiu-table th.el-table__cell .cell { white-space: nowrap; font-weight: 500; font-size: 12px; }
.aiu-table th.el-table__cell { border-bottom: 1px solid rgba(var(--tint-rgb), 0.08) !important; }
.dark .aiu-table th.el-table__cell { font-weight: 460; }
.aiu-table td.el-table__cell { border: 0; padding: 7px 0; }
.aiu-table .cell { padding: 0 8px; font-variant-numeric: tabular-nums; }
.aiu-table th.el-table__cell:first-child .cell, .aiu-table td.el-table__cell:first-child .cell { padding-left: 2px; }
.aiu-table th.el-table__cell:last-child .cell, .aiu-table td.el-table__cell:last-child .cell { padding-right: 4px; }
.aiu-table .el-table__row.clickable { cursor: pointer; }
.aiu-table .el-table__row.gone td { color: var(--ink-3); }
.aiu-cell-sub { margin-left: 8px; font-size: 11.5px; color: var(--ink-3); }
.aiu-cell-none { color: var(--ink-3); }
.aiu-pricing { display: flex; align-items: center; gap: 16px; }
.aiu-pricing-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; font-size: 13px; color: var(--ink-2); }
.aiu-pricing-text .aiu-cell-sub { margin-left: 0; }
.aiu-kpi-link { font-size: 11.5px; }
.aiu-prune { display: flex; flex-direction: column; gap: 10px; }
.aiu-prune-opts { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; }
.aiu-prune-preview { margin: 0; font-size: 12.5px; color: var(--ink-2); }
@media (prefers-reduced-motion: reduce) {
  .aiu-bars g, .aiu-bars rect, .aiu-share-track i { transition: none; }
  .aiu-skel rect { animation: none; }
}
</style>
