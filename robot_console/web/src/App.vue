<!-- 公共框架：左侧导航栏（品牌、页面菜单、语言切换），右侧页头 + 页面内容 + 底部状态栏。
     视觉风格是白色磨砂玻璃 + 科幻 HUD，颜色等变量在 style.css -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import enLocale from 'element-plus/es/locale/lang/en'
import { pages } from './router'
import { LANGS, setLang } from './i18n'
import { useStatusStore } from './stores/status'
import StatusBar from './components/StatusBar.vue'
import RadarMark from './components/RadarMark.vue'

const { t, locale } = useI18n()
const route = useRoute()
const store = useStatusStore()

// Element Plus 自带文字（表格"暂无数据"等）跟随界面语言
const epLocale = computed(() => (locale.value === 'en' ? enLocale : zhCn))
// 语言切换滑块停在第几个按钮上
const langIndex = computed(() => Math.max(0, LANGS.findIndex((l) => l.value === locale.value)))

const no = (i: number) => String(i + 1).padStart(2, '0')
// 页头：当前页面在菜单里的序号和标题
const current = computed(() => {
  const i = pages.findIndex((p) => p.path === route.path)
  return i < 0 ? null : { no: no(i), title: t(pages[i].meta.titleKey) }
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
            v-for="(p, i) in pages" :key="p.path" :to="p.path" class="nav-item"
            active-class="active" :class="{ planned: p.meta.step }" :title="t(p.meta.titleKey)"
          >
            <span class="nav-no mono">{{ no(i) }}</span>
            <el-icon class="nav-icon" :size="17"><component :is="p.meta.icon" /></el-icon>
            <span class="nav-label">{{ t(p.meta.titleKey) }}</span>
            <span v-if="p.meta.step" class="nav-soon">{{ t('planned.badge') }}</span>
          </router-link>
        </nav>

        <div class="rail-foot">
          <div class="lang" role="group" :aria-label="t('app.language')" :style="{ '--i': langIndex }">
            <span class="thumb" />
            <button
              v-for="l in LANGS" :key="l.value" :class="{ on: locale === l.value }"
              :aria-pressed="locale === l.value" @click="setLang(l.value)"
            >{{ l.label }}</button>
          </div>
          <div class="stamp mono">ROS 2 · Humble</div>
        </div>
      </aside>

      <div class="stage">
        <header class="topbar">
          <div v-if="current" class="crumb">
            <span class="crumb-no mono">{{ current.no }}</span>
            <h1>{{ current.title }}</h1>
            <span class="crumb-path mono">{{ route.path }}</span>
          </div>
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
}
.nav-item {
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
  background: rgba(255, 255, 255, 0.6);
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
  box-shadow: 0 0 0 1px rgba(8, 145, 178, 0.12), 0 6px 16px -10px rgba(8, 145, 178, 0.6);
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

.rail-foot {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 12px;
  border-top: 1px solid var(--hairline);
}
/* 语言切换：分段按钮，滑块滑到当前语言 */
.lang {
  position: relative;
  display: grid;
  grid-template-columns: 1fr 1fr;
  padding: 3px;
  border-radius: 11px;
  background: rgba(15, 30, 60, 0.06);
}
.thumb {
  position: absolute;
  top: 3px;
  left: 3px;
  width: calc(50% - 3px);
  height: calc(100% - 6px);
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 2px 8px -2px rgba(20, 60, 120, 0.25);
  transform: translateX(calc(var(--i) * 100%));
  transition: transform 0.25s cubic-bezier(0.3, 0.7, 0.4, 1);
}
.lang button {
  position: relative;
  height: 28px;
  border: none;
  background: none;
  font: inherit;
  font-size: 12.5px;
  color: var(--ink-3);
  cursor: pointer;
  transition: color 0.2s;
}
.lang button.on {
  color: var(--accent);
  font-weight: 600;
}
.lang button:focus-visible {
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
  .stamp {
    display: none;
  }
  .nav-item {
    justify-content: center;
    padding: 0;
  }
  .nav-item.active::before {
    left: -8px;
  }
  .lang {
    grid-template-columns: 1fr;
  }
  .thumb {
    width: calc(100% - 6px);
    height: calc(50% - 3px);
    transform: translateY(calc(var(--i) * 100%));
  }
}
</style>
