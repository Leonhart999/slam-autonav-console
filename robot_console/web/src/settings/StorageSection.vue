<!-- Settings → Data & storage: data folders (console config) with their size, disk space, settings backup. -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { FolderOpened, Download, Upload, RefreshLeft } from '@element-plus/icons-vue'
import { errorText } from '@/messages'
import { openFolder } from '@/openFolder'
import { Files, PieChart } from '@element-plus/icons-vue'
import SettingsCard from './SettingsCard.vue'
import SettingRow from './SettingRow.vue'
import ConfigRow from './ConfigRow.vue'
import { settingsMessages } from './messages'
import { adoptSaved, payload, saving, writable } from './store'
import { settingsApi, type StorageFolder, type StorageInfo } from './api'
import { exportBrowserPrefs, importBrowserPrefs } from './prefs'

const { t } = useI18n({ messages: settingsMessages })

const storage = ref<StorageInfo | null>(null)
const scanning = ref(false)
async function loadStorage() {
  scanning.value = true
  try { storage.value = await settingsApi.storage() } catch { storage.value = null } finally { scanning.value = false }
}
onMounted(loadStorage)

function bytes(n: number) {
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++ }
  return `${n >= 100 || i === 0 ? n.toFixed(0) : n.toFixed(1)} ${u[i]}`
}
function folders(key: string): StorageFolder[] {
  return storage.value?.folders.filter((f) => f.key === key) ?? []
}
function meta(key: string) {
  const list = folders(key)
  if (!list.length) return scanning.value ? t('settings.storage.scanning') : ''
  return list.map((f) => (f.exists
    ? `${f.path} · ${bytes(f.bytes)} · ${t('settings.storage.items', { n: f.items })}${f.partial ? ` ${t('settings.storage.partial')}` : ''}`
    : `${f.path} · ${t('settings.storage.missing')}`)).join('\n')
}
const ROWS = [
  { key: 'data_root', k: 'paths.data_root', msg: 'dataRoot' },
  { key: 'maps', k: 'mapping.maps_dir', msg: 'maps' },
  { key: 'runs', k: 'mapping.runs_dir', msg: 'runs' },
  { key: 'results', k: 'evaluation.results_dir', msg: 'results' },
  { key: 'logs', k: 'server.log_dir', msg: 'logs' },
] as const

const disks = computed(() => storage.value?.disks ?? [])

// ---- backup
const busy = ref(false)
async function exportAll() {
  busy.value = true
  try {
    const e = await settingsApi.export()
    const file = { ...e, browser: exportBrowserPrefs() }
    const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '')
    const name = `robot_console_settings_${stamp}.json`
    const url = URL.createObjectURL(new Blob([JSON.stringify(file, null, 2)], { type: 'application/json' }))
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    ElMessage.success(t('settings.storage.export.done', { name }))
  } catch (err) {
    ElMessage.error(errorText(err))
  } finally {
    busy.value = false
  }
}

const picker = ref<HTMLInputElement | null>(null)
async function importFile(ev: Event) {
  const input = ev.target as HTMLInputElement
  const f = input.files?.[0]
  input.value = ''
  if (!f) return
  let data: Record<string, unknown>
  try {
    data = JSON.parse(await f.text())
    if (data.format !== 'robot_console.settings' || typeof data.console_yaml !== 'string') throw new Error('format')
  } catch {
    ElMessage.error(t('settings.storage.import.badFile'))
    return
  }
  try {
    await ElMessageBox.confirm(t('settings.storage.import.confirm'), t('settings.storage.import.confirmTitle'), {
      type: 'warning', confirmButtonText: t('settings.storage.import.ok'), cancelButtonText: t('settings.storage.cancel'),
    })
  } catch { return }
  busy.value = true
  saving.value = true          // locks every settings field: the answer replaces the draft
  try {
    const r = await settingsApi.import(data.console_yaml as string)
    adoptSaved(r)
    if (r.ignored?.length) ElMessage.warning(t('settings.storage.import.ignored', { n: r.ignored.length, keys: r.ignored.join(', ') }))
    const changedBrowser = importBrowserPrefs(data.browser)
    ElMessage.success(t('settings.storage.import.done'))
    // Browser preferences (theme, language, layout) take effect on a fresh page
    if (changedBrowser) setTimeout(() => window.location.reload(), 900)
  } catch (err) {
    ElMessage.error(errorText(err))
  } finally {
    busy.value = false
    saving.value = false
  }
}

async function resetAll() {
  try {
    await ElMessageBox.confirm(
      t('settings.storage.reset.confirm', { file: payload.value?.user_file ?? '' }), t('settings.storage.reset.confirmTitle'),
      { type: 'warning', confirmButtonText: t('settings.storage.reset.ok'), cancelButtonText: t('settings.storage.cancel') },
    )
  } catch { return }
  busy.value = true
  saving.value = true
  try {
    adoptSaved(await settingsApi.resetAll())
    ElMessage.success(t('settings.storage.reset.done'))
  } catch (err) {
    ElMessage.error(errorText(err))
  } finally {
    busy.value = false
    saving.value = false
  }
}
</script>

<template>
  <div>
    <SettingsCard :title="t('settings.storage.cards.locations')" :icon="FolderOpened" scope="console" :hint="t('settings.storage.cards.locationsHint')">
      <ConfigRow v-for="r in ROWS" :key="r.k" :k="r.k" :msg="`settings.storage.${r.msg}`" placeholder="~/…">
        <template #after>
          <el-tooltip :content="meta(r.key) || '—'" placement="top" :show-after="200">
            <span class="size mono">{{ folders(r.key)[0]?.exists ? bytes(folders(r.key)[0].bytes) : '—' }}</span>
          </el-tooltip>
          <el-button
            :icon="FolderOpened" :disabled="!folders(r.key)[0]?.exists" :title="t('settings.storage.open')"
            @click="openFolder(folders(r.key)[0]?.path)"
          />
        </template>
      </ConfigRow>
      <ConfigRow k="playback.bag_dirs" msg="settings.storage.bags" kind="list" stacked />
      <div v-if="folders('bags').length" class="bag-sizes">
        <span v-for="b in folders('bags')" :key="b.path" class="bag mono">
          {{ b.path }} · {{ b.exists ? `${bytes(b.bytes)} · ${t('settings.storage.items', { n: b.items })}` : t('settings.storage.missing') }}
          <el-button v-if="b.exists" link :icon="FolderOpened" @click="openFolder(b.path)" />
        </span>
      </div>
    </SettingsCard>

    <SettingsCard :title="t('settings.storage.cards.disks')" :icon="PieChart">
      <SettingRow
        v-for="(d, i) in disks" :key="d.path" :label="i === 0 ? t('settings.storage.disk.data') : t('settings.storage.disk.checked')"
        :sub="d.path" :tip="i === 0 ? t('settings.storage.disk.lowTip') : t('settings.storage.disk.checkedTip')"
      >
        <div class="disk">
          <div class="bar"><span :class="{ warn: d.used / d.total > 0.85 }" :style="{ width: `${Math.min(100, (d.used / d.total) * 100)}%` }" /></div>
          <div class="disk-meta">
            <span class="pct mono" :class="{ warn: d.used / d.total > 0.85 }">{{ Math.round((d.used / d.total) * 100) }}%</span>
            <span class="meta">{{ t('settings.storage.disk.usage', { used: bytes(d.used), total: bytes(d.total) }) }} · {{ t('settings.storage.disk.free', { free: bytes(d.free) }) }}</span>
          </div>
        </div>
      </SettingRow>
      <SettingRow v-if="!disks.length" :label="scanning ? t('settings.storage.scanning') : '—'" />
    </SettingsCard>

    <SettingsCard :title="t('settings.storage.cards.backup')" :icon="Files">
      <SettingRow :label="t('settings.storage.export.label')" :sub="t('settings.storage.export.sub')" :tip="t('settings.storage.export.tip')">
        <el-button :icon="Download" :loading="busy" @click="exportAll">{{ t('settings.storage.export.button') }}</el-button>
      </SettingRow>
      <SettingRow :label="t('settings.storage.import.label')" :sub="t('settings.storage.import.sub')" :tip="t('settings.storage.import.tip')">
        <input ref="picker" type="file" accept="application/json,.json" hidden @change="importFile">
        <el-button :icon="Upload" :disabled="!writable || busy" @click="picker?.click()">{{ t('settings.storage.import.button') }}</el-button>
      </SettingRow>
      <SettingRow :label="t('settings.storage.reset.label')" :sub="t('settings.storage.reset.sub')" :tip="t('settings.storage.reset.tip')">
        <el-button :icon="RefreshLeft" type="danger" plain :disabled="!writable || busy" @click="resetAll">
          {{ t('settings.storage.reset.button') }}
        </el-button>
      </SettingRow>
    </SettingsCard>
  </div>
</template>

<style scoped>
.size {
  min-width: 64px;
  padding: 3px 8px;
  border-radius: 7px;
  box-sizing: border-box;
  text-align: center;
  font-size: 11.5px;
  color: var(--ink-2);
  background: rgba(var(--tint-rgb), 0.06);
  cursor: default;
}
.bag-sizes {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 18px 12px;
  margin-top: -4px;
}
.bag { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--ink-3); }
.disk { display: flex; flex-direction: column; align-items: flex-end; gap: 7px; width: 340px; }
.disk-meta { display: flex; align-items: baseline; gap: 10px; }
.pct { font-size: 15px; font-weight: 600; color: var(--ink); }
.pct.warn { color: var(--warn); }
.bar { width: 100%; height: 8px; border-radius: 4px; background: rgba(var(--tint-rgb), 0.1); overflow: hidden; }
.bar span { display: block; height: 100%; border-radius: 4px; background: var(--accent-grad); box-shadow: 0 0 10px rgba(var(--accent-rgb), 0.45); transition: width 0.6s cubic-bezier(0.3, 0.7, 0.4, 1); }
.bar span.warn { background: linear-gradient(90deg, #f59e0b, #dc2626); }
.meta { font-size: 12px; color: var(--ink-3); white-space: nowrap; }
</style>
