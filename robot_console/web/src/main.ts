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
import { initPrefs } from './settings/prefs'
import { startPlugins } from './plugins/start'

async function start() {
  const preview = import.meta.env.MODE === 'console-preview'
  if (preview) {
    try {
      const { installConsolePreview } = await import('./console-preview/install')
      await installConsolePreview()
    } catch {
      const root = document.querySelector('#app')
      if (root) root.textContent = i18n.global.t('app.previewFailed')
      return
    }
  }
  const app = createApp(App)
  app.use(createPinia())
  app.use(router)
  app.use(i18n)
  applyDocumentLang()
  initTheme()
  initPrefs()
  app.mount('#app')
  if (!preview) startPlugins()
}

void start()
