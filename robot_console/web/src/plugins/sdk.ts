// window.RobotConsole: the frontend side of plugin interface v1 (docs/plugins.md).
// Plugins are pre-built scripts that use the console's own Vue and a fixed set of components, so their pages
// look like the rest of the console (same CSS variables, light/dark theme, Element Plus). Only add members here;
// changing or removing one breaks published plugins and needs plugin API 2.
import * as Vue from 'vue'
import { computed } from 'vue'
import {
  ElAlert, ElButton, ElCheckbox, ElDialog, ElDivider, ElDrawer, ElEmpty, ElForm, ElFormItem, ElIcon, ElInput,
  ElInputNumber, ElLink, ElMessage, ElMessageBox, ElOption, ElPopconfirm, ElProgress, ElRadio, ElRadioButton,
  ElRadioGroup, ElScrollbar, ElSelect, ElSlider, ElSwitch, ElTable, ElTableColumn, ElTag, ElTooltip,
} from 'element-plus'
// Explicit imports do not get their styles from unplugin-vue-components: import them here (deduplicated by Vite)
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/checkbox/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/divider/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/icon/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/link/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/popconfirm/style/css'
import 'element-plus/es/components/progress/style/css'
import 'element-plus/es/components/radio/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/radio-group/style/css'
import 'element-plus/es/components/scrollbar/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/slider/style/css'
import 'element-plus/es/components/switch/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/tooltip/style/css'
import GlassPanel from '@/components/GlassPanel.vue'
import InfoTip from '@/components/InfoTip.vue'
import KpiTile from '@/components/KpiTile.vue'
import { request } from '@/http'
import { i18n, pick } from '@/i18n'
import { errorText } from '@/messages'
import { ApiError } from '@/api'
import { effectiveBase, isDark, setTheme, theme, themeChoices } from '@/theme'
import type { CodedMsg } from '@/types'
import { register, setMessageMerger } from './host'

export const PLUGIN_API = 1

function buildSdk() {
  return Object.freeze({
    apiVersion: PLUGIN_API,
    vue: Vue,
    ui: Object.freeze({
      GlassPanel, KpiTile, InfoTip,
      ElAlert, ElButton, ElCheckbox, ElDialog, ElDivider, ElDrawer, ElEmpty, ElForm, ElFormItem, ElIcon, ElInput,
      ElInputNumber, ElLink, ElOption, ElPopconfirm, ElProgress, ElRadio, ElRadioButton, ElRadioGroup, ElScrollbar,
      ElSelect, ElSlider, ElSwitch, ElTable, ElTableColumn, ElTag, ElTooltip, ElMessage, ElMessageBox,
    }),
    /** JSON request with the console's timeouts; errors are ApiError, turn them into text with errorText().
     *  options.timeout (ms) overrides the default timeout, e.g. for slow saves */
    request: <T = unknown>(method: string, path: string, body?: unknown, signal?: AbortSignal,
      options?: { timeout?: number }) =>
      request<T>(method, path, body, {
        signal, validate: (v: unknown): v is T => v !== undefined,
        ...(options?.timeout ? { timeout: options.timeout } : {}),
      }),
    errorText,
    /** text of a {code, params} error object (e.g. a failed plugin job's error), like errorText() */
    codedText: (m: CodedMsg) => errorText(new ApiError(m)),
    i18n: Object.freeze({
      /** t() of one plugin: looks keys up in the messages the plugin registered */
      useT: (id: string) => (key: string, params: Record<string, unknown> = {}) => i18n.global.t(`plugins.${id}.${key}`, params),
      pick,
      locale: computed(() => i18n.global.locale.value),
    }),
    theme: Object.freeze({
      current: computed(() => theme.value),
      base: effectiveBase,
      isDark,
      choices: themeChoices,
      set: (next: string, origin?: { x: number; y: number }) => setTheme(next, origin),
    }),
    register,
  })
}

export type RobotConsoleSdk = ReturnType<typeof buildSdk>

declare global {
  interface Window { RobotConsole?: RobotConsoleSdk }
}

/** Put the SDK on window (once) and let registered messages go into the console's i18n under plugins.<id>. */
export function installSdk() {
  if (window.RobotConsole) return
  setMessageMerger((id, messages) => {
    for (const lang of ['zh-CN', 'en'] as const) {
      const m = messages[lang]
      if (m) i18n.global.mergeLocaleMessage(lang, { plugins: { [id]: m } } as never)
    }
  })
  Object.defineProperty(window, 'RobotConsole', { value: buildSdk(), writable: false, configurable: false })
}
