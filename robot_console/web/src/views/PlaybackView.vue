<!-- 数据回放页：把录好的 rosbag2 数据包放进 SLAM 建图。中间 3D 视图和指标条（MappingStage，和建图页同一份数据），
     右侧按步骤操作：①选数据包，②启动 SLAM，③选片段、回放（倍速、暂停），④保存地图（MapSaveStep）。
     后端在 robot_console/playback.py；回放和仿真不能同时运行（两边都发 /clock 和 SLAM 的输入）。 -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { evalApi } from '@/evaluation/api'
import { pick } from '@/i18n'
import { errorText } from '@/messages'
import { fmtBytes } from '@/format'
import { useStatusStore } from '@/stores/status'
import { createMappingStream, type BagInfo, type PlaybackStorage, type SlamOption } from '@/mapping/stream'
import { loadPrefs, savePrefs } from '@/mapping/prefs'
import { baseOf, savedOptions, useSlamChoice } from '@/mapping/slamChoice'
import type { StackInfo, StackState } from '@/types'
import InfoTip from '@/components/InfoTip.vue'
import MappingStage from '@/components/MappingStage.vue'
import MapSaveStep from '@/components/MapSaveStep.vue'
import BagPicker from '@/components/BagPicker.vue'
import PlaybackControls from '@/components/PlaybackControls.vue'
import BagUploadDialog from '@/components/BagUploadDialog.vue'
import TrashBin, { type TrashItem } from '@/components/TrashBin.vue'

type TagType = 'success' | 'warning' | 'danger' | 'info' | 'primary'

const { t } = useI18n()
const store = useStatusStore()
const stream = createMappingStream(store)
const ms = stream.status
onMounted(stream.start)
onBeforeUnmount(stream.stop)

// 选中的包、算法、倍速保存在浏览器里，下次打开沿用
const saved = loadPrefs()
const chosenBag = ref<string>(typeof saved.pbBag === 'string' ? saved.pbBag : '')
const chosenSlam = ref<string>(typeof saved.pbSlam === 'string' ? saved.pbSlam : '')
const pbRate = ref<number>(typeof saved.pbRate === 'number' ? saved.pbRate : 1)
const chosenOpts = ref<string[]>(savedOptions(saved.pbSlamOpts, saved.pbGround)) // 算法开关（仅激光、地面约束）
watch([chosenBag, chosenSlam, pbRate, chosenOpts], () =>
  savePrefs({ pbBag: chosenBag.value, pbSlam: chosenSlam.value, pbRate: pbRate.value, pbSlamOpts: chosenOpts.value }),
)

// Opened from a run record on the Evaluation page ("Replay with the same set-up", ?bag=&slam=&begin=&end=&rate=&from=):
// pre-select the bag, the SLAM with its switches, the part and the rate. Nothing starts by itself.
const route = useRoute()
const router = useRouter()
const pendingRange = ref<{ bag: string; begin: number; end: number | null } | null>(null)
const pendingSlam = ref<string | null>(null)
const fromRun = ref('')
{
  const q = route.query
  if (typeof q.bag === 'string' && q.bag) {
    chosenBag.value = q.bag
    const num = (v: unknown) => (typeof v === 'string' && v !== '' && Number.isFinite(Number(v)) ? Number(v) : null)
    const rate = num(q.rate)
    if (rate != null) pbRate.value = rate
    const begin = num(q.begin)
    if (begin != null) pendingRange.value = { bag: q.bag, begin, end: num(q.end) }
    if (typeof q.slam === 'string' && q.slam) pendingSlam.value = q.slam
    fromRun.value = typeof q.from === 'string' ? q.from : ''
    if (!pendingSlam.value) ElMessage.info(t('playback.prefilled', { id: fromRun.value || '—' }))
    router.replace({ query: {} })
  }
}

// ---------------- 进程 ----------------

const STATE_TAG: Record<StackState, TagType> = { idle: 'info', running: 'success', stopping: 'warning', exited: 'info' }
const stackOf = (name: string) => store.status?.stacks.find((s) => s.name === name) ?? null
const running = (s: StackInfo | null) => s?.state === 'running'
const busy = ref<Record<string, boolean>>({})
async function toggle(s: StackInfo | null) {
  if (!s || busy.value[s.name]) return
  busy.value[s.name] = true
  try {
    if (running(s)) {
      await api.stopStack(s.name)
    } else {
      // a SLAM is started for the chosen bag: a real robot's bag gets that robot's configuration
      const forBag = allSlamOptions.value.some((o) => o.name === s.name) ? selectedBag.value?.path : undefined
      await api.startStack(s.name, undefined, 'playback', forBag)
      ElMessage.success(t('monitor.started', { name: pick(s.label) }))
    }
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    busy.value[s.name] = false
  }
}
// 仿真在跑时不能回放：建图页的仿真和这里的回放都会发布 /clock 和 SLAM 的输入
const simRunning = computed(() =>
  [ms.value?.stacks.sim ?? 'sim', 'sim_gui'].some((n) => ['running', 'stopping'].includes(stackOf(n)?.state ?? '')),
)

// ---------------- ① 数据包 ----------------
// 后端在 console.yaml playback.bag_dirs 下找 rosbag2 文件夹

const bags = ref<BagInfo[]>([])
const bagRoots = ref<string[]>([])
const bagsLoading = ref(false)
const storage = ref<PlaybackStorage | null>(null) // upload folder, trash, free disk space
async function loadBags() {
  bagsLoading.value = true
  try {
    const [r, st] = await Promise.all([api.playbackBags(), api.playbackStorage()])
    bags.value = r.bags
    bagRoots.value = r.roots
    storage.value = st
  } catch (e) {
    if (store.connected) ElMessage.error(errorText(e))
  } finally {
    bagsLoading.value = false
  }
}
watch(() => store.connected, (c) => {
  if (c && !bags.value.length && !bagsLoading.value) loadBags()
}, { immediate: true })

// Upload (BagUploadDialog), delete (to the trash of the bag's folder), recycle bin (TrashBin: restore, delete, empty)
const uploadOpen = ref(false)
async function onUploaded(path: string) {
  await loadBags()
  chosenBag.value = path
}
const deleting = ref('')
async function deleteBag(b: BagInfo) {
  try {
    await ElMessageBox.confirm(
      t('playback.bag.deleteConfirm', { name: b.name, size: fmtBytes(b.size) }), t('playback.bag.deleteTitle'),
      { type: 'warning', confirmButtonText: t('playback.bag.delete'), cancelButtonText: t('uploads.cancel') },
    )
  } catch {
    return
  }
  deleting.value = b.path
  try {
    const r = await api.deleteBag(b.path)
    if (chosenBag.value === b.path) chosenBag.value = ''
    ElMessage.success(t('playback.bag.deleted', { name: r.name }))
    await loadBags()
    if (trashOpen.value) loadTrash()
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    deleting.value = ''
  }
}
const trashOpen = ref(false)
const trashItems = ref<TrashItem[]>([])
const trashDirs = ref<string[]>([])
const trashLoading = ref(false)
const trashBusy = ref('') // entry being restored / deleted ('*' = emptying)
async function loadTrash() {
  trashLoading.value = true
  try {
    const r = await api.bagTrash()
    // With several bag folders, the details column says which folder's trash an entry is in
    trashItems.value = r.entries.map((e) => ({ id: e.id, name: e.name, detail: r.dirs.length > 1 ? e.folder : undefined,
      deleted: e.deleted, size: e.size, restorable: e.restorable }))
    trashDirs.value = r.dirs
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    trashLoading.value = false
  }
}
async function trashAct(action: 'restore' | 'purge' | 'empty', id = '') {
  trashBusy.value = id || '*'
  try {
    if (action === 'restore') {
      const r = await api.restoreBag(id)
      ElMessage.success(t('playback.bag.restored', { name: r.name }))
    } else if (action === 'purge') {
      const r = await api.purgeBag(id)
      ElMessage.success(t('playback.bag.purged', { size: fmtBytes(r.freed) }))
    } else {
      const r = await api.emptyBagTrash()
      ElMessage.success(t('playback.bag.emptied', { size: fmtBytes(r.freed) }))
    }
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    trashBusy.value = ''
    await Promise.all([loadTrash(), loadBags()])
  }
}

const pb = computed(() => ms.value?.playback ?? null)
const pbActive = computed(() => pb.value?.state === 'running' || pb.value?.state === 'stopping')
// 回放中锁定为正在播放的包
const selectedBagPath = computed(() => (pbActive.value && pb.value?.bag ? pb.value.bag : chosenBag.value))
const selectedBag = computed(() => bags.value.find((b) => b.path === selectedBagPath.value) ?? null)

// ---------------- ② SLAM ----------------
// 只列出能用于数据包的算法（console.yaml slam_profiles 的 sources 含 bag），所选的包还要能提供它的输入（例如 IMU）

const allSlamOptions = computed(() => (ms.value?.slam_options ?? []).filter((o) => stackOf(o.name)))
// pre-selection from a run record (see the top): applied once the SLAM list has arrived
watch(allSlamOptions, (opts) => {
  const o = pendingSlam.value ? opts.find((x) => x.name === pendingSlam.value) : undefined
  if (!o) return
  chosenSlam.value = baseOf(o)
  chosenOpts.value = [...(o.options ?? [])]
  pendingSlam.value = null
  ElMessage.info(t('playback.prefilled', { id: fromRun.value || '—' }))
}, { immediate: true })

// playback run records per bag (Evaluation page), for the "played n times" links in step ①
const runCounts = ref<Record<string, number>>({})
async function loadRunCounts() {
  try {
    const c: Record<string, number> = {}
    for (const r of (await evalApi.runs()).runs) if (r.playback?.bag) c[r.playback.bag] = (c[r.playback.bag] ?? 0) + 1
    runCounts.value = c
  } catch {
    // only a hint; the bag list works without it
  }
}
watch(() => store.connected, (c) => { if (c) loadRunCounts() }, { immediate: true })
watch(() => ms.value?.playback?.run, (id) => { if (id) loadRunCounts() })
const activeSlam = computed(() =>
  allSlamOptions.value.find((o) => ['running', 'stopping'].includes(stackOf(o.name)?.state ?? '')),
)
const slamFits = (o: SlamOption) => !selectedBag.value || !!selectedBag.value.slams[o.name]?.ok
// 算法按钮只列基础算法（FAST-LIO2、GLIM）；变体（仅激光、地面约束）用开关切换，见 mapping/slamChoice.ts。
// 包里没有 IMU 时自动打开“仅激光”（开关锁定为开）
const choice = useSlamChoice({
  all: allSlamOptions, source: 'bag', active: activeSlam, chosen: chosenSlam, chosenOpts, fits: slamFits,
})
const slamOptions = choice.bases
const selectedBase = choice.selectedBase
const selectedSlam = choice.selectedSlam
const slam = computed(() => (selectedSlam.value ? stackOf(selectedSlam.value.name) : null))
// SLAM 是从建图页（仿真）启动的：这里只显示，不控制
const slamFromSim = computed(() => !!slam.value && running(slam.value) && slam.value.origin !== 'playback')
// 所选的包缺 SLAM 需要的输入，或话题被重映射到按仿真机器人调的算法上
const slamBagNote = computed(() => {
  const b = selectedBag.value
  const o = selectedSlam.value
  const fit = b && o ? b.slams[o.name] : undefined
  if (!fit) return ''
  const sensor = b?.sensor ? pick(b.sensor.label) : ''
  if (fit.missing.includes('sensor')) return t('playback.bag.sensorUnsupported', { sensor })
  if (sensor) return t('playback.bag.sensorNote', { sensor })
  if (!fit.ok) {
    const inputs = fit.missing.map((k) => (k === 'imu' ? 'IMU' : t('playback.bag.cloud')))
    return t('playback.bag.slamMissing', { inputs: inputs.join(t('estop.listSep')) })
  }
  if (fit.remapped && (o?.sources ?? []).includes('sim')) return t('playback.bag.remapNote')
  return ''
})
// 启动 SLAM 要先选好能用的包；SLAM 或回放运行中只显示所选的包
const slamStartable = computed(() =>
  !simRunning.value && !!selectedBag.value?.supported && !!selectedSlam.value && slamFits(selectedSlam.value) && !pbActive.value,
)
const locked = computed(() => pbActive.value || !!activeSlam.value)
const shownBags = computed(() => (locked.value && selectedBag.value ? [selectedBag.value] : bags.value))
const canToggle = (s: StackInfo | null, ready: boolean) =>
  store.connected && !!s && s.state !== 'stopping' && (running(s) ? !slamFromSim.value : ready)

// ---------------- ③ 回放 ----------------

// 要放的片段（s，相对于包的开头）；换包时恢复为整个包
const range = ref<[number, number]>([0, 0])
watch(selectedBag, (b, old) => {
  const want = pendingRange.value
  if (b && want && b.path === want.bag) {
    range.value = [Math.max(0, want.begin), Math.min(b.duration, want.end ?? b.duration)]
    pendingRange.value = null
    return
  }
  if (b && (b.path !== old?.path || range.value[1] <= 0 || range.value[1] > b.duration)) range.value = [0, b.duration]
}, { immediate: true })
const pbBusy = ref(false)
async function pbCall(fn: () => Promise<unknown>) {
  if (pbBusy.value) return
  pbBusy.value = true
  try {
    await fn()
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    pbBusy.value = false
  }
}
function startPlayback() {
  const b = selectedBag.value
  if (!b) return
  const [begin, end] = range.value
  pbCall(() => api.startPlayback(b.path, pbRate.value, begin, end >= b.duration - 0.05 ? undefined : end))
}
const pausePlayback = (paused: boolean) => pbCall(() => api.pausePlayback(paused))
const setPlaybackRate = (rate: number) => pbCall(() => api.setPlaybackRate(rate))
const stopPlayback = () => pbCall(() => api.stopPlayback())
const pbBlocked = computed(() => (simRunning.value ? t('playback.simRunning') : null))

// ---------------- 步骤高亮、指标 ----------------

const currentStep = computed(() => {
  const saving = ms.value?.save.state === 'running'
  if (!selectedBag.value) return 1
  if (!running(slam.value) && !pbActive.value && !saving) return 2
  if (saving || (pb.value?.finished && !pb.value.catching_up)) return 4
  return 3
})
const imuState = computed<[string, TagType]>(() => {
  if (selectedSlam.value?.needs_imu === false) return [t('mapping.imu.unused'), 'info']
  if (ms.value?.initialized) return [t('mapping.imu.ok'), 'success']
  if (running(slam.value)) return [t('mapping.imu.waiting'), 'warning']
  return [t('mapping.imu.idle'), 'info']
})
const saveWarn = computed(() => (pb.value?.catching_up ? t('playback.saveCatchingUp') : ''))
</script>

<template>
  <div class="playback">
    <MappingStage :stream="stream" :imu-state="imuState" :waiting-hint="t('playback.waitingHint')" />

    <!-- 操作区 -->
    <aside class="ops glass">
      <p v-if="simRunning" class="note bad banner">
        {{ t('playback.simRunning') }} <router-link to="/mapping">{{ t('playback.toMapping') }}</router-link>
      </p>

      <!-- ① 数据包 -->
      <section class="step" :class="{ current: currentStep === 1 }">
        <header class="step-head">
          <span class="no mono">1</span>
          <span class="title">{{ t('playback.steps.bag') }}</span>
        </header>
        <BagPicker
          :model-value="selectedBagPath" :bags="shownBags" :roots="bagRoots" :loading="bagsLoading" :locked="pbActive"
          :compact="locked && !!selectedBag" :deleting="deleting" :connected="store.connected" :run-counts="runCounts"
          @update:model-value="chosenBag = $event" @refresh="loadBags" @upload="uploadOpen = true"
          @delete="deleteBag"
        >
          <template #trash="{ disabled }">
            <TrashBin
              v-model="trashOpen" :title="t('playback.bag.trashTitle')" :entries="trashItems" :loading="trashLoading"
              :busy="trashBusy" :dirs="trashDirs" :disabled="disabled" @open="loadTrash"
              @restore="trashAct('restore', $event)" @purge="trashAct('purge', $event)" @empty="trashAct('empty')"
            />
          </template>
        </BagPicker>
        <p class="note">{{ locked ? t('playback.bag.locked') : t('playback.bag.hint') }}</p>
      </section>

      <!-- ② SLAM -->
      <section class="step" :class="{ current: currentStep === 2, locked: !selectedBag && !running(slam) }">
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
              :class="{ on: selectedBase?.name === o.name }" role="radio" :aria-checked="selectedBase?.name === o.name"
              :disabled="(!!activeSlam && baseOf(activeSlam) !== o.name) || (!activeSlam && !choice.baseFits(o))"
              @click="chosenSlam = o.name"
            >{{ o.algorithm }}</button>
          </div>
        </div>
        <div v-for="k in choice.switches.value" :key="k" class="field">
          <span class="k">{{ t(`mapping.steps.opt.${k}`) }}<InfoTip :content="t(`mapping.tips.opt.${k}`)" /></span>
          <el-switch
            :model-value="choice.optionOn(k)" size="small" :disabled="choice.optionDisabled(k)"
            @update:model-value="(v: string | number | boolean) => choice.setOption(k, !!v)"
          />
        </div>
        <p v-if="choice.forced.value.includes('lidar')" class="note">{{ t('playback.bag.lidarForced') }}</p>
        <p v-if="slamBagNote" class="note">{{ slamBagNote }}</p>
        <p v-if="selectedSlam?.save_mode === 'dump'" class="note">{{ t('mapping.steps.dumpNote') }}</p>
        <p v-if="slamFromSim" class="note">{{ t('playback.slamFromSim') }}</p>
        <p v-else-if="!selectedBag && !running(slam)" class="note">{{ t('playback.needBag') }}</p>
        <el-button
          class="wide" :type="running(slam) ? 'default' : 'primary'" :loading="!!slam && busy[slam.name]"
          :disabled="!canToggle(slam, slamStartable)" @click="toggle(slam)"
        >{{ running(slam) ? t('mapping.steps.stop') : t('mapping.steps.start') }}</el-button>
      </section>

      <!-- ③ 回放 -->
      <section class="step" :class="{ current: currentStep === 3, locked: !running(slam) && !pbActive }">
        <header class="step-head">
          <span class="no mono">3</span>
          <span class="title">{{ t('playback.steps.play') }}</span>
          <span v-if="pb && pb.state !== 'idle'" class="chip" :class="[STATE_TAG[pb.state], { live: pb.state === 'running' && !pb.paused }]">
            {{ pb.state === 'running' && pb.paused ? t('playback.controls.paused') : pb.finished ? t('playback.controls.done') : t(`monitor.state.${pb.state}`) }}
          </span>
        </header>
        <PlaybackControls
          v-model:rate="pbRate" v-model:range="range" :status="pb" :bag="selectedBag"
          :slam-name="selectedSlam?.name ?? null" :slam-running="running(slam)" :busy="pbBusy"
          :connected="store.connected" :blocked="pbBlocked"
          @start="startPlayback" @pause="pausePlayback" @set-rate="setPlaybackRate" @stop="stopPlayback"
        />
        <p v-if="pb?.run" class="note">
          {{ t('playback.runRecord') }}
          <router-link class="mono" :to="{ path: '/evaluation', query: { run: pb.run } }">{{ pb.run }}</router-link>
        </p>
      </section>

      <!-- ④ 保存 -->
      <MapSaveStep :ms="ms" :current="currentStep === 4" :name-base="selectedBag?.name ?? 'bag'" :warn="saveWarn" />
    </aside>
    <BagUploadDialog v-model="uploadOpen" :storage="storage" :disabled="!store.connected" @uploaded="onUploaded" />
  </div>
</template>

<style scoped src="../mapping/steps.css"></style>

<style scoped>
.playback {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 14px;
  height: 100%;
  min-height: 520px;
}
.banner {
  margin: 0;
  padding: 8px 10px;
  border-radius: 10px;
  background: color-mix(in srgb, var(--bad) 8%, transparent);
}
</style>
