// "Typing" reveal of a new answer: every character fades in from blur(6px) over 120 ms, each one 30 ms after the
// previous. The answer is rendered in full first (so its height is final and nothing jumps), its text nodes are split
// into one <span> per character, and the plain HTML is put back when the last character has arrived.
import type { Directive } from 'vue'
import { reducedMotion } from './spring'

export const CHAR_MS = 120
export const STAGGER_MS = 30

const segmenter = typeof Intl !== 'undefined' && 'Segmenter' in Intl ? new Intl.Segmenter(undefined, { granularity: 'grapheme' }) : null
const graphemes = (s: string) => (segmenter ? Array.from(segmenter.segment(s), (g) => g.segment) : Array.from(s))

interface State { html: string; timer: number }
const states = new WeakMap<HTMLElement, State>()

/** Put the plain HTML back (also used to skip the rest of the animation) */
export function finishReveal(el: HTMLElement) {
  const st = states.get(el)
  if (!st) return
  window.clearTimeout(st.timer)
  states.delete(el)
  el.innerHTML = st.html
  el.classList.remove('aia-revealing')
}

function start(el: HTMLElement) {
  const html = el.innerHTML
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
  const nodes: Text[] = []
  while (walker.nextNode()) nodes.push(walker.currentNode as Text)
  let n = 0
  for (const node of nodes) {
    if (!node.data.trim()) continue
    const frag = document.createDocumentFragment()
    let space = ''
    for (const g of graphemes(node.data)) {
      if (/^\s+$/.test(g)) { space += g; continue }
      if (space) { frag.append(space); space = '' }
      const span = document.createElement('span')
      span.className = 'aia-ch'
      span.style.animationDelay = `${n++ * STAGGER_MS}ms`
      span.textContent = g
      frag.append(span)
    }
    if (space) frag.append(space)
    node.replaceWith(frag)
  }
  if (!n) return
  el.classList.add('aia-revealing')
  const timer = window.setTimeout(() => finishReveal(el), (n - 1) * STAGGER_MS + CHAR_MS + 40)
  states.set(el, { html, timer })
}

/** v-reveal="true" animates the element's text once, when it is mounted */
export const vReveal: Directive<HTMLElement, boolean> = {
  mounted(el, binding) {
    if (binding.value && !reducedMotion()) start(el)
  },
  beforeUnmount(el) {
    const st = states.get(el)
    if (st) window.clearTimeout(st.timer)
    states.delete(el)
  },
}
