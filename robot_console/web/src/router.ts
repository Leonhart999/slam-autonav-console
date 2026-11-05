// 页面路由。meta.titleKey 是菜单标题在语言包里的键，meta.step 是还没实现的页面计划在第几步做
import { createRouter, createWebHashHistory, type RouteRecordSingleView } from 'vue-router'
import MonitorView from '@/views/MonitorView.vue'
import MappingView from '@/views/MappingView.vue'
import NavigationView from '@/views/NavigationView.vue'
import MapsView from '@/views/MapsView.vue'
import EvaluationView from '@/views/EvaluationView.vue'

/** step: set only on pages that still show PlaceholderView (all pages are built now; kept for future pages) */
interface PageMeta {
  titleKey: string
  icon: string
  step?: number
}

export const pages: (RouteRecordSingleView & { meta: PageMeta })[] = [
  { path: '/mapping', name: 'mapping', component: MappingView, meta: { titleKey: 'nav.mapping', icon: 'MapLocation' } },
  { path: '/maps', name: 'maps', component: MapsView, meta: { titleKey: 'nav.maps', icon: 'Files' } },
  { path: '/navigation', name: 'navigation', component: NavigationView, meta: { titleKey: 'nav.navigation', icon: 'Position' } },
  { path: '/evaluation', name: 'evaluation', component: EvaluationView, meta: { titleKey: 'nav.evaluation', icon: 'DataAnalysis' } },
  { path: '/monitor', name: 'monitor', component: MonitorView, meta: { titleKey: 'nav.monitor', icon: 'Monitor' } },
]

export const router = createRouter({
  // hash 模式：地址形如 /#/monitor，后端只需提供 index.html，刷新页面不会 404
  history: createWebHashHistory(),
  routes: [{ path: '/', redirect: '/monitor' }, ...pages],
})
