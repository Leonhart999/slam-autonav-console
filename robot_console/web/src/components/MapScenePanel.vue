<!-- Scene of a map that has none recorded (built from a bag, or imported): navigation needs a simulation to drive
     in, so the map is either linked to the scene its bag was recorded in (suggested from the bag's ground truth)
     or gets a static world generated from its own occupancy grid. See robot_console/map_scene.py. -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import { errorText } from '@/messages'
import { pick as pickText } from '@/i18n'
import type { MapSceneInfo, MappingPreset, NavMap } from '@/types'
import InfoTip from '@/components/InfoTip.vue'

const props = defineProps<{ map: NavMap; disabled: boolean }>()
const emit = defineEmits<{ changed: [] }>()
const { t } = useI18n()

const info = ref<MapSceneInfo | null>(null)
const presets = ref<MappingPreset[]>([])
const choice = ref('')
const height = ref(2.0)
const busy = ref(false)
let seq = 0

async function load() {
  const n = ++seq
  try {
    const [i, p] = await Promise.all([api.mapScene(props.map.name), api.mappingPresets()])
    if (n !== seq) return
    info.value = i
    presets.value = p
    choice.value = i.suggested ?? ''
  } catch {
    // Legacy flat map (no meta.json) or older backend: nothing to offer
    if (n === seq) info.value = null
  }
}
watch(() => [props.map.name, props.map.preset], load, { immediate: true })

const label = (name: string) => {
  const p = presets.value.find((x) => x.name === name)
  return p ? pickText(p.label) : name
}
const needsScene = computed(() => !!info.value && !info.value.preset_ok)

async function run(action: () => Promise<MapSceneInfo>, done: (r: MapSceneInfo) => string) {
  busy.value = true
  try {
    const r = await action()
    info.value = r
    ElMessage.success(done(r))
    emit('changed')
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    busy.value = false
  }
}
const bind = (preset: string) =>
  run(() => api.bindMapScene(props.map.name, preset), () => t('navigation.mapScene.bound', { scene: label(preset) }))
const generate = () =>
  run(() => api.generateMapScene(props.map.name, height.value), (r) =>
    t('navigation.mapScene.generated', { scene: r.preset ?? '', boxes: r.generated?.boxes ?? 0 }))
const unbind = () => run(() => api.unbindMapScene(props.map.name), () => t('navigation.mapScene.unbound'))
</script>

<template>
  <div v-if="info && (needsScene || info.scene_source)" class="map-scene" :class="{ linked: !needsScene }">
    <template v-if="needsScene">
      <p class="note">
        {{ info.source === 'bag' ? t('navigation.mapScene.fromBag') : t('navigation.mapScene.none') }}
      </p>
      <!-- a) the scene the bag was recorded in -->
      <div class="label">
        {{ t('navigation.mapScene.bindTitle') }}<InfoTip :content="t('navigation.mapScene.bindTip')" />
      </div>
      <p v-if="info.suggested" class="hint">
        {{ t('navigation.mapScene.suggested', { scene: label(info.suggested) }) }}
      </p>
      <div class="row">
        <el-select
          v-model="choice" size="small" class="grow" :placeholder="t('navigation.mapScene.pick')"
          :disabled="disabled || busy"
        >
          <el-option v-for="p in presets" :key="p.name" :label="pickText(p.label)" :value="p.name" />
        </el-select>
        <el-button size="small" :disabled="disabled || busy || !choice" @click="bind(choice)">
          {{ t('navigation.mapScene.bind') }}
        </el-button>
      </div>
      <!-- b) real-robot data: a world built from the map itself -->
      <div class="label">
        {{ t('navigation.mapScene.genTitle') }}<InfoTip :content="t('navigation.mapScene.genTip')" />
      </div>
      <div class="row">
        <span class="hint">{{ t('navigation.mapScene.height') }}</span>
        <el-input-number
          v-model="height" size="small" :min="0.5" :max="5" :step="0.5" :precision="1"
          :disabled="disabled || busy" controls-position="right" class="height"
        />
        <el-button size="small" type="primary" plain :loading="busy" :disabled="disabled || busy" @click="generate">
          {{ t('navigation.mapScene.generate') }}
        </el-button>
      </div>
    </template>
    <!-- linked: one compact line, the full scene name in the tooltip -->
    <div v-else class="linked-row">
      <span class="chip primary">{{ t(`navigation.mapScene.kind.${info.scene_source}`) }}</span>
      <span class="scene-name" :title="info.preset ?? ''">{{ label(info.preset ?? '') }}</span>
      <el-button link size="small" type="primary" :disabled="disabled || busy" @click="unbind">
        {{ t('navigation.mapScene.unbind') }}
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.map-scene {
  margin: 10px 0;
  padding: 8px 10px;
  border: 1px dashed var(--line, rgba(99, 102, 241, 0.25));
  border-radius: 8px;
  font-size: 12px;
}
.map-scene.linked {
  padding: 6px 8px;
  border: 0;
  background: rgba(8, 145, 178, 0.05);
  box-shadow: inset 0 0 0 1px var(--hairline);
}
.linked-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.linked-row .chip {
  flex: none;
}
.scene-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--ink-2);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.note {
  margin: 0 0 6px;
  line-height: 1.6;
  color: var(--warn);
}
.label {
  margin: 8px 0 4px;
  font-weight: 600;
  color: var(--ink-2, inherit);
}
.hint {
  margin: 0 0 4px;
  line-height: 1.6;
  color: var(--ink-3);
}
.row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.grow {
  flex: 1;
  min-width: 0;
}
.height {
  width: 84px;
}
</style>
