// SDK bridge for eye_care_ui (plugin interface v1)
import type { Component, ComputedRef } from 'vue'

export interface Bilingual { zh: string; en: string }

export interface PluginThemeDef {
  id: string
  label: Bilingual | string
  base: 'light' | 'dark'
  pageColor?: string
  tokens?: Record<string, string>
  scene?: Record<string, unknown>
  charts?: Record<string, unknown>
}

export interface RobotConsoleSdk {
  apiVersion: number
  request: <T = unknown>(method: string, path: string, body?: unknown, signal?: AbortSignal,
    options?: { timeout?: number }) => Promise<T>
  errorText: (e: unknown) => string
  ui: Record<string, Component> & {
    ElMessage: { success: (m: string) => void; error: (m: string) => void }
  }
  i18n: {
    useT: (id: string) => (key: string, params?: Record<string, unknown>) => string
    pick: (b: Bilingual) => string
    locale: ComputedRef<string>
  }
  theme: {
    current: ComputedRef<string>
    base: ComputedRef<'light' | 'dark'>
    isDark: ComputedRef<boolean>
    choices: ComputedRef<{ id: string; builtin: boolean; base: 'light' | 'dark'; label: Bilingual }[]>
    set: (next: string, origin?: { x: number; y: number }) => Promise<void>
  }
  register: (def: {
    id: string
    messages?: Record<string, unknown>
    pages?: { path: string; title: Bilingual | string; component: Component }[]
    header?: Component[]
    themes?: PluginThemeDef[]
  }) => void
}

declare global {
  interface Window { RobotConsole: RobotConsoleSdk }
}

export const PLUGIN_ID = 'eye_care_ui'
export const THEME_ID = 'eye_care'
export const RC = () => window.RobotConsole
export const t = (key: string, params?: Record<string, unknown>) => RC().i18n.useT(PLUGIN_ID)(key, params)
