import { loadDemo, loadDemoMap } from '@/demo/data'
import { setPreviewFigureUrls, setPreviewReader } from '@/http'
import { setPreviewFeed } from '@/stores/status'
import { buildFixtures, MAP, NAV_SAMPLES, navigationRunId, OFFICE_MAP,
  type ConsoleAssets, type MapSummary, type NavigationAssets } from './fixtures'

/** Use the same frame format as the real mapping stream. The fixture never opens a socket. */
export function cloudFrame(type: 'map' | 'est' | 'gt', points: Float32Array) {
  const json = JSON.stringify({ type, epoch: 1, offset: 0, count: points.length / 3 })
  const header = new TextEncoder().encode(json.padEnd(Math.ceil(json.length / 4) * 4))
  const frame = new ArrayBuffer(4 + header.length + points.byteLength)
  new DataView(frame).setUint32(0, header.length, true)
  new Uint8Array(frame, 4, header.length).set(header)
  new Float32Array(frame, 4 + header.length).set(points)
  return frame
}

export async function installConsolePreview() {
  // The console is hosted at <project>/console/; both entry points use the same public data directory.
  const base = new URL('../data/', document.baseURI).href
  async function asset(name: string) {
    const response = await fetch(`${base}${name}`)
    if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`)
    return response
  }
  const officeBase = `${base}office/`
  const [data, assets, grid, officeRun, officeAssets, officeGrid, navSamples] = await Promise.all([
    loadDemo(base),
    asset('warehouse-console.json').then(res => res.json() as Promise<ConsoleAssets>),
    asset('warehouse-grid.png').then(res => res.blob()),
    asset('office/map.json').then(res => res.json() as Promise<MapSummary>),
    asset('office/office-console.json').then(res => res.json() as Promise<ConsoleAssets>),
    asset('office/office-grid.png').then(res => res.blob()),
    Promise.all(NAV_SAMPLES.map(async sample => ({ sample,
      assets: await asset(`office/${sample}/run.json`).then(res => res.json() as Promise<NavigationAssets>),
    }))),
  ])
  const { routes, status, mapping } = buildFixtures(data, assets, grid.size,
    { run: officeRun, assets: officeAssets, gridBytes: officeGrid.size }, navSamples)
  const cloudBlob = (points: Float32Array) => new Blob([points.slice().buffer], { type: 'application/octet-stream' })
  const cloud = cloudBlob(data.map)
  // Decode the second point cloud only when the visitor opens that map.
  let officeCloud: Promise<Blob> | undefined
  const figures = new Map(NAV_SAMPLES.map(sample => [
    `/api/evaluation/runs/${navigationRunId(sample)}/files/path.png`, `${officeBase}${sample}/path.png`,
  ]))
  setPreviewFigureUrls(figures)
  const frames = [cloudFrame('map', data.map), cloudFrame('est', data.track.est), cloudFrame('gt', data.track.gt)]
  setPreviewFeed({ status, stream(name, json, binary) {
    if (name !== 'mapping') return
    json({ type: 'mapping_reset', epoch: mapping.epoch })
    frames.forEach(binary)
    json({ ...mapping })
  } })
  setPreviewReader(async (path, signal) => {
    signal.throwIfAborted()
    const key = path.split('?')[0]
    if (key === `/api/navigation/maps/${MAP}/grid.png`) return new Response(grid)
    if (key === `/api/maps/${MAP}/cloud` || key === `/api/navigation/maps/${MAP}/cloud`) return new Response(cloud)
    if (key === `/api/navigation/maps/${OFFICE_MAP}/grid.png`) return new Response(officeGrid)
    if (key === `/api/maps/${OFFICE_MAP}/cloud` || key === `/api/navigation/maps/${OFFICE_MAP}/cloud`) {
      officeCloud ??= loadDemoMap(officeBase, officeRun.map).then(cloudBlob).catch(error => {
        officeCloud = undefined
        throw error
      })
      const blob = await officeCloud
      signal.throwIfAborted()
      return new Response(blob)
    }
    if (Object.hasOwn(routes, key)) return Response.json(routes[key])
    if (status.stacks.some(stack => key === `/api/stacks/${stack.name}/log`)) {
      return Response.json({ text: 'Read-only preview. No ROS process is running.' })
    }
    return Response.json({ detail: { code: 'preview_read_only', params: {} } }, { status: 404 })
  })
}
