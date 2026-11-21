// A damped spring (mass 1) stepped on requestAnimationFrame. Used for dragging the panel and for the like button.
//   x'' = -stiffness * (x - target) - damping * x'
// Damping ratio = damping / (2 * sqrt(stiffness)): >= 1 settles without overshoot, < 1 bounces.

export interface SpringConfig {
  stiffness: number
  damping: number
  /** Stop when both |x - target| and |v| fall below this */
  precision: number
}

/** Panel drag: damping ratio 80 / (2 * sqrt(1200)) = 1.15, over-damped, so it never overshoots */
export const DRAG_SPRING: SpringConfig = { stiffness: 1200, damping: 80, precision: 0.05 }
/** Like button: damping ratio 18 / (2 * sqrt(380)) = 0.46, one small bounce */
export const LIKE_SPRING: SpringConfig = { stiffness: 380, damping: 18, precision: 0.0005 }

export const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

const STEP = 1 / 480 // fixed sub-step in seconds: stable for these stiffness / damping values at any frame rate

export function createSpring(initial: number, cfg: SpringConfig, onUpdate: (x: number) => void) {
  let x = initial
  let v = 0
  let target = initial
  let raf = 0
  let last = 0

  function frame(now: number) {
    let dt = Math.min(0.064, (now - last) / 1000) // a backgrounded tab must not integrate a huge step
    last = now
    while (dt > 1e-9) {
      const h = Math.min(STEP, dt)
      v += (-cfg.stiffness * (x - target) - cfg.damping * v) * h
      x += v * h
      dt -= h
    }
    if (Math.abs(x - target) < cfg.precision && Math.abs(v) < cfg.precision) {
      x = target
      v = 0
      raf = 0
      onUpdate(x)
      return
    }
    onUpdate(x)
    raf = requestAnimationFrame(frame)
  }

  function run() {
    if (raf) return
    last = performance.now()
    raf = requestAnimationFrame(frame)
  }

  return {
    get value() { return x },
    get target() { return target },
    /** Move towards `to`; `from` / `velocity` start the motion from a given state */
    to(to: number, opts: { from?: number; velocity?: number } = {}) {
      target = to
      if (opts.from !== undefined) x = opts.from
      if (opts.velocity !== undefined) v = opts.velocity
      if (reducedMotion()) { this.jump(to); return }
      run()
    },
    /** Place at `to` immediately, no animation */
    jump(to: number) {
      if (raf) cancelAnimationFrame(raf)
      raf = 0
      x = target = to
      v = 0
      onUpdate(x)
    },
    stop() {
      if (raf) cancelAnimationFrame(raf)
      raf = 0
    },
  }
}
