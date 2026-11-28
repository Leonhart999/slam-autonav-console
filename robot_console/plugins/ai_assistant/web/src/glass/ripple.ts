// A ring of liquid that runs out from a point across an element (from ClipDeck): an SVG displacement filter whose
// ring-shaped map grows over 1.6 s, plus a light ring (.aia-ripple-shine) drawn in CSS.
import { canvas2d } from './maps'

const FILTER_ID = 'aia-liquid-ripple'
const SIZE = 384
const DURATION = 1600
const STRENGTH = 22
const MAP_BLUR = 2.5

let ringMap: string | null = null
let mapBlurred = false
let parts: { map: SVGFEImageElement; displace: SVGFEDisplacementMapElement; filter: SVGFilterElement } | null = null

function smooth(a: number, b: number, x: number) {
  const t = Math.max(0, Math.min(1, (x - a) / (b - a)))
  return t * t * (3 - 2 * t)
}

function profile(r: number) {
  if (r >= 1 || r < 0.12) return 0
  if (r > 0.93) return Math.sin(((r - 0.93) / (1 - 0.93)) * Math.PI) * 0.6 * (1 - smooth(0.93, 1, r))
  const d = 0.93 - r
  return -Math.sin(d * Math.PI * 2 * 3.2) * Math.exp(-d * 3.6) * smooth(0.12, 0.4, r)
}

function drawRing() {
  const { canvas, context } = canvas2d(SIZE, SIZE)
  if (!context) return null
  const img = context.createImageData(SIZE, SIZE)
  const c = SIZE / 2
  for (let y = 0; y < SIZE; y += 1) {
    for (let x = 0; x < SIZE; x += 1) {
      const u = (x + 0.5 - c) / c
      const v = (y + 0.5 - c) / c
      const r = Math.hypot(u, v)
      const k = profile(r)
      const o = (y * SIZE + x) * 4
      img.data[o] = 128 + (r ? u / r : 0) * k * 127
      img.data[o + 1] = 128 + (r ? v / r : 0) * k * 127
      img.data[o + 2] = 128
      img.data[o + 3] = 255
    }
  }
  context.putImageData(img, 0, 0)
  const out = canvas2d(SIZE, SIZE)
  if (out.context && 'filter' in out.context) {
    const f = `blur(${MAP_BLUR}px)`
    out.context.filter = f
    if (out.context.filter === f) {
      out.context.save()
      out.context.filter = 'none'
      out.context.fillStyle = 'rgb(128, 128, 128)'
      out.context.fillRect(0, 0, SIZE, SIZE)
      out.context.restore()
      out.context.filter = f
      out.context.drawImage(canvas, 0, 0)
      mapBlurred = true
      return out.canvas.toDataURL()
    }
  }
  mapBlurred = false
  return canvas.toDataURL()
}

function ensureFilter() {
  if (parts && document.getElementById(FILTER_ID)) return parts
  ringMap ??= drawRing()
  if (!ringMap) return null
  const NS = 'http://www.w3.org/2000/svg'
  const svg = document.createElementNS(NS, 'svg')
  svg.setAttribute('aria-hidden', 'true')
  svg.setAttribute('width', '0')
  svg.setAttribute('height', '0')
  svg.style.position = 'absolute'
  const filter = document.createElementNS(NS, 'filter')
  filter.id = FILTER_ID
  filter.setAttribute('filterUnits', 'userSpaceOnUse')
  filter.setAttribute('primitiveUnits', 'userSpaceOnUse')
  filter.setAttribute('color-interpolation-filters', 'sRGB')
  const flood = document.createElementNS(NS, 'feFlood')
  flood.setAttribute('flood-color', 'rgb(128, 128, 128)')
  flood.setAttribute('result', 'still')
  const map = document.createElementNS(NS, 'feImage')
  map.setAttribute('href', ringMap)
  map.setAttribute('preserveAspectRatio', 'none')
  map.setAttribute('result', 'ring')
  const merge = document.createElementNS(NS, 'feMerge')
  merge.setAttribute('result', 'raw')
  for (const n of ['still', 'ring']) {
    const node = document.createElementNS(NS, 'feMergeNode')
    node.setAttribute('in', n)
    merge.append(node)
  }
  const displace = document.createElementNS(NS, 'feDisplacementMap')
  displace.setAttribute('in', 'SourceGraphic')
  displace.setAttribute('xChannelSelector', 'R')
  displace.setAttribute('yChannelSelector', 'G')
  if (mapBlurred) {
    displace.setAttribute('in2', 'raw')
    filter.append(flood, map, merge, displace)
  } else {
    const blur = document.createElementNS(NS, 'feGaussianBlur')
    blur.setAttribute('in', 'raw')
    blur.setAttribute('stdDeviation', String(MAP_BLUR))
    blur.setAttribute('result', 'map')
    displace.setAttribute('in2', 'map')
    filter.append(flood, map, merge, blur, displace)
  }
  svg.append(filter)
  document.body.append(svg)
  parts = { map, displace, filter }
  return parts
}

/** Runs a ripple across el from the viewport point (x, y); returns a function that stops it */
export function ripple(el: HTMLElement, x: number, y: number) {
  const p = ensureFilter()
  if (!p) return () => {}
  const r = el.getBoundingClientRect()
  const cx = x - r.left
  const cy = y - r.top
  const reach = Math.hypot(Math.max(cx, r.width - cx), Math.max(cy, r.height - cy)) + 60
  p.filter.setAttribute('x', '0')
  p.filter.setAttribute('y', '0')
  p.filter.setAttribute('width', String(r.width))
  p.filter.setAttribute('height', String(r.height))
  const shine = document.createElement('span')
  shine.className = 'aia-ripple-shine'
  shine.setAttribute('aria-hidden', 'true')
  shine.style.setProperty('--ripple-x', `${cx}px`)
  shine.style.setProperty('--ripple-y', `${cy}px`)
  el.append(shine)
  const t0 = performance.now()
  let raf = 0
  const end = () => {
    cancelAnimationFrame(raf)
    el.style.removeProperty('filter')
    shine.remove()
  }
  const frame = (now: number) => {
    const k = Math.min(1, (now - t0) / DURATION)
    const grow = 1 - Math.pow(1 - k, 2.2)
    const radius = 24 + (reach - 24) * grow
    const fade = Math.pow(1 - k, 1.6)
    p.map.setAttribute('x', String(cx - radius))
    p.map.setAttribute('y', String(cy - radius))
    p.map.setAttribute('width', String(radius * 2))
    p.map.setAttribute('height', String(radius * 2))
    p.displace.setAttribute('scale', (STRENGTH * fade).toFixed(2))
    shine.style.setProperty('--ripple-r', `${radius.toFixed(1)}px`)
    shine.style.opacity = String(Math.min(1, k * 8) * fade)
    if (k < 1) raf = requestAnimationFrame(frame)
    else end()
  }
  el.style.filter = `url(#${FILTER_ID})`
  raf = requestAnimationFrame(frame)
  return end
}
