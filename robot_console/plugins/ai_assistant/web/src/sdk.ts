// What this plugin uses of window.RobotConsole (plugin interface v1, docs/plugins.md)
import type { Component, ComputedRef } from 'vue'

export interface Bilingual { zh: string; en: string }

export interface RobotConsoleSdk {
  apiVersion: number
  request: <T = unknown>(method: string, path: string, body?: unknown, signal?: AbortSignal,
    options?: { timeout?: number }) => Promise<T>
  errorText: (e: unknown) => string
  ui: Record<string, Component> & {
    ElMessage: { success: (m: string) => void; error: (m: string) => void }
    ElMessageBox: { confirm: (message: string, title: string, options?: Record<string, unknown>) => Promise<unknown> }
  }
  i18n: {
    useT: (id: string) => (key: string, params?: Record<string, unknown>) => string
    pick: (b: Bilingual) => string
    locale: ComputedRef<string>
  }
  register: (def: {
    id: string
    messages?: Record<string, unknown>
    pages?: { path: string; title: Bilingual | string; component: Component }[]
    header?: Component[]
  }) => void
}

declare global {
  interface Window { RobotConsole: RobotConsoleSdk }
}

export const PLUGIN_ID = 'ai_assistant'
export const RC = () => window.RobotConsole
export const t = (key: string, params?: Record<string, unknown>) => RC().i18n.useT(PLUGIN_ID)(key, params)

/** Text of a coded error from this plugin's backend (codes are plugin.ai_assistant.<code> or plain <code>) */
export function codeText(code: string, params: Record<string, unknown> = {}) {
  const short = code.replace(/^plugin\.ai_assistant\./, '')
  const text = t(`errors.${short}`, params)
  return text === `plugins.${PLUGIN_ID}.errors.${short}` ? t('errors.unknown', { reason: code }) : text
}
