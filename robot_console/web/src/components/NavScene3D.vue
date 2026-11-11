<!-- 3D navigation view: saved point cloud model (height-colored), 2D grid as a floor texture,
     robot pose, live laser points and Nav2 paths. All data is in the map frame (z up, meters).
     Camera modes as on the mapping page (PointCloudView): follow (orbit, the target trails the robot),
     free (orbit) and top (orthographic, pan and zoom only). -->
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { Line2 } from 'three/examples/jsm/lines/Line2.js'
import { LineGeometry } from 'three/examples/jsm/lines/LineGeometry.js'
import { LineMaterial } from 'three/examples/jsm/lines/LineMaterial.js'
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js'
import { ColladaLoader } from 'three/examples/jsm/loaders/ColladaLoader.js'
import type { NavMap, NavSolid, NavStatus } from '@/types'
import type { ViewMode } from '@/mapping/stream'

const props = withDefaults(defineProps<{
  map: NavMap | null
  image: ImageBitmap | null
  cloud: Float32Array | null
  solid: NavSolid | null
  model: 'cloud' | 'solid'
  status: NavStatus | null
  showCloud: boolean
  showGrid: boolean
  mode?: ViewMode
}>(), { mode: 'free' })

// 3D solid model (box/cylinder primitives and STL shells), already in the map frame
const solidGroup = new THREE.Group()
let solidSeq = 0

function clearSolid() {
  for (const child of [...solidGroup.children]) {
    solidGroup.remove(child)
    child.traverse((o) => {
      const m = o as THREE.Mesh
      m.geometry?.dispose()
      const mat = m.material as THREE.Material | THREE.Material[] | undefined
      if (Array.isArray(mat)) mat.forEach((x) => x.dispose())
      else mat?.dispose()
    })
  }
}

function placed(obj: THREE.Object3D, matrix: number[]) {
  // The backend sends the full pose (including mesh scale) in the map frame
  const holder = new THREE.Group()
  holder.matrixAutoUpdate = false
  holder.matrix.fromArray(matrix)
  holder.add(obj)
  return holder
}

/** Load one mesh file of an original Gazebo model; dae textures resolve relative to the same URL */
async function loadMesh(url: string, format: 'dae' | 'stl'): Promise<THREE.Object3D> {
  if (format === 'stl') {
    const geo = await new STLLoader().loadAsync(url)
    geo.computeVertexNormals()
    return new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ color: 0xcbd5e1, side: THREE.DoubleSide }))
  }
  const dae = await new ColladaLoader().loadAsync(url)
  // The loader resolves null for files it cannot parse; callers already handle rejected loads
  if (!dae) throw new Error(`Cannot parse Collada mesh ${url}`)
  // The loader turns Z-up files into Y-up by rotating -90° about x; our scene is z-up, so rotate back
  // (a Y-up file ends up rotated +90°, i.e. converted to z-up like Gazebo does)
  dae.scene.rotation.x += Math.PI / 2
  return dae.scene
}

async function setSolid(s: NavSolid | null) {
  const seq = ++solidSeq
  clearSolid()
  if (!s) return
  for (const p of s.primitives) {
    const geo =
      p.type === 'box'
        ? new THREE.BoxGeometry(...(p.size as [number, number, number]))
        : new THREE.CylinderGeometry(p.radius, p.radius, p.length, 32)
    if (p.type === 'cylinder') geo.rotateX(Math.PI / 2) // SDF cylinder axis is z
    const mesh = new THREE.Mesh(
      geo,
      new THREE.MeshLambertMaterial({ color: new THREE.Color(p.color[0], p.color[1], p.color[2]) }),
    )
    mesh.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color: 0x64748b })))
    solidGroup.add(placed(mesh, p.matrix))
  }
  // Load every distinct file once and clone it for repeated instances (e.g. many shelves)
  const files = new Map<string, Promise<THREE.Object3D | null>>()
  await Promise.all(
    s.meshes.map(async (m) => {
      const url = `/api/navigation/solid/${encodeURIComponent(m.model)}/${m.file.split('/').map(encodeURIComponent).join('/')}`
      if (!files.has(url)) files.set(url, loadMesh(url, m.format).catch(() => null)) // missing file -> gap only
      const proto = await files.get(url)!
      if (!proto || seq !== solidSeq) return
      solidGroup.add(placed(proto.clone(), m.matrix))
    }),
  )
}

const host = ref<HTMLDivElement | null>(null)
let renderer: THREE.WebGLRenderer
let scene: THREE.Scene
let camera: THREE.PerspectiveCamera
let controls: OrbitControls
let ortho: THREE.OrthographicCamera // top mode
let pan: OrbitControls
const ORTHO_HALF = 15 // half height of the top view at zoom 1 (m)
const CLOUD_SIZE = 0.07 // point sizes in m
const SCAN_SIZE = 0.09
let cloudObj: THREE.Points
let scanObj: THREE.Points
let floor: THREE.Mesh | null = null
const floorGroup = new THREE.Group()
let globalLine: Line2
let localLine: Line2
let robot: THREE.Group
let goalMarker: THREE.Group
let raf = 0
let resizeObs: ResizeObserver | null = null
let floorZ = 0
let framedFor = ''

// Height ramp: light cyan -> blue -> indigo (matches the mapping page)
const RAMP = [
  [0.6, 0.86, 0.93],
  [0.02, 0.64, 0.8],
  [0.15, 0.39, 0.92],
  [0.33, 0.25, 0.85],
]
function ramp(t: number, out: number[], i: number) {
  const x = Math.min(0.999, Math.max(0, t)) * 3
  const k = Math.floor(x)
  const f = x - k
  for (let c = 0; c < 3; c++) out[i + c] = RAMP[k][c] + (RAMP[k + 1][c] - RAMP[k][c]) * f
}

function percentile(values: Float32Array, q: number) {
  const step = Math.max(1, Math.floor(values.length / 20000))
  const sample: number[] = []
  for (let i = 0; i < values.length; i += step) sample.push(values[i])
  sample.sort((a, b) => a - b)
  return sample.length ? sample[Math.min(sample.length - 1, Math.floor(q * sample.length))] : 0
}

function setCloud(data: Float32Array | null) {
  cloudObj.geometry.dispose()
  const geo = new THREE.BufferGeometry()
  if (data && data.length >= 3) {
    const zs = new Float32Array(data.length / 3)
    for (let i = 0; i < zs.length; i++) zs[i] = data[i * 3 + 2]
    const z0 = percentile(zs, 0.02)
    const z1 = Math.max(z0 + 0.5, Math.min(percentile(zs, 0.98), z0 + 4))
    floorZ = z0
    const colors: number[] = new Array(data.length)
    for (let i = 0; i < zs.length; i++) ramp((zs[i] - z0) / (z1 - z0), colors, i * 3)
    geo.setAttribute('position', new THREE.BufferAttribute(data, 3))
    geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3))
  } else {
    floorZ = 0
  }
  cloudObj.geometry = geo
  floorGroup.position.z = floorZ - 0.02
}

function setFloor(m: NavMap | null, image: ImageBitmap | null) {
  if (floor) {
    scene.remove(floorGroup)
    floorGroup.remove(floor)
    floor.geometry.dispose()
    const mat = floor.material as THREE.MeshBasicMaterial
    mat.map?.dispose()
    mat.dispose()
    floor = null
  }
  if (!m || !image) return
  const res = m.grid.resolution
  const [ox, oy, yaw] = m.grid.origin
  const w = m.grid.width * res
  const h = m.grid.height * res
  // Copy into a canvas: WebGL ignores flipY for ImageBitmap, but honors it for canvases.
  // With flipY the image top row lands at v = 1, i.e. the plane's +y edge, matching the map frame.
  const cv = document.createElement('canvas')
  cv.width = image.width
  cv.height = image.height
  cv.getContext('2d')!.drawImage(image, 0, 0)
  const tex = new THREE.CanvasTexture(cv)
  tex.magFilter = THREE.NearestFilter
  tex.colorSpace = THREE.SRGBColorSpace
  const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0.72, depthWrite: false })
  floor = new THREE.Mesh(new THREE.PlaneGeometry(w, h), mat)
  // The plane is centered; put its lower-left corner on the map origin, rotated by the origin yaw
  floor.position.set(w / 2, h / 2, 0)
  floorGroup.add(floor)
  floorGroup.position.set(ox, oy, floorZ - 0.02)
  floorGroup.rotation.z = yaw
  scene.add(floorGroup)
}

function makeRobot() {
  const g = new THREE.Group()
  const body = new THREE.Mesh(
    new THREE.BoxGeometry(0.56, 0.42, 0.22),
    new THREE.MeshBasicMaterial({ color: 0xffffff }),
  )
  body.position.z = 0.11
  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(body.geometry),
    new THREE.LineBasicMaterial({ color: 0x0891b2 }),
  )
  edges.position.copy(body.position)
  const chevron = new THREE.Shape()
  chevron.moveTo(0.22, 0)
  chevron.lineTo(-0.06, 0.13)
  chevron.lineTo(0.02, 0)
  chevron.lineTo(-0.06, -0.13)
  chevron.closePath()
  const arrow = new THREE.Mesh(new THREE.ShapeGeometry(chevron), new THREE.MeshBasicMaterial({ color: 0x0891b2 }))
  arrow.position.z = 0.225
  g.add(body, edges, arrow)
  return g
}

function makeLine(color: number, dashed: boolean) {
  const line = new Line2(new LineGeometry(), new LineMaterial({ color, linewidth: 3, dashed, dashSize: 0.3, gapSize: 0.2 }))
  line.frustumCulled = false
  line.visible = false
  return line
}

function setLine(line: Line2, pts: number[][]) {
  if (pts.length < 2) {
    line.visible = false
    return
  }
  const flat: number[] = []
  for (const p of pts) flat.push(p[0], p[1], floorZ + 0.06)
  line.geometry.dispose()
  const geo = new LineGeometry()
  geo.setPositions(flat)
  line.geometry = geo
  if ((line.material as LineMaterial).dashed) line.computeLineDistances()
  line.visible = true
}

function setScan(pts: number[][]) {
  const arr = new Float32Array(pts.length * 3)
  pts.forEach((p, i) => arr.set(p, i * 3))
  scanObj.geometry.dispose()
  const geo = new THREE.BufferGeometry()
  geo.setAttribute('position', new THREE.BufferAttribute(arr, 3))
  scanObj.geometry = geo
}

/** Default view of the current mode: top shows the whole map, follow sits behind and above the robot,
    free looks at the whole map at an angle */
function resetView() {
  const m = props.map
  if (!m) return
  const res = m.grid.resolution
  const [ox, oy] = m.grid.origin
  const w = m.grid.width * res
  const h = m.grid.height * res
  const cx = ox + w / 2
  const cy = oy + h / 2
  const span = Math.max(w, h, 6)
  if (props.mode === 'top') {
    pan.target.set(cx, cy, floorZ)
    ortho.position.set(cx, cy, floorZ + 500)
    // Fit width and height: the 3D panel is often taller than wide
    const aspect = (ortho.right - ortho.left) / (ortho.top - ortho.bottom)
    ortho.zoom = Math.min((2 * ORTHO_HALF) / (Math.max(h, 6) * 1.1), (2 * ORTHO_HALF * aspect) / (Math.max(w, 6) * 1.1))
    ortho.updateProjectionMatrix()
    pan.update()
    return
  }
  const pose = props.status?.pose
  if (props.mode === 'follow' && pose) {
    controls.target.set(pose.x, pose.y, pose.z)
    camera.position.set(pose.x - Math.cos(pose.yaw) * 7, pose.y - Math.sin(pose.yaw) * 7, pose.z + 5.5)
  } else {
    controls.target.set(cx, cy, floorZ)
    camera.position.set(cx - span * 0.45, cy - span * 0.75, floorZ + span * 0.7)
  }
  controls.update()
}

function applyMode() {
  controls.enabled = props.mode !== 'top'
  pan.enabled = props.mode === 'top'
  resetView()
}

function applyStatus() {
  const s = props.status
  const ok = !!s?.tf_ok
  setScan(ok ? s!.scan : [])
  setLine(globalLine, ok ? s!.global_path : [])
  setLine(localLine, ok ? s!.local_path : [])
  robot.visible = !!s?.pose
  if (s?.pose) {
    robot.position.set(s.pose.x, s.pose.y, s.pose.z)
    robot.rotation.z = s.pose.yaw
  }
  const g = s?.goal
  goalMarker.visible = !!g && (g.state === 'pending' || g.state === 'active' || g.state === 'canceling')
  if (g && goalMarker.visible) {
    goalMarker.position.set(g.x, g.y, floorZ + 0.03)
    goalMarker.rotation.z = g.yaw
  }
}

/** Goal marker: flat ring and heading arrow on the floor, plus a pole so it is visible behind shelves */
function makeGoalMarker() {
  const color = 0x16a34a
  const g = new THREE.Group()
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.22, 0.3, 40),
    new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide }),
  )
  const head = new THREE.Shape()
  head.moveTo(0.75, 0)
  head.lineTo(0.45, 0.16)
  head.lineTo(0.45, -0.16)
  head.closePath()
  const arrowHead = new THREE.Mesh(new THREE.ShapeGeometry(head), new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide }))
  const shaft = new THREE.Mesh(new THREE.PlaneGeometry(0.17, 0.06), new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide }))
  shaft.position.x = 0.385
  const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.025, 1.2, 12), new THREE.MeshBasicMaterial({ color }))
  pole.rotation.x = Math.PI / 2
  pole.position.z = 0.6
  g.add(ring, arrowHead, shaft, pole)
  g.visible = false
  g.renderOrder = 5
  return g
}

function resize() {
  const el = host.value
  if (!el) return
  const w = Math.max(1, el.clientWidth)
  const h = Math.max(1, el.clientHeight)
  renderer.setSize(w, h)
  camera.aspect = w / h
  camera.updateProjectionMatrix()
  ortho.left = -ORTHO_HALF * (w / h)
  ortho.right = ORTHO_HALF * (w / h)
  ortho.top = ORTHO_HALF
  ortho.bottom = -ORTHO_HALF
  ortho.updateProjectionMatrix()
  for (const l of [globalLine, localLine]) (l.material as LineMaterial).resolution.set(w, h)
}

function tick() {
  raf = requestAnimationFrame(tick)
  if (document.hidden) return
  const solidMode = props.model === 'solid' && !!props.solid
  cloudObj.visible = props.showCloud && !solidMode
  solidGroup.visible = solidMode
  if (floor) floor.visible = props.showGrid
  const top = props.mode === 'top'
  // Follow: the orbit target glides to the robot and the camera moves with it, keeping the user's angle
  const pose = props.status?.pose
  if (props.mode === 'follow' && pose) {
    const d = new THREE.Vector3(pose.x, pose.y, pose.z).sub(controls.target).multiplyScalar(0.12)
    controls.target.add(d)
    camera.position.add(d)
  }
  // An orthographic camera draws points at a fixed pixel size: convert the metric size at the current zoom
  const pxPerM = top ? (renderer.domElement.clientHeight * ortho.zoom) / (ortho.top - ortho.bottom) : 0
  ;(cloudObj.material as THREE.PointsMaterial).size = top ? Math.max(1.5, CLOUD_SIZE * pxPerM) : CLOUD_SIZE
  ;(scanObj.material as THREE.PointsMaterial).size = top ? Math.max(2, SCAN_SIZE * pxPerM) : SCAN_SIZE
  if (top) pan.update()
  else controls.update()
  renderer.render(scene, top ? ortho : camera)
}

onMounted(() => {
  const el = host.value!
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.setClearColor(0x000000, 0)
  el.appendChild(renderer.domElement)
  scene = new THREE.Scene()
  camera = new THREE.PerspectiveCamera(50, 1, 0.05, 3000)
  camera.up.set(0, 0, 1)
  camera.position.set(-8, -10, 8)
  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.maxPolarAngle = Math.PI * 0.49
  ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 3000)
  ortho.position.set(0, 0, 500)
  pan = new OrbitControls(ortho, renderer.domElement)
  pan.enableRotate = false
  pan.screenSpacePanning = true
  pan.mouseButtons = { LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN }
  pan.enabled = false

  cloudObj = new THREE.Points(
    new THREE.BufferGeometry(),
    new THREE.PointsMaterial({ size: CLOUD_SIZE, vertexColors: true, sizeAttenuation: true }),
  )
  cloudObj.frustumCulled = false
  scanObj = new THREE.Points(
    new THREE.BufferGeometry(),
    new THREE.PointsMaterial({ size: SCAN_SIZE, color: 0xe11d48, sizeAttenuation: true }),
  )
  scanObj.frustumCulled = false
  scanObj.renderOrder = 3
  globalLine = makeLine(0x2a78d6, false)
  localLine = makeLine(0xeb6834, true)
  robot = makeRobot()
  robot.visible = false
  goalMarker = makeGoalMarker()
  scene.add(cloudObj, scanObj, globalLine, localLine, robot, solidGroup, goalMarker)
  scene.add(new THREE.AmbientLight(0xffffff, 0.75))
  const sun = new THREE.DirectionalLight(0xffffff, 0.8)
  sun.position.set(-4, -6, 10)
  scene.add(sun)

  resizeObs = new ResizeObserver(resize)
  resizeObs.observe(el)
  resize()
  setCloud(props.cloud)
  setFloor(props.map, props.image)
  setSolid(props.solid)
  applyStatus()
  applyMode()
  raf = requestAnimationFrame(tick)
})

watch(() => props.mode, applyMode)

watch(() => props.solid, (s) => setSolid(s))

watch(
  () => props.cloud,
  (c) => {
    setCloud(c)
    applyStatus() // Paths are drawn just above the floor height derived from the cloud
  },
)
watch(
  () => [props.map, props.image] as const,
  ([m, image]) => {
    setFloor(m, image)
    const key = m && image ? m.name : ''
    if (key && key !== framedFor) {
      framedFor = key
      resetView()
    }
  },
)
watch(() => props.status, applyStatus)

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  resizeObs?.disconnect()
  controls.dispose()
  pan.dispose()
  solidSeq++
  clearSolid()
  setFloor(null, null)
  scene.traverse((o) => {
    const m = o as THREE.Mesh
    m.geometry?.dispose()
    const mat = m.material as THREE.Material | THREE.Material[] | undefined
    if (Array.isArray(mat)) mat.forEach((x) => x.dispose())
    else mat?.dispose()
  })
  renderer.dispose()
  renderer.domElement.remove()
})

defineExpose({ resetView })
</script>

<template>
  <div ref="host" class="scene3d" />
</template>

<style scoped>
.scene3d {
  position: absolute;
  inset: 0;
  cursor: grab;
}
.scene3d:active {
  cursor: grabbing;
}
.scene3d :deep(canvas) {
  display: block;
  outline: none;
}
</style>
