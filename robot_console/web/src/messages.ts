// 把后端返回的代码（错误、进程停止结果）翻译成当前语言的文字
import { ApiError } from './api'
import { i18n, pick } from './i18n'
import { useStatusStore } from './stores/status'
import type { CodedMsg } from './types'

/** 参数里的进程名（如 sim）换成当前语言的显示名（如"仿真"/"Simulation"） */
function withLabels(params: CodedMsg['params']) {
  if (typeof params.name !== 'string') return params
  const stack = useStatusStore().status?.stacks.find((s) => s.name === params.name)
  return stack ? { ...params, name: pick(stack.label) } : params
}

export function resultText(m: CodedMsg): string {
  const key = `result.${m.code}`
  return i18n.global.te(key) ? i18n.global.t(key, withLabels(m.params)) : m.code
}

export function errorText(e: unknown): string {
  const t = i18n.global.t
  if (e instanceof ApiError) {
    const key = `error.${e.coded.code}`
    // 导入参数中的 name 是用户文件名称，不能误替换成同名进程的标签。
    const params = e.coded.code.startsWith('import_') ? e.coded.params : withLabels(e.coded.params)
    return i18n.global.te(key) ? t(key, params) : t('error.unknown', { reason: e.coded.code })
  }
  // 网络错误等（例如后端没启动时 fetch 直接失败）
  return t('error.unknown', { reason: e instanceof Error ? e.message : String(e) })
}
