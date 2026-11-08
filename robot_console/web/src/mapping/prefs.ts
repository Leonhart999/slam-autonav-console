// Page settings of the mapping and playback pages kept in the browser (one localStorage entry, each page and
// component reads and writes only its own keys). Storage may be disabled: then nothing is kept.
const KEY = 'robot_console.mapping_view'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function loadPrefs(): Record<string, any> {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? '{}')
    return v && typeof v === 'object' ? v : {}
  } catch {
    return {}
  }
}

export function savePrefs(part: Record<string, unknown>) {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...loadPrefs(), ...part }))
  } catch {
    // 浏览器禁用了存储，不影响使用
  }
}
