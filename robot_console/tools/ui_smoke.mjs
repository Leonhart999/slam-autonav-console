// UI smoke test driver: headless Edge over the DevTools protocol, every page x every language.
// Runs with Windows Node (WSL's NAT network cannot reach a Windows debugging port); tools/ui_smoke.sh starts the
// console instance and calls this file. Usage:
//   node ui_smoke.mjs --base http://localhost:8010 --out <Windows dir> --src <web/src dir> [--edge <msedge.exe>]
//                     [--viewport 1440x1000]
// Checks per page: uncaught exceptions, console.error, vue-i18n / Vue warnings, failed requests (4xx/5xx and
// network errors, minus EXPECTED), raw i18n keys visible in the page text or in title/aria-label/placeholder, and layout problems (text or
// controls clipped by their box, table headers wrapping, unwanted horizontal scrollbars, content past the window).
// Before each screenshot it waits for page requests to finish and finite animations (page fade-in) to end.
// Writes <out>/<lang>_<page>.png, summary.json and summary.md; exit code 1 when anything failed.
import { spawn } from 'node:child_process'
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d }
const BASE = arg('base', 'http://localhost:8010')
const OUT = arg('out')
const SRC = arg('src')
const EDGE = arg('edge', 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe')
const PORT = Number(arg('port', 9340))
const SETTLE = Number(arg('settle', 4000))
const [VW, VH] = arg('viewport', '1440x1000').split('x').map(Number)
// --pages: comma-separated routes, optionally with a query (e.g. "navigation?map=office"), to check pages in a
// state prepared beforehand; --tag: prefix for the screenshot names
const PAGES = arg('pages', 'mapping,playback,maps,navigation,evaluation,monitor').split(',')
const TAG = arg('tag', '')
const LANGS = ['zh-CN', 'en']
// Requests that fail by design when no simulation is running; each entry says why.
const EXPECTED = [
  // { re: /\/api\/example$/, status: 404, why: '...' },
]
if (!OUT || !SRC) { console.error('need --out and --src'); process.exit(2) }
mkdirSync(OUT, { recursive: true })

// ---------- i18n key roots, so that "mapping.tips.voxels" is recognised as a raw key ----------
function walk(dir) {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f)
    return statSync(p).isDirectory() ? walk(p) : /\.(ts|vue)$/.test(f) ? [p] : []
  })
}
const roots = new Set()
for (const m of readFileSync(join(SRC, 'locales', 'zh-CN.ts'), 'utf8').matchAll(/^ {2}(\w+):/gm)) roots.add(m[1])
for (const f of walk(SRC)) {
  const text = readFileSync(f, 'utf8')
  for (const m of text.matchAll(/(?:'zh-CN'|"zh-CN"|const zh\s*=)\s*:?\s*\{\s*(\w+)\s*:/g)) roots.add(m[1])
}
const KEY_RE = new RegExp(`(?:^|[\\s(（“"'])((?:${[...roots].join('|')})(?:\\.[A-Za-z0-9_]+)+)(?=$|[\\s)）”"',.:;])`, 'g')

// ---------- layout check, runs inside the page ----------
// Reports things a user would see as broken; returns short descriptions ("kind: element "text"").
function layoutCheck() {
  const out = new Set()
  const vw = document.documentElement.clientWidth
  const label = (el) => {
    const cls = [...el.classList].slice(0, 2).join('.')
    const txt = (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 40)
    return `${el.tagName.toLowerCase()}${cls ? '.' + cls : ''}${txt ? ` "${txt}"` : ''}`
  }
  const shown = (el) => {
    const b = el.getBoundingClientRect()
    return b.width > 0 && b.height > 0 && getComputedStyle(el).visibility !== 'hidden'
  }
  // Lines of text a box renders (distinct line tops of its text)
  // (icons sit a few px higher than the text, so tops closer than half a line count as the same line)
  const lines = (el) => {
    const range = document.createRange()
    range.selectNodeContents(el)
    const half = (parseFloat(getComputedStyle(el).fontSize) || 14) * 0.6
    const tops = [...range.getClientRects()].filter((q) => q.width > 1).map((q) => q.top).sort((a, b) => a - b)
    return tops.filter((t, i) => i === 0 || t - tops[i - 1] > half).length
  }
  // In-flow descendants that stick out of el on the right (absolutely placed decoration does not count)
  const sticksOut = (el) => {
    const right = el.getBoundingClientRect().right
    let worst = 0, who = null
    for (const c of el.querySelectorAll('*')) {
      if (['absolute', 'fixed'].includes(getComputedStyle(c).position) || !shown(c) || c.closest('canvas, svg')) continue
      let p = c.parentElement, decor = false
      while (p && p !== el) { if (['absolute', 'fixed'].includes(getComputedStyle(p).position)) { decor = true; break } p = p.parentElement }
      if (decor) continue
      const d = c.getBoundingClientRect().right - right
      if (d > worst) { worst = d; who = c }
    }
    return worst > 2 ? [Math.round(worst), who] : null
  }
  const root = document.querySelector('.stage') ?? document.body
  for (const el of root.querySelectorAll('*')) {
    if (!shown(el) || el.closest('canvas, svg, .el-slider, .el-select-dropdown, .el-popper')) continue
    const cs = getComputedStyle(el)
    const overX = el.scrollWidth - el.clientWidth
    // Clipped: a box that hides overflow but whose content is wider. Table cells that show a tooltip with the
    // full text (show-overflow-tooltip) and text that ends in an ellipsis are clipped on purpose.
    if (overX > 2 && ['hidden', 'clip'].includes(cs.overflowX) && !el.classList.contains('el-tooltip') &&
        !el.matches('.el-scrollbar__wrap, .el-input__wrapper, .el-select__wrapper, .el-table__header-wrapper')) {
      const hit = sticksOut(el)
      if (hit) out.add(`clipped by ${hit[0]}px: ${label(hit[1])} in ${label(el).slice(0, 60)}`)
    }
    // A horizontal scrollbar inside the page (tables too, whose scrollbar only shows on hover): columns or a
    // toolbar do not fit
    if (overX > 2 && ['auto', 'scroll'].includes(cs.overflowX))
      out.add(`horizontal scroll ${overX}px: ${label(el).slice(0, 80)}`)
    // Content past the right edge of the window
    // (only outermost offenders: inside a box that clips or scrolls, the two checks above already report it)
    const b = el.getBoundingClientRect()
    if (b.right > vw + 1 && cs.position !== 'fixed') {
      let p = el.parentElement, inside = false
      while (p && !inside) { inside = getComputedStyle(p).overflowX !== 'visible'; p = p.parentElement }
      if (!inside) out.add(`past window edge by ${Math.round(b.right - vw)}px: ${label(el)}`)
    }
  }
  // The page fade-in has to end at full opacity (a page left half transparent looks disabled)
  for (const el of document.querySelectorAll('.content > *')) {
    let o = 1
    for (let p = el; p; p = p.parentElement) o *= parseFloat(getComputedStyle(p).opacity)
    if (o < 0.99) out.add(`page content at opacity ${o.toFixed(2)} (${el.className}): ` +
      document.getAnimations().map((a) => `${a.constructor.name}:${a.transitionProperty ?? a.animationName ?? ''}:${a.playState}:${Math.round(a.currentTime ?? -1)}`).join(','))
  }
  // Table headers and buttons should stay on one line
  for (const el of root.querySelectorAll('th .cell, td .cell > .num, td .cell > .name, .el-button > span, .chip, ' +
    '.el-radio-button__inner, .el-segmented__item-label'))
    if (shown(el) && lines(el) > 1) out.add(`wraps to ${lines(el)} lines: ${label(el)}`)
  // One-line controls whose text runs past their own box (el-button is white-space: nowrap with visible
  // overflow, so the text spills over the border instead of being clipped or wrapped)
  for (const el of root.querySelectorAll('.el-button, .el-tag, .chip, .el-radio-button__inner, .el-segmented__item'))
    if (shown(el) && el.scrollWidth > el.clientWidth + 2)
      out.add(`text overflows its box by ${el.scrollWidth - el.clientWidth}px: ${label(el)}`)
  return [...out].slice(0, 40)
}

// ---------- Edge + CDP ----------
const profile = mkdtempSync(join(tmpdir(), 'ui_smoke_'))
// The renderer-backgrounding switches keep headless Edge producing frames: without them the animation timeline
// sometimes stays at 0, Vue's page fade-in never finishes and pages are captured half transparent.
const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--disable-renderer-backgrounding', '--disable-background-timer-throttling', '--disable-backgrounding-occluded-windows',
  `--user-data-dir=${profile}`, `--remote-debugging-port=${PORT}`, 'about:blank'], { stdio: 'ignore' })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
let target
for (let i = 0; i < 40 && !target; i++) {
  await sleep(250)
  try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find((t) => t.type === 'page') } catch {}
}
if (!target) { console.error('Edge did not open its debugging port'); edge.kill(); process.exit(2) }
const ws = new WebSocket(target.webSocketDebuggerUrl)
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j })
let seq = 0
const pending = new Map()
let sink = null           // current page's collector
ws.onmessage = (e) => {
  const m = JSON.parse(e.data)
  if (m.id) { pending.get(m.id)?.(m); pending.delete(m.id); return }
  if (sink) sink(m.method, m.params)
}
const send = (method, params = {}) => new Promise((r) => { const i = ++seq; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })) })
const evaluate = async (expression) =>
  (await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })).result?.result?.value

await send('Runtime.enable')
await send('Network.enable')
await send('Page.enable')
await send('Page.bringToFront')
await send('Emulation.setFocusEmulationEnabled', { enabled: true })
await send('Emulation.setDeviceMetricsOverride', { width: VW, height: VH, deviceScaleFactor: 1, mobile: false })

const text = (args) => (args ?? []).map((a) => a.value ?? a.description ?? '').join(' ').slice(0, 400)
const results = []
for (const lang of LANGS) {
  await send('Page.navigate', { url: `${BASE}/#/monitor` })
  await sleep(1500)
  await evaluate(`localStorage.setItem('robot_console.lang', ${JSON.stringify(lang)})`)
  for (const page of PAGES) {
    const r = { lang, page, exceptions: [], errors: [], warnings: [], requests: [], keys: [], layout: [] }
    const urls = new Map()
    const inflight = new Set()
    sink = (method, p) => {
      if (method === 'Runtime.exceptionThrown') r.exceptions.push((p.exceptionDetails.exception?.description ?? p.exceptionDetails.text).slice(0, 400))
      else if (method === 'Runtime.consoleAPICalled') {
        const t = text(p.args)
        if (p.type === 'error') r.errors.push(t)
        else if (p.type === 'warning' && /Not found '.*' key|\[intlify\]|\[Vue warn\]/.test(t)) r.warnings.push(t)
      } else if (method === 'Network.requestWillBeSent') { urls.set(p.requestId, p.request.url); inflight.add(p.requestId) }
      else if (method === 'Network.loadingFinished') inflight.delete(p.requestId)
      else if (method === 'Network.responseReceived' && p.response.status >= 400) {
        const exp = EXPECTED.find((x) => x.re.test(p.response.url) && x.status === p.response.status)
        if (!exp) r.requests.push(`${p.response.status} ${p.response.url.replace(BASE, '')}`)
      } else if (method === 'Network.loadingFailed' && (inflight.delete(p.requestId), !p.canceled) && p.errorText !== 'net::ERR_ABORTED') {
        r.requests.push(`${p.errorText} ${(urls.get(p.requestId) ?? '?').replace(BASE, '')}`)
      }
    }
    // A fresh load per page, so every page starts from its own mount (and the language is read from storage)
    await send('Page.navigate', { url: `${BASE}/?smoke=${lang}-${page}#/${page}` })
    await sleep(SETTLE)
    // Settle: no request in flight for 0.5 s (max 10 s more), then no finite animation still running (max 3 s)
    for (let t = 0, quiet = 0; t < 10000 && quiet < 500; t += 100) { await sleep(100); quiet = inflight.size ? 0 : quiet + 100 }
    for (let t = 0; t < 3000; t += 100) {
      const busy = await evaluate(`document.getAnimations().filter((a) => a.playState === 'running' &&
        Number.isFinite(a.effect?.getComputedTiming().endTime)).length`)
      if (!busy) break
      await sleep(100)
    }
    await sleep(300)   // let a frame or two render after the last change
    const found = await evaluate(`(() => {
      const re = ${KEY_RE.toString()}
      const out = new Set()
      const file = /\.(py|ya?ml|pgm|png|pcd|json|csv|tum|sdf|launch|xml|txt|log|ts|vue)$/   // file names are not keys
      const scan = (s) => { for (const m of String(s ?? '').matchAll(re)) if (!file.test(m[1])) out.add(m[1]) }
      // Self-check of the detector on synthetic text, removed again before the real scan
      const probe = document.createElement('div')
      probe.textContent = 'x mapping.tips.__smokeProbe x'
      probe.title = 'navigation.__smokeProbe'
      document.body.append(probe)
      const probeSeen = []
      { const w = document.createTreeWalker(probe, NodeFilter.SHOW_TEXT)
        while (w.nextNode()) for (const m of w.currentNode.nodeValue.matchAll(re)) probeSeen.push(m[1])
        for (const m of probe.title.matchAll(re)) probeSeen.push(m[1]) }
      probe.remove()
      if (probeSeen.length !== 2) out.add('__detector_broken__')
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
      while (walker.nextNode()) scan(walker.currentNode.nodeValue)
      for (const el of document.querySelectorAll('[title],[aria-label],[placeholder]'))
        for (const a of ['title', 'aria-label', 'placeholder']) scan(el.getAttribute(a))
      return [...out]
    })()`)
    r.keys = found ?? []
    r.layout = (await evaluate(`(${layoutCheck.toString()})()`)) ?? ['__layout_check_failed__']
    const metrics = await send('Page.getLayoutMetrics')
    const size = metrics.result.cssContentSize ?? metrics.result.contentSize
    const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true,
      clip: { x: 0, y: 0, width: Math.min(size.width, VW), height: Math.min(Math.max(size.height, VH), 6000), scale: 1 } })
    const file = `${TAG}${lang}_${page.replace(/[^\w-]+/g, '_')}.png`
    writeFileSync(join(OUT, file), Buffer.from(shot.result.data, 'base64'))
    r.screenshot = file
    r.ok = !r.exceptions.length && !r.errors.length && !r.warnings.length && !r.requests.length && !r.keys.length &&
      !r.layout.length
    sink = null
    results.push(r)
  }
}
ws.close()
edge.kill()
await sleep(500)
try { rmSync(profile, { recursive: true, force: true }) } catch {}

// ---------- report ----------
const cols = ['exceptions', 'errors', 'warnings', 'requests', 'keys', 'layout']
const lines = ['| lang | page | result | ' + cols.join(' | ') + ' |', '|' + '---|'.repeat(cols.length + 3)]
for (const r of results) lines.push(`| ${r.lang} | ${r.page} | ${r.ok ? 'PASS' : 'FAIL'} | ${cols.map((c) => r[c].length).join(' | ')} |`)
const details = results.filter((r) => !r.ok).flatMap((r) =>
  [`\n### ${r.lang} ${r.page}`, ...cols.flatMap((c) => r[c].map((x) => `- ${c}: ${x}`))])
const md = ['# UI smoke', '', `base ${BASE}, viewport ${VW}x${VH}, ${new Date().toISOString()}`, '', ...lines, ...details].join('\n')
writeFileSync(join(OUT, 'summary.md'), md + '\n')
writeFileSync(join(OUT, 'summary.json'), JSON.stringify(results, null, 2))
console.log(lines.join('\n'))
if (details.length) console.log(details.join('\n'))
const failed = results.filter((r) => !r.ok).length
console.log(`\n${results.length - failed}/${results.length} passed; report in ${OUT}`)
process.exit(failed ? 1 : 0)
