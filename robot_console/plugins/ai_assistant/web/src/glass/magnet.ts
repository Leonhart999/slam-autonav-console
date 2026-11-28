// Magnetic liquid glass (from ClipDeck): under the pointer a control leans and stretches toward it, a spotlight
// follows the pointer, and a press squashes it; neighbours in the same .aia-magnet-group shrink a little.
// Values go to CSS variables (--lg-sx, --lg-sy, --lg-tx, --lg-ty, --lg-spot-x, --lg-spot-y) that glass.css uses.
let lastPointer: { x: number; y: number } | null = null
let tracking = false

function trackPointer() {
  if (tracking) return
  tracking = true
  window.addEventListener('pointermove', (e) => { lastPointer = { x: e.clientX, y: e.clientY } }, { capture: true, passive: true })
}

const written = new WeakMap<HTMLElement, Map<string, string>>()
function setVar(el: HTMLElement, name: string, value: string) {
  let m = written.get(el)
  if (!m) { m = new Map(); written.set(el, m) }
  if (m.get(name) !== value) { m.set(name, value); el.style.setProperty(name, value) }
}
const MOVE_VARS = ['--lg-tx', '--lg-ty', '--lg-sx', '--lg-sy']

/** Makes el magnetic; strength = elasticity / 0.15. Returns the cleanup. */
export function magnetize(el: HTMLElement, strength: number) {
  trackPointer()
  const target = (el.closest('.aia-magnet-target') as HTMLElement | null) ?? el
  const group = el.closest('.aia-magnet-group')
  let pressed = false
  let raf = 0
  let px = 0
  let py = 0
  const cancel = () => { if (raf) { cancelAnimationFrame(raf); raf = 0 } }
  const apply = (x: number, y: number) => {
    const r = target.getBoundingClientRect()
    if (!r.width || !r.height) return
    const u = ((x - r.left) / r.width - 0.5) * 2
    const v = ((y - r.top) / r.height - 0.5) * 2
    const grow = Math.abs(r.width - r.height) < 4 ? 0.1 : 0.045
    const lean = Math.abs(u) * strength
    const [sx, sy, tx, ty] = pressed
      ? [1 - grow * 0.6, 1 - grow * 0.9, u * 2 * strength, v * strength]
      : [1 + grow + lean * 0.012, 1 + grow - lean * 0.008, u * 4 * strength, v * 2.5 * strength - 1]
    const own = el.getBoundingClientRect()
    setVar(el, '--lg-sx', sx.toFixed(4))
    setVar(el, '--lg-sy', sy.toFixed(4))
    setVar(el, '--lg-tx', `${tx.toFixed(2)}px`)
    setVar(el, '--lg-ty', `${ty.toFixed(2)}px`)
    setVar(el, '--lg-spot-x', `${Math.round(x - own.left)}px`)
    setVar(el, '--lg-spot-y', `${Math.round(y - own.top)}px`)
  }
  const enter = (x: number, y: number) => {
    cancel()
    el.classList.add('is-hovered')
    group?.classList.add('is-magnet-on')
    apply(x, y)
  }
  const leave = () => {
    cancel()
    pressed = false
    el.classList.remove('is-hovered', 'is-pressed')
    for (const n of MOVE_VARS) { el.style.removeProperty(n); written.get(el)?.delete(n) }
    if (group && !group.querySelector('.aia-liquid.is-hovered')) group.classList.remove('is-magnet-on')
  }
  const onEnter = (e: PointerEvent) => enter(e.clientX, e.clientY)
  const onMove = (e: PointerEvent) => {
    px = e.clientX
    py = e.clientY
    if (!raf) raf = requestAnimationFrame(() => { raf = 0; apply(px, py) })
  }
  const onDown = (e: PointerEvent) => { cancel(); pressed = true; el.classList.add('is-pressed'); apply(e.clientX, e.clientY) }
  const onUp = (e: PointerEvent) => { cancel(); pressed = false; el.classList.remove('is-pressed'); apply(e.clientX, e.clientY) }
  target.addEventListener('pointerenter', onEnter)
  target.addEventListener('pointermove', onMove)
  target.addEventListener('pointerleave', leave)
  target.addEventListener('pointerdown', onDown)
  target.addEventListener('pointerup', onUp)
  if (lastPointer && target.matches(':hover')) enter(lastPointer.x, lastPointer.y)
  return () => {
    target.removeEventListener('pointerenter', onEnter)
    target.removeEventListener('pointermove', onMove)
    target.removeEventListener('pointerleave', leave)
    target.removeEventListener('pointerdown', onDown)
    target.removeEventListener('pointerup', onUp)
    leave()
  }
}
