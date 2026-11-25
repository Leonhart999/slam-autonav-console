<!-- Plugins page: the plugins found in the plugin folders, their state, the enable switch (applies at the next
     console start), settings and folders; a second tab is the plugin market (components/PluginMarket.vue).
     Design: docs/plugins.md. -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { Box, Delete, Document, FolderOpened, Link, Refresh, Setting, View } from '@element-plus/icons-vue'
import { ApiError, isReadOnlyPreview } from '@/http'
import { pick } from '@/i18n'
import { errorText } from '@/messages'
import { openFolder } from '@/openFolder'
import GlassPanel from '@/components/GlassPanel.vue'
import InfoTip from '@/components/InfoTip.vue'
import PluginMarket from '@/components/PluginMarket.vue'
import PluginSettingsDialog from '@/components/PluginSettingsDialog.vue'
import { pluginApi } from '@/plugins/api'
import { pagePath, registry } from '@/plugins/host'
import type { PluginInfo, PluginList, PluginState, PythonState } from '@/plugins/types'

const { t } = useI18n()
const preview = isReadOnlyPreview()
const route = useRoute()
const router = useRouter()
const tab = ref<'installed' | 'market'>(route.query.tab === 'market' ? 'market' : 'installed')
watch(tab, (v) => router.replace({ query: { ...route.query, tab: v === 'market' ? 'market' : undefined } }))
watch(() => route.query.tab, (v) => { tab.value = v === 'market' ? 'market' : 'installed' })
const market = ref<InstanceType<typeof PluginMarket> | null>(null)
const removeOpen = ref(false)
const removeFor = ref<{ id: string; name: string } | null>(null)
const removePurge = ref(false)
const removing = ref(false)
const list = ref<PluginList | null>(null)
const loadError = ref('')
const scanning = ref(false)
const busy = ref('')
const settingsOpen = ref(false)
const settingsFor = ref<PluginInfo | null>(null)

const plugins = computed(() => list.value?.plugins ?? [])
const marketCount = ref(0)
if (preview) void pluginApi.market().then(view => { marketCount.value = view.plugins.length }).catch(() => {})
const pending = computed(() => plugins.value.filter((p) => p.restart_needed))
const STATE_CHIP: Record<PluginState, string> = {
  active: 'success', disabled: 'info', failed: 'danger', invalid: 'danger', duplicate: 'warning', incompatible: 'warning',
}
const PY_CHIP: Record<PythonState, string> = {
  ready: 'success', missing: 'info', installing: 'warning', outdated: 'warning', failed: 'danger',
}
const pyBusy = ref('')
const logOpen = ref(false)
const logFor = ref<PluginInfo | null>(null)
const logText = ref('')
let poll: ReturnType<typeof setTimeout> | null = null
const usable = (p: PluginInfo) => !!p.manifest && !['invalid', 'duplicate', 'incompatible'].includes(p.state)
const nameOf = (p: PluginInfo) => (p.manifest ? pick(p.manifest.name) : p.id)
const issueText = (p: PluginInfo) => (p.issue ? errorText(new ApiError(p.issue)) : '')
const frontendIssue = (p: PluginInfo) => {
  const issue = registry.issues[p.id]
  return issue ? t(`pluginsPage.load.${issue.code}`, issue.params) : ''
}
const firstPage = (p: PluginInfo) => registry.pages.find((q) => q.pluginId === p.id)
const parts = (p: PluginInfo) => [
  p.id, p.manifest ? `v${p.manifest.version}` : '', p.manifest?.author ?? '', p.manifest?.license ?? '',
].filter(Boolean)
const kinds = (p: PluginInfo) => [p.has_backend ? t('pluginsPage.kind.backend') : '',
  p.has_frontend ? t('pluginsPage.kind.frontend') : '', p.settings.length ? t('pluginsPage.kind.settings') : '']
  .filter(Boolean).join(' · ')

async function load() {
  const before = new Map(plugins.value.map((p) => [p.id, p.python?.state]))
  try {
    list.value = await pluginApi.list()
    loadError.value = ''
  } catch (e) {
    loadError.value = errorText(e)
  }
  for (const p of plugins.value) {
    if (before.get(p.id) !== 'installing' || !p.python || p.python.state === 'installing') continue
    if (p.python.state === 'ready') ElMessage.success(t('pluginsPage.python.done', { name: nameOf(p) }))
    else if (p.python.state === 'failed') {
      ElMessage.error(t('pluginsPage.python.failed', { name: nameOf(p), error: p.python.error ?? '' }))
    }
  }
  schedulePoll()
}

/** While a venv is being installed the list is refreshed every 2 s */
function schedulePoll() {
  if (poll) clearTimeout(poll)
  poll = plugins.value.some((p) => p.python?.state === 'installing') ? setTimeout(load, 2000) : null
}

function replace(updated: PluginInfo) {
  if (list.value) list.value = { ...list.value, plugins: list.value.plugins.map((q) => (q.id === updated.id ? updated : q)) }
}

async function installPython(p: PluginInfo) {
  pyBusy.value = p.id
  try {
    replace(await pluginApi.installPython(p.id))
    ElMessage.info(t('pluginsPage.python.started', { name: nameOf(p) }))
    schedulePoll()
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    pyBusy.value = ''
  }
}

async function removePython(p: PluginInfo) {
  try {
    await ElMessageBox.confirm(t('pluginsPage.python.removeConfirm', { name: nameOf(p) }), t('pluginsPage.python.remove'), {
      type: 'warning', confirmButtonText: t('pluginsPage.python.remove'), cancelButtonText: t('pluginsPage.confirm.cancel'),
    })
  } catch {
    return
  }
  pyBusy.value = p.id
  try {
    replace(await pluginApi.removePython(p.id))
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    pyBusy.value = ''
  }
}

async function showLog(p: PluginInfo) {
  logFor.value = p
  logText.value = ''
  logOpen.value = true
  try {
    logText.value = (await pluginApi.pythonLog(p.id)).text
  } catch (e) {
    logText.value = errorText(e)
  }
}

async function rescan() {
  scanning.value = true
  try {
    list.value = await pluginApi.rescan()
    ElMessage.success(t('pluginsPage.rescanned', { n: list.value.plugins.length }))
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    scanning.value = false
  }
}

async function toggle(p: PluginInfo, enabled: boolean) {
  // Confirm before code that is not running yet will run (re-enabling a plugin that still runs needs no warning)
  if (enabled && p.state !== 'active') {
    try {
      await ElMessageBox.confirm(t('pluginsPage.confirm.text', { name: nameOf(p) }), t('pluginsPage.confirm.title'), {
        type: 'warning', confirmButtonText: t('pluginsPage.confirm.ok'), cancelButtonText: t('pluginsPage.confirm.cancel'),
      })
    } catch {
      return
    }
  }
  busy.value = p.id
  try {
    const updated = await pluginApi.setEnabled(p.id, enabled)
    if (list.value) list.value = { ...list.value, plugins: list.value.plugins.map((q) => (q.id === p.id ? updated : q)) }
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    busy.value = ''
  }
}

function askUninstall(id: string, name: string) {
  removeFor.value = { id, name }
  removePurge.value = false
  removeOpen.value = true
}

async function uninstall() {
  if (!removeFor.value) return
  removing.value = true
  try {
    await pluginApi.uninstall(removeFor.value.id, removePurge.value)
    ElMessage.success(t('pluginsPage.market.removed', { name: removeFor.value.name }))
    removeOpen.value = false
    await load()
    await market.value?.load()
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    removing.value = false
  }
}

function openSettings(p: PluginInfo) {
  settingsFor.value = p
  settingsOpen.value = true
}

onMounted(load)
onUnmounted(() => {
  if (poll) clearTimeout(poll)
})
</script>

<template>
  <div class="plugins-page">
    <GlassPanel :title="t(preview ? 'pluginsPage.preview.title' : 'pluginsPage.title')" :tip="preview ? undefined : t('pluginsPage.tip')">
      <template v-if="!preview" #extra>
        <el-button size="small" :icon="Refresh" :loading="scanning" @click="rescan">{{ t('pluginsPage.rescan') }}</el-button>
        <el-button size="small" :icon="FolderOpened" :disabled="!list?.paths.length" @click="openFolder(list?.paths[0])">
          {{ t('pluginsPage.openDir') }}
        </el-button>
      </template>
      <div class="intro">
        <div v-if="!preview" class="dirs">
          <span class="label">{{ t('pluginsPage.dirs') }}</span>
          <span v-for="d in list?.dirs ?? []" :key="d" class="mono path">{{ d }}</span>
        </div>
        <p class="trust">{{ t(preview ? 'pluginsPage.preview.hint' : 'pluginsPage.trust') }}</p>
        <el-alert v-if="loadError" type="error" :closable="false" show-icon :title="loadError" />
        <el-alert v-if="list?.disabled_by_env" type="warning" :closable="false" show-icon :title="t('pluginsPage.envDisabled')" />
        <el-alert
          v-if="pending.length" type="warning" :closable="false" show-icon
          :title="t('pluginsPage.restart', { names: pending.map(nameOf).join(t('pluginsPage.sep')) })"
        />
      </div>
    </GlassPanel>

    <el-radio-group v-model="tab" size="small" class="tabs">
      <el-radio-button value="installed">{{ t(preview ? 'pluginsPage.preview.bundled' : 'pluginsPage.tabs.installed', { n: plugins.length }) }}</el-radio-button>
      <el-radio-button value="market">{{ preview ? t('pluginsPage.preview.market', { n: marketCount }) : t('pluginsPage.tabs.market') }}</el-radio-button>
    </el-radio-group>

    <PluginMarket v-if="tab === 'market'" ref="market" @changed="load" @uninstall="askUninstall" />
    <div v-else-if="plugins.length" class="cards">
      <GlassPanel v-for="p in plugins" :key="p.path" :title="nameOf(p)">
        <template #extra>
          <template v-if="!preview">
            <span class="chip" :class="STATE_CHIP[p.state]">{{ t(`pluginsPage.state.${p.state}`) }}</span>
            <el-switch
              :model-value="p.enabled" :disabled="!usable(p) || (busy !== '' && busy !== p.id)" :loading="busy === p.id"
              :aria-label="t('pluginsPage.enable')" :title="t('pluginsPage.enable')"
              @update:model-value="(v: string | number | boolean) => toggle(p, v === true)"
            />
          </template>
        </template>
        <div class="meta mono">{{ parts(p).join(' · ') }}</div>
        <div v-if="kinds(p)" class="kinds">{{ kinds(p) }}</div>
        <p v-if="p.manifest && pick(p.manifest.description)" class="desc">{{ pick(p.manifest.description) }}</p>
        <div v-if="!preview" class="path mono" :title="p.dir">{{ p.dir }}</div>
        <el-alert v-if="p.issue" type="error" :closable="false" :title="issueText(p)" class="issue" />
        <el-alert v-if="frontendIssue(p)" type="warning" :closable="false" :title="frontendIssue(p)" class="issue" />
        <div v-if="p.restart_needed" class="restart">
          {{ p.pending_version != null && p.state === 'active'
            ? (p.pending_version ? t('pluginsPage.willUpdate', { version: p.pending_version }) : t('pluginsPage.willRemove'))
            : t(p.enabled ? 'pluginsPage.willLoad' : 'pluginsPage.willUnload') }}
        </div>
        <div v-if="p.python" class="python">
          <div class="py-head">
            <span class="label">{{ t('pluginsPage.python.title') }}</span>
            <InfoTip :content="t('pluginsPage.python.tip', { env: p.python.env })" />
            <span class="chip" :class="PY_CHIP[p.python.state]">{{ t(`pluginsPage.python.state.${p.python.state}`) }}</span>
            <span v-if="p.python.state === 'ready'" class="py-info">
              {{ t('pluginsPage.python.installed', { n: p.python.packages.length, python: p.python.python ?? '?' }) }}
            </span>
          </div>
          <div class="reqs mono">{{ p.python.requirements.join('  ') }}</div>
          <div v-if="p.python.state === 'outdated'" class="restart">{{ t('pluginsPage.python.outdatedHint') }}</div>
          <el-alert v-if="p.python.state === 'failed' && p.python.error" type="error" :closable="false" :title="p.python.error" class="issue" />
          <div class="actions">
            <el-button
              size="small" :icon="Box" :type="p.python.state === 'ready' ? 'default' : 'primary'"
              :loading="p.python.state === 'installing' || pyBusy === p.id"
              :disabled="p.python.state === 'installing' || (pyBusy !== '' && pyBusy !== p.id)"
              @click="installPython(p)"
            >
              {{ t(p.python.state === 'missing' || p.python.state === 'installing'
                ? 'pluginsPage.python.install' : 'pluginsPage.python.reinstall') }}
            </el-button>
            <el-button size="small" :icon="Document" @click="showLog(p)">{{ t('pluginsPage.python.log') }}</el-button>
            <el-button
              v-if="p.python.state === 'ready' || p.python.state === 'outdated'" size="small" :icon="Delete"
              :disabled="pyBusy !== ''" @click="removePython(p)"
            >
              {{ t('pluginsPage.python.remove') }}
            </el-button>
          </div>
        </div>
        <div v-if="preview && p.manifest?.homepage" class="actions">
          <a :href="p.manifest.homepage" target="_blank" rel="noopener noreferrer">
            <el-button size="small" :icon="Link">{{ t('pluginsPage.preview.source') }}</el-button>
          </a>
        </div>
        <div v-if="!preview" class="actions">
          <el-button size="small" :icon="Setting" :disabled="!p.manifest || !p.settings.length" @click="openSettings(p)">
            {{ t('pluginsPage.settingsBtn') }}
          </el-button>
          <router-link v-if="firstPage(p)" :to="pagePath(firstPage(p)!)">
            <el-button size="small" :icon="View">{{ t('pluginsPage.openPage') }}</el-button>
          </router-link>
          <el-button size="small" :icon="FolderOpened" @click="openFolder(p.path)">{{ t('pluginsPage.openPluginDir') }}</el-button>
          <a v-if="p.manifest?.homepage" :href="p.manifest.homepage" target="_blank" rel="noopener noreferrer">
            <el-button size="small" :icon="Link">{{ t('pluginsPage.homepage') }}</el-button>
          </a>
          <el-button v-if="p.origin && p.pending_version !== ''" size="small" :icon="Delete" @click="askUninstall(p.id, nameOf(p))">
            {{ t('pluginsPage.market.uninstall') }}
          </el-button>
        </div>
      </GlassPanel>
    </div>
    <GlassPanel v-else-if="list">
      <el-empty :image-size="80" :description="t('pluginsPage.empty', { dir: list.dirs[0] ?? '' })" />
    </GlassPanel>

    <PluginSettingsDialog v-model="settingsOpen" :plugin="settingsFor" />
    <el-dialog v-model="removeOpen" :title="t('pluginsPage.market.removeTitle', { name: removeFor?.name ?? '' })" width="min(520px, 94vw)">
      <p class="remove-text">{{ t('pluginsPage.market.removeText') }}</p>
      <el-checkbox v-model="removePurge">{{ t('pluginsPage.market.purge') }}</el-checkbox>
      <template #footer>
        <el-button @click="removeOpen = false">{{ t('pluginsPage.confirm.cancel') }}</el-button>
        <el-button type="danger" :loading="removing" @click="uninstall">{{ t('pluginsPage.market.uninstall') }}</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="logOpen" :title="t('pluginsPage.python.logTitle', { name: logFor ? nameOf(logFor) : '' })" width="min(860px, 94vw)">
      <pre class="py-log mono">{{ logText || t('pluginsPage.python.logEmpty') }}</pre>
      <template #footer>
        <el-button @click="logOpen = false">{{ t('pluginsPage.python.close') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.plugins-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.tabs {
  align-self: flex-start;
}
.remove-text {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink);
}
.intro {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.dirs {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 12px;
  font-size: 13px;
}
.label {
  color: var(--ink-3);
}
.path {
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
.kinds {
  margin-top: 4px;
  font-size: 12px;
  color: var(--ink-3);
}
.desc {
  margin: 10px 0 6px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink);
}
.issue {
  margin-top: 10px;
}
.restart {
  margin-top: 8px;
  font-size: 12px;
  color: color-mix(in srgb, var(--warn) 70%, var(--ink));
}
/* Element Plus' amber text on its pale amber background is too faint (about 2:1) */
.plugins-page :deep(.el-alert--warning.is-light .el-alert__title),
.plugins-page :deep(.el-alert--warning.is-light .el-alert__icon) {
  color: color-mix(in srgb, var(--warn) 70%, var(--ink));
}
.python {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--hairline);
}
.py-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 8px;
  font-size: 13px;
}
.py-info {
  font-size: 12px;
  color: var(--ink-3);
}
.reqs {
  margin-top: 6px;
  font-size: 12px;
  color: var(--ink-2);
  overflow-wrap: anywhere;
}
.py-log {
  max-height: 60vh;
  overflow: auto;
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: var(--ink);
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
