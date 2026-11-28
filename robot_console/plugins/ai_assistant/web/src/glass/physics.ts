// Springs for the liquid glass (from ClipDeck): a lens that glides between buttons, a drop that follows the selected
// segment, presses that squash. Each value is { x, v, t } (position, velocity, target), stepped in 4 ms sub-steps.
import { shown } from '../prefs'

export interface SpringValue { x: number; v: number; t: number }

export const STIFF = 360
export const SQUASH = 0.6
export const DAMP = 2 * Math.sqrt(STIFF) * (1 - 0.62 * SQUASH)
export const PRESS_STIFF = 900
export const PRESS_DAMP = 2 * Math.sqrt(PRESS_STIFF) * (0.55 - 0.35 * SQUASH)
export const VIS_DAMP = 2 * Math.sqrt(STIFF)
const SUB_STEP = 0.004

export const spring = (x = 0): SpringValue => ({ x, v: 0, t: x })
export const clamp01 = (n: number) => Math.max(0, Math.min(1, n))
export const settled = (s: SpringValue) => Math.abs(s.x - s.t) < 0.005 && Math.abs(s.v) < 0.005

export function step(s: SpringValue, dt: number, k: number, c: number) {
  const a = -k * (s.x - s.t) - c * s.v
  s.v += a * dt
  s.x += s.v * dt
}

export function snap(s: SpringValue) {
  s.x = s.t
  s.v = 0
}

export function substeps(dt: number, fn: (dt: number) => void) {
  const n = Math.max(1, Math.ceil(dt / SUB_STEP))
  for (let i = 0; i < n; i += 1) fn(dt / n)
}

let reduceQuery: MediaQueryList | null | undefined
export function noMotion() {
  if (reduceQuery === undefined) reduceQuery = window.matchMedia?.('(prefers-reduced-motion: reduce)') ?? null
  return !shown.value.animations || !!reduceQuery?.matches
}

/** requestAnimationFrame loop that runs while frame(dt) returns true; kick() starts it again */
export function loop(frame: (dt: number) => boolean) {
  let raf = 0
  let last = 0
  const tick = (now: number) => {
    const dt = last ? Math.min(0.034, (now - last) / 1000) : 1 / 60
    last = now
    if (frame(dt)) raf = requestAnimationFrame(tick)
    else { raf = 0; last = 0 }
  }
  return {
    kick() { if (!raf) raf = requestAnimationFrame(tick) },
    stop() { if (raf) cancelAnimationFrame(raf); raf = 0; last = 0 },
  }
}
