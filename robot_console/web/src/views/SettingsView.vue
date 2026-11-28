<!-- Settings page (/settings/<section>). The side menu switches to the settings sections while it is open (App.vue).
     Console-config sections share one draft (settings/store.ts) and one save bar at the bottom. -->
<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CircleCloseFilled, InfoFilled, WarningFilled } from '@element-plus/icons-vue'
import { settingsMessages } from '@/settings/messages'
import {
  dirtyKeys, discard, loadError, loadSettings, loading, pendingRestart, payload, requestRestart, saveSettings, saving,
  sectionOf, writable,
} from '@/settings/store'
import GeneralSection from '@/settings/GeneralSection.vue'
import StorageSection from '@/settings/StorageSection.vue'
import ExperimentSection from '@/settings/ExperimentSection.vue'
import SafetySection from '@/settings/SafetySection.vue'
import NetworkSection from '@/settings/NetworkSection.vue'
import AboutSection from '@/settings/AboutSection.vue'

const { t } = useI18n({ messages: settingsMessages })
const g = useI18n()   // global: section titles (settingsNav.*)
const route = useRoute()
const router = useRouter()

const SECTIONS = {
  general: GeneralSection, storage: StorageSection, experiment: ExperimentSection,
  safety: SafetySection, network: NetworkSection, about: AboutSection,
} as const
type Section = keyof typeof SECTIONS
const section = computed<Section>(() => {
  const s = String(route.params.section ?? 'general')
  return (s in SECTIONS ? s : 'general') as Section
})
const usesConfig = computed(() => ['storage', 'experiment', 'safety', 'network'].includes(section.value))

onMounted(() => { void loadSettings() })

async function save(restart: boolean) {
  // a new port: the page cannot follow the restart by itself, so tell the user where the console will be
  const oldPort = payload.value?.fields['server.port']?.value
  const portChanged = dirtyKeys.value.includes('server.port')
  const ok = await saveSettings(restart, (key) => {
    // the row of the rejected key shows the reason; bring its section into view
    const sec = sectionOf(key)
    ElMessage.error(t('settings.save.invalid', { section: sec ? g.t(`settingsNav.${sec}`) : key }))
    if (sec && sec !== section.value) void router.push(`/settings/${sec}`)
  })
  if (ok && !restart) ElMessage.success(t('settings.save.saved'))
  const newPort = payload.value?.fields['server.port']?.value
  if (ok && portChanged && newPort !== oldPort) {
    const url = `${location.protocol}//${location.hostname}:${newPort}/`
    ElMessageBox.confirm(t('settings.save.portMoved', { url }), t('settings.save.portTitle'), {
      type: 'info', confirmButtonText: t('settings.save.openNew'), cancelButtonText: t('settings.save.stay'),
    }).then(() => { window.open(url, '_blank', 'noopener') }).catch(() => { /* stay */ })
  }
}

// Leaving the settings pages with unsaved console settings: ask first
onBeforeRouteLeave(async (to) => {
  if (to.path.startsWith('/settings') || !dirtyKeys.value.length) return true
  try {
    await ElMessageBox.confirm(t('settings.save.leaveAsk', { n: dirtyKeys.value.length }), t('settings.save.leaveTitle'), {
      type: 'warning', confirmButtonText: t('settings.save.leave'), cancelButtonText: t('settings.save.stay'),
    })
    discard()
    return true
  } catch {
    return false
  }
})
</script>

<template>
  <div class="settings-page">
    <div class="inner">
      <template v-if="usesConfig">
        <div v-if="loadError" class="notice bad glass">
          <el-icon :size="16"><CircleCloseFilled /></el-icon>
          <span>{{ t('settings.loadFailed', { reason: loadError }) }}</span>
          <el-button size="small" :loading="loading" @click="loadSettings(true)">{{ t('settings.retry') }}</el-button>
        </div>
        <div v-else-if="payload && !writable" class="notice glass">
          <el-icon :size="16"><InfoFilled /></el-icon><span>{{ t('settings.readOnly') }}</span>
        </div>
        <div v-else-if="pendingRestart && !dirtyKeys.length" class="notice warn glass">
          <el-icon :size="16"><WarningFilled /></el-icon>
          <span>{{ t('settings.save.pending') }}</span>
          <el-button size="small" type="warning" plain @click="requestRestart">{{ t('settings.save.restartNow') }}</el-button>
        </div>
      </template>

      <div v-if="usesConfig && !payload" v-loading="loading" class="placeholder" />
      <transition v-else name="section" mode="out-in">
        <component :is="SECTIONS[section]" :key="section" class="section" />
      </transition>

      <transition name="bar">
        <div v-if="dirtyKeys.length" class="save-bar">
          <span class="pulse" />
          <span class="grow">{{ t('settings.save.unsaved', { n: dirtyKeys.length }) }}</span>
          <el-button :disabled="saving" @click="discard">{{ t('settings.save.discard') }}</el-button>
          <el-button type="primary" plain :loading="saving" :disabled="!writable" @click="save(false)">{{ t('settings.save.save') }}</el-button>
          <el-button type="primary" :loading="saving" :disabled="!writable" @click="save(true)">{{ t('settings.save.saveRestart') }}</el-button>
        </div>
      </transition>
    </div>
  </div>
</template>

<style scoped>
.settings-page {
  height: 100%;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-gutter: stable;
}
.inner {
  position: relative;
  width: 100%;
  max-width: 1000px;
  min-height: 100%;
  margin: 0;
  padding: 4px 8px 0;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
}
.section { padding-bottom: 28px; }
.notice {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  padding: 11px 14px;
  border-radius: 12px;
  font-size: 13px;
  color: var(--ink-2);
}
.notice span { flex: 1; }
.notice .el-icon { color: var(--accent); }
.notice.warn { color: var(--note-ink); background: var(--note-bg); box-shadow: 0 0 0 1px var(--note-edge); }
.notice.warn .el-icon { color: var(--warn); }
.notice.bad, .notice.bad .el-icon { color: var(--bad); }
.placeholder { height: 240px; }

/* Floating save bar: stays at the bottom of the scroll area while there are unsaved changes */
.save-bar {
  position: sticky;
  bottom: 12px;
  z-index: 3;
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 auto 12px;
  width: min(720px, 100%);
  box-sizing: border-box;
  padding: 10px 12px 10px 16px;
  border-radius: 14px;
  font-size: 13px;
  color: var(--ink);
  background: var(--glass-strong);
  backdrop-filter: var(--blur);
  box-shadow: 0 0 0 1px rgba(var(--accent-rgb), 0.28), 0 16px 34px -14px rgba(var(--shade-rgb), 0.55);
}
.pulse {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent);
  animation: pulse 1.8s ease-out infinite;
}
@keyframes pulse {
  0% { box-shadow: 0 0 0 0 rgba(var(--accent-rgb), 0.55); }
  100% { box-shadow: 0 0 0 8px rgba(var(--accent-rgb), 0); }
}
.grow { flex: 1; }
.bar-enter-active, .bar-leave-active { transition: opacity 0.22s, transform 0.22s cubic-bezier(0.3, 0.7, 0.4, 1); }
.bar-enter-from, .bar-leave-to { opacity: 0; transform: translateY(14px); }
.section-enter-active, .section-leave-active { transition: opacity 0.16s, transform 0.16s; }
.section-enter-from { opacity: 0; transform: translateY(6px); }
.section-leave-to { opacity: 0; }
</style>

<!-- Element Plus controls inside the settings page, matched to the console's glass look (light and dark) -->
<style>
.settings-page .el-segmented {
  --el-segmented-bg-color: rgba(var(--tint-rgb), 0.07);
  --el-segmented-color: var(--ink-3);
  --el-segmented-item-selected-bg-color: var(--paper);
  --el-segmented-item-selected-color: var(--accent);
  --el-segmented-item-hover-color: var(--ink);
  --el-segmented-item-hover-bg-color: rgba(var(--paper-rgb), 0.45);
  --el-segmented-item-active-bg-color: rgba(var(--paper-rgb), 0.7);
  --el-segmented-padding: 3px;
  border-radius: 11px;
}
.settings-page .el-segmented__item { padding: 0 12px; border-radius: 8px; }
.settings-page .el-segmented__item-selected {
  border-radius: 8px;
  box-shadow: 0 2px 8px -2px rgba(var(--shade-rgb), 0.3), 0 0 0 1px rgba(var(--accent-rgb), 0.12);
}
.settings-page .el-segmented__item.is-selected { font-weight: 600; }
.settings-page .el-input__wrapper,
.settings-page .el-select__wrapper {
  background: rgba(var(--paper-rgb), 0.7);
  box-shadow: 0 0 0 1px var(--hairline) inset;
  border-radius: 9px;
  transition: box-shadow 0.15s, background 0.15s;
}
.settings-page .el-input__wrapper:hover,
.settings-page .el-select__wrapper:hover { box-shadow: 0 0 0 1px rgba(var(--accent-rgb), 0.35) inset; }
.settings-page .el-input__wrapper.is-focus,
.settings-page .el-select__wrapper.is-focused {
  background: var(--paper);
  box-shadow: 0 0 0 1px var(--accent) inset, 0 0 0 3px rgba(var(--accent-rgb), 0.14);
}
.settings-page .el-input-number__decrease,
.settings-page .el-input-number__increase { background: transparent; border-color: var(--hairline); }
.settings-page .el-select .el-tag {
  --el-tag-bg-color: rgba(var(--accent-rgb), 0.1);
  --el-tag-border-color: rgba(var(--accent-rgb), 0.18);
  --el-tag-text-color: var(--accent);
  border-radius: 7px;
  font-family: var(--font-mono);
  font-size: 11.5px;
}
.settings-page .el-select .el-tag .el-tag__close { color: var(--accent); }
.settings-page .el-switch { --el-switch-off-color: rgba(var(--tint-rgb), 0.22); }
</style>
