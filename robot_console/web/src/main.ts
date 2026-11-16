import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import './style.css'

import App from './App.vue'
import { router } from './router'
import { applyDocumentLang, i18n } from './i18n'
import { initTheme } from './theme'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(i18n)
applyDocumentLang()
initTheme()
app.mount('#app')
