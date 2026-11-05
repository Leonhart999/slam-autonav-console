import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import * as Icons from '@element-plus/icons-vue'
import 'element-plus/dist/index.css'
import './style.css'

import App from './App.vue'
import { router } from './router'
import { applyDocumentLang, i18n } from './i18n'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(i18n)
// Element Plus 组件自带文字（如"暂无数据"）的语言在 App.vue 的 el-config-provider 里随界面语言切换
app.use(ElementPlus)
// 注册全部图标组件，菜单里按名字使用（如 <component is="Monitor" />）
for (const [name, comp] of Object.entries(Icons)) app.component(name, comp)
applyDocumentLang()
app.mount('#app')
