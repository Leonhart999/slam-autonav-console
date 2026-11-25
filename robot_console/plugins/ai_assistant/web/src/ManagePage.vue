<!-- "AI model services" page: three tabs. Model services (the profile list and editor), OAuth sign-in (the embedded
     CPA engine and its accounts) and usage statistics. The tab is kept in the hash (?tab=oauth|usage). -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElRadioButton, ElRadioGroup } from 'element-plus'
import { t } from './sdk'
import ProvidersPage from './ProvidersPage.vue'
import OAuthPage from './OAuthPage.vue'
import UsagePage from './UsagePage.vue'

type Tab = 'services' | 'oauth' | 'usage'
const TABS: Tab[] = ['services', 'oauth', 'usage']

function fromHash(): Tab {
  const q = location.hash.split('?')[1] ?? ''
  const tab = new URLSearchParams(q).get('tab') as Tab | null
  return tab && TABS.includes(tab) ? tab : 'services'
}

const tab = ref<Tab>(fromHash())
const services = ref<InstanceType<typeof ProvidersPage> | null>(null)
const shown = computed(() => tab.value)

async function pick(next: string | number | boolean | undefined) {
  const want = String(next) as Tab
  if (want === tab.value) return
  if (tab.value === 'services' && services.value && !(await services.value.confirmLeave())) return
  tab.value = want
  const base = location.hash.split('?')[0]
  history.replaceState(history.state, '', want === 'services' ? base : `${base}?tab=${want}`)
}

function onHash() {
  if (location.hash.startsWith('#/ext/ai_assistant/providers')) tab.value = fromHash()
}
onMounted(() => window.addEventListener('hashchange', onHash))
onUnmounted(() => window.removeEventListener('hashchange', onHash))
</script>

<template>
  <div class="aip-manage">
    <div class="aip-tabs">
      <el-radio-group :model-value="tab" size="default" @update:model-value="pick">
        <el-radio-button v-for="k in TABS" :key="k" :value="k">{{ t(`tabs.${k}`) }}</el-radio-button>
      </el-radio-group>
    </div>
    <transition name="aip-tab" mode="out-in">
      <ProvidersPage v-if="shown === 'services'" ref="services" key="services" />
      <OAuthPage v-else-if="shown === 'oauth'" key="oauth" @go-services="pick('services')" />
      <UsagePage v-else key="usage" />
    </transition>
  </div>
</template>

<style>
.aip-manage { display: flex; flex-direction: column; gap: 14px; }
.aip-tabs { display: flex; }
.aip-tabs .el-radio-group { padding: 3px; border-radius: 11px; background: rgba(var(--tint-rgb), 0.05); gap: 2px; }
.aip-tabs .el-radio-button__inner {
  border: 0 !important;
  border-radius: 8px !important;
  padding: 7px 16px;
  background: transparent;
  box-shadow: none !important;
  color: var(--ink-2);
  font-weight: 500;
}
.dark .aip-tabs .el-radio-button__inner { font-weight: 460; }
.aip-tabs .el-radio-button.is-active .el-radio-button__inner {
  color: var(--ink);
  background: var(--card-solid);
  box-shadow: 0 1px 3px rgba(var(--shade-rgb), 0.12) !important;
}
.aip-tab-enter-active, .aip-tab-leave-active { transition: opacity 0.2s cubic-bezier(0.2, 0.8, 0.2, 1), translate 0.2s cubic-bezier(0.2, 0.8, 0.2, 1); }
.aip-tab-enter-from { opacity: 0; translate: 0 4px; }
.aip-tab-leave-to { opacity: 0; }
@media (prefers-reduced-motion: reduce) { .aip-tab-enter-active, .aip-tab-leave-active { transition: none; } }
</style>
