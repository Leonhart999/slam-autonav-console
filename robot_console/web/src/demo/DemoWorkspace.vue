<!-- The loaded demo: 3D replay on the left, the notice, the run and its evaluation on the right -->
<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import GlassPanel from '@/components/GlassPanel.vue'
import DemoStage from './DemoStage.vue'
import DemoEval from './DemoEval.vue'
import { useReplay } from './replay'
import type { DemoData } from './data'

const props = defineProps<{ data: DemoData; installUrl: string }>()
const { t, te, locale } = useI18n()
const replay = useReplay(props.data)
const run = props.data.run

const fmt = (n: number) => new Intl.NumberFormat(locale.value).format(n)
const sceneText = () => {
  const key = `about.sceneValue.${run.scene}`
  return te(key) ? t(key) : run.scene
}

// Space plays / pauses, unless a control that uses the key has the focus
function onKey(e: KeyboardEvent) {
  if (e.code !== 'Space' || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return
  const el = e.target as HTMLElement | null
  if (el?.closest('button, input, textarea, select, a, [role="slider"], [contenteditable="true"]')) return
  e.preventDefault()
  replay.toggle()
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="workspace">
    <DemoStage class="stage" :replay="replay" />

    <aside class="side">
      <div class="notice glass" role="note">
        <span class="chip primary">{{ t('replay.badge') }}</span>
        <b>{{ t('replay.title') }}</b>
        <p>{{ t('replay.body') }}</p>
        <a :href="installUrl" target="_blank" rel="noopener">{{ t('replay.install') }} ↗</a>
      </div>

      <GlassPanel :title="t('about.title')">
        <dl class="facts">
          <dt>{{ t('about.scene') }}</dt><dd>{{ sceneText() }}</dd>
          <dt>{{ t('about.sensors') }}</dt><dd>{{ t('about.sensorsValue') }}</dd>
          <dt>{{ t('about.algorithm') }}</dt><dd>{{ run.algorithm }}</dd>
          <dt>{{ t('about.duration') }}</dt><dd class="mono">{{ run.duration.toFixed(1) }} s</dd>
          <dt>{{ t('about.length') }}</dt><dd class="mono">{{ run.length.toFixed(1) }} m</dd>
          <dt>{{ t('about.map') }}</dt><dd>{{ t('about.mapValue', { count: fmt(run.voxels), voxel: run.map.voxel }) }}</dd>
        </dl>
      </GlassPanel>

      <DemoEval :run="run" :time="replay.time.value" />
    </aside>
  </div>
</template>

<style scoped>
.workspace {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 380px;
  gap: 14px;
  min-height: 0;
}
.stage {
  min-height: 0;
}
.side {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  padding: 1px; /* keeps the panel shadows inside the scroll box */
}
.notice {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  padding: 14px 16px;
  border-radius: var(--radius);
  font-size: 13px;
}
.notice b {
  margin-top: 4px;
  font-size: 14px;
}
.notice p {
  margin: 0;
  line-height: 1.6;
  color: var(--ink-2);
}
.notice a {
  color: var(--accent);
  font-weight: 600;
  text-decoration: none;
}
.notice a:hover {
  text-decoration: underline;
}
.facts {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 8px 14px;
  margin: 0;
  font-size: 13px;
}
.facts dt {
  color: var(--ink-3);
}
.facts dd {
  margin: 0;
}

@media (max-width: 1100px) {
  .workspace {
    grid-template-columns: minmax(0, 1fr);
  }
  .stage {
    height: max(480px, 72vh);
  }
  .side {
    overflow: visible;
  }
}
</style>
