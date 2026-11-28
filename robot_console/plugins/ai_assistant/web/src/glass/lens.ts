// Lenses that glide between buttons (ported from ClipDeck). Buttons marked data-lens inside the group are targets.
// useHoverLens: a round lens fades in under the hovered / keyboard-focused button and glides to the next one,
//   stretching with its speed and squashing on a press.
// useDropLens: a drop that always sits under the selected segment (aria-selected="true"), leaning a little toward
//   the hovered one.
import { onBeforeUnmount, watch, type Ref } from 'vue'
import { DAMP, PRESS_DAMP, PRESS_STIFF, SQUASH, STIFF, VIS_DAMP, clamp01, loop, noMotion, settled, snap, spring, step,
  substeps } from './physics'

const targetOf = (e: Event) => (e.target instanceof Element ? (e.target.closest('[data-lens]') as HTMLElement | null) : null)

export function useHoverLens(group: Ref<HTMLElement | null>, lensEl: Ref<HTMLElement | null>, enabled: Ref<boolean>) {
  let cleanup: (() => void) | null = null

  function bind() {
    cleanup?.()
    cleanup = null
    const g = group.value
    const l = lensEl.value
    if (!g || !l) return
    if (!enabled.value) { l.style.display = 'none'; return }
    const s = { x: spring(), vis: spring(), press: spring() }
    let half = l.offsetWidth / 2
    let measured = half > 0
    const paint = () => {
      if (s.vis.x < 0.01) { if (l.style.display !== 'none') l.style.display = 'none'; return }
      if (l.style.display === 'none') l.style.display = ''
      else if (!measured) { const w = l.offsetWidth; if (w > 0) { half = w / 2; measured = true } }
      const stretch = Math.min(Math.abs(s.x.v) / 900, 0.55) * SQUASH
      const grow = 0.55 + 0.45 * clamp01(s.vis.x)
      const sx = (1 + stretch + s.press.x * 0.16) * grow
      const sy = (1 - stretch * 0.45 - s.press.x * 0.16) * grow
      l.style.transform = `translateX(${(s.x.x - half).toFixed(2)}px) scale(${sx.toFixed(4)}, ${sy.toFixed(4)})`
      l.style.setProperty('--vis', clamp01(s.vis.x).toFixed(3))
    }
    const run = loop((dt) => {
      if (noMotion()) { snap(s.x); snap(s.vis); snap(s.press) }
      else substeps(dt, (h) => { step(s.x, h, STIFF, DAMP); step(s.vis, h, STIFF, VIS_DAMP); step(s.press, h, PRESS_STIFF, PRESS_DAMP) })
      paint()
      return !(settled(s.x) && settled(s.vis) && settled(s.press))
    })
    const goTo = (b: HTMLElement) => {
      const c = b.offsetLeft + b.offsetWidth / 2
      if (!half && b.offsetWidth > 0) half = b.offsetWidth / 2
      if (s.vis.x < 0.05) { s.x.x = c; s.x.v = 0 }
      s.x.t = c
      s.vis.t = 1
      run.kick()
    }
    const over = (e: PointerEvent) => { const b = targetOf(e); if (b && g.contains(b) && !(b as HTMLButtonElement).disabled) goTo(b) }
    const down = (e: PointerEvent) => { const b = targetOf(e); if (b && g.contains(b)) { goTo(b); s.press.v = 7; run.kick() } }
    const hide = () => { s.vis.t = 0; run.kick() }
    const focusIn = (e: FocusEvent) => { const b = targetOf(e); if (b && g.contains(b) && b.matches(':focus-visible')) goTo(b) }
    const focusOut = (e: FocusEvent) => { if (!(e.relatedTarget instanceof Node && g.contains(e.relatedTarget))) hide() }
    paint()
    g.addEventListener('pointerover', over)
    g.addEventListener('pointerdown', down)
    g.addEventListener('pointerleave', hide)
    g.addEventListener('focusin', focusIn)
    g.addEventListener('focusout', focusOut)
    cleanup = () => {
      run.stop()
      g.removeEventListener('pointerover', over)
      g.removeEventListener('pointerdown', down)
      g.removeEventListener('pointerleave', hide)
      g.removeEventListener('focusin', focusIn)
      g.removeEventListener('focusout', focusOut)
    }
  }

  watch([group, lensEl, enabled], bind, { flush: 'post', immediate: true })
  onBeforeUnmount(() => cleanup?.())
}

export function useDropLens(group: Ref<HTMLElement | null>, drop: Ref<HTMLElement | null>, selected: Ref<unknown>, enabled: Ref<boolean>) {
  let cleanup: (() => void) | null = null
  let select: ((jump: boolean) => void) | null = null

  function bind() {
    cleanup?.()
    cleanup = null
    select = null
    const g = group.value
    const d = drop.value
    if (!g || !d || !enabled.value) return
    const s = { x: spring(), press: spring() }
    let hovered: HTMLElement | null = null
    const current = () => g.querySelector<HTMLElement>('[data-lens][aria-selected="true"]')
    const paint = () => {
      const stretch = Math.min(Math.abs(s.x.v) / 1100, 0.6) * SQUASH
      const sx = 1 + stretch + s.press.x * 0.1
      const sy = 1 - stretch * 0.4 - s.press.x * 0.12
      d.style.transform = `translateX(${s.x.x.toFixed(2)}px) scale(${sx.toFixed(4)}, ${sy.toFixed(4)})`
    }
    const run = loop((dt) => {
      if (noMotion()) { snap(s.x); snap(s.press) }
      else substeps(dt, (h) => { step(s.x, h, STIFF, DAMP); step(s.press, h, PRESS_STIFF, PRESS_DAMP) })
      paint()
      return !(settled(s.x) && settled(s.press))
    })
    const place = (jump: boolean) => {
      const c = current()
      if (!c) return
      const left = c.offsetLeft
      const lean = ((hovered && hovered !== c ? hovered.offsetLeft : left) - left) * 0.1
      d.style.width = `${c.offsetWidth}px`
      s.x.t = left + lean
      if (jump) snap(s.x)
      run.kick()
    }
    select = (jump) => { if (!jump) s.press.v = Math.max(s.press.v, 5); place(jump) }
    const over = (e: PointerEvent) => { const b = targetOf(e); if (b && b !== hovered && g.contains(b)) { hovered = b; place(false) } }
    const leave = () => { hovered = null; place(false) }
    const down = (e: PointerEvent) => { if (targetOf(e)) { s.press.v = 5; run.kick() } }
    place(true)
    paint()
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => place(true))
    ro?.observe(g)
    g.addEventListener('pointerover', over)
    g.addEventListener('pointerleave', leave)
    g.addEventListener('pointerdown', down)
    cleanup = () => {
      run.stop()
      ro?.disconnect()
      g.removeEventListener('pointerover', over)
      g.removeEventListener('pointerleave', leave)
      g.removeEventListener('pointerdown', down)
    }
  }

  watch([group, drop, enabled], bind, { flush: 'post', immediate: true })
  watch(selected, () => select?.(false), { flush: 'post' })
  onBeforeUnmount(() => cleanup?.())
}
