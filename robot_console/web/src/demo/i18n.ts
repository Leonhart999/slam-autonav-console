// Language of the web demo: the same two languages and the same saved choice as the console
import { createI18n } from 'vue-i18n'
import { demoMessages } from './messages'

export type DemoLang = 'zh-CN' | 'en'
export const LANGS: { value: DemoLang; label: string }[] = [
  { value: 'zh-CN', label: '中文' },
  { value: 'en', label: 'EN' },
]
const LANG_KEY = 'robot_console.lang'

function initialLang(): DemoLang {
  try {
    const saved = localStorage.getItem(LANG_KEY)
    if (saved === 'zh-CN' || saved === 'en') return saved
  } catch { /* storage blocked: fall back to the browser language */ }
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en'
}

export const i18n = createI18n({
  legacy: false,
  locale: initialLang(),
  fallbackLocale: 'en',
  messages: demoMessages,
})

export function applyDocumentLang() {
  document.documentElement.lang = i18n.global.locale.value
  document.title = i18n.global.t('app.title')
}

export function setLang(lang: DemoLang) {
  i18n.global.locale.value = lang
  try {
    localStorage.setItem(LANG_KEY, lang)
  } catch { /* private mode: lasts until the page is closed */ }
  applyDocumentLang()
}
