// Displacement maps for the liquid glass (from ClipDeck, after liquid-glass-react). For a rounded rectangle of a
// given size and corner radius, R/G/B encode how far the backdrop is pulled near the edge, so an SVG
// feDisplacementMap bends what lies under the control like the rim of a lens. An optional edge mask limits the
// chromatic aberration to the rim. Maps are drawn at most 480 px wide and cached (64 entries, least recently used).
import type { LiquidMode } from '../prefs'

export interface LiquidMaps { map: string; mask: string; shape: string }

const MAX_SIDE = 480
const CACHE_SIZE = 64
const cache = new Map<string, LiquidMaps | null>()

export function canvas2d(w: number, h: number) {
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  return { canvas, context: canvas.getContext('2d', { willReadFrequently: true }) }
}

function smooth(a: number, b: number, x: number) {
  const t = Math.max(0, Math.min(1, (x - a) / (b - a)))
  return t * t * (3 - 2 * t)
}

function shapeUrl(w: number, h: number, r: number) {
  const rr = Math.min(r, w / 2, h / 2)
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"><rect width="${w}" height="${h}" rx="${rr}" fill="#fff"/></svg>`
  return `data:image/svg+xml,${encodeURIComponent(svg)}`
}

function sdRoundBox(x: number, y: number, bx: number, by: number, r: number) {
  const qx = Math.abs(x) - bx + r
  const qy = Math.abs(y) - by + r
  return qx <= 0 && qy <= 0 ? Math.max(qx, qy) - r : Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) - r
}

/** The "shader" mode: a smooth lens over the whole surface */
function shaderMap(w: number, h: number) {
  if (w < 1 || h < 1) return null
  const s = Math.min(1, MAX_SIDE / Math.max(w, h))
  const cw = Math.max(1, Math.round(w * s))
  const ch = Math.max(1, Math.round(h * s))
  const { canvas, context } = canvas2d(cw, ch)
  if (!context) return null
  const d = new Float32Array(cw * ch * 2)
  let peak = 0
  for (let y = 0; y < ch; y += 1) {
    for (let x = 0; x < cw; x += 1) {
      const u = x / cw - 0.5
      const v = y / ch - 0.5
      const dist = sdRoundBox(u, v, 0.3, 0.2, 0.6)
      const k = smooth(0, 1, smooth(0.8, 0, dist - 0.15))
      const dx = (u * k + 0.5) * cw - x
      const dy = (v * k + 0.5) * ch - y
      peak = Math.max(peak, Math.abs(dx), Math.abs(dy))
      const i = (y * cw + x) * 2
      d[i] = dx
      d[i + 1] = dy
    }
  }
  peak = Math.max(peak, 1)
  const img = context.createImageData(cw, ch)
  for (let y = 0; y < ch; y += 1) {
    for (let x = 0; x < cw; x += 1) {
      const i = (y * cw + x) * 2
      const edge = Math.min(1, Math.min(x, y, cw - x - 1, ch - y - 1) / 2)
      const r = (d[i] * edge) / peak + 0.5
      const g = (d[i + 1] * edge) / peak + 0.5
      const o = (y * cw + x) * 4
      img.data[o] = Math.max(0, Math.min(255, r * 255))
      img.data[o + 1] = Math.max(0, Math.min(255, g * 255))
      img.data[o + 2] = Math.max(0, Math.min(255, g * 255))
      img.data[o + 3] = 255
    }
  }
  context.putImageData(img, 0, 0)
  return canvas.toDataURL()
}

function edgeMask(w: number, h: number, hx: number, hy: number, r: number, band: number) {
  const { canvas, context } = canvas2d(w, h)
  if (!context) return null
  const img = context.createImageData(w, h)
  for (let y = 0; y < h; y += 1) {
    const py = y + 0.5 - hy
    for (let x = 0; x < w; x += 1) {
      const px = x + 0.5 - hx
      const ex = Math.abs(px) - (hx - r)
      const ey = Math.abs(py) - (hy - r)
      const inside = ex <= 0 && ey <= 0 ? r - Math.max(ex, ey) : r - Math.hypot(Math.max(ex, 0), Math.max(ey, 0))
      if (inside < band) {
        const a = Math.max(0, Math.min(1, 1 - inside / band))
        const o = (y * w + x) * 4
        img.data[o] = 255
        img.data[o + 1] = 255
        img.data[o + 2] = 255
        img.data[o + 3] = Math.min(1, a * 1.5) * 255
      }
    }
  }
  context.putImageData(img, 0, 0)
  return canvas.toDataURL()
}

function build(mode: LiquidMode | 'lens', w: number, h: number, radius: number, withMask: boolean): LiquidMaps | null {
  if (w < 2 || h < 2) return null
  const s = Math.min(1, MAX_SIDE / Math.max(w, h))
  const cw = Math.max(2, Math.round(w * s))
  const ch = Math.max(2, Math.round(h * s))
  const hx = cw / 2
  const hy = ch / 2
  const r = Math.min(radius * s, hx, hy)
  const band = mode === 'prominent' ? Math.max(4, Math.min(hy, r * 1.6, 30 * s)) : Math.max(3, Math.min(r, 16 * s))
  if (mode === 'shader') {
    const map = shaderMap(cw, ch)
    if (map !== null) {
      const mask = withMask ? edgeMask(cw, ch, hx, hy, r, band) : ''
      return mask === null ? null : { map, mask, shape: shapeUrl(w, h, radius) }
    }
  }
  const { canvas, context } = canvas2d(cw, ch)
  if (!context) return null
  const maskCanvas = withMask ? canvas2d(cw, ch) : null
  if (maskCanvas && !maskCanvas.context) return null
  const img = context.createImageData(cw, ch)
  const maskImg = maskCanvas?.context ? maskCanvas.context.createImageData(cw, ch) : null
  for (let y = 0; y < ch; y += 1) {
    for (let x = 0; x < cw; x += 1) {
      const px = x + 0.5 - hx
      const py = y + 0.5 - hy
      let dx = 0, dy = 0, m = 0
      if (mode === 'polar' || mode === 'lens') {
        const u = px / hx
        const v = py / hy
        const dist = Math.hypot(u, v)
        const k = mode === 'lens' ? 0.3 + 0.35 * smooth(0.5, 1, dist) : smooth(0.35, 1, dist)
        dx = -u * k
        dy = -v * k
        m = k
      } else {
        const ex = Math.abs(px) - (hx - r)
        const ey = Math.abs(py) - (hy - r)
        const inside = ex <= 0 && ey <= 0 ? r - Math.max(ex, ey) : r - Math.hypot(Math.max(ex, 0), Math.max(ey, 0))
        if (inside < band) {
          let nx = 0, ny = 0
          if (ex > 0 && ey > 0) {
            const len = Math.hypot(ex, ey) || 1
            nx = (ex / len) * Math.sign(px)
            ny = (ey / len) * Math.sign(py)
          } else if (ex > ey) nx = Math.sign(px)
          else ny = Math.sign(py)
          const k = Math.max(0, Math.min(1, 1 - inside / band))
          const f = mode === 'prominent' ? k : k * k
          dx = -nx * f
          dy = -ny * f
          m = Math.min(1, k * 1.5)
        }
      }
      const o = (y * cw + x) * 4
      img.data[o] = 128 + dx * 127
      img.data[o + 1] = 128 + dy * 127
      img.data[o + 2] = 128 + dy * 127
      img.data[o + 3] = 255
      if (maskImg) {
        maskImg.data[o] = 255
        maskImg.data[o + 1] = 255
        maskImg.data[o + 2] = 255
        maskImg.data[o + 3] = m * 255
      }
    }
  }
  context.putImageData(img, 0, 0)
  let mask = ''
  if (maskCanvas?.context && maskImg) {
    maskCanvas.context.putImageData(maskImg, 0, 0)
    mask = maskCanvas.canvas.toDataURL()
  }
  return { map: canvas.toDataURL(), mask, shape: shapeUrl(w, h, radius) }
}

export function liquidMaps(mode: LiquidMode | 'lens', w: number, h: number, radius: number, withMask = true) {
  const key = `${mode} ${w} ${h} ${radius} ${withMask ? 1 : 0}`
  if (cache.has(key)) {
    const hit = cache.get(key) ?? null
    cache.delete(key)
    cache.set(key, hit)
    return hit
  }
  const made = build(mode, w, h, radius, withMask)
  if (cache.size >= CACHE_SIZE) {
    const oldest = cache.keys().next().value
    if (oldest !== undefined) cache.delete(oldest)
  }
  cache.set(key, made)
  return made
}
