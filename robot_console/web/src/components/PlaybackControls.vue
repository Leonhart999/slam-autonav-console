<!-- Playback page, step ③: choose the part of the bag, play / pause / stop it, pick the rate, show progress.
     Purely presentational: PlaybackView calls the API and passes the backend status in. There is no seeking
     during a playback: a jump inside one SLAM run breaks the SLAM, so the part is chosen before playing. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { fmtDuration } from '@/format'
import type { BagInfo, PlaybackStatus } from '@/mapping/stream'

const props = defineProps<{
  status: PlaybackStatus | null
  bag: BagInfo | null // selected in step ①
  slamName: string | null // SLAM chosen in step ②
  slamRunning: boolean
  busy: boolean
  connected: boolean
  blocked?: string | null // the page's own reason why nothing can be played (e.g. the simulation runs)
}>()
const rate = defineModel<number>('rate', { required: true })
const range = defineModel<[number, number]>('range', { required: true }) // part to play, s into the bag
const emit = defineEmits<{ start: []; pause: [paused: boolean]; setRate: [rate: number]; stop: [] }>()
const { t } = useI18n()
const LAG_WARN = 2 // s behind the playback clock before the page suggests a lower rate

const st = computed(() => props.status)
const active = computed(() => st.value?.state === 'running' || st.value?.state === 'stopping')
const playing = computed(() => st.value?.state === 'running')
const rates = computed(() => st.value?.rates ?? [0.5, 1, 2, 4, 8])
// While playing (or just finished) the timeline shows the backend's playback, otherwise the chosen part
const shown = computed(() => (active.value || st.value?.finished ? st.value : null))
const duration = computed(() => shown.value?.duration ?? props.bag?.duration ?? 0)
const part = computed<[number, number]>(() => shown.value?.range ?? range.value)
const position = computed(() => shown.value?.position ?? part.value[0])
const pctOf = (v: number) => (duration.value > 0 ? Math.min(100, Math.max(0, (100 * v) / duration.value)) : 0)
const band = computed(() => ({ left: `${pctOf(part.value[0])}%`, width: `${pctOf(part.value[1]) - pctOf(part.value[0])}%` }))
const fill = computed(() => ({ left: `${pctOf(part.value[0])}%`, width: `${pctOf(position.value) - pctOf(part.value[0])}%` }))
const wholeBag = computed(() => range.value[0] <= 0 && range.value[1] >= duration.value - 0.05)
const plan = computed(() => {
  const p = active.value ? st.value?.plan : props.slamName ? props.bag?.slams[props.slamName]?.plan : undefined
  return p ? Object.values(p) : []
})

// Why the play button is disabled (null = it can start)
const blocker = computed(() => {
  if (props.blocked) return props.blocked
  if (!props.bag) return t('playback.controls.needBag')
  if (props.slamName && !props.bag.slams[props.slamName]?.ok) return t('playback.controls.incompatible')
  if (!props.slamRunning) return t('playback.controls.needSlam')
  if (st.value?.needs_slam_restart) return t('playback.controls.restartSlam')
  if (!st.value?.slam_ready) return t('playback.controls.waitSlam')
  return null
})
const canStart = computed(() => props.connected && !active.value && !props.busy && !blocker.value)

function pickRate(r: number) {
  rate.value = r
  if (playing.value && st.value?.rate !== r) emit('setRate', r)
}
function wholeRange() {
  range.value = [0, duration.value]
}
</script>

<template>
  <div class="pb">
    <!-- Choose the part before playing -->
    <template v-if="!shown && bag">
      <div class="row">
        <span class="k">{{ t('playback.controls.part') }}</span>
        <span class="mono part">
          {{ fmtDuration(range[0]) }} – {{ fmtDuration(range[1]) }}
          <small>({{ fmtDuration(range[1] - range[0]) }})</small>
        </span>
      </div>
      <el-slider
        v-model="range" range class="range" :min="0" :max="Math.max(duration, 1)" :step="0.5"
        :disabled="!bag || !connected || busy" :format-tooltip="fmtDuration" size="small"
        :aria-label="t('playback.controls.part')"
      />
      <div class="part-hint">
        <span>{{ t('playback.controls.partHint') }}</span>
        <el-button v-if="!wholeBag" link type="primary" size="small" @click="wholeRange">{{ t('playback.controls.whole') }}</el-button>
      </div>
    </template>
    <!-- Progress of the running / finished playback -->
    <div v-else-if="shown" class="progress">
      <div class="track" :class="{ live: playing && !st?.paused }">
        <b class="band" :style="band" /><i :style="fill" />
      </div>
      <div class="times mono">
        <span>{{ fmtDuration(position) }}</span>
        <span>{{ fmtDuration(part[0]) }} – {{ fmtDuration(part[1]) }} / {{ fmtDuration(duration) }}</span>
      </div>
    </div>

    <div class="row">
      <span class="k">{{ t('playback.controls.rate') }}</span>
      <div class="seg">
        <button
          v-for="r in rates" :key="r" type="button" :class="{ on: (playing ? st?.rate : rate) === r }"
          :disabled="!connected || busy || st?.state === 'stopping'" @click="pickRate(r)"
        >{{ r }}×</button>
      </div>
    </div>

    <div v-if="plan.length" class="plan mono">
      <span v-for="p in plan" :key="p.from">{{ p.from === p.to ? p.from : `${p.from} → ${p.to}` }}</span>
    </div>

    <p v-if="st?.catching_up" class="note warn">{{ t('playback.controls.catchingUp', { lag: st.slam_lag ?? 0 }) }}</p>
    <p v-else-if="st?.finished && !active" class="note ok">{{ t('playback.controls.finished') }}</p>
    <p v-else-if="!active && blocker" class="note">{{ blocker }}</p>
    <p v-else-if="playing && (st?.slam_lag ?? 0) > LAG_WARN" class="note warn">
      {{ t('playback.controls.lagging', { lag: st?.slam_lag ?? 0 }) }}
    </p>
    <p v-else-if="st?.paused && playing" class="note">{{ t('playback.controls.pausedHint') }}</p>
    <p v-else-if="!active" class="note">{{ t('playback.controls.hint') }}</p>
    <p v-if="st?.state === 'exited' && !st.finished && st.exit_code != null" class="note bad">
      {{ t('playback.controls.failed', { code: st.exit_code }) }}
    </p>

    <div class="buttons">
      <el-button v-if="!active" class="grow" type="primary" :loading="busy" :disabled="!canStart" @click="emit('start')">
        <el-icon><VideoPlay /></el-icon><span>{{ t('playback.controls.play') }}</span>
      </el-button>
      <template v-else>
        <el-button class="grow" :loading="busy" :disabled="!playing" @click="emit('pause', !st?.paused)">
          <el-icon v-if="st?.paused"><VideoPlay /></el-icon><el-icon v-else><VideoPause /></el-icon>
          <span>{{ st?.paused ? t('playback.controls.resume') : t('playback.controls.pause') }}</span>
        </el-button>
        <el-button class="grow" :disabled="!playing || busy" @click="emit('stop')">{{ t('playback.controls.stop') }}</el-button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.progress {
  margin-bottom: 10px;
}
.track {
  height: 5px;
  border-radius: 5px;
  background: var(--hairline);
  overflow: hidden;
}
.track i {
  display: block;
  height: 100%;
  border-radius: 5px;
  background: var(--accent-grad);
  transition: width 0.4s linear;
}
.track.live i {
  box-shadow: 0 0 6px rgba(6, 182, 212, 0.7);
}
.times {
  display: flex;
  justify-content: space-between;
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--ink-3);
}
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}
.k {
  color: var(--ink-3);
  font-size: 12px;
}
.seg {
  display: inline-flex;
  padding: 3px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.55);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.seg button {
  height: 24px;
  padding: 0 10px;
  border: none;
  border-radius: 7px;
  background: none;
  font: inherit;
  font-size: 12px;
  color: var(--ink-3);
  cursor: pointer;
}
.seg button.on {
  color: var(--accent);
  font-weight: 600;
  background: #fff;
  box-shadow: 0 2px 8px -3px rgba(20, 60, 120, 0.3);
}
.seg button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}
.seg button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
.plan {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 8px;
  font-size: 11px;
  color: var(--ink-3);
  word-break: break-all;
}
.note {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-3);
}
.note.ok {
  color: var(--ok);
}
.note.warn {
  color: var(--warn);
}
.note.bad {
  color: var(--bad);
}
.buttons {
  display: flex;
  gap: 8px;
}
.grow {
  flex: 1;
  margin: 0;
}
.grow .el-icon {
  margin-right: 4px;
}
.track {
  position: relative;
}
.track .band {
  position: absolute;
  top: 0;
  bottom: 0;
  border-radius: 5px;
  background: rgba(8, 145, 178, 0.16);
}
.track i {
  position: absolute;
  top: 0;
}
.part {
  font-size: 12px;
}
.part small {
  color: var(--ink-3);
}
.range {
  padding: 0 8px;
  box-sizing: border-box;
}
.part-hint {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 2px 0 8px;
  font-size: 11.5px;
  color: var(--ink-3);
}
</style>
