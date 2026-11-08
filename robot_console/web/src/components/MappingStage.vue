<!-- 3D view of the live mapping data (point cloud map, current scan, trajectory, robot) with its HUD, and the
     metrics strip below it. Used by the mapping page (simulation) and the playback page (bags); the parent owns
     the data stream (createMappingStream) and passes it in. Layers, camera mode and point size are kept in the
     browser (mapping/prefs.ts); ?view=top in the address picks the camera mode. -->
<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { fmtCount, fmtDuration } from '@/format'
import { useStatusStore } from '@/stores/status'
import { loadPrefs, savePrefs } from '@/mapping/prefs'
import type { LayerKey, MappingStream, ViewMode } from '@/mapping/stream'
import PointCloudView from '@/components/PointCloudView.vue'
import RadarMark from '@/components/RadarMark.vue'
import InfoTip from '@/components/InfoTip.vue'

type TagType = 'success' | 'warning' | 'danger' | 'info' | 'primary'

const props = defineProps<{
  stream: MappingStream
  imuState: [string, TagType] // label and chip colour of the IMU metric (depends on the chosen SLAM)
  waitingHint: string // shown under the radar while there is no map yet
}>()
const { t } = useI18n()
const store = useStatusStore()
const ms = props.stream.status

const LAYERS: LayerKey[] = ['map', 'scan', 'est', 'gt', 'grid']
const MODES: ViewMode[] = ['follow', 'free', 'top']
const saved = loadPrefs()
const route = useRoute()
const layers = reactive<Record<LayerKey, boolean>>({ map: true, scan: true, est: true, gt: false, grid: true, ...saved.layers })
const mode = ref<ViewMode>(
  MODES.find((m) => m === route.query.view) ?? (MODES.includes(saved.mode) ? saved.mode : 'free'),
)
const pointSize = ref<number>(typeof saved.pointSize === 'number' ? saved.pointSize : 0.85)
watch([layers, mode, pointSize], () => savePrefs({ layers: { ...layers }, mode: mode.value, pointSize: pointSize.value }))
const viewer = ref<InstanceType<typeof PointCloudView> | null>(null)

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

// ---------------- 指标条 ----------------

function topicHz(name: string | undefined) {
  const tp = store.status?.topics.find((x) => x.name === name)
  return tp && tp.publishers > 0 ? `${tp.hz.toFixed(1)} Hz` : '—'
}
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
        <div class="wait-hint">{{ waitingHint }}</div>
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
</template>

<style scoped src="../mapping/stage.css"></style>
