<!-- 建图 3D 视图：体素地图（按高度着色）、当前帧、估计轨迹、真值轨迹、地面网格、小车。
     数据来自 createMappingStream()；每帧检查各数组的版本号，有变化才把新增部分传给显卡。
     坐标系是 z 轴朝上的建图坐标系（camera_init），单位米 -->
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { Line2 } from 'three/examples/jsm/lines/Line2.js'
import { LineGeometry } from 'three/examples/jsm/lines/LineGeometry.js'
import { LineMaterial } from 'three/examples/jsm/lines/LineMaterial.js'
import type { LayerKey, MappingStream, PointBuffer, ViewMode } from '@/mapping/stream'

const props = defineProps<{
  stream: MappingStream
  layers: Record<LayerKey, boolean>
  mode: ViewMode
  pointSize: number // 点大小倍数，1 = 和体素一样大
}>()

const host = ref<HTMLDivElement | null>(null)

// ---------------- 着色器：点按世界尺寸缩放，地图按高度着色 ----------------

const POINT_VERT = /* glsl */ `
uniform float uSize;   // 点的边长（m）
uniform float uScale;  // 米 → 像素的换算系数（每帧按相机更新）
uniform float uOrtho;  // 1 = 正交相机（俯视），点大小不随距离变化
uniform float uZMin;
uniform float uZMax;
uniform vec3 uColor;
uniform float uSolid;  // 1 = 统一颜色（当前帧），0 = 按高度着色（地图）
varying vec3 vColor;

vec3 ramp(float t) {
  vec3 a = vec3(0.60, 0.86, 0.93);  // 低处：淡青
  vec3 b = vec3(0.02, 0.64, 0.80);  // 青
  vec3 c = vec3(0.15, 0.39, 0.92);  // 蓝
  vec3 d = vec3(0.33, 0.25, 0.85);  // 高处：靛
  if (t < 0.33) return mix(a, b, t / 0.33);
  if (t < 0.66) return mix(b, c, (t - 0.33) / 0.33);
  return mix(c, d, (t - 0.66) / 0.34);
}

void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mv;
  float px = uOrtho > 0.5 ? uSize * uScale : uSize * uScale / -mv.z;
  gl_PointSize = clamp(px, 1.5, 18.0);
  float t = clamp((position.z - uZMin) / max(uZMax - uZMin, 0.1), 0.0, 1.0);
  vColor = uSolid > 0.5 ? uColor : ramp(t);
}`

const POINT_FRAG = /* glsl */ `
varying vec3 vColor;
void main() {
  vec2 c = gl_PointCoord - 0.5;
  float d = dot(c, c);
  if (d > 0.25) discard;          // 画成圆点
  gl_FragColor = vec4(vColor * (1.08 - d * 0.9), 1.0);   // 中心略亮，有一点立体感
}`

function makePoints(solid: boolean, color: number) {
  const mat = new THREE.ShaderMaterial({
    vertexShader: POINT_VERT,
    fragmentShader: POINT_FRAG,
    uniforms: {
      uSize: { value: 0.1 },
      uScale: { value: 1 },
      uOrtho: { value: 0 },
      uZMin: { value: -0.5 },
      uZMax: { value: 2.5 },
      uColor: { value: new THREE.Color(color) },
      uSolid: { value: solid ? 1 : 0 },
    },
  })
  const pts = new THREE.Points(new THREE.BufferGeometry(), mat)
  pts.frustumCulled = false // 包围球不随数据更新，关掉视锥剔除
  return pts
}

// ---------------- 把 PointBuffer 同步到显卡 ----------------

interface PointSync {
  obj: THREE.Points
  buf: PointBuffer
  version: number
  resets: number
  data: Float32Array | null
  uploaded: number
}

function syncPoints(s: PointSync) {
  const b = s.buf
  if (b.version === s.version) return
  if (b.data !== s.data) {
    // 第一次，或者数组扩容了：换一个新的几何体（旧的释放显存）
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(b.data, 3).setUsage(THREE.DynamicDrawUsage))
    s.obj.geometry.dispose()
    s.obj.geometry = geo
    s.data = b.data
  } else {
    const attr = s.obj.geometry.getAttribute('position') as THREE.BufferAttribute
    // 清空过就整体重传，否则只传新增的部分
    const from = b.resets !== s.resets ? 0 : s.uploaded
    if (b.count > from) attr.addUpdateRange(from * 3, (b.count - from) * 3)
    attr.needsUpdate = true
  }
  s.obj.geometry.setDrawRange(0, b.count)
  s.uploaded = b.count
  s.version = b.version
  s.resets = b.resets
}

interface LineSync {
  obj: Line2
  buf: PointBuffer
  version: number
}

function makeLine(color: number, dashed: boolean): Line2 {
  const mat = new LineMaterial({ color, linewidth: dashed ? 2 : 3, dashed, dashSize: 0.25, gapSize: 0.18 })
  const line = new Line2(new LineGeometry(), mat)
  line.frustumCulled = false
  return line
}

function syncLine(s: LineSync) {
  const b = s.buf
  if (b.version === s.version) return
  s.version = b.version
  if (b.count < 2) return
  const geo = new LineGeometry()
  geo.setPositions(b.data.subarray(0, b.count * 3))
  s.obj.geometry.dispose()
  s.obj.geometry = geo
  if ((s.obj.material as LineMaterial).dashed) s.obj.computeLineDistances()
}

// ---------------- 小车：白色车身 + 青色朝向箭头 + 向外扩散的扫描环 ----------------

function roundedRect(w: number, h: number, r: number) {
  const s = new THREE.Shape()
  const x = -w / 2
  const y = -h / 2
  s.moveTo(x + r, y)
  s.lineTo(x + w - r, y)
  s.quadraticCurveTo(x + w, y, x + w, y + r)
  s.lineTo(x + w, y + h - r)
  s.quadraticCurveTo(x + w, y + h, x + w - r, y + h)
  s.lineTo(x + r, y + h)
  s.quadraticCurveTo(x, y + h, x, y + h - r)
  s.lineTo(x, y + r)
  s.quadraticCurveTo(x, y, x + r, y)
  return s
}

function makeRobot() {
  const group = new THREE.Group()
  const bodyShape = roundedRect(0.56, 0.42, 0.09)
  const body = new THREE.Mesh(new THREE.ShapeGeometry(bodyShape), new THREE.MeshBasicMaterial({ color: 0xffffff }))
  const edge = new THREE.LineLoop(
    new THREE.BufferGeometry().setFromPoints(bodyShape.getPoints(6)),
    new THREE.LineBasicMaterial({ color: 0x0891b2 }),
  )
  const chevron = new THREE.Shape()
  chevron.moveTo(0.22, 0)
  chevron.lineTo(-0.06, 0.13)
  chevron.lineTo(0.02, 0)
  chevron.lineTo(-0.06, -0.13)
  chevron.closePath()
  const arrow = new THREE.Mesh(new THREE.ShapeGeometry(chevron), new THREE.MeshBasicMaterial({ color: 0x0891b2 }))
  arrow.position.z = 0.003
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.94, 1.0, 72),
    new THREE.MeshBasicMaterial({ color: 0x06b6d4, transparent: true, depthWrite: false }),
  )
  group.add(ring, body, edge, arrow)
  group.renderOrder = 10
  return { group, ring }
}

// ---------------- 场景 ----------------

let renderer: THREE.WebGLRenderer
let scene: THREE.Scene
let persp: THREE.PerspectiveCamera
let ortho: THREE.OrthographicCamera
let orbit: OrbitControls
let pan: OrbitControls
let mapSync: PointSync
let scanSync: PointSync
let estSync: LineSync
let gtSync: LineSync
let robot: ReturnType<typeof makeRobot>
let grid: THREE.GridHelper | null = null
let gridKey = ''
let raf = 0
let resizeObs: ResizeObserver | null = null
let framedEpoch = -1 // 哪一次建图已经自动调整过视角
const ORTHO_HALF = 15 // 俯视相机 zoom = 1 时，画面半高（m）
const tmp = new THREE.Vector2()

function activeCamera(): THREE.Camera {
  return props.mode === 'top' ? ortho : persp
}

function extentOf() {
  return props.stream.status.value?.extent ?? null
}

/** 恢复默认视角：俯视看全图；跟随时在车后上方；自由时斜着看全图 */
function resetView() {
  const st = props.stream.status.value
  const e = st?.extent
  const cx = e ? (e[0] + e[3]) / 2 : 0
  const cy = e ? (e[1] + e[4]) / 2 : 0
  const cz = e ? e[2] : 0
  const span = e ? Math.max(e[3] - e[0], e[4] - e[1], 8) : 12
  if (props.mode === 'top') {
    pan.target.set(cx, cy, 0)
    ortho.position.set(cx, cy, 500)
    ortho.zoom = (2 * ORTHO_HALF) / (span * 1.15)
    ortho.updateProjectionMatrix()
    pan.update()
    return
  }
  const pose = st?.pose
  if (props.mode === 'follow' && pose) {
    orbit.target.set(pose.x, pose.y, pose.z)
    persp.position.set(pose.x - Math.cos(pose.yaw) * 9, pose.y - Math.sin(pose.yaw) * 9, pose.z + 7)
  } else {
    orbit.target.set(cx, cy, cz)
    persp.position.set(cx - span * 0.55, cy - span * 0.8, cz + span * 0.75)
  }
  orbit.update()
}

/** 地面网格：盖住地图范围，放在地图最低处 */
function updateGrid() {
  const e = extentOf()
  const size = e ? Math.max(40, Math.ceil((Math.max(e[3] - e[0], e[4] - e[1]) * 1.4 + 10) / 10) * 10) : 40
  const cx = e ? Math.round((e[0] + e[3]) / 2 / 5) * 5 : 0
  const cy = e ? Math.round((e[1] + e[4]) / 2 / 5) * 5 : 0
  const z = e ? e[2] - 0.03 : -0.3
  const key = `${size},${cx},${cy},${z.toFixed(1)}`
  if (key === gridKey) return
  gridKey = key
  if (grid) {
    scene.remove(grid)
    grid.geometry.dispose()
    ;(grid.material as THREE.Material).dispose()
  }
  grid = new THREE.GridHelper(size, size, 0x9db4cc, 0xd5dfea)
  grid.rotation.x = Math.PI / 2 // GridHelper 默认在 xz 平面，转到 xy 平面
  grid.position.set(cx, cy, z)
  const m = grid.material as THREE.LineBasicMaterial
  m.transparent = true
  m.opacity = 0.75
  m.depthWrite = false
  scene.add(grid)
}

function resize() {
  const el = host.value
  if (!el) return
  const w = Math.max(1, el.clientWidth)
  const h = Math.max(1, el.clientHeight)
  renderer.setSize(w, h)
  persp.aspect = w / h
  persp.updateProjectionMatrix()
  const a = w / h
  ortho.left = -ORTHO_HALF * a
  ortho.right = ORTHO_HALF * a
  ortho.top = ORTHO_HALF
  ortho.bottom = -ORTHO_HALF
  ortho.updateProjectionMatrix()
  for (const l of [estSync.obj, gtSync.obj]) (l.material as LineMaterial).resolution.set(w, h)
}

function tick(t: number) {
  raf = requestAnimationFrame(tick)
  const st = props.stream.status.value

  syncPoints(mapSync)
  syncPoints(scanSync)
  syncLine(estSync)
  syncLine(gtSync)
  updateGrid()

  // 新一次建图第一次有数据时，自动把视角对准地图
  if (st && st.epoch !== framedEpoch && props.stream.map.count > 0) {
    framedEpoch = st.epoch
    resetView()
  }

  // 图层开关
  mapSync.obj.visible = props.layers.map
  scanSync.obj.visible = props.layers.scan
  estSync.obj.visible = props.layers.est && props.stream.est.count >= 2
  gtSync.obj.visible = props.layers.gt && props.stream.gt.count >= 2
  if (grid) grid.visible = props.layers.grid

  // 高度着色范围：地图最低处 ~ 最高处（最多 4 m，墙体颜色层次更明显）
  const e = st?.extent
  const zmin = e ? e[2] : -0.5
  const zmax = e ? Math.min(e[5], e[2] + 4) : 2.5
  const voxel = st?.voxel ?? 0.1
  const ortho1 = props.mode === 'top'
  renderer.getDrawingBufferSize(tmp)
  const scale = ortho1
    ? (tmp.y * ortho.zoom) / (ortho.top - ortho.bottom)
    : tmp.y / (2 * Math.tan(THREE.MathUtils.degToRad(persp.fov / 2)))
  for (const [s, size] of [
    [mapSync, voxel * props.pointSize],
    [scanSync, 0.07 * props.pointSize],
  ] as const) {
    const u = (s.obj.material as THREE.ShaderMaterial).uniforms
    u.uSize.value = size
    u.uScale.value = scale
    u.uOrtho.value = ortho1 ? 1 : 0
    u.uZMin.value = zmin
    u.uZMax.value = zmax
  }

  // 小车位姿 + 扫描环动画（2 s 一个周期）
  const pose = st?.pose
  robot.group.visible = !!pose
  if (pose) {
    robot.group.position.set(pose.x, pose.y, pose.z)
    robot.group.rotation.z = pose.yaw
    const phase = (t % 2000) / 2000
    robot.ring.scale.setScalar(0.35 + phase * 2.4)
    ;(robot.ring.material as THREE.MeshBasicMaterial).opacity = 0.5 * (1 - phase)
    // 跟随：相机目标平滑地移向小车，相机跟着平移，保持当前视角
    if (props.mode === 'follow') {
      const d = new THREE.Vector3(pose.x, pose.y, pose.z).sub(orbit.target).multiplyScalar(0.12)
      orbit.target.add(d)
      persp.position.add(d)
    }
  }

  if (props.mode === 'top') pan.update()
  else orbit.update()
  renderer.render(scene, activeCamera())
}

onMounted(() => {
  const el = host.value!
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.setClearColor(0x000000, 0) // 透明背景，透出外面的磨砂玻璃
  el.appendChild(renderer.domElement)

  scene = new THREE.Scene()
  persp = new THREE.PerspectiveCamera(50, 1, 0.05, 3000)
  persp.up.set(0, 0, 1) // z 轴朝上，要在创建 OrbitControls 之前设置
  persp.position.set(-8, -10, 8)
  orbit = new OrbitControls(persp, renderer.domElement)
  orbit.enableDamping = true
  orbit.maxPolarAngle = Math.PI * 0.49 // 不能转到地面以下

  ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 3000)
  ortho.position.set(0, 0, 500)
  pan = new OrbitControls(ortho, renderer.domElement)
  pan.enableRotate = false
  pan.screenSpacePanning = true
  pan.mouseButtons = { LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN }
  pan.enabled = false

  const s = props.stream
  mapSync = { obj: makePoints(false, 0), buf: s.map, version: -1, resets: -1, data: null, uploaded: 0 }
  scanSync = { obj: makePoints(true, 0x00b4d8), buf: s.scan, version: -1, resets: -1, data: null, uploaded: 0 }
  scanSync.obj.renderOrder = 2
  estSync = { obj: makeLine(0x2563eb, false), buf: s.est, version: -1 }
  gtSync = { obj: makeLine(0x8a97ad, true), buf: s.gt, version: -1 }
  robot = makeRobot()
  scene.add(mapSync.obj, scanSync.obj, estSync.obj, gtSync.obj, robot.group)

  resizeObs = new ResizeObserver(resize)
  resizeObs.observe(el)
  resize()
  applyMode()
  raf = requestAnimationFrame(tick)
})

function applyMode() {
  orbit.enabled = props.mode !== 'top'
  pan.enabled = props.mode === 'top'
  resetView()
}
watch(() => props.mode, applyMode)

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  resizeObs?.disconnect()
  orbit.dispose()
  pan.dispose()
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
  <div ref="host" class="view" />
</template>

<style scoped>
.view {
  position: absolute;
  inset: 0;
  cursor: grab;
}
.view:active {
  cursor: grabbing;
}
.view :deep(canvas) {
  display: block;
  outline: none;
}
</style>
