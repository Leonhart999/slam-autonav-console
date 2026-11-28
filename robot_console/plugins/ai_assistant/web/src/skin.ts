// Pointer spotlight for rows in the glass skin: one listener on a container tells the row under the pointer where the
// pointer is (--mx, --my, px from the row's top left) and marks it data-lit; glass.css draws the highlight, the
// spotlight inside it and the running rim (ClipDeck's panel cards). The mark stays 400 ms after the pointer leaves,
// so the highlight fades out instead of blinking.
import type { Directive } from 'vue'
import { isGlass } from './prefs'

const ROWS = '.aia-hist-item, .aia-chip, .aip-item, .aip-preset'
const LINGER_MS = 400

interface SpotEl extends HTMLElement { _aiaSpot?: () => void }

export const vSpot: Directive<SpotEl> = {
  mounted(el) {
    let lit: HTMLElement | null = null
    let raf = 0
    let px = 0
    let py = 0
    const timers = new WeakMap<HTMLElement, number>()
    const unlight = (row: HTMLElement) => {
      timers.set(row, window.setTimeout(() => { delete row.dataset.lit }, LINGER_MS))
    }
    const light = (row: HTMLElement) => {
      window.clearTimeout(timers.get(row))
      row.dataset.lit = ''
      // restart the rim from the same place every time
      for (const a of row.querySelector('.aia-card-edge')?.getAnimations?.({ subtree: true }) ?? []) a.currentTime = 0
    }
    const move = (e: PointerEvent) => {
      if (!isGlass.value || e.pointerType === 'touch') return
      const row = (e.target as Element | null)?.closest?.(ROWS) as HTMLElement | null
      if (row !== lit) {
        if (lit) unlight(lit)
        lit = row
        if (row) light(row)
      }
      if (!row) return
      px = e.clientX
      py = e.clientY
      if (!raf) {
        raf = requestAnimationFrame(() => {
          raf = 0
          if (!lit) return
          const r = lit.getBoundingClientRect()
          lit.style.setProperty('--mx', `${Math.round(px - r.left)}px`)
          lit.style.setProperty('--my', `${Math.round(py - r.top)}px`)
        })
      }
    }
    const leave = () => {
      if (lit) unlight(lit)
      lit = null
    }
    el.addEventListener('pointermove', move, { passive: true })
    el.addEventListener('pointerleave', leave)
    el._aiaSpot = () => {
      cancelAnimationFrame(raf)
      el.removeEventListener('pointermove', move)
      el.removeEventListener('pointerleave', leave)
    }
  },
  unmounted(el) {
    el._aiaSpot?.()
  },
}
