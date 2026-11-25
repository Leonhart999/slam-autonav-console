// Sign-in providers of the embedded CPA engine (CLIProxyAPI v8), in the order the OAuth tab shows them.
// device = the service shows a code the user types in at a verification page; browser = a sign-in page that redirects
// back to a local callback port (which may not reach the WSL backend: the dialog then takes the pasted address).
import type { OAuthProvider } from './api'

export interface ProviderCard {
  id: OAuthProvider
  name: string
  flow: 'device' | 'browser'
  color: string               // avatar colour (presets.avatarColor names)
  domain?: string
  /** subscription sign-in that the provider's terms may not allow in third-party tools */
  termsNote?: boolean
  /** CPA credential "provider"/"type" values that belong to this card */
  matches: string[]
}

export const KIMI: ProviderCard[] = [
  { id: 'kimi', name: 'Kimi', flow: 'device', color: 'slate', domain: 'kimi.com', matches: ['kimi'] },
  { id: 'kimi-ai', name: 'Kimi', flow: 'device', color: 'slate', domain: 'kimi.ai', matches: ['kimi-ai', 'kimi_ai'] },
]

export const DEVICE: ProviderCard[] = [
  { id: 'copilot', name: 'GitHub Copilot', flow: 'device', color: 'slate', termsNote: true, matches: ['copilot', 'github-copilot'] },
  { id: 'meta', name: 'Muse (Meta)', flow: 'device', color: 'blue', matches: ['meta', 'muse'] },
  // CPA v8.1.2 starts xAI with is_webui but answers with a device code
  { id: 'xai', name: 'xAI', flow: 'device', color: 'slate', matches: ['xai', 'grok'] },
]

export const BROWSER: ProviderCard[] = [
  { id: 'codex', name: 'Codex', flow: 'browser', color: 'teal', matches: ['codex', 'openai'] },
  { id: 'claude', name: 'Claude', flow: 'browser', color: 'orange', termsNote: true, matches: ['claude', 'anthropic'] },
  { id: 'antigravity', name: 'Antigravity', flow: 'browser', color: 'indigo', termsNote: true, matches: ['antigravity', 'gemini-cli'] },
  { id: 'devin', name: 'Devin', flow: 'browser', color: 'green', matches: ['devin'] },
]

export const ALL_CARDS = [...KIMI, ...DEVICE, ...BROWSER]

/** The card a credential belongs to (by its provider / type field) */
export function cardOf(provider: string | undefined): ProviderCard | null {
  const p = (provider || '').toLowerCase()
  if (!p) return null
  return ALL_CARDS.find((c) => c.matches.includes(p)) ?? ALL_CARDS.find((c) => c.matches.some((m) => p.startsWith(m))) ?? null
}

export const VERTEX_COLOR = 'blue'
