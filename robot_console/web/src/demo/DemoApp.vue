<!-- Page shell of the web demo: header (name, demo badge, links, language, look), the workspace once the data
     has loaded, and the credits. Loading problems and a browser without WebGL get a message instead. -->
<script setup lang="ts">
import { Moon, Sunny } from '@element-plus/icons-vue'
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { useI18n } from 'vue-i18n'
import RadarMark from '@/components/RadarMark.vue'
import { isDark, setTheme } from '@/theme'
import DemoWorkspace from './DemoWorkspace.vue'
import { loadDemo, type DemoData } from './data'
import { LANGS, setLang } from './i18n'

const REPO = 'https://github.com/Leonhart999/slam-autonav-console'
const { t, locale } = useI18n()

const installUrl = computed(() =>
  locale.value === 'zh-CN' ? `${REPO}/blob/main/README.zh-CN.md#快速开始` : `${REPO}#quick-start`)

function hasWebgl() {
  try {
    const c = document.createElement('canvas')
    return !!(c.getContext('webgl2') || c.getContext('webgl'))
  } catch {
    return false
  }
}
const webgl = hasWebgl()

const data = shallowRef<DemoData | null>(null)
const failed = ref<string | null>(null)
let abort: AbortController | null = null

async function load() {
  abort?.abort()
  abort = new AbortController()
  failed.value = null
  try {
    data.value = await loadDemo(`${import.meta.env.BASE_URL}data/`, abort.signal)
  } catch (e) {
    if ((e as Error).name !== 'AbortError') failed.value = (e as Error).message
  }
}
onMounted(() => { if (webgl) load() })
onBeforeUnmount(() => abort?.abort())

function toggleTheme(e: MouseEvent) {
  setTheme(isDark.value ? 'light' : 'dark', { x: e.clientX, y: e.clientY })
}
</script>

<template>
  <div class="page">
    <header class="top glass">
      <RadarMark :size="34" />
      <div class="brand">
        <b>SLAM and AutoNav Console</b>
        <span class="chip primary live">{{ t('header.demo') }}</span>
      </div>
      <nav class="actions">
        <a class="link" :href="REPO" target="_blank" rel="noopener">
          <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" /></svg>
          {{ t('header.github') }}
        </a>
        <div class="seg">
          <button v-for="l in LANGS" :key="l.value" :class="{ on: locale === l.value }" :aria-pressed="locale === l.value" @click="setLang(l.value)">
            {{ l.label }}
          </button>
        </div>
        <button class="icon-btn" :title="t(`header.theme.${isDark ? 'light' : 'dark'}`)" :aria-label="t(`header.theme.${isDark ? 'light' : 'dark'}`)" @click="toggleTheme">
          <el-icon :size="16"><Sunny v-if="isDark" /><Moon v-else /></el-icon>
        </button>
      </nav>
    </header>

    <main class="body">
      <DemoWorkspace v-if="data" :data="data" :install-url="installUrl" />
      <div v-else class="state glass">
        <RadarMark :size="112" :spin="webgl && !failed" />
        <template v-if="!webgl">
          <div class="state-title">{{ t('load.noWebgl') }}</div>
        </template>
        <template v-else-if="failed">
          <div class="state-title">{{ t('load.failed') }}</div>
          <code class="state-hint">{{ failed }}</code>
          <el-button type="primary" @click="load">{{ t('load.retry') }}</el-button>
        </template>
        <div v-else class="state-title">{{ t('load.loading') }}</div>
      </div>
    </main>

    <footer class="foot">
      <span>{{ t('footer.models') }}</span>
      <span>{{ t('footer.licence') }}</span>
    </footer>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  box-sizing: border-box;
  height: 100%;
  padding: 14px 18px 12px;
}
.top {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 14px;
  border-radius: var(--radius);
}
.brand {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 12px;
  min-width: 0;
}
.brand b {
  font-size: 16px;
  letter-spacing: 0.01em;
}
.actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
}
.link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border-radius: 10px;
  color: var(--ink-2);
  font-size: 13px;
  font-weight: 500;
  text-decoration: none;
  background: rgba(var(--paper-rgb), 0.7);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.link:hover {
  color: var(--accent);
}
.seg {
  display: inline-flex;
  padding: 3px;
  border-radius: 10px;
  background: rgba(var(--paper-rgb), 0.55);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.seg button {
  height: 26px;
  padding: 0 10px;
  border: none;
  border-radius: 7px;
  background: none;
  font: inherit;
  font-size: 12.5px;
  color: var(--ink-3);
  cursor: pointer;
}
.seg button.on {
  color: var(--accent);
  font-weight: 600;
  background: var(--paper);
  box-shadow: 0 2px 8px -3px rgba(var(--shade-rgb), 0.3);
}
.icon-btn {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 10px;
  color: var(--ink-2);
  background: rgba(var(--paper-rgb), 0.7);
  box-shadow: inset 0 0 0 1px var(--hairline);
  cursor: pointer;
}
.icon-btn:hover {
  color: var(--accent);
}
.link:focus-visible,
.seg button:focus-visible,
.icon-btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
}
.body > :deep(.workspace) {
  flex: 1;
}
.state {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 40px 20px;
  border-radius: var(--radius);
  text-align: center;
}
.state-title {
  font-size: 15px;
  font-weight: 600;
}
.state-hint {
  font-size: 12px;
  color: var(--ink-3);
}

.foot {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  justify-content: center;
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--ink-3);
  text-align: center;
}

@media (max-width: 1100px) {
  .page {
    height: auto;
    min-height: 100%;
  }
}
@media (max-width: 640px) {
  .page {
    padding: 10px;
  }
  .top {
    flex-wrap: wrap;
  }
  .actions {
    width: 100%;
    justify-content: flex-end;
  }
}
</style>
