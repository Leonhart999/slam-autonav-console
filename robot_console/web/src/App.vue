<!-- 公共框架：左侧导航栏（品牌、流程页面菜单、插件页面、底部工具组 系统监控/插件、外观与语言切换），
     右侧页头 + 页面内容 + 底部状态栏。视觉风格是白色磨砂玻璃 + 科幻 HUD，颜色等变量在 style.css -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Grid, Moon, Sunny } from '@element-plus/icons-vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import enLocale from 'element-plus/es/locale/lang/en'
import { footPages, mainPages } from './router'
import { LANGS, pick, setLang } from './i18n'
import { pagePath, registry } from './plugins/host'
import { THEMES, setTheme, theme, type Theme } from './theme'
import { useStatusStore } from './stores/status'
import StatusBar from './components/StatusBar.vue'
import RadarMark from './components/RadarMark.vue'
import TaskCenter from './components/TaskCenter.vue'
import PluginSlot from './components/PluginSlot.vue'

const { t, locale } = useI18n()
const route = useRoute()
const store = useStatusStore()

// Element Plus 自带文字（表格"暂无数据"等）跟随界面语言
const epLocale = computed(() => (locale.value === 'en' ? enLocale : zhCn))
// 语言切换滑块停在第几个按钮上
const langIndex = computed(() => Math.max(0, LANGS.findIndex((l) => l.value === locale.value)))

// Appearance switch: the new look spreads from the button that was pressed
const THEME_ICON = { light: Sunny, dark: Moon }
const THEME_LABEL = { light: 'app.themeLight', dark: 'app.themeDark' } as const
function pickTheme(next: Theme, e: MouseEvent) {
  const box = (e.currentTarget as HTMLElement).getBoundingClientRect()
  setTheme(next, { x: box.left + box.width / 2, y: box.top + box.height / 2 })
}

const no = (i: number) => String(i + 1).padStart(2, '0')
// Pages added by plugins: their own group under the workflow pages, numbered on from them
const pluginPages = computed(() => registry.pages.map((p) => ({
  path: pagePath(p), title: pick(p.title), icon: p.icon ?? Grid,
})))
// Badges of the bottom tool entries: backend link on Monitor, plugin load problems on Plugins
const pluginIssues = computed(() => Object.keys(registry.issues).length)
// 页头：当前页面在菜单里的序号和标题（底部工具页没有序号，显示图标）
const current = computed(() => {
  const i = mainPages.findIndex((p) => p.path === route.path)
  if (i >= 0) return { no: no(i), title: t(mainPages[i].meta.titleKey) }
  const j = pluginPages.value.findIndex((p) => p.path === route.path)
  if (j >= 0) return { no: no(mainPages.length + j), title: pluginPages.value[j].title }
  const f = footPages.find((p) => p.path === route.path)
  return f ? { icon: f.meta.icon, title: t(f.meta.titleKey) } : null
})

// 页头右侧的本地时钟（24 小时制，和语言无关）
const now = ref(new Date())
let clockTimer: number | undefined
const clock = computed(() => now.value.toLocaleTimeString('en-GB', { hour12: false }))

onMounted(() => {
  store.connect()
  clockTimer = window.setInterval(() => (now.value = new Date()), 1000)
})
onUnmounted(() => window.clearInterval(clockTimer))
</script>

<template>
  <el-config-provider :locale="epLocale">
    <div class="shell">
      <aside class="rail glass">
        <div class="brand">
          <RadarMark :size="40" />
          <div class="brand-text">
            <div class="brand-1">{{ t('app.logoLine1') }}</div>
            <div class="brand-2">{{ t('app.logoLine2') }}</div>
          </div>
        </div>

        <nav class="nav">
          <router-link
            v-for="(p, i) in mainPages" :key="p.path" :to="p.path" class="nav-item"
            active-class="active" :class="{ planned: p.meta.step }" :title="t(p.meta.titleKey)"
          >
            <span class="nav-no mono">{{ no(i) }}</span>
            <el-icon class="nav-icon" :size="17"><component :is="p.meta.icon" /></el-icon>
            <span class="nav-label">{{ t(p.meta.titleKey) }}</span>
            <span v-if="p.meta.step" class="nav-soon">{{ t('planned.badge') }}</span>
          </router-link>
          <template v-if="pluginPages.length">
            <div class="nav-group">
              <span class="nav-group-label">{{ t('app.extensions') }}</span>
            </div>
            <router-link
              v-for="(p, i) in pluginPages" :key="p.path" :to="p.path" class="nav-item" active-class="active"
              :title="p.title"
            >
              <span class="nav-no mono">{{ no(mainPages.length + i) }}</span>
              <el-icon class="nav-icon" :size="17"><component :is="p.icon" /></el-icon>
              <span class="nav-label">{{ p.title }}</span>
            </router-link>
          </template>
        </nav>

        <!-- Tool pages: a compact group at the bottom, apart from the numbered workflow above -->
        <nav class="tools">
          <router-link
            v-for="p in footPages" :key="p.path" :to="p.path" class="nav-item tool"
            active-class="active" :title="t(p.meta.titleKey)"
          >
            <el-icon class="nav-icon" :size="16"><component :is="p.meta.icon" /></el-icon>
            <span class="nav-label">{{ t(p.meta.titleKey) }}</span>
            <span
              v-if="p.name === 'monitor'" class="tool-dot" :class="store.connected ? 'ok' : 'down'"
              :title="t(store.connected ? 'app.linkUp' : 'app.linkDown')"
            />
            <span
              v-else-if="p.name === 'plugins' && pluginIssues" class="tool-badge mono"
              :title="t('app.pluginIssues', { n: pluginIssues })"
            >{{ pluginIssues }}</span>
          </router-link>
        </nav>

        <div class="rail-foot">
          <div class="prefs">
            <div class="seg seg-theme" role="group" :aria-label="t('app.theme')" :style="{ '--i': THEMES.indexOf(theme) }">
              <span class="thumb" />
              <button
                v-for="th in THEMES" :key="th" :class="{ on: theme === th }" :aria-pressed="theme === th"
                :title="t(THEME_LABEL[th])" :aria-label="t(THEME_LABEL[th])" :data-theme="th"
                @click="pickTheme(th, $event)"
              >
                <el-icon :size="14"><component :is="THEME_ICON[th]" /></el-icon>
              </button>
            </div>
            <div class="seg" role="group" :aria-label="t('app.language')" :style="{ '--i': langIndex }">
              <span class="thumb" />
              <button
                v-for="l in LANGS" :key="l.value" :class="{ on: locale === l.value }"
                :aria-pressed="locale === l.value" @click="setLang(l.value)"
              >{{ l.label }}</button>
            </div>
          </div>
          <div class="stamp mono">ROS 2 · Humble</div>
        </div>
      </aside>

      <div class="stage">
        <header class="topbar">
          <div v-if="current" class="crumb">
            <span v-if="current.no" class="crumb-no mono">{{ current.no }}</span>
            <el-icon v-else class="crumb-icon" :size="18"><component :is="current.icon" /></el-icon>
            <h1>{{ current.title }}</h1>
            <span class="crumb-path mono">{{ route.path }}</span>
          </div>
          <PluginSlot v-for="h in registry.header" :key="h.key" :plugin-id="h.pluginId" :component="h.component" />
          <TaskCenter />
          <div class="clock">
            <span class="clock-label">{{ t('app.localTime') }}</span>
            <span class="clock-time mono">{{ clock }}</span>
          </div>
        </header>

        <main class="content">
          <router-view v-slot="{ Component }">
            <transition name="page" mode="out-in">
              <component :is="Component" :key="route.path" />
            </transition>
          </router-view>
        </main>

        <StatusBar />
      </div>
    </div>
  </el-config-provider>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 14px;
  height: 100%;
  padding: 14px;
  box-sizing: border-box;
}

/* ---------------- 左侧导航栏 ---------------- */
.rail {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 18px 12px 14px;
  border-radius: 20px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 6px 18px;
  margin-bottom: 10px;
  border-bottom: 1px solid var(--hairline);
}
.brand-1 {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 0.12em;
  background: var(--accent-grad);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.brand-2 {
  margin-top: 2px;
  font-size: 12px;
  color: var(--ink-2);
  letter-spacing: 0.04em;
}
.nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  /* Plugin pages can make the menu longer than a short window: scroll it. The negative margin + padding keep
     the active marker (left of the item) inside the scroll box */
  min-height: 0;
  overflow-y: auto;
  scrollbar-width: thin;
  margin: 0 -12px;
  padding: 0 12px;
}
.nav-group {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 10px 2px;
  font-size: 11px;
  color: var(--ink-3);
}
/* hairline after the label, running to the right edge of the menu */
.nav-group::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--hairline);
}
.nav-item {
  flex: none;
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  height: 42px;
  padding: 0 10px;
  border-radius: 12px;
  color: var(--ink-2);
  font-size: 14px;
  text-decoration: none;
  transition: background 0.2s, color 0.2s;
}
.nav-item:hover {
  background: rgba(var(--paper-rgb), 0.6);
  color: var(--ink);
}
.nav-item:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
.nav-no {
  font-size: 10.5px;
  color: var(--ink-3);
  opacity: 0.8;
}
.nav-label {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.nav-soon {
  padding: 2px 6px;
  border-radius: 6px;
  font-size: 10.5px;
  color: var(--ink-3);
  background: var(--hairline);
}
.nav-item.planned .nav-icon,
.nav-item.planned .nav-label {
  opacity: 0.75;
}
/* 当前页面：亮白底 + 左侧发光竖条 */
.nav-item.active {
  background: var(--glass-strong);
  color: var(--ink);
  font-weight: 600;
  box-shadow: 0 0 0 1px rgba(var(--accent-rgb), 0.12), 0 6px 16px -10px rgba(var(--accent-rgb), 0.6);
}
.nav-item.active::before {
  content: '';
  position: absolute;
  left: -12px;
  top: 10px;
  bottom: 10px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--accent-grad);
  box-shadow: 0 0 10px rgba(6, 182, 212, 0.8);
}
.nav-item.active .nav-no,
.nav-item.active .nav-icon {
  color: var(--accent);
  opacity: 1;
}

/* 底部工具组：系统监控、插件。比上面的流程菜单矮一些、没有序号 */
.tools {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-top: 10px;
  margin: 8px -12px 0;
  padding-inline: 12px;
  border-top: 1px solid var(--hairline);
}
.nav-item.tool {
  height: 36px;
  font-size: 13.5px;
}
.nav-item.tool.active::before {
  top: 8px;
  bottom: 8px;
}
/* backend link: green when the status socket is up, red while it reconnects */
.tool-dot {
  flex: none;
  width: 7px;
  height: 7px;
  margin-right: 3px;
  border-radius: 50%;
}
.tool-dot.ok {
  background: var(--ok);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok) 18%, transparent);
}
.tool-dot.down {
  background: var(--bad);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--bad) 18%, transparent);
}
.tool-badge {
  flex: none;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  box-sizing: border-box;
  font-size: 11px;
  line-height: 18px;
  text-align: center;
  color: var(--warn);
  background: color-mix(in srgb, var(--warn) 14%, transparent);
}

.rail-foot {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
  padding-top: 12px;
  border-top: 1px solid var(--hairline);
}
/* 外观 + 语言：一行两个分段按钮，滑块滑到当前选项 */
.prefs {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 8px;
}
.seg {
  position: relative;
  display: grid;
  grid-template-columns: 1fr 1fr;
  padding: 3px;
  border-radius: 11px;
  background: rgba(var(--tint-rgb), 0.06);
}
.seg-theme {
  grid-template-columns: 30px 30px;
}
.thumb {
  position: absolute;
  top: 3px;
  left: 3px;
  width: calc(50% - 3px);
  height: calc(100% - 6px);
  border-radius: 8px;
  background: var(--paper);
  box-shadow: 0 2px 8px -2px rgba(var(--shade-rgb), 0.25);
  transform: translateX(calc(var(--i) * 100%));
  transition: transform 0.25s cubic-bezier(0.3, 0.7, 0.4, 1);
}
.seg button {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  height: 28px;
  padding: 0;
  border: none;
  background: none;
  font: inherit;
  font-size: 12.5px;
  color: var(--ink-3);
  cursor: pointer;
  transition: color 0.2s;
}
.seg button.on {
  color: var(--accent);
  font-weight: 600;
}
.seg button:focus-visible {
  outline: 2px solid var(--accent);
  border-radius: 8px;
}
.stamp {
  text-align: center;
  font-size: 10.5px;
  letter-spacing: 0.08em;
  color: var(--ink-3);
}

/* ---------------- 右侧 ---------------- */
.stage {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  min-height: 0;
}
.topbar {
  display: flex;
  align-items: flex-end;
  gap: 16px;
  padding: 4px 6px 0;
}
.crumb {
  display: flex;
  align-items: baseline;
  gap: 12px;
}
.crumb-no {
  font-size: 13px;
  font-weight: 600;
  color: var(--accent);
}
.crumb-icon {
  align-self: center;
  color: var(--accent);
}
.crumb h1 {
  margin: 0;
  font-size: 24px;
  font-weight: 650;
  letter-spacing: 0.01em;
}
.crumb-path {
  font-size: 12px;
  color: var(--ink-3);
}
.clock {
  margin-left: auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}
.clock-label {
  font-size: 11px;
  color: var(--ink-3);
}
.clock-time {
  font-size: 18px;
  font-weight: 500;
  letter-spacing: 0.04em;
}
.content {
  flex: 1;
  min-height: 0;
  overflow: auto;
  /* 留一点内边距，面板的阴影和角标不会被裁掉 */
  margin: -6px;
  padding: 6px;
}

/* 切换页面：淡入 + 轻微上移 */
.page-enter-active,
.page-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}
.page-enter-from {
  opacity: 0;
  transform: translateY(6px);
}
.page-leave-to {
  opacity: 0;
}

/* 窗口较窄时，导航栏收成只有图标 */
@media (max-width: 1180px) {
  .shell {
    grid-template-columns: 68px minmax(0, 1fr);
  }
  .rail {
    padding-inline: 8px;
  }
  .brand {
    justify-content: center;
    padding-inline: 0;
  }
  .brand-text,
  .nav-no,
  .nav-label,
  .nav-soon,
  .nav-group-label,
  .stamp {
    display: none;
  }
  .nav-group {
    margin-inline: 8px;
  }
  .nav-item {
    justify-content: center;
    padding: 0;
  }
  .nav-item.active::before {
    left: -8px;
  }
  /* icon-only rail: the status dot and the issue count sit on the icon's corner */
  .tool-dot,
  .tool-badge {
    position: absolute;
    top: 6px;
    right: 8px;
    margin: 0;
  }
  .tool-badge {
    min-width: 15px;
    height: 15px;
    padding: 0 3px;
    font-size: 10px;
    line-height: 15px;
    top: 3px;
    right: 4px;
  }
  .prefs,
  .seg,
  .seg-theme {
    grid-template-columns: 1fr;
  }
  .thumb {
    width: calc(100% - 6px);
    height: calc(50% - 3px);
    transform: translateY(calc(var(--i) * 100%));
  }
}
</style>
