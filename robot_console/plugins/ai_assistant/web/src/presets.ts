// Starting points for a new model service (like cc-switch's provider presets). Only addresses and websites: models
// change too often to hard-code, so the editor fetches them from the service ("Fetch model list").
import type { Effort, EffortValue, ProfileInput } from './api'

export interface Preset {
  key: string
  name: string
  format: 'openai' | 'anthropic'
  base_url: string
  website: string
  color: string
}

export const PRESETS: Preset[] = [
  { key: 'custom', name: '', format: 'openai', base_url: '', website: '', color: '' },
  { key: 'openai', name: 'OpenAI', format: 'openai', base_url: 'https://api.openai.com/v1', website: 'https://platform.openai.com', color: 'teal' },
  { key: 'anthropic', name: 'Claude', format: 'anthropic', base_url: '', website: 'https://console.anthropic.com', color: 'orange' },
  { key: 'deepseek', name: 'DeepSeek', format: 'openai', base_url: 'https://api.deepseek.com/v1', website: 'https://platform.deepseek.com', color: 'blue' },
  { key: 'qwen', name: 'Qwen', format: 'openai', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', website: 'https://bailian.console.aliyun.com', color: 'violet' },
  { key: 'kimi', name: 'Kimi', format: 'openai', base_url: 'https://api.moonshot.cn/v1', website: 'https://platform.moonshot.cn', color: 'slate' },
  { key: 'zhipu', name: 'Zhipu GLM', format: 'openai', base_url: 'https://open.bigmodel.cn/api/paas/v4', website: 'https://open.bigmodel.cn', color: 'indigo' },
  { key: 'siliconflow', name: 'SiliconFlow', format: 'openai', base_url: 'https://api.siliconflow.cn/v1', website: 'https://cloud.siliconflow.cn', color: 'pink' },
  { key: 'openrouter', name: 'OpenRouter', format: 'openai', base_url: 'https://openrouter.ai/api/v1', website: 'https://openrouter.ai', color: 'cyan' },
  { key: 'ollama', name: 'Ollama', format: 'openai', base_url: 'http://localhost:11434/v1', website: 'https://ollama.com', color: 'green' },
]

export const COLORS = ['cyan', 'blue', 'indigo', 'violet', 'pink', 'red', 'orange', 'amber', 'green', 'teal', 'slate']
const HUES: Record<string, string> = {
  cyan: '190 80% 42%', blue: '217 80% 52%', indigo: '239 65% 58%', violet: '262 62% 58%', pink: '330 70% 55%',
  red: '0 70% 54%', orange: '24 88% 52%', amber: '40 90% 46%', green: '152 60% 38%', teal: '174 70% 36%',
  slate: '215 20% 45%',
}

/** Colour of a profile's avatar: its own, or one picked from the name so it stays the same */
export function avatarColor(color: string, name: string) {
  let key = color
  if (!HUES[key]) {
    let h = 0
    for (const ch of name) h = (h * 31 + ch.codePointAt(0)!) >>> 0
    key = COLORS[h % COLORS.length]
  }
  return `hsl(${HUES[key]})`
}

export const initial = (name: string) => (Array.from(name.trim())[0] ?? '?').toUpperCase()

export const EFFORT_VALUES: EffortValue[] = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra']
export const EFFORTS: Effort[] = ['', ...EFFORT_VALUES]

/** Compact summary of checked reasoning effort levels (e.g. "low -> ultra" when contiguous, else "none, high, max") */
export function effortSummary(efforts: EffortValue[], fallback: Effort = '', emptyText = ''): string {
  const ordered = EFFORT_VALUES.filter((e) => efforts.includes(e))
  if (!ordered.length) return fallback || emptyText
  if (ordered.length === 1) return ordered[0]
  const first = EFFORT_VALUES.indexOf(ordered[0])
  const last = EFFORT_VALUES.indexOf(ordered[ordered.length - 1])
  if (ordered.length >= 3 && last - first + 1 === ordered.length) return `${ordered[0]} → ${ordered[ordered.length - 1]}`
  return ordered.join(', ')
}

export const USER_AGENTS = [
  { key: 'chrome', value: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36' },
  { key: 'firefox', value: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) Gecko/20100101 Firefox/145.0' },
  { key: 'curl', value: 'curl/8.11.0' },
]

/** A new profile from a preset; `name` is the preset's name in the current language */
export function blankProfile(p: Preset = PRESETS[0], name = p.name): ProfileInput {
  return {
    name, note: '', website: p.website, color: p.color, format: p.format, base_url: p.base_url, endpoints: [],
    full_url: false, models: [], model: '', proxy: '', user_agent: '', headers: {}, body: {}, api_key: '',
  }
}
