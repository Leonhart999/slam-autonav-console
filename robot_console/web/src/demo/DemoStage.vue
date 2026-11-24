<!-- 3D stage of the web demo: the console's point cloud view (PointCloudView) over the saved map, with the
     layer and camera HUD of the mapping page, a replay bar and the metrics strip. The replay (useReplay) is
     owned by the parent and passed in. -->
<script setup lang="ts">
import { Aim, CaretRight, RefreshLeft, VideoPause } from '@element-plus/icons-vue'
import { computed, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { LayerKey, ViewMode } from '@/mapping/stream'
import PointCloudView from '@/components/PointCloudView.vue'
import InfoTip from '@/components/InfoTip.vue'
import { ROOF_Z, SPEEDS, type useReplay } from './replay'

const props = defineProps<{ replay: ReturnType<typeof useReplay> }>()
const { t, locale } = useI18n()
const r = props.replay

type DemoLayer = Exclude<LayerKey, 'scan'>
const LAYERS: DemoLayer[] = ['map', 'est', 'gt', 'grid']
const MODES: ViewMode[] = ['follow', 'free', 'top']
// scan stays off: the demo has the saved map, not the scans of the run
const layers = reactive<Record<LayerKey, boolean>>({ map: true, scan: false, est: true, gt: true, grid: true })
const mode = ref<ViewMode>('follow') // first look: the robot in its aisle; Free / Top show the whole map
const pointSize = ref(0.85)
const viewer = ref<InstanceType<typeof PointCloudView> | null>(null)

const st = computed(() => r.stream.status.value)
const pose = computed(() => st.value?.pose ?? null)
const zRange = computed(() => {
  const e = st.value?.extent
  return e ? [e[2], Math.min(e[5], e[2] + 4)] : null
})

const fmtTime = (s: number) => `${s.toFixed(1)} s`
const count = computed(() => new Intl.NumberFormat(locale.value).format(st.value?.voxels ?? 0))
const metrics = computed(() => {
  const m = st.value
  const e = m?.extent
  return [
    { key: 'time', value: `${(m?.duration ?? 0).toFixed(1)} / ${r.duration.toFixed(1)} s` },
    { key: 'length', value: m ? `${m.length.toFixed(1)} m` : '—' },
    { key: 'speed', value: m ? `${m.speed.toFixed(2)} m/s` : '—' },
    { key: 'voxels', value: count.value },
    { key: 'extent', value: e ? `${(e[3] - e[0]).toFixed(1)}×${(e[4] - e[1]).toFixed(1)} m` : '—' },
    { key: 'ape', value: m?.error != null ? `${(m.error * 100).toFixed(1)} cm` : '—', tip: t('metrics.apeTip') },
  ]
})

const slider = computed({
  get: () => r.time.value,
  set: (v: number) => r.seek(v),
})
</script>

<template>
  <div class="main">
    <section class="viewport glass">
      <PointCloudView ref="viewer" :stream="r.stream" :layers="layers" :mode="mode" :point-size="pointSize" />

      <div class="hud tl">
        <button
          v-for="k in LAYERS" :key="k" class="layer" :class="[k, { on: layers[k] }]"
          :aria-pressed="layers[k]" @click="layers[k] = !layers[k]"
        >
          <i class="swatch" />{{ t(`layers.${k}`) }}
        </button>
        <button class="layer roof" :class="{ on: r.roof.value }" :aria-pressed="r.roof.value" @click="r.roof.value = !r.roof.value">
          <i class="swatch" />{{ t('layers.roof', { h: ROOF_Z }) }}
        </button>
      </div>

      <div class="hud tr">
        <div class="seg">
          <button v-for="m in MODES" :key="m" :class="{ on: mode === m }" :aria-pressed="mode === m" @click="mode = m">
            {{ t(`views.${m}`) }}
          </button>
        </div>
        <button class="icon-btn" :title="t('views.reset')" :aria-label="t('views.reset')" @click="viewer?.resetView()">
          <el-icon :size="16"><Aim /></el-icon>
        </button>
      </div>

      <div v-if="pose" class="hud bl mono">
        <span><i>x</i>{{ pose.x.toFixed(2) }}</span>
        <span><i>y</i>{{ pose.y.toFixed(2) }}</span>
        <span><i>θ</i>{{ ((pose.yaw * 180) / Math.PI).toFixed(0) }}°</span>
      </div>

      <div class="hud br">
        <div v-if="zRange" class="legend">
          <span class="k">{{ t('hud.height') }}</span>
          <span class="mono">{{ zRange[0].toFixed(1) }}</span>
          <i class="ramp" />
          <span class="mono">{{ zRange[1].toFixed(1) }} m</span>
        </div>
        <div class="size">
          <span class="k">{{ t('hud.pointSize') }}</span>
          <el-slider v-model="pointSize" :min="0.5" :max="3" :step="0.25" :show-tooltip="false" size="small" />
        </div>
      </div>
    </section>

    <div class="replay glass">
      <button
        class="play" :class="{ on: r.playing.value }" :aria-label="r.playing.value ? t('stage.pause') : t('stage.play')"
        :title="`${r.playing.value ? t('stage.pause') : t('stage.play')} · ${t('stage.keyHint')}`" @click="r.toggle()"
      >
        <el-icon :size="20"><VideoPause v-if="r.playing.value" /><CaretRight v-else /></el-icon>
      </button>
      <button class="icon-btn" :title="t('stage.restart')" :aria-label="t('stage.restart')" @click="r.seek(0)">
        <el-icon :size="16"><RefreshLeft /></el-icon>
      </button>
      <span class="clock mono">{{ fmtTime(r.time.value) }}</span>
      <el-slider
        v-model="slider" class="timeline" :min="0" :max="r.duration" :step="0.1" :show-tooltip="false"
        :aria-label="t('stage.time')"
      />
      <span class="clock mono dim">{{ fmtTime(r.duration) }}</span>
      <div class="seg" role="group" :aria-label="t('stage.speed')" :title="t('stage.speed')">
        <button v-for="s in SPEEDS" :key="s" :class="{ on: r.speed.value === s }" :aria-pressed="r.speed.value === s" @click="r.speed.value = s">
          {{ s }}×
        </button>
      </div>
    </div>

    <div class="metrics glass">
      <div v-for="m in metrics" :key="m.key" class="metric">
        <span class="k">{{ t(`metrics.${m.key}`) }}<InfoTip v-if="m.tip" :content="m.tip" /></span>
        <span class="v mono">{{ m.value }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped src="../mapping/stage.css"></style>
<style scoped>
.main {
  height: 100%;
}
.viewport {
  min-height: 360px;
}
.metrics {
  grid-template-columns: repeat(6, minmax(0, 1fr));
}
/* stage.css switches to 4 columns below 1500 px (the console has a side menu); the demo stays at 6 */
@media (max-width: 1500px) {
  .metrics {
    grid-template-columns: repeat(6, minmax(0, 1fr));
  }
  .metric:nth-child(5) {
    border-left: 1px solid var(--hairline);
  }
}
@media (max-width: 760px) {
  .metrics {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .metric:nth-child(4) {
    border-left: none;
  }
}
.layer.roof .swatch {
  background: linear-gradient(180deg, #5440d9 0 45%, transparent 45%);
  box-shadow: inset 0 0 0 1px #5440d9;
}

.replay {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 14px;
  border-radius: var(--radius);
}
.play {
  display: grid;
  flex: none;
  place-items: center;
  width: 40px;
  height: 40px;
  border: none;
  border-radius: 12px;
  color: #fff;
  background: var(--accent-grad);
  box-shadow: 0 6px 16px -8px rgba(37, 99, 235, 0.75);
  cursor: pointer;
}
.play:hover {
  filter: brightness(1.08);
}
.play:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.clock {
  flex: none;
  min-width: 56px;
  font-size: 13px;
  text-align: right;
}
.clock.dim {
  text-align: left;
  color: var(--ink-3);
}
.timeline {
  flex: 1;
  min-width: 80px;
}
.replay .seg {
  flex: none;
}

@media (max-width: 640px) {
  .replay {
    flex-wrap: wrap;
  }
  .timeline {
    order: 5;
    flex-basis: 100%;
  }
  .clock.dim {
    display: none;
  }
}
</style>
