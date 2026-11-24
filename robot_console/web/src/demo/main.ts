// Entry of the static web demo (demo/index.html, built with vite.demo.config.ts). It reuses the console's
// look and 3D view but has its own small page and texts, and reads recorded data instead of a backend.
import { createApp } from 'vue'
import 'element-plus/theme-chalk/dark/css-vars.css'
import '@/style.css'

import DemoApp from './DemoApp.vue'
import { applyDocumentLang, i18n } from './i18n'
import { initTheme } from '@/theme'

const app = createApp(DemoApp)
app.use(i18n)
applyDocumentLang()
initTheme()
app.mount('#app')
