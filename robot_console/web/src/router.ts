// 页面路由。meta.titleKey 是菜单标题在语言包里的键，meta.step 是还没实现的页面计划在第几步做
import type { Component } from 'vue'
import { MapLocation, VideoPlay, Files, Position, DataAnalysis, Monitor, Box } from '@element-plus/icons-vue'
import { createRouter, createWebHashHistory, type RouteRecordSingleView } from 'vue-router'
import { startPath } from '@/settings/prefs'
const MonitorView = () => import('@/views/MonitorView.vue')
const MappingView = () => import('@/views/MappingView.vue')
const PlaybackView = () => import('@/views/PlaybackView.vue')
const NavigationView = () => import('@/views/NavigationView.vue')
const MapsView = () => import('@/views/MapsView.vue')
const EvaluationView = () => import('@/views/EvaluationView.vue')
const PluginsView = () => import('@/views/PluginsView.vue')
const PluginPageHost = () => import('@/views/PluginPageHost.vue')
const SettingsView = () => import('@/views/SettingsView.vue')

/** step: set only on pages that still show PlaceholderView (all pages are built now; kept for future pages) */
interface PageMeta {
  titleKey: string
  icon: Component
  step?: number
  /** foot: a tool page in the bottom group of the side menu (no number), otherwise a numbered workflow page */
  place?: 'foot'
}

export const pages: (RouteRecordSingleView & { meta: PageMeta })[] = [
  {
    path: '/mapping', name: 'mapping', component: MappingView, meta: { titleKey: 'nav.mapping', icon: MapLocation },
    // bag playback used to be a mode of the mapping page (?source=bag)
    beforeEnter: (to) => (to.query.source === 'bag' ? { path: '/playback' } : true),
  },
  { path: '/playback', name: 'playback', component: PlaybackView, meta: { titleKey: 'nav.playback', icon: VideoPlay } },
  { path: '/maps', name: 'maps', component: MapsView, meta: { titleKey: 'nav.maps', icon: Files } },
  { path: '/navigation', name: 'navigation', component: NavigationView, meta: { titleKey: 'nav.navigation', icon: Position } },
  { path: '/evaluation', name: 'evaluation', component: EvaluationView, meta: { titleKey: 'nav.evaluation', icon: DataAnalysis } },
  { path: '/monitor', name: 'monitor', component: MonitorView, meta: { titleKey: 'nav.monitor', icon: Monitor, place: 'foot' } },
  { path: '/plugins', name: 'plugins', component: PluginsView, meta: { titleKey: 'nav.plugins', icon: Box, place: 'foot' } },
]

/** Numbered workflow pages (top of the side menu) and tool pages (bottom group) */
export const mainPages = pages.filter((p) => p.meta.place !== 'foot')
export const footPages = pages.filter((p) => p.meta.place === 'foot')

export const router = createRouter({
  // hash 模式：地址形如 /#/monitor，后端只需提供 index.html，刷新页面不会 404
  history: createWebHashHistory(),
  // Pages added by plugins (plugins/host.ts) are not in `pages`: one route shows them all, also before they load
  // '/' leads to the start page chosen in Settings → General (the last page, or a fixed one)
  routes: [
    { path: '/', redirect: () => (import.meta.env.MODE === 'console-preview' ? '/mapping'
      : startPath((p) => pages.some((x) => x.path === p) || /^\/ext\/[\w-]+\/[\w-]+$/.test(p))) },
    ...pages,
    { path: '/ext/:id/:page', name: 'plugin-page', component: PluginPageHost },
    // Settings: the side menu shows the settings sections while one of these is open (App.vue)
    { path: '/settings', redirect: '/settings/general' },
    { path: '/settings/:section', name: 'settings', component: SettingsView },
  ],
})
