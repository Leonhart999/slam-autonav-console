<!-- Settings → General: browser-only preferences (prefs.ts) plus language and theme. All apply immediately. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { LANGS, pick, setLang, type Lang } from '@/i18n'
import { theme, themeChoices } from '@/theme'
import { mainPages } from '@/router'
import { Bell, Brush, Menu } from '@element-plus/icons-vue'
import SettingsCard from './SettingsCard.vue'
import SettingRow from './SettingRow.vue'
import { settingsMessages } from './messages'
import { followSystemTheme, pickThemeManually, prefs, START_PAGES, ZOOMS } from './prefs'

const { t, locale } = useI18n({ messages: settingsMessages, useScope: 'local' })
const g = useI18n()   // global messages: page titles

const themeValue = computed(() => (prefs.followSystem ? '__system' : theme.value))
function onTheme(v: string | number | boolean | undefined) {
  if (v === '__system') followSystemTheme()
  else if (typeof v === 'string') pickThemeManually(v)
}
const themeOptions = computed(() => [
  { value: '__system', label: t('settings.general.theme.system') },
  ...themeChoices.value.map((c) => ({ value: c.id, label: pick(c.label) })),
])

const startOptions = computed(() => START_PAGES.map((p) => {
  if (p === 'last') return { value: p, label: t('settings.general.startPage.last') }
  const page = mainPages.find((m) => m.path === p)
  return { value: p, label: page ? g.t(page.meta.titleKey) : g.t('nav.monitor') }
}))

async function onNotify(on: string | number | boolean) {
  if (!on) { prefs.notifyTasks = false; return }
  if (typeof Notification === 'undefined') {
    ElMessage.warning(t('settings.general.notify.unsupported'))
    return
  }
  let perm = Notification.permission
  if (perm === 'default') perm = await Notification.requestPermission()
  if (perm !== 'granted') {
    ElMessage.warning(t('settings.general.notify.denied'))
    prefs.notifyTasks = false
    return
  }
  prefs.notifyTasks = true
}

const m = (k: string) => `settings.general.${k}`
</script>

<template>
  <div>
    <SettingsCard :title="t('settings.general.cards.appearance')" :icon="Brush" scope="browser">
      <SettingRow :label="t(m('language.label'))" :sub="t(m('language.sub'))" :tip="t(m('language.tip'))">
        <el-segmented
          :model-value="locale" :options="LANGS.map((l) => ({ value: l.value, label: l.value === 'en' ? 'English' : '简体中文' }))"
          @change="(v: string | number | boolean) => setLang(v as Lang)"
        />
      </SettingRow>
      <SettingRow :label="t(m('theme.label'))" :sub="t(m('theme.sub'))" :tip="t(m('theme.tip'))">
        <el-segmented :model-value="themeValue" :options="themeOptions" @change="onTheme" />
      </SettingRow>
      <SettingRow :label="t(m('density.label'))" :sub="t(m('density.sub'))" :tip="t(m('density.tip'))">
        <el-segmented
          v-model="prefs.density"
          :options="[{ value: 'compact', label: t(m('density.compact')) }, { value: 'comfortable', label: t(m('density.comfortable')) }]"
        />
      </SettingRow>
      <SettingRow :label="t(m('zoom.label'))" :sub="t(m('zoom.sub'))" :tip="t(m('zoom.tip'))">
        <el-segmented v-model="prefs.zoom" :options="ZOOMS.map((z) => ({ value: z, label: `${z}%` }))" />
      </SettingRow>
      <SettingRow :label="t(m('motion.label'))" :sub="t(m('motion.sub'))" :tip="t(m('motion.tip'))">
        <el-segmented
          v-model="prefs.motion"
          :options="[{ value: 'full', label: t(m('motion.full')) }, { value: 'reduced', label: t(m('motion.reduced')) }]"
        />
      </SettingRow>
    </SettingsCard>

    <SettingsCard :title="t('settings.general.cards.layout')" :icon="Menu" scope="browser">
      <SettingRow :label="t(m('pluginGroup.label'))" :sub="t(m('pluginGroup.sub'))" :tip="t(m('pluginGroup.tip'))">
        <el-switch v-model="prefs.showPluginGroup" />
      </SettingRow>
      <SettingRow :label="t(m('numbers.label'))" :sub="t(m('numbers.sub'))" :tip="t(m('numbers.tip'))">
        <el-switch v-model="prefs.showNumbers" />
      </SettingRow>
      <SettingRow :label="t(m('clock.label'))" :sub="t(m('clock.sub'))" :tip="t(m('clock.tip'))">
        <el-switch v-model="prefs.showClock" />
      </SettingRow>
    </SettingsCard>

    <SettingsCard :title="t('settings.general.cards.startup')" :icon="Bell" scope="browser">
      <SettingRow :label="t(m('startPage.label'))" :sub="t(m('startPage.sub'))" :tip="t(m('startPage.tip'))">
        <el-select v-model="prefs.startPage" class="sel">
          <el-option v-for="o in startOptions" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
      </SettingRow>
      <SettingRow :label="t(m('notify.label'))" :sub="t(m('notify.sub'))" :tip="t(m('notify.tip'))">
        <el-switch :model-value="prefs.notifyTasks" @change="onNotify" />
      </SettingRow>
    </SettingsCard>
  </div>
</template>

<style scoped>
.sel { width: 200px; }
</style>
