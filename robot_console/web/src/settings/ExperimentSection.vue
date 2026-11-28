<!-- Settings → Experiment defaults: evaluation, batch mapping, navigation and playback values of console.yaml. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { pick } from '@/i18n'
import { Grid, Position, TrendCharts, VideoPlay } from '@element-plus/icons-vue'
import SettingsCard from './SettingsCard.vue'
import ConfigRow from './ConfigRow.vue'
import { settingsMessages } from './messages'
import { payload } from './store'

const { t } = useI18n({ messages: settingsMessages })
const slamOptions = computed(() => (payload.value?.options['batch.slam_stacks'] ?? [])
  .map((o) => ({ value: o.name, label: pick(o.label) })))
const m = (k: string) => `settings.experiment.${k}`
</script>

<template>
  <div>
    <SettingsCard :title="t('settings.experiment.cards.evaluation')" :icon="TrendCharts" scope="console">
      <ConfigRow k="evaluation.rpe_delta" :msg="m('rpeDelta')" kind="number" :min="0.01" :max="100" :step="0.5" unit="m" />
      <ConfigRow k="evaluation.max_diff" :msg="m('maxDiff')" kind="number" :min="0.0001" :max="1" :step="0.005" :precision="4" unit="s" />
      <ConfigRow k="mapping.gt_topic" :msg="m('gtTopic')" placeholder="/ground_truth" />
    </SettingsCard>

    <SettingsCard :title="t('settings.experiment.cards.batch')" :icon="Grid" scope="console">
      <ConfigRow k="batch.slam_stacks" :msg="m('slamStacks')" kind="multi" :options="slamOptions" stacked />
      <ConfigRow k="batch.run_timeout" :msg="m('runTimeout')" kind="number" :min="30" :max="86400" :step="60" :precision="0" unit="s" />
      <ConfigRow k="batch.ready_timeout" :msg="m('readyTimeout')" kind="number" :min="5" :max="3600" :step="10" :precision="0" unit="s" />
      <ConfigRow k="batch.max_runs" :msg="m('maxRuns')" kind="number" :min="1" :max="1000" :precision="0" />
      <ConfigRow k="batch.speeds" :msg="m('speeds')" kind="numlist" unit="×" />
    </SettingsCard>

    <SettingsCard :title="t('settings.experiment.cards.navigation')" :icon="Position" scope="console">
      <ConfigRow k="navigation.default_map" :msg="m('defaultMap')" placeholder="~/maps/xxx.yaml" />
      <ConfigRow k="navigation.goal_timeout" :msg="m('goalTimeout')" kind="number" :min="1" :max="86400" :step="10" unit="s" />
      <ConfigRow k="navigation.speed_scale_range" :msg="m('speedRange')" kind="range" :min="0.1" :max="20" :step="0.5" unit="×" />
      <ConfigRow k="navigation.max_w_cap" :msg="m('maxWCap')" kind="number" :min="0.1" :max="10" :step="0.1" unit="rad/s" />
    </SettingsCard>

    <SettingsCard :title="t('settings.experiment.cards.playback')" :icon="VideoPlay" scope="console">
      <ConfigRow k="playback.rates" :msg="m('rates')" kind="numlist" unit="×" />
    </SettingsCard>
  </div>
</template>
