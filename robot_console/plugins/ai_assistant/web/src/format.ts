// Number, money and time formatting shared by the history list and the usage page
import type { Money } from './api'
import { RC, t } from './sdk'

/** 1,284 / 12.4k / 3.21M */
export function compact(n: number | null | undefined): string {
  const v = Number(n) || 0
  const a = Math.abs(v)
  if (a < 1000) return String(Math.round(v))
  if (a < 1e6) return `${+(v / 1e3).toFixed(a < 1e4 ? 2 : 1)}k`
  if (a < 1e9) return `${+(v / 1e6).toFixed(a < 1e7 ? 2 : 1)}M`
  return `${+(v / 1e9).toFixed(2)}B`
}

export const grouped = (n: number | null | undefined) => (Number(n) || 0).toLocaleString('en-US')

const SYMBOL: Record<string, string> = { USD: '$', CNY: '¥' }

/** 4 significant digits, the currency sign added here (texts must not contain a dollar sign) */
export function money(m: Money): string {
  const sym = SYMBOL[m.currency] ?? `${m.currency} `
  const a = Math.abs(m.amount)
  const digits = a === 0 ? 2 : a >= 100 ? 2 : Math.max(2, Math.min(6, 3 - Math.floor(Math.log10(a))))
  return `${sym}${m.amount.toFixed(digits)}`
}

export const moneyList = (list: Money[] | null | undefined) =>
  (list ?? []).filter((m) => m.amount > 0 || (list ?? []).length === 1).map(money).join(' + ')

export function seconds(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`
}

/** "3 minutes ago" style, from unix seconds */
export function relative(ts: number, now = Date.now() / 1000): string {
  const d = Math.max(0, now - ts)
  if (d < 60) return t('time.now')
  if (d < 3600) return t('time.minutes', { n: Math.floor(d / 60) })
  if (d < 86400) return t('time.hours', { n: Math.floor(d / 3600) })
  if (d < 86400 * 30) return t('time.days', { n: Math.floor(d / 86400) })
  return day(ts)
}

export function day(ts: number): string {
  const d = new Date(ts * 1000)
  const same = d.getFullYear() === new Date().getFullYear()
  const md = `${d.getMonth() + 1}/${d.getDate()}`
  return same ? md : `${String(d.getFullYear()).slice(2)}/${md}`
}

export const hour = (ts: number) => `${String(new Date(ts * 1000).getHours()).padStart(2, '0')}:00`

export const isZh = () => RC().i18n.locale.value.startsWith('zh')

/** Short model name for dense rows: drops a vendor prefix like "anthropic/" */
export const shortModel = (m: string) => (m || '').replace(/^.*\//, '')
