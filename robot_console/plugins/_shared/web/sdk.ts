// What the plugins in this repository use of window.RobotConsole (plugin interface v1, docs/plugins.md).
// vue and element-plus are not bundled: the build maps them to RobotConsole.vue / RobotConsole.ui.
import type { Component, ComputedRef } from 'vue'

export interface Bilingual { zh: string; en: string }
export interface Coded { code: string; params: Record<string, unknown> }

export interface RobotConsoleSdk {
  apiVersion: number
  ui: Record<string, Component>
  request: <T = unknown>(method: string, path: string, body?: unknown, signal?: AbortSignal,
    options?: { timeout?: number }) => Promise<T>
  errorText: (e: unknown) => string
  /** text of a {code, params} error (console codes and plugin.<id>.<code>); newer consoles only */
  codedText?: (m: Coded) => string
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

export const RC = () => window.RobotConsole
