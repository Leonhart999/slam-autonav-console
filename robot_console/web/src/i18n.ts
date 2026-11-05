// 界面语言：中文 / English。选择保存在浏览器 localStorage 里，下次打开沿用
import { createI18n } from 'vue-i18n'
import zhCN from './locales/zh-CN'
import en from './locales/en'
import type { Bilingual } from './types'

export type Lang = 'zh-CN' | 'en'
export const LANGS: { value: Lang; label: string }[] = [
  { value: 'zh-CN', label: '中文' },
  { value: 'en', label: 'EN' },
]

const STORAGE_KEY = 'robot_console.lang'

function initialLang(): Lang {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'zh-CN' || saved === 'en') return saved
  // 第一次打开：浏览器是中文就用中文，否则用英文
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en'
}

export const i18n = createI18n({
  legacy: false, // 用组合式 API（useI18n）
  locale: initialLang(),
  fallbackLocale: 'zh-CN',
  messages: { 'zh-CN': zhCN, en },
})

/** 切换语言：更新界面、网页标题和 <html lang>，并记住选择 */
export function setLang(lang: Lang) {
  i18n.global.locale.value = lang
  localStorage.setItem(STORAGE_KEY, lang)
  applyDocumentLang()
}

export function applyDocumentLang() {
  document.documentElement.lang = i18n.global.locale.value
  document.title = i18n.global.t('app.title')
}

/** 从后端给的 {zh, en} 里取当前语言的文字 */
export function pick(text: Bilingual): string {
  return i18n.global.locale.value === 'en' ? text.en : text.zh
}
