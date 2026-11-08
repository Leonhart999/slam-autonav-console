<!-- 建图页（仿真）：中间 3D 视图和指标条（MappingStage），右侧按步骤操作。
     ①选场景预设并启动仿真，②开始建图，③自动航点（显示进度）或遥控（摇杆、键盘），④保存到地图库（MapSaveStep）。
     录好的数据包在「数据回放」页（PlaybackView）建图 -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { pick } from '@/i18n'
import { errorText } from '@/messages'
import { useStatusStore } from '@/stores/status'
import { createMappingStream } from '@/mapping/stream'
import { loadPrefs, savePrefs } from '@/mapping/prefs'
import type { MappingPreset, StackInfo, StackState } from '@/types'
import TeleopPad from '@/components/TeleopPad.vue'
import MappingImportDialog from '@/components/MappingImportDialog.vue'
import InfoTip from '@/components/InfoTip.vue'
import MappingStage from '@/components/MappingStage.vue'
import MapSaveStep from '@/components/MapSaveStep.vue'

type TagType = 'success' | 'warning' | 'danger' | 'info' | 'primary'

const { t } = useI18n()
const store = useStatusStore()
const stream = createMappingStream(store)
const ms = stream.status
onMounted(stream.start)
onBeforeUnmount(stream.stop)

// ---------------- 页面设置 ----------------
// 场景、算法、移动方式保存在浏览器里（mapping/prefs.ts），下次打开沿用；地址里的 ?move=teleop 可以直接指定移动方式

const MOVES = ['teleop', 'waypoints'] as const
type MoveMode = (typeof MOVES)[number]
const saved = loadPrefs()
const route = useRoute()
const chosen = ref<string>(typeof saved.preset === 'string' ? saved.preset : '') // 步骤 ① 选中的场景
const chosenSlam = ref<string>(typeof saved.slam === 'string' ? saved.slam : '') // 步骤 ② 选中的 SLAM 算法
const moveMode = ref<MoveMode>(
  MOVES.find((m) => m === route.query.move) ?? (MOVES.includes(saved.move) ? saved.move : 'waypoints'),
)
watch([chosen, chosenSlam, moveMode], () =>
  savePrefs({ preset: chosen.value, slam: chosenSlam.value, move: moveMode.value }),
)

// ---------------- 进程（步骤 ①②③） ----------------

const STATE_TAG: Record<StackState, TagType> = { idle: 'info', running: 'success', stopping: 'warning', exited: 'info' }
const DEFAULT_STACKS = { sim: 'sim', slam: 'mapping_fastlio', waypoints: 'waypoints' }
const names = computed(() => ({ ...DEFAULT_STACKS, ...(ms.value?.stacks ?? {}) }))
const stackOf = (name: string) => store.status?.stacks.find((s) => s.name === name) ?? null
const sim = computed(() => stackOf(names.value.sim))
// SLAM 算法（console.yaml mapping.slam_profiles）：有一个在运行时锁定为它，否则用页面上选中的。
// 只列出能用于仿真的（sources 含 sim）；只用于数据包的算法（GLIM 仅激光）在数据回放页
const allSlamOptions = computed(() => (ms.value?.slam_options ?? []).filter((o) => stackOf(o.name)))
const slamOptions = computed(() => allSlamOptions.value.filter((o) => (o.sources ?? ['sim', 'bag']).includes('sim')))
const activeSlam = computed(() =>
  allSlamOptions.value.find((o) => ['running', 'stopping'].includes(stackOf(o.name)?.state ?? '')),
)
const selectedSlam = computed(() => {
  if (activeSlam.value) return activeSlam.value
  const opts = slamOptions.value
  return opts.find((o) => o.name === chosenSlam.value) ?? opts.find((o) => o.name === names.value.slam) ?? opts[0]
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

// ---------------- 数据回放页占用中 ----------------
// 回放和仿真都发布 /clock 和 SLAM 的输入，不能同时运行；从数据回放页启动的 SLAM 也不在这里控制
const pb = computed(() => ms.value?.playback ?? null)
const pbActive = computed(() => pb.value?.state === 'running' || pb.value?.state === 'stopping')
const playbackBusy = computed(() => pbActive.value || (!!slam.value && running(slam.value) && slam.value.origin === 'playback'))

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

// ---------------- 指标条的 IMU 状态 ----------------

const imuState = computed<[string, TagType]>(() => {
  if (selectedSlam.value?.needs_imu === false) return [t('mapping.imu.unused'), 'info']
  if (ms.value?.initialized) return [t('mapping.imu.ok'), 'success']
  if (running(slam.value)) return [t('mapping.imu.waiting'), 'warning']
  return [t('mapping.imu.idle'), 'info']
})

</script>

<template>
  <div class="mapping">
    <MappingStage :stream="stream" :imu-state="imuState" :waiting-hint="t('mapping.waitingHint')" />

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
        <p v-if="playbackBusy" class="note bad">
          {{ t('mapping.steps.playbackBusy') }} <router-link to="/playback">{{ t('mapping.steps.toPlayback') }}</router-link>
        </p>
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
          :disabled="!canToggle(sim, !playbackBusy)" @click="toggle(sim, selectedPreset || undefined)"
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
          :disabled="!canToggle(slam, running(sim) && !playbackBusy)" @click="toggle(slam)"
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
      <MapSaveStep :ms="ms" :current="currentStep === 4" :name-base="selectedPreset || 'map'" />
    </aside>
    <MappingImportDialog
      v-model="importOpen" :disabled="presetLocked || !store.connected || (!!sim && !!busy[sim.name])"
      @imported="onImported"
    />
  </div>
</template>

<style scoped src="../mapping/steps.css"></style>

<style scoped>
.mapping {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 14px;
  height: 100%;
  min-height: 520px;
}
.ops {
  min-height: 0;
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
</style>
