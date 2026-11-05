<!-- Navigation page (steps 3a + 3b): pick a saved map, start/stop Nav2 on it with a parameter variant, and watch the
     robot in two views: 2D occupancy grid with Nav2 costmaps (left) and 3D point cloud model (right), both with robot
     pose, laser points and Nav2 paths. Initial pose, single goals and waypoint missions are set on the 2D view;
     every mission is recorded in ~/runs/nav_<time>/ (see robot_console/nav_mission.py). -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, shallowReactive, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, fetchAsset } from '@/api'
import { errorText } from '@/messages'
import { useStatusStore } from '@/stores/status'
import type {
  CostmapLayer, MapTool, NavGoalState, NavMap, NavMission, NavSolid, NavStatus, PickedPose, StackState, WaypointMark,
} from '@/types'
import NavGrid2D from '@/components/NavGrid2D.vue'
import NavScene3D from '@/components/NavScene3D.vue'
import NavigationImportDialog from '@/components/NavigationImportDialog.vue'
import InfoTip from '@/components/InfoTip.vue'
import WaypointPanel from '@/components/WaypointPanel.vue'

type TagType = 'success' | 'warning' | 'danger' | 'info' | 'primary'

const { t } = useI18n()
const store = useStatusStore()

// ---------------- Saved preferences ----------------
const PREF_KEY = 'robot_console.navigation_view'
const saved = (() => {
  try {
    return JSON.parse(localStorage.getItem(PREF_KEY) ?? '{}')
  } catch {
    return {}
  }
})()
// ?map=<name> and ?model=cloud|solid in the address override the saved choice (like ?view= on the mapping page)
const route = useRoute()
const qMap = typeof route.query.map === 'string' ? route.query.map : null
const qModel = route.query.model === 'solid' || route.query.model === 'cloud' ? route.query.model : null
const chosen = ref<string>(qMap ?? (typeof saved.map === 'string' ? saved.map : ''))
const showCloud = ref<boolean>(saved.cloud !== false)
const showGrid = ref<boolean>(saved.grid !== false)
const model3d = ref<'cloud' | 'solid'>(qModel ?? (saved.model === 'solid' ? 'solid' : 'cloud'))
const showCost = reactive({ global: saved.costGlobal === true, local: saved.costLocal !== false })
const variant = ref<string>(typeof saved.variant === 'string' ? saved.variant : '')
watch([chosen, showCloud, showGrid, model3d, () => showCost.global, () => showCost.local, variant], () => {
  try {
    localStorage.setItem(
      PREF_KEY,
      JSON.stringify({
        map: chosen.value, cloud: showCloud.value, grid: showGrid.value, model: model3d.value,
        costGlobal: showCost.global, costLocal: showCost.local, variant: variant.value,
      }),
    )
  } catch {
    // Storage disabled: preferences are just not remembered
  }
})

// ---------------- Map list ----------------
const maps = ref<NavMap[]>([])
const mapsError = ref('')
async function loadMaps() {
  try {
    maps.value = await api.navigationMaps()
    mapsError.value = ''
  } catch (e) {
    mapsError.value = errorText(e)
  }
}

// ---------------- Nav2 parameter variants (nav2_params_<variant>.yaml) ----------------
const variants = ref<string[]>([])
async function loadVariants() {
  try {
    const r = await api.navigationVariants()
    variants.value = r.variants
    if (!r.variants.includes(variant.value)) variant.value = r.default
  } catch {
    // Older backend: start without choosing (its default)
  }
}

// ---------------- Status polling ----------------
const nav = shallowRef<NavStatus | null>(null)
const statusError = ref('')
let pollTimer = 0
let pollSeq = 0
let alive = true
async function poll() {
  const seq = ++pollSeq
  try {
    if (!document.hidden) {
      const s = await api.navigationStatus()
      if (seq === pollSeq && alive) {
        nav.value = s
        statusError.value = ''
      }
    }
  } catch (e) {
    if (seq === pollSeq && alive) statusError.value = errorText(e)
  } finally {
    if (alive) pollTimer = window.setTimeout(poll, 300)
  }
}

onMounted(() => {
  loadMaps()
  loadVariants()
  poll()
})
onBeforeUnmount(() => {
  alive = false
  clearTimeout(pollTimer)
})
watch(
  () => store.connected,
  (c) => {
    if (c) {
      loadMaps()
      loadVariants()
    }
  },
)

// ---------------- Active / selected map ----------------
const state = computed<StackState>(() => nav.value?.state ?? 'idle')
const active = computed(() => state.value === 'running' || state.value === 'stopping')
// While navigation runs, the view must show the map Nav2 actually loaded
const effectiveName = computed(() => {
  if (active.value && nav.value?.map) return nav.value.map
  if (maps.value.some((m) => m.name === chosen.value)) return chosen.value
  return maps.value[0]?.name ?? ''
})
const effectiveMap = computed(() => maps.value.find((m) => m.name === effectiveName.value) ?? null)
const selectValue = computed({
  get: () => effectiveName.value,
  set: (v: string) => {
    if (!active.value && !busy.value) chosen.value = v
  },
})

// ---------------- Map assets (grid image + point cloud) ----------------
const image = shallowRef<ImageBitmap | null>(null)
const cloud = shallowRef<Float32Array | null>(null)
const solid = shallowRef<NavSolid | null>(null)
const loadingAssets = ref(false)
const assetError = ref('')
let assetAbort: AbortController | null = null

function setImage(next: ImageBitmap | null) {
  image.value?.close()
  image.value = next
}

async function loadAssets(m: NavMap | null) {
  assetAbort?.abort()
  setImage(null)
  cloud.value = null
  solid.value = null
  assetError.value = ''
  if (!m) return
  const ctrl = new AbortController()
  assetAbort = ctrl
  loadingAssets.value = true
  const base = `/api/navigation/maps/${encodeURIComponent(m.name)}`
  try {
    const blob = await (await fetchAsset(`${base}/grid.png`, ctrl.signal)).blob()
    const bmp = await createImageBitmap(blob)
    if (ctrl.signal.aborted) {
      bmp.close()
      return
    }
    setImage(bmp)
    if (m.has_cloud) {
      const buf = await (await fetchAsset(`${base}/cloud`, ctrl.signal)).arrayBuffer()
      if (ctrl.signal.aborted) return
      cloud.value = new Float32Array(buf, 0, Math.floor(buf.byteLength / 12) * 3)
    }
    if (m.has_solid) {
      const sol = (await (await fetchAsset(`${base}/solid`, ctrl.signal)).json()) as NavSolid
      if (ctrl.signal.aborted) return
      solid.value = sol
    }
  } catch (e) {
    if (!ctrl.signal.aborted) assetError.value = errorText(e)
  } finally {
    if (assetAbort === ctrl) loadingAssets.value = false
  }
}
watch(effectiveMap, (m, old) => {
  if (m?.name !== old?.name) loadAssets(m)
})
onMounted(() => loadAssets(effectiveMap.value))
onBeforeUnmount(() => {
  assetAbort?.abort()
  setImage(null)
})

// ---------------- Simulation switch (independent of the mapping page) ----------------
// There is only one Gazebo. Starting it here uses the scene recorded in the map, so the robot spawns at the
// map origin, which is AMCL's default initial pose.
const simInfo = computed(() => nav.value?.sim ?? null)
const simState = computed<StackState>(() => simInfo.value?.state ?? 'idle')
const simJob = computed(() => simInfo.value?.job ?? 'idle')
const simBusy = ref(false)
const mapScene = computed(() => effectiveMap.value?.preset ?? null)
// Simulation started from another page: the robot may have left its spawn point
const simForeign = computed(() => simState.value === 'running' && simInfo.value?.origin !== 'navigation')
const simSwitchBlock = computed(() => {
  if (!store.connected) return t('navigation.block.offline')
  if (active.value) return t('navigation.sim.stopNavFirst')
  if (!effectiveMap.value) return t('navigation.block.noMap')
  if (!mapScene.value && simState.value !== 'running') return t('navigation.sim.noScene')
  return ''
})
const simOn = computed({
  get: () => simState.value === 'running' || simState.value === 'stopping' || simJob.value === 'restarting',
  set: (on: boolean) => {
    void toggleSim(on)
  },
})
async function toggleSim(on: boolean) {
  const m = effectiveMap.value
  simBusy.value = true
  try {
    if (on && m) {
      await api.startNavSim(m.name)
      ElMessage.success(t('navigation.sim.started', { scene: m.preset ?? '' }))
    } else if (!on) {
      await api.stopNavSim()
    }
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    simBusy.value = false
  }
}
async function restartSim() {
  const m = effectiveMap.value
  if (!m) return
  simBusy.value = true
  try {
    await api.restartNavSim(m.name)
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    simBusy.value = false
  }
}

// ---------------- Start / stop ----------------
const busy = ref(false)
const simRunning = computed(() => !!nav.value?.sim_running)
const sceneMismatch = computed(() => {
  const m = effectiveMap.value
  const sim = nav.value?.sim_preset
  return !active.value && !!m?.preset && !!sim && simRunning.value && m.preset !== sim
})
const startBlock = computed(() => {
  if (!store.connected) return t('navigation.block.offline')
  if (!effectiveMap.value) return t('navigation.block.noMap')
  if (!simRunning.value) return t('navigation.block.noSim')
  if (sceneMismatch.value) {
    return t('navigation.block.scene', { map: effectiveMap.value!.preset!, sim: nav.value!.sim_preset! })
  }
  return ''
})

async function start() {
  const m = effectiveMap.value
  if (!m) return
  busy.value = true
  try {
    await api.startNavigation(m.name, variant.value || undefined)
    ElMessage.success(t('navigation.started', { name: m.name }))
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    busy.value = false
  }
}
async function stop() {
  busy.value = true
  try {
    await api.stopNavigation()
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    busy.value = false
  }
}

// ---------------- Display helpers ----------------
const STATE_TAG: Record<StackState, TagType> = { idle: 'info', running: 'success', stopping: 'warning', exited: 'info' }
const running = computed(() => state.value === 'running')
const localization = computed(() => {
  const s = nav.value
  if (!running.value || !s) return { type: 'info' as TagType, text: t('navigation.loc.idle') }
  if (s.tf_ok) return { type: 'success' as TagType, text: t('navigation.loc.ok', { age: (s.age ?? 0).toFixed(1) }) }
  return { type: 'warning' as TagType, text: t('navigation.loc.waiting') }
})
const nav2Ready = computed(() => running.value && !!nav.value?.ready)
const sizeText = computed(() => {
  const g = effectiveMap.value?.grid
  return g ? `${(g.width * g.resolution).toFixed(1)} × ${(g.height * g.resolution).toFixed(1)} m` : '—'
})
const poseText = computed(() => {
  const p = nav.value?.pose
  if (!p) return null
  return { x: p.x.toFixed(2), y: p.y.toFixed(2), yaw: ((p.yaw * 180) / Math.PI).toFixed(0) }
})
const counts = computed(() => ({
  scan: nav.value?.scan.length ?? 0,
  global: nav.value?.global_path.length ?? 0,
  local: nav.value?.local_path.length ?? 0,
}))

// Both models exist -> user may switch; otherwise show whichever exists
const canSwitchModel = computed(() => !!cloud.value && !!solid.value)
const shownModel = computed<'cloud' | 'solid'>(() => {
  if (canSwitchModel.value) return model3d.value
  return solid.value && !cloud.value ? 'solid' : 'cloud'
})

// ---------------- Initial pose, goal, cancel (3b) ----------------
const tool = ref<MapTool>('pan')
const sending = ref(false)
const goal = computed(() => nav.value?.goal ?? null)
const goalActive = computed(() => !!goal.value && ['pending', 'active', 'canceling'].includes(goal.value.state))
const canPick = computed(() => running.value && !!nav.value?.tf_ok)
const canGoal = computed(() => canPick.value && nav2Ready.value)

function toggleTool(next: Exclude<MapTool, 'pan'>) {
  tool.value = tool.value === next ? 'pan' : next
}
// Leave picking mode when navigation stops, or with Esc
watch(running, (r) => {
  if (!r) tool.value = 'pan'
})
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') tool.value = 'pan'
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

async function onPick(kind: Exclude<MapTool, 'pan'>, p: PickedPose) {
  if (kind === 'waypoint') {
    // Stay in waypoint mode: every click adds the next point (Esc or the button ends it)
    waypoints.value = [...waypoints.value, p]
    return
  }
  tool.value = 'pan'
  sending.value = true
  try {
    if (kind === 'pose') {
      await api.setInitialPose(p)
      ElMessage.success(t('navigation.goal.poseSet', { x: p.x.toFixed(2), y: p.y.toFixed(2) }))
    } else {
      await api.sendGoal(p)
    }
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    sending.value = false
  }
}

async function cancelGoal() {
  sending.value = true
  try {
    await api.cancelGoal()
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    sending.value = false
  }
}

// ---------------- Speed multiplier ----------------
// Local value follows the backend except while the user is dragging / the request is in flight
const speedInfo = computed(() => nav.value?.speed ?? null)
const speedScale = ref(1)
const speedBusy = ref(false)
let speedDragging = false
watch(
  () => speedInfo.value?.scale,
  (k) => {
    if (k != null && !speedDragging && !speedBusy.value) speedScale.value = k
  },
  { immediate: true },
)
const speedPreview = computed(() => {
  const s = speedInfo.value
  if (!s?.max_v) return null
  // Linear speed limit = 1x speed x multiplier (same formula as the backend)
  return { v: ((s.max_v / s.scale) * speedScale.value).toFixed(2) }
})
// Line under the slider: the resulting speed limit, and whether/when the multiplier takes effect
const speedNote = computed(() => {
  const s = speedInfo.value
  const parts: string[] = []
  if (speedPreview.value) parts.push(t('navigation.speed.limit', { v: speedPreview.value.v }))
  if (running.value && s && !s.applied && !s.error) parts.push(t('navigation.speed.applying'))
  else if (!running.value) parts.push(t('navigation.speed.later'))
  return parts.join(' · ')
})
function onSpeedInput() {
  speedDragging = true
}
async function onSpeedChange(k: number) {
  speedDragging = false
  speedBusy.value = true
  try {
    await api.setNavSpeed(k)
  } catch (e) {
    ElMessage.error(errorText(e))
    if (speedInfo.value) speedScale.value = speedInfo.value.scale
  } finally {
    speedBusy.value = false
  }
}
const speedMarks = computed(() => ({ 1: '1×' }))

// One notice per goal when it ends (arrived / failed / cancelled / rejected)
const GOAL_END: Partial<Record<NavGoalState, 'success' | 'error' | 'warning' | 'info'>> = {
  succeeded: 'success', aborted: 'error', canceled: 'info', rejected: 'warning',
}
let notified = 0
watch(goal, (g) => {
  if (!g || g.seq === notified) return
  const kind = GOAL_END[g.state]
  if (!kind) return
  notified = g.seq
  ElMessage({ type: kind, message: t(`navigation.goal.end.${g.state}`, { time: g.elapsed.toFixed(1) }), duration: 5000 })
})

const GOAL_TAG: Record<NavGoalState, TagType> = {
  pending: 'warning', active: 'primary', canceling: 'warning',
  succeeded: 'success', aborted: 'danger', canceled: 'info', rejected: 'danger',
}
const amclText = computed(() => {
  const a = nav.value?.amcl
  if (!a) return null
  const xy = Math.max(a.std_x, a.std_y)
  return { xy: xy.toFixed(2), yaw: ((a.std_yaw * 180) / Math.PI).toFixed(1), good: xy < 0.3 }
})

// 导入只登记导航地图；可选绑定已有场景，绝不自动启动仿真。
const importOpen = ref(false)
async function onImported(map: NavMap) {
  await loadMaps()
  if (!maps.value.some((m) => m.name === map.name)) maps.value.push(map)
  chosen.value = map.name
}

const grid2d = ref<InstanceType<typeof NavGrid2D> | null>(null)
const scene3d = ref<InstanceType<typeof NavScene3D> | null>(null)

// ---------------- Waypoint missions (3b) ----------------
const waypoints = ref<PickedPose[]>([])
const mission = computed<NavMission | null>(() => nav.value?.mission ?? null)
const missionRunning = computed(() => mission.value?.state === 'running')
watch(missionRunning, (r) => {
  if (r && tool.value === 'waypoint') tool.value = 'pan'
})
// While a mission runs the map shows its goals with their results; otherwise the editable draft
const marks = computed<WaypointMark[]>(() => {
  const m = mission.value
  if (m?.state === 'running' && m.total > 1) {
    return m.goals.map((g) => ({
      x: g.x, y: g.y, yaw: g.yaw,
      kind: g.result ? (g.result === 'succeeded' ? 'ok' : 'fail') : g.state === 'waiting' ? 'waiting' : 'active',
    }))
  }
  return waypoints.value.map((p) => ({ ...p, kind: 'draft' as const }))
})
// One notice when a mission of several goals ends (single goals already get the per-goal notice)
watch(
  () => [mission.value?.id, mission.value?.state] as const,
  ([id, st], old) => {
    const m = mission.value
    if (!m || !old || id !== old[0] || old[1] !== 'running' || st === 'running' || m.total < 2) return
    ElMessage({
      type: m.summary.succeeded === m.total ? 'success' : 'warning',
      message: t('navMission.ended', { ok: m.summary.succeeded, n: m.total, id: m.id, runs: store.paths.runs }),
      duration: 6000,
    })
  },
)

// ---------------- Costmap overlays (2D view) ----------------
// shallowReactive: an ImageBitmap must not be wrapped in a proxy (drawImage rejects it)
const costmaps = shallowReactive<{ global: CostmapLayer | null; local: CostmapLayer | null }>({ global: null, local: null })
const costLoading = { global: false, local: false }
const costShown = { global: -1, local: -1 } // version currently displayed
function setLayer(key: 'global' | 'local', next: CostmapLayer | null) {
  costmaps[key]?.image.close()
  costmaps[key] = next
}
async function fetchCostmap(key: 'global' | 'local', version: number) {
  if (costLoading[key]) return // the next poll fetches the newer version
  costLoading[key] = true
  try {
    const res = await fetch(`/api/navigation/costmap/${key}.png`, { cache: 'no-store' })
    if (!res.ok) return
    const num = (h: string) => (res.headers.get(h) ?? '').split(',').map(Number)
    const [ox, oy, oyaw] = num('X-Origin')
    const [w, h] = num('X-Size')
    const resolution = num('X-Resolution')[0]
    const image = await createImageBitmap(await res.blob())
    if (!alive || !showCost[key] || ![ox, oy, oyaw, w, h, resolution].every(Number.isFinite)) {
      image.close()
      return
    }
    setLayer(key, { image, origin: [ox, oy, oyaw], resolution, width: w, height: h })
    costShown[key] = version
  } catch {
    // Network hiccup: try again on the next status
  } finally {
    costLoading[key] = false
  }
}
watch(
  () => [nav.value?.costmaps, showCost.global, showCost.local, running.value] as const,
  () => {
    for (const key of ['global', 'local'] as const) {
      const v = nav.value?.costmaps?.[key] ?? null
      if (!running.value || !showCost[key] || v == null) {
        setLayer(key, null)
        costShown[key] = -1
      } else if (v !== costShown[key]) {
        void fetchCostmap(key, v)
      }
    }
  },
)
onBeforeUnmount(() => {
  setLayer('global', null)
  setLayer('local', null)
})
</script>

<template>
  <div class="navigation">
    <div class="views">
      <!-- 2D occupancy grid -->
      <section class="viewport">
        <NavGrid2D
          ref="grid2d" :map="effectiveMap" :image="image" :status="nav" :tool="tool" :waypoints="marks"
          :costmaps="costmaps" @pick="onPick"
        />
        <div class="hud tl">
          <span class="title">{{ t('navigation.view2d') }}</span>
          <el-checkbox v-model="showCost.global" size="small">{{ t('navigation.layers.costGlobal') }}</el-checkbox>
          <el-checkbox v-model="showCost.local" size="small">{{ t('navigation.layers.costLocal') }}</el-checkbox>
          <InfoTip :content="t('navigation.tips.costmap')" placement="bottom" />
          <span v-if="tool !== 'pan'" class="picking" :class="tool">{{ t(`navigation.goal.how.${tool}`) }}</span>
        </div>
        <div class="hud tr">
          <el-button size="small" circle :title="t('navigation.fit')" @click="grid2d?.fit()">
            <el-icon><Aim /></el-icon>
          </el-button>
        </div>
        <div class="hud bl legend">
          <span><i class="sw robot" />{{ t('navigation.legend.robot') }}</span>
          <span><i class="sw scan" />{{ t('navigation.legend.scan') }}</span>
          <span><i class="sw global" />{{ t('navigation.legend.global') }}</span>
          <span><i class="sw local" />{{ t('navigation.legend.local') }}</span>
          <span><i class="sw goal" />{{ t('navigation.legend.goal') }}</span>
          <span><i class="sw waypoint" />{{ t('navigation.legend.waypoint') }}</span>
          <span v-if="showCost.global || showCost.local"><i class="sw cost" />{{ t('navigation.legend.costmap') }}</span>
        </div>
        <div v-if="!effectiveMap || (!image && !loadingAssets)" class="empty">
          {{ assetError || mapsError || t('navigation.noMaps') }}
        </div>
      </section>

      <!-- 3D point cloud model -->
      <section class="viewport">
        <NavScene3D
          ref="scene3d"
          :map="effectiveMap"
          :image="image"
          :cloud="cloud"
          :solid="solid"
          :model="shownModel"
          :status="nav"
          :show-cloud="showCloud"
          :show-grid="showGrid"
        />
        <div class="hud tl">
          <span class="title">{{ t('navigation.view3d') }}</span>
          <el-radio-group v-if="canSwitchModel" v-model="model3d" size="small">
            <el-radio-button value="cloud">{{ t('navigation.model.cloud') }}</el-radio-button>
            <el-radio-button value="solid">{{ t('navigation.model.solid') }}</el-radio-button>
          </el-radio-group>
          <el-checkbox v-model="showCloud" size="small" :disabled="!cloud || shownModel === 'solid'">{{ t('navigation.layers.cloud') }}</el-checkbox>
          <el-checkbox v-model="showGrid" size="small">{{ t('navigation.layers.grid') }}</el-checkbox>
        </div>
        <div class="hud tr">
          <el-button size="small" circle :title="t('navigation.fit')" @click="scene3d?.resetView()">
            <el-icon><Aim /></el-icon>
          </el-button>
        </div>
        <div v-if="effectiveMap && !effectiveMap.has_cloud" class="hud bl note">
          {{ t('navigation.noCloud') }}
        </div>
        <div v-if="loadingAssets" class="empty">{{ t('navigation.loading') }}</div>
      </section>
    </div>

    <aside class="side glass">
      <section class="block">
        <h3>{{ t('navigation.steps.map') }}</h3>
        <el-select v-model="selectValue" class="wide" :disabled="active || busy || !maps.length" filterable>
          <el-option v-for="m in maps" :key="m.name" :label="m.name" :value="m.name">
            <span>{{ m.name }}</span>
            <span class="opt-tag">{{ m.has_cloud ? '2D + 3D' : '2D' }}</span>
          </el-option>
        </el-select>
        <p v-if="active" class="muted">{{ t('navigation.mapLocked') }}</p>
        <el-button
          class="wide import-btn" size="small" plain :disabled="active || busy || !store.connected"
          @click="importOpen = true"
        >{{ t('navigation.import.button') }}</el-button>
        <dl v-if="effectiveMap" class="facts">
          <dt>{{ t('navigation.facts.size') }}</dt>
          <dd>{{ sizeText }}</dd>
          <dt>{{ t('navigation.facts.resolution') }}</dt>
          <dd>{{ effectiveMap.grid.resolution }} m</dd>
          <dt>{{ t('navigation.facts.scene') }}</dt>
          <dd>{{ effectiveMap.preset ?? t('navigation.facts.unknown') }}</dd>
          <dt>{{ t('navigation.facts.cloud') }}</dt>
          <dd>{{ cloud ? (cloud.length / 3).toLocaleString() : effectiveMap.has_cloud ? '…' : '—' }}</dd>
        </dl>
      </section>

      <section class="block">
        <!-- Same layout as ③ navigation: title + state tag, facts, one full-width start/stop button -->
        <h3>
          {{ t('navigation.steps.sim') }}
          <el-tag size="small" :type="simJob !== 'idle' ? 'warning' : STATE_TAG[simState]" effect="light">
            {{ simJob !== 'idle' ? t(`navigation.sim.${simJob}`) : t(`monitor.state.${simState}`) }}
          </el-tag>
        </h3>
        <dl class="facts sim-facts">
          <dt>{{ t('navigation.sim.scene') }}</dt>
          <dd>{{ (simState === 'running' ? nav?.sim_preset : mapScene) ?? '—' }}</dd>
          <template v-if="simState === 'running' && simInfo?.origin">
            <dt>{{ t('navigation.sim.startedBy') }}</dt>
            <dd>{{ t(`navigation.sim.origin.${simInfo.origin}`) }}</dd>
          </template>
        </dl>
        <el-button
          class="wide"
          :type="simOn ? 'danger' : 'primary'"
          :plain="simOn"
          :loading="simBusy || simJob !== 'idle' || simState === 'stopping'"
          :disabled="!!simSwitchBlock || simBusy || simJob !== 'idle' || simState === 'stopping'"
          @click="simOn = !simOn"
        >
          {{ simOn ? t('navigation.sim.stop') : t('navigation.sim.start') }}
        </el-button>
        <p v-if="simSwitchBlock && simJob === 'idle'" class="warn">{{ simSwitchBlock }}</p>
        <template v-if="(simForeign || sceneMismatch) && simJob === 'idle'">
          <p class="warn">{{ sceneMismatch ? t('navigation.sim.mismatch') : t('navigation.sim.foreign') }}</p>
          <el-button
            class="wide restart"
            type="warning"
            plain
            :disabled="active || !mapScene || simBusy"
            @click="restartSim"
          >
            {{ t('navigation.sim.restart') }}
          </el-button>
        </template>
        <p v-if="simInfo?.error" class="warn mono">{{ simInfo.error }}</p>
      </section>

      <section class="block">
        <h3>
          {{ t('navigation.steps.nav') }}
          <el-tag size="small" :type="STATE_TAG[state]" effect="light">{{ t(`monitor.state.${state}`) }}</el-tag>
        </h3>
        <div v-if="variants.length" class="variant">
          <span>{{ t('navigation.variant.label') }}<InfoTip :content="t('navigation.tips.variant')" /></span>
          <el-select
            v-if="!active" v-model="variant" size="small" class="variant-select" :disabled="busy"
          >
            <el-option v-for="v in variants" :key="v" :label="v" :value="v" />
          </el-select>
          <b v-else class="mono">{{ nav?.variant ?? '—' }}</b>
        </div>
        <el-button
          v-if="!active"
          class="wide"
          type="primary"
          :loading="busy"
          :disabled="!!startBlock || busy"
          @click="start"
        >
          {{ t('navigation.start') }}
        </el-button>
        <el-button v-else class="wide" type="danger" plain :loading="busy || state === 'stopping'" @click="stop">
          {{ t('navigation.stop') }}
        </el-button>
        <p v-if="!active && startBlock" class="warn">{{ startBlock }}</p>
        <!-- Negative codes mean the process was stopped by a signal (normal stop); only show real failures -->
        <p v-if="state === 'exited' && (nav?.exit_code ?? 0) > 0" class="warn">
          {{ t('monitor.exitCode', { code: nav!.exit_code! }) }}
        </p>
        <p class="muted">{{ t('navigation.initialPoseHint') }}</p>
      </section>

      <section class="block">
        <h3>
          {{ t('navigation.steps.goal') }}
          <el-tag v-if="goal" size="small" :type="GOAL_TAG[goal.state]" effect="light">
            {{ t(`navigation.goal.state.${goal.state}`) }}
          </el-tag>
        </h3>
        <div class="tools">
          <el-button
            size="small"
            :type="tool === 'pose' ? 'primary' : 'default'"
            :disabled="!canPick || sending"
            @click="toggleTool('pose')"
          >
            <i class="dot pose" />{{ t('navigation.goal.setPose') }}
          </el-button>
          <el-button
            size="small"
            :type="tool === 'goal' ? 'primary' : 'default'"
            :disabled="!canGoal || sending"
            @click="toggleTool('goal')"
          >
            <i class="dot goal" />{{ t('navigation.goal.pick') }}
          </el-button>
        </div>
        <el-button
          class="wide cancel"
          size="small"
          type="danger"
          plain
          :disabled="!goalActive || goal?.state === 'canceling' || sending"
          @click="cancelGoal"
        >
          {{ t('navigation.goal.cancel') }}
        </el-button>
        <div class="speed">
          <div class="speed-head">
            <span>{{ t('navigation.speed.label') }}<InfoTip :content="t('navigation.tips.speed')" /></span>
            <b class="mono">{{ speedScale.toFixed(2) }}×</b>
          </div>
          <el-slider
            v-model="speedScale"
            :min="speedInfo?.range[0] ?? 0.5"
            :max="speedInfo?.range[1] ?? 4"
            :step="0.25"
            :marks="speedMarks"
            :show-tooltip="false"
            :disabled="!speedInfo?.max_v || speedBusy"
            size="small"
            @input="onSpeedInput"
            @change="(v: number | number[]) => onSpeedChange(Array.isArray(v) ? v[0] : v)"
          />
          <p v-if="speedNote" class="muted">{{ speedNote }}</p>
          <p v-if="speedInfo?.error" class="warn mono">{{ speedInfo.error }}</p>
        </div>
        <p v-if="!running" class="muted">{{ t('navigation.goal.needNav') }}</p>
        <p v-else-if="!canPick" class="muted">{{ t('navigation.goal.needLoc') }}</p>
        <p v-else-if="tool !== 'pan'" class="hint">{{ t(`navigation.goal.how.${tool}`) }}</p>
        <ul v-if="goal" class="kv goal-kv">
          <li>
            <span>{{ t('navigation.goal.target') }}</span>
            <b>x {{ goal.x.toFixed(2) }} · y {{ goal.y.toFixed(2) }} · θ {{ ((goal.yaw * 180) / Math.PI).toFixed(0) }}°</b>
          </li>
          <li>
            <span>{{ t('navigation.goal.remaining') }}</span>
            <!-- Nav2's first feedback is all zeros before the controller starts; hide it until it is real -->
            <b>{{ goal.feedback && goalActive && goal.feedback.nav_time > 0 ? `${goal.feedback.distance.toFixed(2)} m` : '—' }}</b>
          </li>
          <li>
            <span>{{ t('navigation.goal.elapsed') }}</span><b>{{ goal.elapsed.toFixed(1) }} s</b>
          </li>
          <li>
            <span>{{ t('navigation.goal.recoveries') }}<InfoTip :content="t('navigation.tips.recoveries')" /></span><b>{{ goal.feedback?.recoveries ?? 0 }}</b>
          </li>
        </ul>
      </section>

      <section class="block">
        <h3>{{ t('navigation.steps.mission') }}</h3>
        <WaypointPanel
          v-model="waypoints"
          :map-name="effectiveMap?.name ?? ''"
          :mission="mission"
          :picking="tool === 'waypoint'"
          :can-pick="canPick"
          :can-run="canGoal"
          @toggle-pick="toggleTool('waypoint')"
        />
      </section>

      <section class="block">
        <h3>{{ t('navigation.steps.state') }}</h3>
        <ul class="kv">
          <li>
            <span>{{ t('navigation.amcl.label') }}<InfoTip :content="t('navigation.tips.amcl')" /></span>
            <b v-if="amclText" :class="{ ok: amclText.good, bad: !amclText.good }">
              ±{{ amclText.xy }} m · ±{{ amclText.yaw }}°
            </b>
            <b v-else>—</b>
          </li>
          <li>
            <span>{{ t('navigation.loc.label') }}</span>
            <el-tag size="small" :type="localization.type" effect="light">{{ localization.text }}</el-tag>
          </li>
          <li>
            <span>Nav2</span>
            <el-tag size="small" :type="nav2Ready ? 'success' : 'info'" effect="light">
              {{ nav2Ready ? t('navigation.nav2.ready') : t('navigation.nav2.waiting') }}
            </el-tag>
          </li>
          <li>
            <span>{{ t('navigation.pose') }}</span>
            <b v-if="poseText">x {{ poseText.x }} · y {{ poseText.y }} · θ {{ poseText.yaw }}°</b>
            <b v-else>—</b>
          </li>
          <li>
            <span>{{ t('navigation.legend.scan') }}</span><b>{{ counts.scan }}</b>
          </li>
          <li>
            <span>{{ t('navigation.legend.global') }}</span><b>{{ counts.global }}</b>
          </li>
          <li>
            <span>{{ t('navigation.legend.local') }}</span><b>{{ counts.local }}</b>
          </li>
        </ul>
        <p v-if="running && !nav?.tf_ok && nav?.error" class="warn mono">{{ nav.error }}</p>
        <p v-if="running && nav?.tf_ok && !counts.global" class="muted">{{ t('navigation.noPath') }}</p>
        <p v-if="statusError" class="warn">{{ statusError }}</p>
      </section>
    </aside>
    <NavigationImportDialog v-model="importOpen" :disabled="active || busy || !store.connected" @imported="onImported" />
  </div>
</template>

<style scoped>
.navigation {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 14px;
  height: 100%;
  min-height: 520px;
}
.views {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  min-width: 0;
  min-height: 0;
}
.viewport {
  position: relative;
  min-height: 320px;
  border-radius: var(--radius);
  overflow: hidden;
  background:
    radial-gradient(ellipse at 50% 35%, rgba(255, 255, 255, 0.92), rgba(255, 255, 255, 0.55) 70%),
    var(--glass);
  box-shadow: var(--shadow);
}
.hud {
  position: absolute;
  z-index: 3;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
}
.tl {
  top: 12px;
  left: 14px;
  flex-wrap: wrap;
  padding: 4px 10px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(12px);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.tl .title {
  font-weight: 650;
  color: var(--ink);
}
.tr {
  top: 12px;
  right: 14px;
}
.bl {
  left: 14px;
  bottom: 12px;
  flex-wrap: wrap;
  padding: 5px 12px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.78);
  backdrop-filter: blur(12px);
  box-shadow: inset 0 0 0 1px var(--hairline);
  color: var(--ink-2);
}
.legend {
  gap: 12px;
}
.sw {
  display: inline-block;
  margin-right: 5px;
  vertical-align: middle;
}
.sw.robot {
  width: 12px;
  height: 9px;
  border: 1.5px solid #0891b2;
  border-radius: 2px;
  background: #fff;
}
.sw.scan {
  width: 6px;
  height: 6px;
  background: #e11d48;
}
.sw.global {
  width: 16px;
  height: 3px;
  border-radius: 2px;
  background: #2a78d6;
}
.sw.local {
  width: 16px;
  border-top: 3px dashed #eb6834;
}
.note {
  color: var(--ink-3);
}
.empty {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  text-align: center;
  color: var(--ink-3);
  pointer-events: none;
}

.side {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 16px;
  border-radius: var(--radius);
  overflow-y: auto;
}
.block {
  padding: 10px 0 12px;
  border-bottom: 1px solid var(--hairline);
}
.block:last-child {
  border-bottom: 0;
}
h3 {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 0 0 10px;
  font-size: 14px;
  font-weight: 650;
}
.wide {
  width: 100%;
}
.import-btn {
  margin-top: 8px;
}
.tools {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.tools .el-button {
  margin: 0;
}
.cancel {
  margin-top: 8px;
}
.facts.sim-facts {
  margin: 0 0 10px;
}
.restart {
  margin: 8px 0 0;
}
.speed {
  margin-top: 12px;
  padding: 8px 10px 4px;
  border-radius: 10px;
  background: rgba(8, 145, 178, 0.05);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.speed-head {
  display: flex;
  justify-content: space-between;
  font-size: 12.5px;
  color: var(--ink-3);
}
.speed-head b {
  color: var(--ink);
}
.speed .el-slider {
  margin: 0 6px 14px;
}
.speed .muted {
  margin-top: 4px;
}
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-right: 6px;
  border-radius: 50%;
  border: 2px solid currentColor;
}
.dot.pose {
  color: #7c3aed;
}
.dot.goal {
  color: #16a34a;
}
.sw.goal {
  width: 9px;
  height: 9px;
  border: 2px solid #16a34a;
  border-radius: 50%;
}
.sw.waypoint {
  width: 11px;
  height: 11px;
  border: 2px solid #0891b2;
  border-radius: 50%;
}
.sw.cost {
  width: 16px;
  height: 9px;
  border-radius: 2px;
  background: linear-gradient(90deg, rgba(56, 189, 248, 0.5), rgba(244, 63, 94, 0.7), #881337);
}
.picking.waypoint {
  background: #0891b2;
}
.variant {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
  font-size: 12.5px;
  color: var(--ink-3);
}
.variant > span {
  display: inline-flex;
  align-items: center;
  white-space: nowrap;
}
.variant-select {
  flex: 0 1 150px;
  min-width: 96px;
}
.variant b {
  color: var(--ink);
}
.hint {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--accent);
}
.picking {
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 12px;
  color: #fff;
}
.picking.pose {
  background: #7c3aed;
}
.picking.goal {
  background: #16a34a;
}
.goal-kv {
  margin-top: 10px;
}
.kv b.ok {
  color: var(--ok);
}
.kv b.bad {
  color: var(--warn);
}
.opt-tag {
  float: right;
  font-size: 11.5px;
  color: var(--ink-3);
}
.muted,
.warn {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-3);
}
.warn {
  color: var(--warn);
}
.mono {
  font-family: var(--font-mono);
  word-break: break-all;
}
.facts {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 12px;
  margin: 10px 0 0;
  font-size: 12.5px;
}
.facts dt {
  color: var(--ink-3);
}
.facts dd {
  margin: 0;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.kv {
  display: flex;
  flex-direction: column;
  gap: 7px;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 12.5px;
}
.kv li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.kv span {
  color: var(--ink-3);
}
.kv b {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

@media (max-width: 1280px) {
  .views {
    grid-template-columns: minmax(0, 1fr);
    grid-auto-rows: minmax(320px, 1fr);
  }
}
@media (max-width: 960px) {
  .navigation {
    grid-template-columns: minmax(0, 1fr);
    height: auto;
  }
}
</style>
