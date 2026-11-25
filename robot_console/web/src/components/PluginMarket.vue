<!-- Plugin market: the plugins of the index on GitHub (plugins.market.index_urls), installed into the first plugin
     folder after a size and SHA-256 check; also installs a plugin zip chosen on this computer. Installed plugins
     start disabled like every new plugin. Backend: robot_console/plugin_market.py. -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Download, Link, Refresh, Upload } from '@element-plus/icons-vue'
import { ApiError, isReadOnlyPreview } from '@/http'
import { pick } from '@/i18n'
import { errorText } from '@/messages'
import { fmtBytes } from '@/format'
import GlassPanel from '@/components/GlassPanel.vue'
import { pluginApi } from '@/plugins/api'
import type { MarketEntry, MarketResult, MarketStatus, MarketView } from '@/plugins/types'

const emit = defineEmits<{ changed: []; uninstall: [id: string, name: string] }>()
const { t } = useI18n()
const preview = isReadOnlyPreview()
const view = ref<MarketView | null>(null)
const loadError = ref('')
const refreshing = ref(false)
const busy = ref('')
const fileInput = ref<HTMLInputElement | null>(null)

const STATUS_CHIP: Record<MarketStatus, string> = {
  available: 'info', installed: 'success', update: 'warning', newer: 'info', incompatible: 'danger',
}
const entries = computed(() => view.value?.plugins ?? [])
const fetched = computed(() => {
  const s = view.value?.fetched
  if (!s) return ''
  const d = new Date(s * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
})
const errorMsg = computed(() => (view.value?.error ? errorText(new ApiError(view.value.error)) : ''))
const canInstall = (e: MarketEntry) => ['available', 'update', 'newer'].includes(e.status)
  || (e.status === 'installed' && e.installed?.origin !== 'manual')
const removable = (e: MarketEntry) => e.installed?.origin === 'market' || e.installed?.origin === 'file'
const actionText = (e: MarketEntry) => t(`pluginsPage.market.action.${e.status === 'installed' ? 'reinstall'
  : e.status === 'update' ? 'update' : e.status === 'newer' ? 'downgrade' : 'install'}`)
const short = (sha: string) => `${sha.slice(0, 12)}…`

async function load(refresh = false) {
  refreshing.value = refresh
  try {
    view.value = await pluginApi.market(refresh)
    loadError.value = ''
  } catch (e) {
    loadError.value = errorText(e)
  } finally {
    refreshing.value = false
  }
}

function done(out: MarketResult, name: string, version: string) {
  view.value = out.market
  const p = out.plugin
  ElMessage.success(t(p?.restart_needed && p.state === 'active' ? 'pluginsPage.market.updated' : 'pluginsPage.market.installed',
    { name, version }))
  emit('changed')
}

async function install(e: MarketEntry) {
  const name = pick(e.name)
  try {
    await ElMessageBox.confirm(
      t('pluginsPage.market.confirmText', { name, version: e.version, url: e.url, sha: e.sha256 }),
      t('pluginsPage.market.confirmTitle'),
      { type: 'warning', confirmButtonText: actionText(e), cancelButtonText: t('pluginsPage.confirm.cancel') },
    )
  } catch {
    return
  }
  busy.value = e.id
  try {
    done(await pluginApi.marketInstall(e.id), name, e.version)
  } catch (err) {
    ElMessage.error(errorText(err))
    await load()
  } finally {
    busy.value = ''
  }
}

async function upload(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  busy.value = '#file'
  try {
    const out = await pluginApi.marketUpload(file)
    const p = out.plugin
    done(out, p?.manifest ? pick(p.manifest.name) : (p?.id ?? file.name), p?.manifest?.version ?? '')
    if (!out.verified) ElMessage.warning(t('pluginsPage.market.unverified', { sha: out.sha256 ?? '' }))
  } catch (err) {
    ElMessage.error(errorText(err))
  } finally {
    busy.value = ''
  }
}

async function chooseFile() {
  try {
    await ElMessageBox.confirm(t('pluginsPage.market.fileText'), t('pluginsPage.market.fromFile'), {
      type: 'warning', confirmButtonText: t('pluginsPage.market.chooseFile'), cancelButtonText: t('pluginsPage.confirm.cancel'),
    })
  } catch {
    return
  }
  fileInput.value?.click()
}

defineExpose({ load })
onMounted(async () => {
  await load(false)
  if (!preview && !loadError.value && view.value && !view.value.error) void load(true)
})
</script>

<template>
  <div class="market">
    <GlassPanel :title="t('pluginsPage.market.title')" :tip="preview ? undefined : t('pluginsPage.market.tip')">
      <template v-if="!preview" #extra>
        <el-button size="small" :icon="Refresh" :loading="refreshing" @click="load(true)">{{ t('pluginsPage.market.refresh') }}</el-button>
        <el-button size="small" :icon="Upload" :loading="busy === '#file'" :disabled="busy !== '' && busy !== '#file'" @click="chooseFile">
          {{ t('pluginsPage.market.fromFile') }}
        </el-button>
        <input ref="fileInput" type="file" accept=".zip,application/zip" class="file" @change="upload">
      </template>
      <div class="info">
        <div class="row">
          <span class="label">{{ t('pluginsPage.market.source') }}</span>
          <span class="mono path">{{ view?.source ?? (view?.index_urls[0] ?? '') }}</span>
        </div>
        <div v-if="fetched || view?.updated" class="row">
          <template v-if="!preview">
            <span class="label">{{ t('pluginsPage.market.fetched') }}</span>
            <span class="mono">{{ fetched }}</span>
          </template>
          <span v-if="view?.updated" class="dim">{{ t('pluginsPage.market.updated_at', { date: view.updated }) }}</span>
        </div>
        <div v-if="view?.install_dir" class="row">
          <span class="label">{{ t('pluginsPage.market.installDir') }}</span>
          <span class="mono path">{{ view.install_dir }}</span>
        </div>
        <p class="trust">{{ t(preview ? 'pluginsPage.preview.marketHint' : 'pluginsPage.market.trust') }}</p>
        <el-alert v-if="loadError" type="error" :closable="false" show-icon :title="loadError" />
        <el-alert
          v-if="errorMsg" :type="view?.stale ? 'warning' : 'error'" :closable="false" show-icon
          :title="view?.stale ? t('pluginsPage.market.stale') : t('pluginsPage.market.unreachable')" :description="errorMsg"
        />
        <el-alert
          v-if="view?.skipped.length" type="warning" :closable="false" show-icon
          :title="t('pluginsPage.market.skipped', { n: view.skipped.length })"
          :description="view.skipped.map((s) => errorText(new ApiError(s))).join('；')"
        />
      </div>
    </GlassPanel>

    <div v-if="entries.length" class="cards">
      <GlassPanel v-for="e in entries" :key="e.id" :title="pick(e.name)">
        <template #extra>
          <span class="chip" :class="STATUS_CHIP[e.status]">{{ t(`pluginsPage.market.status.${e.status}`, { version: e.installed?.version ?? '' }) }}</span>
        </template>
        <div class="meta mono">{{ [e.id, `v${e.version}`, e.author, e.license, fmtBytes(e.size)].filter(Boolean).join(' · ') }}</div>
        <p v-if="pick(e.description)" class="desc">{{ pick(e.description) }}</p>
        <div v-if="e.upstream" class="line">
          <span class="label">{{ t('pluginsPage.market.basedOn') }}</span>
          <a :href="e.upstream" target="_blank" rel="noopener noreferrer" class="mono">{{ e.upstream.replace(/^https:\/\/github\.com\//, '') }}</a>
        </div>
        <div v-if="e.requirements.length" class="line">
          <span class="label">{{ t('pluginsPage.market.packages') }}</span>
          <span class="mono reqs">{{ e.requirements.join('  ') }}</span>
        </div>
        <div class="line">
          <span class="label">SHA-256</span>
          <span class="mono" :title="e.sha256">{{ short(e.sha256) }}</span>
          <span v-if="e.published" class="dim">{{ t('pluginsPage.market.published', { date: e.published }) }}</span>
        </div>
        <div v-if="e.installed" class="line">
          <span class="label">{{ t('pluginsPage.market.local') }}</span>
          <span>{{ t(`pluginsPage.market.origin.${e.installed.origin}`, { version: e.installed.version }) }}</span>
          <span class="mono dim">{{ e.installed.dir }}</span>
        </div>
        <div v-if="e.status === 'incompatible'" class="warn">
          {{ t('pluginsPage.market.needsApi', { api: e.api, supported: view?.supported_apis.join(', ') ?? '' }) }}
        </div>
        <div v-else-if="e.installed?.origin === 'manual'" class="warn">{{ t('pluginsPage.market.manual') }}</div>
        <div class="actions">
          <el-button
            v-if="!preview && canInstall(e)" size="small" :icon="Download" :type="e.status === 'installed' ? 'default' : 'primary'"
            :loading="busy === e.id" :disabled="busy !== '' && busy !== e.id" @click="install(e)"
          >
            {{ actionText(e) }}
          </el-button>
          <el-button v-if="!preview && removable(e)" size="small" :icon="Delete" :disabled="busy !== ''" @click="emit('uninstall', e.id, pick(e.name))">
            {{ t('pluginsPage.market.uninstall') }}
          </el-button>
          <a v-if="e.source || e.homepage" :href="e.source || e.homepage" target="_blank" rel="noopener noreferrer">
            <el-button size="small" :icon="Link">{{ t('pluginsPage.market.sourceCode') }}</el-button>
          </a>
        </div>
      </GlassPanel>
    </div>
    <GlassPanel v-else-if="view && !errorMsg">
      <el-empty :image-size="80" :description="t('pluginsPage.market.empty')" />
    </GlassPanel>
  </div>
</template>

<style scoped>
.market {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.file {
  display: none;
}
.info {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.row,
.line {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 10px;
  font-size: 13px;
}
.line {
  margin-top: 6px;
  font-size: 12.5px;
}
.label {
  color: var(--ink-3);
}
.dim {
  font-size: 12px;
  color: var(--ink-3);
}
.path,
.reqs {
  font-size: 12px;
  color: var(--ink-2);
  overflow-wrap: anywhere;
}
.trust {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--ink-2);
}
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(380px, 100%), 1fr));
  gap: 14px;
}
.meta {
  font-size: 12px;
  color: var(--ink-3);
}
.desc {
  margin: 10px 0 6px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink);
}
.line a {
  color: var(--accent);
  font-size: 12px;
}
.warn {
  margin-top: 8px;
  font-size: 12px;
  color: color-mix(in srgb, var(--warn) 70%, var(--ink));
}
.market :deep(.el-alert--warning.is-light .el-alert__title),
.market :deep(.el-alert--warning.is-light .el-alert__icon),
.market :deep(.el-alert--warning.is-light .el-alert__description) {
  color: color-mix(in srgb, var(--warn) 70%, var(--ink));
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.actions .el-button + .el-button {
  margin-left: 0;
}
</style>
