<!-- Pricing of the usage estimate, opened from the usage tab: the models.dev sync (on/off, which providers, sync now),
     the price table (search, add, edit, delete) and a one-off import of single models.dev models. Prices are per
     1M tokens; the backend (pricing.py) matches them to the calls and prices older calls that had no price. -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  ElButton, ElCheckbox, ElDialog, ElInput, ElInputNumber, ElMessage, ElMessageBox, ElOption, ElRadioButton,
  ElRadioGroup, ElSelect, ElSwitch, ElTable, ElTableColumn,
} from 'element-plus'
import {
  PRICE_FIELDS, deletePrices, getPricing, importPrices, savePrice, searchCatalog, setPricingSync, syncPricing,
  type Catalog, type CatalogModel, type Currency, type PriceEntry, type PriceField, type Pricing,
} from './api'
import { rate, relative } from './format'
import { RC, t } from './sdk'
import Icon from './Icon.vue'

const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ changed: [] }>()

const data = ref<Pricing | null>(null)
const error = ref('')
const busy = ref(false)
const q = ref('')
let poll = 0
let dirty = false

async function load() {
  try {
    data.value = await getPricing()
    error.value = ''
  } catch (e) {
    error.value = RC().errorText(e)
  }
  // a sync the backend started in the background: look again until it is done
  window.clearTimeout(poll)
  if (open.value && data.value?.sync.running) poll = window.setTimeout(() => { void load(); dirty = true }, 1200)
}

watch(open, (o) => {
  if (o) { dirty = false; void load() } else {
    window.clearTimeout(poll)
    if (dirty) emit('changed')
  }
})

function changed(next: Pricing | null = null) {
  if (next) data.value = next
  dirty = true
}

const entries = computed(() => {
  const all = data.value?.entries ?? []
  const s = q.value.trim().toLowerCase()
  return s ? all.filter((e) => e.id.toLowerCase().includes(s) || e.name.toLowerCase().includes(s)) : all
})
const currencies = computed(() => [...new Set((data.value?.entries ?? []).map((e) => e.currency))])
const unitText = computed(() => t('pricing.list.unit', { c: (currencies.value.length ? currencies.value : ['USD']).join(' / ') }))
const cell = (e: PriceEntry, f: PriceField) => (e[f] === null || e[f] === undefined ? '—' : rate(e[f] as number, e.currency))

// ---- sync ----
const sync = computed(() => data.value?.sync)
const syncLine = computed(() => {
  const s = sync.value
  if (!s) return ''
  if (s.running) return t('pricing.sync.running')
  const names = s.providers.map((p) => s.provider_names[p] ?? p)
  const who = names.length > 3 ? t('pricing.sync.more', { names: names.slice(0, 3).join('、'), n: names.length }) : names.join('、')
  const when = s.last ? t('pricing.sync.last', { time: relative(s.last) }) : t('pricing.sync.never')
  return who ? `${when} · ${who}` : `${when} · ${t('pricing.sync.noProviders')}`
})

async function toggleSync(on: string | number | boolean) {
  try {
    changed(await setPricingSync({ enabled: !!on }))
    if (on && !data.value?.sync.last) await syncNow()
  } catch (e) {
    ElMessage.error(RC().errorText(e))
  }
}

async function syncNow() {
  busy.value = true
  try {
    const next = await syncPricing()
    changed(next)
    ElMessage.success(t('pricing.sync.done', { n: next.entries.length }))
  } catch (e) {
    ElMessage.error(RC().errorText(e))
    void load()
  } finally {
    busy.value = false
  }
}

async function unhide() {
  try {
    changed(await setPricingSync({ unhide: true }))
    if (data.value?.sync.enabled) await syncNow()
  } catch (e) {
    ElMessage.error(RC().errorText(e))
  }
}

// ---- add / edit ----
interface Form { id: string; name: string; currency: Currency; input: number | null; output: number | null; cache_read: number | null; cache_write: number | null }
const editOpen = ref(false)
const editing = ref<PriceEntry | null>(null)
const form = ref<Form>({ id: '', name: '', currency: 'USD', input: null, output: null, cache_read: null, cache_write: null })
const saving = ref(false)
const formOk = computed(() => !!form.value.id.trim() && PRICE_FIELDS.some((f) => typeof form.value[f] === 'number'))

function startEdit(e: PriceEntry | null) {
  editing.value = e
  form.value = e
    ? { id: e.id, name: e.name === e.id ? '' : e.name, currency: e.currency, input: e.input, output: e.output, cache_read: e.cache_read, cache_write: e.cache_write }
    : { id: q.value.trim(), name: '', currency: 'USD', input: null, output: null, cache_read: null, cache_write: null }
  editOpen.value = true
}

async function saveEdit() {
  if (!formOk.value) return
  saving.value = true
  try {
    const f = form.value
    const rates = Object.fromEntries(PRICE_FIELDS.map((k) => [k, typeof f[k] === 'number' ? f[k] : null]))
    await savePrice({ id: f.id.trim(), name: f.name.trim(), currency: f.currency, ...rates }, editing.value?.id)
    editOpen.value = false
    ElMessage.success(t('pricing.edit.saved'))
    changed()
    await load()
  } catch (e) {
    ElMessage.error(RC().errorText(e))
  } finally {
    saving.value = false
  }
}

async function remove(e: PriceEntry) {
  try {
    await ElMessageBox.confirm(t(e.source === 'sync' ? 'pricing.edit.deleteSynced' : 'pricing.edit.deleteText', { id: e.id }),
      t('pricing.edit.delete'), { type: 'warning', confirmButtonText: t('pricing.edit.delete'), cancelButtonText: t('pricing.edit.cancel') })
  } catch {
    return
  }
  try {
    await deletePrices([e.id])
    ElMessage.success(t('pricing.edit.deleted'))
    changed()
    await load()
  } catch (err) {
    ElMessage.error(RC().errorText(err))
  }
}

// ---- providers to sync ----
const provOpen = ref(false)
const provList = ref<Catalog['providers']>([])
const provPicked = ref<string[]>([])
const provQ = ref('')
const provLoading = ref(false)
const provSaving = ref(false)
const provShown = computed(() => {
  const s = provQ.value.trim().toLowerCase()
  const list = s ? provList.value.filter((p) => p.id.toLowerCase().includes(s) || p.name.toLowerCase().includes(s)) : provList.value
  // the picked ones first, in their order (it decides which price wins), then the rest by name
  const rank = (id: string) => { const i = provPicked.value.indexOf(id); return i < 0 ? 1e9 : i }
  return [...list].sort((a, b) => rank(a.id) - rank(b.id) || a.name.localeCompare(b.name))
})

async function openProviders() {
  provOpen.value = true
  provQ.value = ''
  provPicked.value = [...(sync.value?.providers ?? [])]
  provLoading.value = true
  try {
    provList.value = (await searchCatalog('', '', 1)).providers
  } catch (e) {
    ElMessage.error(RC().errorText(e))
    provOpen.value = false
  } finally {
    provLoading.value = false
  }
}

function toggleProvider(id: string, on: boolean) {
  provPicked.value = on ? [...provPicked.value.filter((p) => p !== id), id] : provPicked.value.filter((p) => p !== id)
}

async function saveProviders() {
  provSaving.value = true
  try {
    changed(await setPricingSync({ providers: provPicked.value }))
    provOpen.value = false
    if (data.value?.sync.enabled) await syncNow()
  } catch (e) {
    ElMessage.error(RC().errorText(e))
  } finally {
    provSaving.value = false
  }
}

// ---- import single models ----
const impOpen = ref(false)
const impQ = ref('')
const impProvider = ref('')
const impResult = ref<Catalog | null>(null)
const impPicked = ref<CatalogModel[]>([])
const impLoading = ref(false)
const importing = ref(false)
const IMPORT_LIMIT = 200
let impSeq = 0
let impTimer = 0

async function searchImport() {
  const n = ++impSeq
  impLoading.value = true
  try {
    const r = await searchCatalog(impQ.value.trim(), impProvider.value, IMPORT_LIMIT)
    if (n === impSeq) impResult.value = r
  } catch (e) {
    if (n === impSeq) ElMessage.error(RC().errorText(e))
  } finally {
    if (n === impSeq) impLoading.value = false
  }
}
watch([impQ, impProvider], () => {
  if (!impOpen.value) return
  window.clearTimeout(impTimer)
  impTimer = window.setTimeout(() => void searchImport(), 250)
})

function openImport() {
  impOpen.value = true
  impQ.value = q.value.trim()
  impProvider.value = ''
  impPicked.value = []
  void searchImport()
}

async function doImport() {
  if (!impPicked.value.length) return
  importing.value = true
  try {
    const r = await importPrices(impPicked.value.map((m) => ({ provider: m.provider, id: m.id })))
    changed(r)
    impOpen.value = false
    ElMessage.success(t('pricing.import.done', { n: r.imported }))
  } catch (e) {
    ElMessage.error(RC().errorText(e))
  } finally {
    importing.value = false
  }
}
const impRowKey = (m: CatalogModel) => `${m.provider}/${m.id}`
</script>

<template>
  <el-dialog v-model="open" :title="t('pricing.title')" width="min(980px, 96vw)" append-to-body class="aip-dialog aiup-dialog">
    <div class="aiup">
      <div v-if="error" class="aip-error">{{ error }}</div>

      <section class="aiup-sync">
        <Icon name="refresh" class="aiup-sync-ico" :class="{ spin: sync?.running || busy }" />
        <div class="aiup-sync-text">
          <div class="aiup-sync-title">
            {{ t('pricing.sync.title') }} · {{ sync?.enabled ? t('pricing.sync.on') : t('pricing.sync.off') }}
          </div>
          <div class="aiup-sync-sub" :class="{ bad: !!sync?.error && !sync?.running }">
            {{ sync?.error && !sync?.running ? t('pricing.sync.failed', { error: sync.error }) : syncLine }}
          </div>
        </div>
        <el-switch :model-value="!!sync?.enabled" :disabled="!sync" :aria-label="t('pricing.sync.title')" @update:model-value="toggleSync" />
        <el-button :disabled="!sync" @click="openProviders"><Icon name="sliders" />{{ t('pricing.sync.pick') }}</el-button>
        <el-button :loading="busy || !!sync?.running" :disabled="!sync || !sync.providers.length" @click="syncNow">
          <Icon v-if="!busy && !sync?.running" name="refresh" />{{ t('pricing.sync.now') }}
        </el-button>
      </section>
      <p class="aip-help">{{ t('pricing.sync.help') }}</p>

      <div class="aiup-bar">
        <span class="aiup-count">{{ t('pricing.list.title') }} · {{ data?.entries.length ?? 0 }}</span>
        <span class="aiup-unit">{{ unitText }}</span>
        <span class="aip-grow" />
        <el-input v-model="q" clearable :placeholder="t('pricing.list.search')" class="aiup-search">
          <template #prefix><Icon name="search" /></template>
        </el-input>
        <el-button @click="openImport"><Icon name="download" />{{ t('pricing.list.import') }}</el-button>
        <el-button type="primary" @click="startEdit(null)"><Icon name="plus" />{{ t('pricing.list.add') }}</el-button>
      </div>
      <div v-if="data?.hidden" class="aiup-hidden">
        {{ t('pricing.list.hidden', { n: data.hidden }) }}
        <button type="button" class="aip-link" @click="unhide">{{ t('pricing.list.restore') }}</button>
      </div>

      <el-table :data="entries" class="aiu-table aiup-table" max-height="52vh" row-key="id"
                :empty-text="data ? (q ? t('pricing.list.noMatch') : t('pricing.list.empty')) : '…'">
        <el-table-column :label="t('pricing.col.model')" min-width="300">
          <template #default="{ row }">
            <div class="aiup-model">
              <span class="aiup-name">{{ row.name }}</span>
              <span v-if="row.name !== row.id" class="aiup-id mono">{{ row.id }}</span>
              <span class="aip-tag" :class="{ on: row.source === 'manual' }"
                    :title="row.provider ? (data?.sync.provider_names[row.provider] ?? row.provider) : ''">{{ t(`pricing.source.${row.source}`) }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column v-for="f in PRICE_FIELDS" :key="f" :label="t(`pricing.col.${f}`)" align="right" min-width="92">
          <template #default="{ row }"><span :class="{ 'aiu-cell-none': row[f] === null }">{{ cell(row as PriceEntry, f) }}</span></template>
        </el-table-column>
        <el-table-column width="76" align="right">
          <template #default="{ row }">
            <button type="button" class="aip-ibtn" :title="t('pricing.edit.titleEdit')" @click="startEdit(row as PriceEntry)"><Icon name="edit" /></button>
            <button type="button" class="aip-ibtn" :title="t('pricing.edit.delete')" @click="remove(row as PriceEntry)"><Icon name="trash" /></button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- add / edit one price -->
    <el-dialog v-model="editOpen" :title="t(editing ? 'pricing.edit.titleEdit' : 'pricing.edit.titleAdd')" width="min(460px, 94vw)"
               append-to-body class="aip-dialog">
      <div class="aiup-form">
        <label class="aiup-field">
          <span>{{ t('pricing.edit.id') }}</span>
          <el-input v-model="form.id" maxlength="200" class="mono" placeholder="claude-opus-5-5" />
          <span class="aip-help">{{ t('pricing.edit.idHelp') }}</span>
        </label>
        <label class="aiup-field">
          <span>{{ t('pricing.edit.name') }}</span>
          <el-input v-model="form.name" maxlength="200" :placeholder="form.id || t('pricing.edit.namePlaceholder')" />
        </label>
        <div class="aiup-field">
          <span>{{ t('pricing.edit.currency') }}</span>
          <el-radio-group v-model="form.currency">
            <el-radio-button value="USD">USD</el-radio-button>
            <el-radio-button value="CNY">CNY</el-radio-button>
          </el-radio-group>
        </div>
        <div class="aiup-rates">
          <label v-for="f in PRICE_FIELDS" :key="f" class="aiup-rate">
            <span>{{ t(`pricing.col.${f}`) }}</span>
            <el-input-number v-model="form[f]" :min="0" :max="1000000" :controls="false" :value-on-clear="null" placeholder="—" />
          </label>
        </div>
        <p class="aip-help">{{ t('pricing.edit.unit', { c: form.currency }) }}<template v-if="editing && editing.source !== 'manual'"> {{ t('pricing.edit.manualNote') }}</template></p>
        <div class="aip-row"><span class="aip-grow" />
          <el-button text @click="editOpen = false">{{ t('pricing.edit.cancel') }}</el-button>
          <el-button type="primary" :disabled="!formOk" :loading="saving" @click="saveEdit">{{ t('pricing.edit.save') }}</el-button>
        </div>
      </div>
    </el-dialog>

    <!-- providers to sync -->
    <el-dialog v-model="provOpen" :title="t('pricing.providers.title')" width="min(520px, 94vw)" append-to-body class="aip-dialog">
      <div class="aiup-form">
        <p class="aip-help">{{ t('pricing.providers.help') }}</p>
        <el-input v-model="provQ" clearable :placeholder="t('pricing.providers.search')">
          <template #prefix><Icon name="search" /></template>
        </el-input>
        <div class="aiup-provs">
          <div v-if="provLoading" class="aip-empty">{{ t('pricing.providers.loading') }}</div>
          <label v-for="p in provShown" v-else :key="p.id" class="aiup-prov">
            <el-checkbox :model-value="provPicked.includes(p.id)" @update:model-value="(on: string | number | boolean) => toggleProvider(p.id, !!on)" />
            <span class="aiup-prov-name">{{ p.name }}</span>
            <span class="aiup-id mono">{{ p.id }}</span>
            <span class="aip-grow" />
            <span v-if="provPicked.includes(p.id)" class="aip-tag on">{{ provPicked.indexOf(p.id) + 1 }}</span>
            <span class="aiup-prov-n">{{ t('pricing.providers.models', { n: p.count }) }}</span>
          </label>
        </div>
        <div class="aip-row">
          <span class="aip-dim">{{ t('pricing.providers.selected', { n: provPicked.length }) }}</span>
          <span class="aip-grow" />
          <el-button text @click="provOpen = false">{{ t('pricing.edit.cancel') }}</el-button>
          <el-button type="primary" :loading="provSaving" @click="saveProviders">{{ t('pricing.edit.save') }}</el-button>
        </div>
      </div>
    </el-dialog>

    <!-- import single models -->
    <el-dialog v-model="impOpen" :title="t('pricing.import.title')" width="min(900px, 96vw)" append-to-body class="aip-dialog">
      <div class="aiup-form">
        <p class="aip-help">{{ t('pricing.import.help') }}</p>
        <div class="aip-row">
          <el-input v-model="impQ" clearable :placeholder="t('pricing.import.search')">
            <template #prefix><Icon name="search" /></template>
          </el-input>
          <el-select v-model="impProvider" clearable filterable :placeholder="t('pricing.import.provider')" class="aiup-provsel">
            <el-option v-for="p in impResult?.providers ?? []" :key="p.id" :value="p.id" :label="p.name" />
          </el-select>
        </div>
        <el-table :data="impResult?.models ?? []" class="aiu-table" max-height="46vh" :row-key="impRowKey"
                  :empty-text="impLoading ? '…' : t('pricing.list.noMatch')" @selection-change="(rows: CatalogModel[]) => (impPicked = rows)">
          <el-table-column type="selection" width="36" reserve-selection />
          <el-table-column :label="t('pricing.col.model')" min-width="260">
            <template #default="{ row }">
              <div class="aiup-model">
                <span class="aiup-name">{{ row.name }}</span>
                <span class="aiup-id mono">{{ row.id }}</span>
                <span v-if="row.added" class="aip-tag">{{ t('pricing.import.added') }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column :label="t('pricing.import.providerCol')" prop="provider_name" min-width="120" show-overflow-tooltip />
          <el-table-column v-for="f in PRICE_FIELDS" :key="f" :label="t(`pricing.col.${f}`)" align="right" min-width="84">
            <template #default="{ row }"><span :class="{ 'aiu-cell-none': row[f] === null }">{{ row[f] === null ? '—' : rate(row[f], 'USD') }}</span></template>
          </el-table-column>
        </el-table>
        <div class="aip-row">
          <span class="aip-dim">
            <template v-if="impResult?.fetched">{{ t('pricing.import.fetched', { time: relative(impResult.fetched) }) }}</template>
            <template v-if="(impResult?.models.length ?? 0) >= IMPORT_LIMIT"> · {{ t('pricing.import.limit', { n: IMPORT_LIMIT }) }}</template>
          </span>
          <span class="aip-grow" />
          <el-button text @click="impOpen = false">{{ t('pricing.edit.cancel') }}</el-button>
          <el-button type="primary" :disabled="!impPicked.length" :loading="importing" @click="doImport">
            {{ t('pricing.import.confirm', { n: impPicked.length }) }}
          </el-button>
        </div>
      </div>
    </el-dialog>
  </el-dialog>
</template>

<style>
/* pricing dialog (aiup-), opened from the usage tab */
.aiup { display: flex; flex-direction: column; gap: 10px; font-variant-numeric: tabular-nums; }
.aiup .aip-help { margin: -4px 2px 0; }
.aiup-sync { display: flex; align-items: center; gap: 12px; padding: 14px 16px; border-radius: 14px; background: var(--aip-l2); }
.aiup-sync .el-button { margin: 0; }
.aiup-sync-ico { flex: none; font-size: 18px; color: var(--ink-2); margin-right: 0 !important; }
.aiup-sync-ico.spin { animation: aiup-spin 1s linear infinite; }
@keyframes aiup-spin { to { rotate: 360deg; } }
.aiup-sync-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.aiup-sync-title { font-size: 14px; font-weight: 600; color: var(--ink); }
.dark .aiup-sync-title { font-weight: 560; }
.aiup-sync-sub { font-size: 12px; color: var(--ink-3); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.aiup-sync-sub.bad { color: var(--bad); }
.aiup-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 6px; }
.aiup-bar .el-button { margin: 0; }
.aiup-count { font-size: 14px; font-weight: 600; color: var(--ink); }
.dark .aiup-count { font-weight: 560; }
.aiup-unit { font-size: 12px; color: var(--ink-3); }
.aiup-search { width: 240px; }
.aiup-hidden { font-size: 12px; color: var(--ink-3); }
.aiup-model { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.aiup-name { color: var(--ink); white-space: nowrap; }
.aiup-id { min-width: 0; font-size: 11.5px; color: var(--ink-3); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.aiup-model .aip-tag { margin-left: auto; }
.aiup-table .aip-ibtn .aia-i { margin-right: 0; }
.aiup-form { display: flex; flex-direction: column; gap: 12px; }
.aiup-form .aip-help { margin: 0; }
.aiup-field { display: flex; flex-direction: column; gap: 5px; font-size: 12.5px; color: var(--ink-2); }
.aiup-field .el-radio-group { align-self: flex-start; }
.aiup-rates { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 14px; }
.aiup-rate { display: flex; flex-direction: column; gap: 5px; font-size: 12.5px; color: var(--ink-2); }
.aiup-rate .el-input-number { width: 100%; }
.aiup-rate .el-input__inner { text-align: right; }
.aiup-provs { display: flex; flex-direction: column; max-height: 46vh; overflow-y: auto; margin: 0 -4px; }
.aiup-prov { display: flex; align-items: center; gap: 8px; padding: 3px 6px; border-radius: 8px; font-size: 13px; cursor: pointer; }
.aiup-prov:hover { background: var(--aip-l1); }
.aiup-prov-name { color: var(--ink); white-space: nowrap; }
.aiup-prov-n { font-size: 11.5px; color: var(--ink-3); white-space: nowrap; }
.aiup-provsel { width: 220px; flex: none; }
@media (prefers-reduced-motion: reduce) { .aiup-sync-ico.spin { animation: none; } }
</style>
