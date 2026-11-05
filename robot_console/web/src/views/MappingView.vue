<!-- 建图页：中间 3D 视图（点云地图、轨迹、小车），下方指标条，右侧按步骤操作。
     ①选场景预设并启动仿真，②开始建图，③自动航点（显示进度）或遥控（摇杆、键盘）；保存到地图库在 2d 加入 -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { fmtCount, fmtDuration } from '@/format'
import { pick } from '@/i18n'
import { errorText, resultText } from '@/messages'
import { useStatusStore } from '@/stores/status'
import { createMappingStream, type LayerKey, type SaveJob, type ViewMode } from '@/mapping/stream'
import type { MappingPreset, StackInfo, StackState } from '@/types'
import PointCloudView from '@/components/PointCloudView.vue'
import RadarMark from '@/components/RadarMark.vue'
import TeleopPad from '@/components/TeleopPad.vue'
import MappingImportDialog from '@/components/MappingImportDialog.vue'
import InfoTip from '@/components/InfoTip.vue'

type TagType = 'success' | 'warning' | 'danger' | 'info' | 'primary'

const { t } = useI18n()
const store = useStatusStore()
const stream = createMappingStream(store)
const ms = stream.status
onMounted(stream.start)
onBeforeUnmount(stream.stop)

// ---------------- 3D 视图的控件 ----------------
// 图层、相机模式、点大小、场景、移动方式保存在浏览器里，下次打开沿用；
// 地址里的 ?view=top、?move=teleop 可以直接指定相机模式和移动方式

const LAYERS: LayerKey[] = ['map', 'scan', 'est', 'gt', 'grid']
const MODES: ViewMode[] = ['follow', 'free', 'top']
const MOVES = ['teleop', 'waypoints'] as const
type MoveMode = (typeof MOVES)[number]
const VIEW_KEY = 'robot_console.mapping_view'
const saved = (() => {
  try {
    return JSON.parse(localStorage.getItem(VIEW_KEY) ?? '{}')
  } catch {
    return {}
  }
})()
const route = useRoute()
const layers = reactive<Record<LayerKey, boolean>>({ map: true, scan: true, est: true, gt: false, grid: true, ...saved.layers })
const mode = ref<ViewMode>(
  MODES.find((m) => m === route.query.view) ?? (MODES.includes(saved.mode) ? saved.mode : 'free'),
)
const pointSize = ref<number>(typeof saved.pointSize === 'number' ? saved.pointSize : 0.85)
const chosen = ref<string>(typeof saved.preset === 'string' ? saved.preset : '') // 步骤 ① 选中的场景
const chosenSlam = ref<string>(typeof saved.slam === 'string' ? saved.slam : '') // 步骤 ② 选中的 SLAM 算法
const moveMode = ref<MoveMode>(
  MOVES.find((m) => m === route.query.move) ?? (MOVES.includes(saved.move) ? saved.move : 'waypoints'),
)
const viewer = ref<InstanceType<typeof PointCloudView> | null>(null)
watch([layers, mode, pointSize, chosen, chosenSlam, moveMode], () => {
  try {
    localStorage.setItem(
      VIEW_KEY,
      JSON.stringify({
        layers, mode: mode.value, pointSize: pointSize.value, preset: chosen.value, slam: chosenSlam.value,
        move: moveMode.value,
      }),
    )
  } catch {
    // 浏览器禁用了存储，不影响使用
  }
})

const hasData = computed(() => (ms.value?.voxels ?? 0) > 0)
// 点云或里程计有多个发布者：多半是上次没退出的 FAST-LIO 还在跑，两边数据混在一起
const dupTopics = computed(() => {
  const m = ms.value
  if (!m?.publishers) return []
  return (['scan', 'odom'] as const)
    .filter((k) => m.publishers[k] > 1)
    .map((k) => `${m.topics[k]} ×${m.publishers[k]}`)
})
const pose = computed(() => ms.value?.pose ?? null)
// 高度色标的范围，和 PointCloudView 里的着色范围一致（最多 4 m）
const zRange = computed(() => {
  const e = ms.value?.extent
  return e ? [e[2], Math.min(e[5], e[2] + 4)] : null
})

// ---------------- 进程（步骤 ①②③） ----------------

const STATE_TAG: Record<StackState, TagType> = { idle: 'info', running: 'success', stopping: 'warning', exited: 'info' }
const DEFAULT_STACKS = { sim: 'sim', slam: 'mapping_fastlio', waypoints: 'waypoints' }
const names = computed(() => ({ ...DEFAULT_STACKS, ...(ms.value?.stacks ?? {}) }))
const stackOf = (name: string) => store.status?.stacks.find((s) => s.name === name) ?? null
const sim = computed(() => stackOf(names.value.sim))
// SLAM 算法（console.yaml mapping.slam_profiles）：有一个在运行时锁定为它，否则用页面上选中的
const slamOptions = computed(() => (ms.value?.slam_options ?? []).filter((o) => stackOf(o.name)))
const activeSlam = computed(() =>
  slamOptions.value.find((o) => ['running', 'stopping'].includes(stackOf(o.name)?.state ?? '')),
)
const selectedSlam = computed(() => {
  if (activeSlam.value) return activeSlam.value
  return slamOptions.value.find((o) => o.name === chosenSlam.value)
    ?? slamOptions.value.find((o) => o.name === names.value.slam) ?? slamOptions.value[0]
})
const slam = computed(() => stackOf(selectedSlam.value?.name ?? names.value.slam))
const waypoints = computed(() => stackOf(names.value.waypoints))
const running = (s: StackInfo | null) => s?.state === 'running'

const busy = ref<Record<string, boolean>>({})
async function toggle(s: StackInfo | null, preset?: string) {
  if (!s || busy.value[s.name]) return
  // 即使浏览器残留了航点偏好，自定义场景也不能启动自动航点；停止操作不受限制。
  if (!running(s) && s.name === names.value.waypoints && !waypointsAvailable.value) {
    moveMode.value = 'teleop'
    ElMessage.warning(t('error.import_waypoints_unavailable'))
    return
  }
  busy.value[s.name] = true
  try {
    if (running(s)) {
      await api.stopStack(s.name)
    } else {
      await api.startStack(s.name, preset, 'mapping')
      ElMessage.success(t('monitor.started', { name: pick(s.label) }))
    }
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    busy.value[s.name] = false
  }
}

// ---------------- 场景预设（步骤 ①） ----------------
// 内置与导入的场景均从后端加载；上传只登记预设，不启动仿真。

const presets = ref<MappingPreset[]>([])
const importOpen = ref(false)
async function onImported(preset: MappingPreset) {
  await loadPresets()
  // 刷新暂时失败时仍保留接口已经成功返回的预设。
  if (!presets.value.some((p) => p.name === preset.name)) presets.value.push(preset)
  chosen.value = preset.name
  moveMode.value = 'teleop'
}
async function loadPresets() {
  try {
    presets.value = await api.mappingPresets()
  } catch {
    // 后端还没连上，连上后再取
  }
}
onMounted(loadPresets)
// Uploaded scenes can be deleted (moved to the scenes trash on the backend); built-in ones cannot
const deleting = ref('')
async function deleteScene(p: MappingPreset) {
  try {
    await ElMessageBox.confirm(t('mapping.steps.deleteConfirm', { name: pick(p.label) }), t('mapping.steps.deleteTitle'), {
      type: 'warning', confirmButtonText: t('mapping.steps.delete'), cancelButtonText: t('mapping.steps.cancel'),
    })
  } catch {
    return
  }
  deleting.value = p.name
  try {
    await api.deleteScene(p.name)
    presets.value = presets.value.filter((x) => x.name !== p.name)
    if (chosen.value === p.name) chosen.value = ''
    ElMessage.success(t('mapping.steps.deleted', { name: pick(p.label) }))
    await loadPresets()
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    deleting.value = ''
  }
}
watch(
  () => store.connected,
  (c) => {
    if (c && !presets.value.length) loadPresets()
  },
)
// 仿真没停之前不能换场景，显示它实际用的预设
const presetLocked = computed(() => sim.value?.state === 'running' || sim.value?.state === 'stopping')
const selectedPreset = computed(() => {
  if (presetLocked.value && sim.value?.preset) return sim.value.preset
  if (presets.value.some((p) => p.name === chosen.value)) return chosen.value
  return presets.value[0]?.name ?? ''
})
const selectedPresetInfo = computed(() => presets.value.find((p) => p.name === selectedPreset.value))
const waypointsAvailable = computed(() => {
  const p = selectedPresetInfo.value
  return !!p && !p.custom && p.waypoints !== false
})
watch([selectedPresetInfo, moveMode], () => {
  if (selectedPresetInfo.value && !waypointsAvailable.value && moveMode.value === 'waypoints') moveMode.value = 'teleop'
}, { immediate: true })
// 仿真运行中只显示正在用的场景，把操作区的空间留给遥控
const shownPresets = computed(() =>
  presetLocked.value ? presets.value.filter((p) => p.name === selectedPreset.value) : presets.value,
)

// 航点进度（后端从航点进程日志里解析）
const wp = computed(() => ms.value?.waypoints ?? null)
const wpText = computed(() => {
  const w = wp.value
  if (!w || w.state === 'idle' || w.total == null) return ''
  if (w.timeout) return t('mapping.steps.wpTimeout', { done: w.done, total: w.total })
  if (w.state === 'exited' && w.done === w.total) return t('mapping.steps.wpDone', { total: w.total })
  return t('mapping.steps.wpProgress', { done: w.done, total: w.total })
})

/** 按钮是否可点：运行中的总能停；没运行的要满足前置条件 */
function canToggle(s: StackInfo | null, ready: boolean) {
  if (!store.connected || !s || s.state === 'stopping') return false
  return running(s) || ready
}

const slamReady = computed(() => running(slam.value) && !!ms.value?.initialized)
// 当前该做哪一步（高亮）：走了一段（> 5 m）之后提示可以保存
const currentStep = computed(() => {
  if (!running(sim.value)) return 1
  if (!slamReady.value) return 2
  if ((ms.value?.length ?? 0) > 5 || ms.value?.save.state === 'running') return 4
  return 3
})

// ---------------- 保存地图（步骤 ④） ----------------
// 后台依次：FAST-LIO 写点云 → 复制进 ~/maps/<名称>/ → 投影成 2D 栅格 → 写 meta.json

const SAVE_STEPS_ALL = ['stop_slam', 'map_save', 'copy', 'project', 'figures', 'meta'] as const
type SaveStep = (typeof SAVE_STEPS_ALL)[number]
// GLIM 只在退出时写出优化后的地图：保存时第一步是停止它，FAST-LIO 是调用 /map_save
const SAVE_STEPS = computed<SaveStep[]>(() =>
  SAVE_STEPS_ALL.filter((s) => s !== (ms.value?.save_mode === 'dump' ? 'map_save' : 'stop_slam')),
)
const NAME_RE = /^[A-Za-z0-9_-]{1,64}$/
const mapName = ref('')
const job = computed<SaveJob>(() => ms.value?.save ?? { state: 'idle' })
const saving = computed(() => job.value.state === 'running')
// 名称留空时用"场景_月日_时分"
function suggestName() {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  // 日期后缀占 10 位，为最长的自定义场景名预留空间，确保总长度不超过 64。
  return `${(selectedPreset.value || 'map').slice(0, 54)}_${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}`
}
const placeholder = ref(suggestName())
watch(selectedPreset, () => (placeholder.value = suggestName())) // 场景列表加载后、换场景后更新默认名
const nameInvalid = computed(() => mapName.value !== '' && !NAME_RE.test(mapName.value))
// SLAM 在发里程计、地图非空就能保存（不要求是从网页启动的 FAST-LIO；后端会检查 /map_save 服务在不在）
const slamLive = computed(() => (ms.value?.publishers?.odom ?? 0) > 0 && (ms.value?.voxels ?? 0) > 0)
const canSave = computed(() => store.connected && slamLive.value && !saving.value && !nameInvalid.value)
async function saveMap() {
  if (!canSave.value) return
  const name = mapName.value || placeholder.value
  try {
    await api.saveMap(name)
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}
// 保存完成后清空输入框、换一个新的默认名
watch(
  () => job.value.state,
  (s, old) => {
    if (old === 'running' && s === 'done') {
      ElMessage.success(t('mapping.save.savedTo', { dir: job.value.result?.dir ?? '' }))
      mapName.value = ''
      placeholder.value = suggestName()
    }
  },
)
const saveStepState = (s: SaveStep) => {
  const steps = SAVE_STEPS.value
  const cur = steps.indexOf((job.value.step as SaveStep | undefined) ?? steps[0])
  const i = steps.indexOf(s)
  return i < cur ? 'done' : i === cur ? 'current' : 'pending'
}
const previewUrl = computed(() =>
  job.value.state === 'done' && job.value.result?.grid
    ? `/api/maps/${encodeURIComponent(job.value.name ?? '')}/preview.png?t=${job.value.finished ?? 0}`
    : '',
)
// moveMode（遥控 / 航点）定义在上面，和视图设置一起保存

// ---------------- 遥控（步骤 ③） ----------------
// 摇杆或键盘给出方向（-1..1），乘以速度上限得到 {v, w}；按住时每 100 ms 发一次，松开立即发一次零速度。
// 后端 0.5 s 没收到新指令就自动停车，所以网页卡住、断开、切到别的页面时车都会停

const tp = computed(() => ms.value?.teleop ?? null)
const teleopReady = computed(
  () => store.connected && running(sim.value) && !!tp.value && !tp.value.locked && !tp.value.busy,
)
// 第 ③ 步变灰：航点要等建图开始；遥控只要仿真在跑就行（不建图也能先开着车看看）
const moveLocked = computed(() =>
  moveMode.value === 'teleop' ? !running(sim.value) : !slamReady.value && !running(waypoints.value),
)
const teleopNote = computed(() => {
  if (!running(sim.value)) return t('mapping.teleop.needSim')
  if (tp.value?.busy) return t('mapping.teleop.busy')
  return t('mapping.teleop.hint')
})
const vLimit = ref(1.0) // 网页上的速度上限，不能超过后端配置的 max_v、max_w
const wLimit = ref(0.8)
// Recommended band on the speed slider, as left/width percentages of the slider track
const V_MIN = 0.1
const recV = computed(() => tp.value?.recommended_v ?? null)
const recBand = computed(() => {
  const r = recV.value
  const max = tp.value?.max_v ?? 0.6
  if (!r || max <= V_MIN) return null
  const pos = (v: number) => (Math.min(Math.max(v, V_MIN), max) - V_MIN) / (max - V_MIN) * 100
  return { left: `${pos(r[0])}%`, width: `${pos(r[1]) - pos(r[0])}%` }
})
const overSpeed = computed(() => !!recV.value && vLimit.value > recV.value[1] + 1e-6)
watch(tp, (s) => {
  if (s && vLimit.value > s.max_v) vLimit.value = s.max_v
  if (s && wLimit.value > s.max_w) wLimit.value = s.max_w
})

const stick = ref({ x: 0, y: 0 })
const keys = reactive({ fwd: false, back: false, left: false, right: false })
const KEYMAP: Record<string, keyof typeof keys> = {
  KeyW: 'fwd', ArrowUp: 'fwd', KeyS: 'back', ArrowDown: 'back',
  KeyA: 'left', ArrowLeft: 'left', KeyD: 'right', ArrowRight: 'right',
}
// 按着键盘时以键盘为准，否则用摇杆。角速度向左转为正（ROS 约定）
const cmd = computed(() => {
  const useKeys = keys.fwd || keys.back || keys.left || keys.right
  const lin = useKeys ? +keys.fwd - +keys.back : stick.value.y
  const ang = useKeys ? +keys.left - +keys.right : -stick.value.x
  return { v: lin * vLimit.value, w: ang * wLimit.value }
})
function clearInput() {
  stick.value = { x: 0, y: 0 }
  keys.fwd = keys.back = keys.left = keys.right = false
}

let sentMoving = false // 上一次发的是不是非零速度（是的话松开时要补发一次零速度）
function teleopTick() {
  const { v, w } = cmd.value
  if (moveMode.value === 'teleop' && teleopReady.value && (v !== 0 || w !== 0)) {
    store.send({ type: 'teleop', v, w })
    sentMoving = true
  } else if (sentMoving) {
    store.send({ type: 'teleop', v: 0, w: 0 })
    sentMoving = false
  }
}
watch(cmd, teleopTick) // 输入一变就立刻发，不等下一个 100 ms
watch(moveMode, clearInput)

function isTyping(el: EventTarget | null) {
  const e = el as HTMLElement | null
  return !!e && (e.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.tagName))
}
// 键盘：W/S 前进后退，A/D 左右转（方向键同理），空格停车。只在选了"遥控"、且不在输入框里打字时生效
function onKey(ev: KeyboardEvent, down: boolean) {
  if (moveMode.value !== 'teleop' || ev.ctrlKey || ev.altKey || ev.metaKey) return
  if (down && isTyping(ev.target)) return
  if (ev.code === 'Space') {
    if (down) clearInput()
    ev.preventDefault()
    return
  }
  const k = KEYMAP[ev.code]
  if (!k) return
  keys[k] = down
  ev.preventDefault() // 方向键不滚动页面
}
const onKeyDown = (e: KeyboardEvent) => onKey(e, true)
const onKeyUp = (e: KeyboardEvent) => onKey(e, false)
const onHidden = () => {
  if (document.hidden) clearInput()
}
let teleopTimer = 0
onMounted(() => {
  teleopTimer = window.setInterval(teleopTick, 100)
  window.addEventListener('keydown', onKeyDown)
  window.addEventListener('keyup', onKeyUp)
  window.addEventListener('blur', clearInput) // 切到别的窗口时松开的键收不到 keyup
  document.addEventListener('visibilitychange', onHidden)
})
onBeforeUnmount(() => {
  window.clearInterval(teleopTimer)
  window.removeEventListener('keydown', onKeyDown)
  window.removeEventListener('keyup', onKeyUp)
  window.removeEventListener('blur', clearInput)
  document.removeEventListener('visibilitychange', onHidden)
  clearInput()
  teleopTick() // 离开页面时如果正在动，补发零速度
})

async function unlockTeleop() {
  try {
    await api.unlockTeleop()
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}

// ---------------- 指标条 ----------------

function topicHz(name: string | undefined) {
  const tp = store.status?.topics.find((x) => x.name === name)
  return tp && tp.publishers > 0 ? `${tp.hz.toFixed(1)} Hz` : '—'
}

const imuState = computed<[string, TagType]>(() => {
  if (ms.value?.initialized) return [t('mapping.imu.ok'), 'success']
  if (running(slam.value)) return [t('mapping.imu.waiting'), 'warning']
  return [t('mapping.imu.idle'), 'info']
})

const metrics = computed(() => {
  const m = ms.value
  const e = m?.extent
  return [
    { key: 'duration', value: fmtDuration(m?.duration) },
    { key: 'length', value: m ? `${m.length.toFixed(1)} m` : '—' },
    { key: 'voxels', value: m ? fmtCount(m.voxels) : '—' },
    { key: 'extent', value: e ? `${(e[3] - e[0]).toFixed(1)}×${(e[4] - e[1]).toFixed(1)} m` : '—' },
    { key: 'scanHz', value: topicHz(m?.topics.scan ?? '/cloud_registered') },
    { key: 'odomHz', value: topicHz(m?.topics.odom ?? '/Odometry') },
    { key: 'error', value: m?.error != null ? `${(m.error * 100).toFixed(1)} cm` : '—' },
  ]
})
// Hover text for metrics that need explaining; the others are self-explanatory.
function metricTip(key: string): string | null {
  if (key === 'error') return t('mapping.metrics.errorHint')
  return ['voxels', 'scanHz', 'odomHz'].includes(key) ? t(`mapping.tips.${key}`) : null
}
</script>

<template>
  <div class="mapping">
    <div class="main">
      <!-- 3D 视图 -->
      <section class="viewport glass">
        <PointCloudView ref="viewer" :stream="stream" :layers="layers" :mode="mode" :point-size="pointSize" />

        <div class="hud tl">
          <button
            v-for="k in LAYERS" :key="k" class="layer" :class="[k, { on: layers[k] }]"
            :aria-pressed="layers[k]" @click="layers[k] = !layers[k]"
          >
            <i class="swatch" />{{ t(`mapping.layers.${k}`) }}
          </button>
        </div>

        <div class="hud tr">
          <div class="seg">
            <button v-for="m in MODES" :key="m" :class="{ on: mode === m }" @click="mode = m">
              {{ t(`mapping.views.${m}`) }}
            </button>
          </div>
          <button class="icon-btn" :title="t('mapping.views.reset')" @click="viewer?.resetView()">
            <el-icon :size="16"><Aim /></el-icon>
          </button>
        </div>

        <div v-if="dupTopics.length" class="alert" role="alert">
          <el-icon :size="18"><WarningFilled /></el-icon>
          <div>
            <b>{{ t('mapping.dup.title', { topics: dupTopics.join(t('estop.listSep')) }) }}</b>
            <p>{{ t('mapping.dup.hint') }}</p>
          </div>
        </div>

        <div v-if="pose" class="hud bl mono">
          <span><i>x</i>{{ pose.x.toFixed(2) }}</span>
          <span><i>y</i>{{ pose.y.toFixed(2) }}</span>
          <span><i>θ</i>{{ ((pose.yaw * 180) / Math.PI).toFixed(0) }}°</span>
          <span><i>v</i>{{ (ms?.speed ?? 0).toFixed(2) }} m/s</span>
        </div>

        <div class="hud br">
          <div v-if="zRange" class="legend">
            <span class="k">{{ t('mapping.hud.height') }}</span>
            <span class="mono">{{ zRange[0].toFixed(1) }}</span>
            <i class="ramp" />
            <span class="mono">{{ zRange[1].toFixed(1) }} m</span>
          </div>
          <div class="size">
            <span class="k">{{ t('mapping.hud.pointSize') }}</span>
            <el-slider v-model="pointSize" :min="0.5" :max="3" :step="0.25" :show-tooltip="false" size="small" />
          </div>
        </div>

        <div v-if="!hasData" class="wait">
          <RadarMark :size="112" />
          <div class="wait-title">
            {{ store.connected ? t('mapping.waiting', { topic: ms?.topics.scan ?? '/cloud_registered' }) : t('mapping.offline') }}
          </div>
          <div class="wait-hint">{{ t('mapping.waitingHint') }}</div>
        </div>
      </section>

      <!-- 指标条 -->
      <div class="metrics glass">
        <div v-for="m in metrics" :key="m.key" class="metric">
          <span class="k">{{ t(`mapping.metrics.${m.key}`) }}<InfoTip v-if="metricTip(m.key)" :content="metricTip(m.key)!" /></span>
          <span class="v mono">{{ m.value }}</span>
        </div>
        <div class="metric">
          <span class="k">{{ t('mapping.metrics.imu') }}<InfoTip :content="t('mapping.tips.imu')" /></span>
          <span class="chip" :class="[imuState[1], { live: imuState[1] === 'warning' }]">{{ imuState[0] }}</span>
        </div>
      </div>
    </div>

    <!-- 操作区 -->
    <aside class="ops glass">
      <!-- ① 仿真 -->
      <section class="step" :class="{ current: currentStep === 1 }">
        <header class="step-head">
          <span class="no mono">1</span>
          <span class="title">{{ t('mapping.steps.sim') }}</span>
          <span v-if="sim" class="chip" :class="[STATE_TAG[sim.state], { live: running(sim) }]">
            {{ t(`monitor.state.${sim.state}`) }}
          </span>
        </header>
        <div class="presets" role="radiogroup" :aria-label="t('mapping.steps.scene')">
          <div v-for="p in shownPresets" :key="p.name" class="preset-row">
            <button
              class="preset" :class="{ on: selectedPreset === p.name, deletable: p.custom && !presetLocked }"
              role="radio" :aria-checked="selectedPreset === p.name" :disabled="presetLocked && selectedPreset !== p.name"
              @click="chosen = p.name"
            >
              <span class="radio" />
              <span class="preset-text">
                <span class="preset-name">{{ pick(p.label) }}</span>
                <span class="preset-desc">{{ pick(p.description) }}</span>
              </span>
            </button>
            <el-button
              v-if="p.custom && !presetLocked" class="preset-del" circle text size="small"
              :title="t('mapping.steps.delete')" :aria-label="`${t('mapping.steps.delete')} ${pick(p.label)}`"
              :loading="deleting === p.name" :disabled="!store.connected || !!deleting" @click="deleteScene(p)"
            >
              <el-icon v-if="deleting !== p.name"><Delete /></el-icon>
            </el-button>
          </div>
          <button
            v-if="!presetLocked" type="button" class="preset upload" :disabled="!store.connected || (!!sim && busy[sim.name])"
            @click="importOpen = true"
          >
            <el-icon class="upload-icon" :size="15"><Upload /></el-icon>
            <span class="preset-text">
              <span class="preset-name">{{ t('mapping.steps.upload') }}</span>
              <span class="preset-desc">{{ t('mapping.steps.uploadDesc') }}</span>
            </span>
          </button>
        </div>
        <p v-if="presetLocked" class="note">{{ t('mapping.steps.sceneLocked') }}</p>
        <el-button
          class="wide" :type="running(sim) ? 'default' : 'primary'" :loading="!!sim && busy[sim.name]"
          :disabled="!canToggle(sim, true)" @click="toggle(sim, selectedPreset || undefined)"
        >{{ running(sim) ? t('mapping.steps.stop') : t('mapping.steps.start') }}</el-button>
      </section>

      <!-- ② SLAM -->
      <section class="step" :class="{ current: currentStep === 2, locked: !running(sim) && !running(slam) }">
        <header class="step-head">
          <span class="no mono">2</span>
          <span class="title">{{ t('mapping.steps.slam') }}</span>
          <span v-if="slam" class="chip" :class="[STATE_TAG[slam.state], { live: running(slam) }]">
            {{ t(`monitor.state.${slam.state}`) }}
          </span>
        </header>
        <div class="field stacked">
          <span class="k">{{ t('mapping.steps.algorithm') }}<InfoTip :content="t('mapping.tips.algorithm')" /></span>
          <div class="algos" role="radiogroup" :aria-label="t('mapping.steps.algorithm')">
            <button
              v-for="o in slamOptions" :key="o.name" type="button" class="algo"
              :class="{ on: selectedSlam?.name === o.name }" role="radio" :aria-checked="selectedSlam?.name === o.name"
              :disabled="!!activeSlam && activeSlam.name !== o.name" @click="chosenSlam = o.name"
            >{{ o.algorithm }}</button>
          </div>
        </div>
        <p v-if="selectedSlam?.save_mode === 'dump'" class="note">{{ t('mapping.steps.dumpNote') }}</p>
        <p v-if="!running(sim) && !running(slam)" class="note">{{ t('mapping.steps.needSim') }}</p>
        <el-button
          class="wide" :type="running(slam) ? 'default' : 'primary'" :loading="!!slam && busy[slam.name]"
          :disabled="!canToggle(slam, running(sim))" @click="toggle(slam)"
        >{{ running(slam) ? t('mapping.steps.stop') : t('mapping.steps.start') }}</el-button>
      </section>

      <!-- ③ 移动 -->
      <section class="step" :class="{ current: currentStep === 3, locked: moveLocked }">
        <header class="step-head">
          <span class="no mono">3</span>
          <span class="title">{{ t('mapping.steps.move') }}</span>
          <span v-if="waypoints && moveMode === 'waypoints'" class="chip" :class="[STATE_TAG[waypoints.state], { live: running(waypoints) }]">
            {{ t(`monitor.state.${waypoints.state}`) }}
          </span>
        </header>
        <div class="seg full">
          <button :class="{ on: moveMode === 'teleop' }" @click="moveMode = 'teleop'">{{ t('mapping.steps.teleop') }}</button>
          <button :class="{ on: moveMode === 'waypoints' }" :disabled="!waypointsAvailable" @click="moveMode = 'waypoints'">{{ t('mapping.steps.waypoints') }}</button>
        </div>
        <p v-if="selectedPresetInfo && !waypointsAvailable" class="note">{{ t('error.import_waypoints_unavailable') }}</p>
        <template v-if="moveMode === 'waypoints'">
          <p class="note">{{ slamReady || running(waypoints) ? t('mapping.steps.waypointsHint') : t('mapping.steps.needSlam') }}</p>
          <div v-if="wpText" class="wp">
            <div class="wp-text"><span>{{ wpText }}</span></div>
            <div class="wp-track" :class="{ bad: wp?.timeout }">
              <i :style="{ width: `${wp?.total ? (100 * wp.done) / wp.total : 0}%` }" />
            </div>
          </div>
          <el-button
            class="wide" :type="running(waypoints) ? 'default' : 'primary'" :loading="!!waypoints && busy[waypoints.name]"
            :disabled="!canToggle(waypoints, slamReady && waypointsAvailable)" @click="toggle(waypoints)"
          >{{ running(waypoints) ? t('mapping.steps.stop') : t('mapping.steps.start') }}</el-button>
        </template>
        <template v-else>
          <div v-if="tp?.locked" class="lockbar">
            <span>{{ t('mapping.teleop.locked') }}</span>
            <el-button size="small" type="warning" plain @click="unlockTeleop">{{ t('mapping.teleop.unlock') }}</el-button>
          </div>
          <p v-else class="note">{{ teleopNote }}</p>
          <div class="teleop">
            <TeleopPad v-model="stick" :size="132" :disabled="!teleopReady" />
            <div class="readout">
              <div class="mono"><span class="k">v</span>{{ cmd.v.toFixed(2) }}<small>m/s</small></div>
              <div class="mono"><span class="k">ω</span>{{ cmd.w.toFixed(2) }}<small>rad/s</small></div>
              <span class="chip" :class="tp?.active ? 'success live' : 'info'">
                {{ tp?.active ? t('mapping.teleop.moving') : t('mapping.teleop.idle') }}
              </span>
            </div>
          </div>
          <div class="limit">
            <span class="k">{{ t('mapping.teleop.speed') }}<InfoTip :content="t('mapping.tips.speed')" /></span>
            <div class="slider-wrap">
              <!-- Recommended band drawn under the slider track -->
              <i v-if="recBand" class="rec-band" :style="recBand" :title="t('mapping.teleop.recommended', { lo: recV![0], hi: recV![1] })" />
              <el-slider v-model="vLimit" :min="V_MIN" :max="tp?.max_v ?? 0.6" :step="0.05" :show-tooltip="false" size="small" />
            </div>
            <span class="mono" :class="{ over: overSpeed }">{{ vLimit.toFixed(2) }}</span>
          </div>
          <div v-if="recV" class="rec-note" :class="{ over: overSpeed }">
            <i class="rec-swatch" />
            {{ overSpeed ? t('mapping.teleop.overSpeed', { hi: recV[1] }) : t('mapping.teleop.recommended', { lo: recV[0], hi: recV[1] }) }}
          </div>
          <div class="limit">
            <span class="k">{{ t('mapping.teleop.turn') }}<InfoTip :content="t('mapping.tips.turn')" /></span>
            <el-slider v-model="wLimit" :min="0.1" :max="tp?.max_w ?? 1.2" :step="0.05" :show-tooltip="false" size="small" />
            <span class="mono">{{ wLimit.toFixed(2) }}</span>
          </div>
          <div class="keys">
            <kbd>W</kbd><kbd>A</kbd><kbd>S</kbd><kbd>D</kbd><span class="slash">/</span><kbd>↑↓←→</kbd>
            <span>{{ t('mapping.teleop.keys') }}</span>
          </div>
        </template>
      </section>

      <!-- ④ 保存 -->
      <section class="step" :class="{ current: currentStep === 4, locked: !slamLive && !saving }">
        <header class="step-head">
          <span class="no mono">4</span>
          <span class="title">{{ t('mapping.steps.save') }}</span>
          <span v-if="job.state === 'running'" class="chip warning live">{{ t('mapping.save.saving') }}</span>
          <span v-else-if="job.state === 'done'" class="chip success">{{ t('mapping.save.saved') }}</span>
          <span v-else-if="job.state === 'failed'" class="chip danger">{{ t('mapping.save.failedShort') }}</span>
        </header>
        <el-input
          v-model.trim="mapName" class="name-input" :placeholder="placeholder" :disabled="saving" maxlength="64"
          :aria-label="t('mapping.save.name')" @keydown.enter="saveMap"
        >
          <template #prefix><span class="name-prefix mono">{{ store.paths.maps }}/</span></template>
        </el-input>
        <p class="note" :class="{ bad: nameInvalid }">
          {{ nameInvalid ? t('mapping.save.nameRule') : slamLive ? t('mapping.save.hint') : t('mapping.save.needSlam') }}
        </p>
        <p v-if="ms?.save_mode === 'dump' && slamLive && !saving" class="note">{{ t('mapping.save.dumpHint') }}</p>
        <el-button class="wide" type="primary" :loading="saving" :disabled="!canSave" @click="saveMap">
          {{ t('mapping.steps.save') }}
        </el-button>

        <!-- 进度 -->
        <ol v-if="saving" class="save-steps">
          <li v-for="s in SAVE_STEPS" :key="s" :class="saveStepState(s)">
            <i class="dot" />{{ t(`mapping.save.steps.${s}`) }}
          </li>
        </ol>

        <!-- 结果 -->
        <div v-if="job.state === 'done' && job.result" class="saved">
          <img v-if="previewUrl" :src="previewUrl" :alt="t('mapping.save.preview')" class="preview" />
          <div class="saved-text">
            <b class="mono">{{ job.name }}</b>
            <span>{{ t('mapping.save.points', { points: fmtCount(job.result.points ?? 0) }) }}</span>
            <span v-if="job.result.grid">
              {{ t('mapping.save.grid', { w: job.result.grid.size_m[0], h: job.result.grid.size_m[1] }) }}
            </span>
            <span v-if="job.result.figures_error" class="bad">{{ t('mapping.save.figuresFailed', { reason: job.result.figures_error }) }}</span>
            <span v-if="job.result.project_error" class="bad">
              {{ t('mapping.save.projectFailed', { reason: job.result.project_error }) }}
            </span>
          </div>
        </div>
        <p v-if="job.state === 'failed'" class="note bad">
          {{ t('mapping.save.failed', { reason: resultText({ code: job.code ?? 'save_error', params: job.params ?? {} }) }) }}
        </p>
        <p v-if="ms?.run" class="note run">{{ t('mapping.save.run', { run: ms.run, runs: store.paths.runs }) }}</p>
      </section>
    </aside>
    <MappingImportDialog
      v-model="importOpen" :disabled="presetLocked || !store.connected || (!!sim && !!busy[sim.name])"
      @imported="onImported"
    />
  </div>
</template>

<style scoped>
.mapping {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 14px;
  height: 100%;
  min-height: 520px;
}
.main {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  min-height: 0;
}

/* ---------------- 3D 视图 ---------------- */
.viewport {
  position: relative;
  flex: 1;
  min-height: 0;
  border-radius: var(--radius);
  overflow: hidden;
  background:
    radial-gradient(ellipse at 50% 35%, rgba(255, 255, 255, 0.92), rgba(255, 255, 255, 0.55) 70%),
    var(--glass);
}
/* HUD 角标 */
.viewport::before,
.viewport::after {
  content: '';
  position: absolute;
  z-index: 2;
  width: 18px;
  height: 18px;
  pointer-events: none;
  border: 0 solid var(--accent);
  opacity: 0.6;
}
.viewport::before {
  top: 8px;
  left: 8px;
  border-width: 1.5px 0 0 1.5px;
  border-top-left-radius: 6px;
}
.viewport::after {
  right: 8px;
  bottom: 8px;
  border-width: 0 1.5px 1.5px 0;
  border-bottom-right-radius: 6px;
}

.hud {
  position: absolute;
  z-index: 3;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
}
.tl {
  top: 14px;
  left: 16px;
  flex-wrap: wrap;
  max-width: 60%;
}
.tr {
  top: 14px;
  right: 16px;
}
.bl {
  left: 16px;
  bottom: 14px;
  gap: 14px;
  padding: 6px 12px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(12px);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.bl i {
  margin-right: 5px;
  font-style: normal;
  color: var(--accent);
}
.br {
  right: 16px;
  bottom: 14px;
  gap: 12px;
  padding: 4px 12px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(12px);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.k {
  color: var(--ink-3);
  font-size: 12px;
}

/* 图层开关 */
.layer {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 11px;
  border: none;
  border-radius: 999px;
  font: inherit;
  font-size: 12.5px;
  color: var(--ink-3);
  background: rgba(255, 255, 255, 0.5);
  backdrop-filter: blur(12px);
  box-shadow: inset 0 0 0 1px var(--hairline);
  cursor: pointer;
  transition: all 0.2s;
}
.layer.on {
  color: var(--ink);
  background: rgba(255, 255, 255, 0.9);
  box-shadow: inset 0 0 0 1px rgba(8, 145, 178, 0.25), 0 4px 12px -6px rgba(8, 145, 178, 0.45);
}
.swatch {
  width: 10px;
  height: 10px;
  border-radius: 3px;
  opacity: 0.35;
  transition: opacity 0.2s;
}
.layer.on .swatch {
  opacity: 1;
}
.layer.map .swatch { background: linear-gradient(135deg, #99dbed, #0aa3cc, #2563eb, #5440d9); }
.layer.scan .swatch { background: #00b4d8; border-radius: 50%; }
.layer.est .swatch { height: 3px; background: #2563eb; }
.layer.gt .swatch { height: 0; border-top: 2px dashed #8a97ad; border-radius: 0; }
.layer.grid .swatch { background: repeating-linear-gradient(90deg, #9db4cc 0 1px, transparent 1px 4px), repeating-linear-gradient(0deg, #9db4cc 0 1px, transparent 1px 4px); }

/* 分段按钮（相机模式、移动方式） */
.seg {
  display: inline-flex;
  padding: 3px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.55);
  backdrop-filter: blur(12px);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.seg button {
  height: 26px;
  padding: 0 12px;
  border: none;
  border-radius: 7px;
  background: none;
  font: inherit;
  font-size: 12.5px;
  color: var(--ink-3);
  cursor: pointer;
  transition: all 0.2s;
}
.seg button.on {
  color: var(--accent);
  font-weight: 600;
  background: #fff;
  box-shadow: 0 2px 8px -3px rgba(20, 60, 120, 0.3);
}
.seg.full {
  display: flex;
  margin-bottom: 10px;
}
.seg.full button {
  flex: 1;
}
.icon-btn {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 10px;
  color: var(--ink-2);
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(12px);
  box-shadow: inset 0 0 0 1px var(--hairline);
  cursor: pointer;
}
.icon-btn:hover {
  color: var(--accent);
}
.layer:focus-visible,
.seg button:focus-visible,
.icon-btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.legend {
  display: flex;
  align-items: center;
  gap: 6px;
}
.ramp {
  width: 72px;
  height: 6px;
  border-radius: 3px;
  background: linear-gradient(90deg, #99dbed, #05a3cc, #2663eb, #5440d9);
}
.size {
  display: flex;
  align-items: center;
  gap: 8px;
}
.size .el-slider {
  width: 84px;
}

/* 多个 SLAM 同时发布时的警告 */
.alert {
  position: absolute;
  z-index: 4;
  top: 56px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  gap: 10px;
  width: min(560px, calc(100% - 32px));
  box-sizing: border-box;
  padding: 12px 16px;
  border-radius: 12px;
  color: #92400e;
  background: rgba(255, 251, 235, 0.92);
  backdrop-filter: blur(12px);
  box-shadow: inset 0 0 0 1px rgba(217, 119, 6, 0.35), 0 10px 24px -12px rgba(217, 119, 6, 0.6);
  font-size: 13px;
}
.alert .el-icon {
  flex: none;
  margin-top: 1px;
  color: var(--warn);
}
.alert p {
  margin: 4px 0 0;
  line-height: 1.55;
  color: #a16207;
}

/* 没有数据时的等待提示 */
.wait {
  position: absolute;
  inset: 0;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  pointer-events: none;
  text-align: center;
}
.wait-title {
  margin-top: 8px;
  font-size: 15px;
  font-weight: 600;
}
.wait-hint {
  font-size: 13px;
  color: var(--ink-3);
}

/* ---------------- 指标条 ---------------- */
.metrics {
  display: grid;
  grid-template-columns: repeat(8, minmax(0, 1fr));
  border-radius: var(--radius);
  padding: 10px 6px;
}
.metric {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 2px 12px;
  min-width: 0;
  border-left: 1px solid var(--hairline);
}
.metric:first-child {
  border-left: none;
}
.metric .v {
  font-size: 15px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.metric .chip {
  align-self: flex-start;
}

/* ---------------- 操作区 ---------------- */
.ops {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border-radius: var(--radius);
  overflow: auto;
}
.step {
  position: relative;
  padding: 12px 14px 14px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.45);
  box-shadow: inset 0 0 0 1px var(--hairline);
  transition: background 0.25s, box-shadow 0.25s;
}
.step.current {
  background: rgba(255, 255, 255, 0.92);
  box-shadow: inset 0 0 0 1px rgba(8, 145, 178, 0.3), 0 8px 22px -14px rgba(8, 145, 178, 0.8);
}
.step.current::before {
  content: '';
  position: absolute;
  left: -1px;
  top: 14px;
  width: 3px;
  height: 18px;
  border-radius: 0 3px 3px 0;
  background: var(--accent-grad);
  box-shadow: 0 0 8px rgba(6, 182, 212, 0.8);
}
.step.locked .step-head .title,
.step.locked .field {
  opacity: 0.6;
}
.step-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.no {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 700;
  color: #fff;
  background: var(--accent-grad);
}
.step.locked .no {
  background: var(--idle);
}
.title {
  font-weight: 600;
  font-size: 14px;
}
.step-head .chip {
  margin-left: auto;
}
.field {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
  font-size: 13px;
}
.algos {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
/* 标签在上、内容在下（英文较长时不会挤成两行） */
.field.stacked {
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}
.algo {
  padding: 3px 9px;
  border: 0;
  border-radius: 7px;
  font: inherit;
  font-size: 12px;
  white-space: nowrap;
  color: var(--ink-2);
  background: var(--hairline);
  cursor: pointer;
}
.algo:disabled {
  color: var(--ink-3);
  cursor: not-allowed;
}
.algo:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
.algo.on {
  color: var(--accent);
  font-weight: 600;
  background: rgba(8, 145, 178, 0.1);
  box-shadow: inset 0 0 0 1px rgba(8, 145, 178, 0.25);
}
.note {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-3);
}
.note.soon {
  margin: 0;
}
.wide {
  width: 100%;
}

/* 场景预设：单选卡片 */
.presets {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 10px;
}
.preset {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  box-sizing: border-box;
  width: 100%;
  padding: 9px 10px;
  border: none;
  border-radius: 10px;
  font: inherit;
  text-align: left;
  color: inherit;
  background: rgba(255, 255, 255, 0.55);
  box-shadow: inset 0 0 0 1px var(--hairline);
  cursor: pointer;
  transition: background 0.2s, box-shadow 0.2s;
}
.preset:hover:not(:disabled):not(.upload) {
  background: rgba(255, 255, 255, 0.9);
}
.preset.on {
  background: #fff;
  box-shadow: inset 0 0 0 1.5px rgba(8, 145, 178, 0.55), 0 6px 16px -10px rgba(8, 145, 178, 0.7);
}
.preset:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.preset:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
.radio {
  flex: none;
  width: 14px;
  height: 14px;
  margin-top: 2px;
  border-radius: 50%;
  box-shadow: inset 0 0 0 1.5px var(--idle);
  transition: box-shadow 0.2s;
}
.preset.on .radio {
  box-shadow: inset 0 0 0 4px var(--accent);
}
.preset-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.preset-name {
  font-size: 13px;
  font-weight: 600;
}
/* Uploaded scenes: a delete button in the card's top-right corner (outside the card button, no nesting) */
.preset-row {
  position: relative;
}
.preset.deletable {
  padding-right: 34px;
}
.preset-del {
  position: absolute;
  top: 5px;
  right: 5px;
  color: var(--ink-3);
}
.preset-del:hover:not(.is-disabled) {
  color: var(--bad);
}
.preset-desc {
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--ink-3);
}
.preset.upload {
  cursor: pointer;
  background: transparent;
  box-shadow: none;
  border: 1px dashed rgba(122, 135, 157, 0.5);
}
.preset.upload .preset-name {
  color: var(--ink-2);
}
.upload-icon {
  flex: none;
  margin-top: 1px;
  color: var(--ink-3);
}
.preset.upload:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.7);
}
.seg button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

/* 航点进度 */
.wp {
  margin-bottom: 10px;
}
.wp-text {
  font-size: 12.5px;
  margin-bottom: 6px;
}
.wp-track {
  height: 4px;
  border-radius: 4px;
  background: var(--hairline);
  overflow: hidden;
}
.wp-track i {
  display: block;
  height: 100%;
  border-radius: 4px;
  background: var(--accent-grad);
  box-shadow: 0 0 6px rgba(6, 182, 212, 0.7);
  transition: width 0.5s ease;
}
.wp-track.bad i {
  background: var(--warn);
  box-shadow: none;
}

/* 保存地图 */
.name-input {
  margin-bottom: 6px;
}
.name-prefix {
  font-size: 11.5px;
  color: var(--ink-3);
}
.note.bad,
.bad {
  color: var(--bad);
}
.note.run {
  margin: 10px 0 0;
  word-break: break-all;
}
.save-steps {
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
  font-size: 12.5px;
}
.save-steps li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
  color: var(--ink-3);
}
.save-steps .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  box-shadow: inset 0 0 0 1.5px var(--idle);
}
.save-steps li.done {
  color: var(--ink-2);
}
.save-steps li.done .dot {
  box-shadow: none;
  background: var(--ok);
}
.save-steps li.current {
  color: var(--ink);
  font-weight: 600;
}
.save-steps li.current .dot {
  box-shadow: none;
  background: var(--accent);
  --chip: var(--accent);
  animation: pulse 1.4s ease-out infinite;
}
.saved {
  display: flex;
  gap: 10px;
  margin-top: 10px;
  padding: 8px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.7);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.preview {
  flex: none;
  width: 92px;
  height: 92px;
  object-fit: contain;
  border-radius: 6px;
  background: #fff;
  image-rendering: pixelated;
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.saved-text {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  font-size: 12px;
  color: var(--ink-2);
  word-break: break-all;
}
.saved-text b {
  font-size: 13px;
  color: var(--ink);
}

/* 遥控 */
.lockbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
  padding: 8px 10px;
  border-radius: 10px;
  font-size: 12.5px;
  color: #92400e;
  background: rgba(254, 243, 199, 0.7);
  box-shadow: inset 0 0 0 1px rgba(217, 119, 6, 0.3);
}
.teleop {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 8px;
}
.readout {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.readout .mono {
  font-size: 17px;
  font-weight: 600;
  white-space: nowrap;
}
.readout .k {
  display: inline-block;
  width: 16px;
  font-size: 13px;
  color: var(--accent);
}
.readout small {
  margin-left: 4px;
  font-size: 11px;
  font-weight: 400;
  color: var(--ink-3);
}
.readout .chip {
  align-self: flex-start;
}
.limit {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) 34px;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}
.limit .mono {
  text-align: right;
  font-size: 12px;
}
.limit .mono.over,
.rec-note.over {
  color: var(--warn);
}
/* Recommended speed band: a translucent green bar over the slider track (does not take clicks) */
.slider-wrap {
  position: relative;
  min-width: 0;
}
.rec-band {
  position: absolute;
  top: 50%;
  z-index: 1;
  height: 12px;
  border-radius: 4px;
  transform: translateY(-50%);
  background: rgba(5, 150, 105, 0.18);
  box-shadow: inset 0 0 0 1px rgba(5, 150, 105, 0.45);
  pointer-events: none;
}
.rec-note {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 2px;
  font-size: 11.5px;
  color: var(--ink-3);
}
.rec-swatch {
  width: 14px;
  height: 8px;
  border-radius: 3px;
  background: rgba(5, 150, 105, 0.18);
  box-shadow: inset 0 0 0 1px rgba(5, 150, 105, 0.45);
}
.keys {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
  font-size: 11.5px;
  color: var(--ink-3);
}
kbd {
  min-width: 18px;
  padding: 1px 5px;
  border-radius: 5px;
  font-family: var(--font-mono);
  font-size: 11px;
  text-align: center;
  color: var(--ink-2);
  background: #fff;
  box-shadow: inset 0 -1.5px 0 rgba(15, 30, 60, 0.12), inset 0 0 0 1px var(--hairline);
}
.slash {
  margin: 0 2px;
}

@media (max-width: 1500px) {
  .metrics {
    grid-template-columns: repeat(4, minmax(0, 1fr));
    row-gap: 10px;
  }
  .metric:nth-child(5) {
    border-left: none;
  }
}
</style>
