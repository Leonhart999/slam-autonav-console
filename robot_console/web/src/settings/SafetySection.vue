<!-- Settings → Teleop & safety: teleop limits and E-stop behaviour from console.yaml. -->
<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { Odometer, Warning } from '@element-plus/icons-vue'
import SettingsCard from './SettingsCard.vue'
import ConfigRow from './ConfigRow.vue'
import { settingsMessages } from './messages'

const { t } = useI18n({ messages: settingsMessages })
const m = (k: string) => `settings.safety.${k}`
</script>

<template>
  <div>
    <SettingsCard :title="t('settings.safety.cards.teleop')" :icon="Odometer" scope="console">
      <ConfigRow k="teleop.max_v" :msg="m('maxV')" kind="number" :min="0.05" :max="20" :step="0.1" unit="m/s" />
      <ConfigRow k="teleop.recommended_v" :msg="m('recommendedV')" kind="range" :min="0" :max="20" :step="0.1" unit="m/s" />
      <ConfigRow k="teleop.max_w" :msg="m('maxW')" kind="number" :min="0.05" :max="10" :step="0.1" unit="rad/s" />
      <ConfigRow k="teleop.timeout" :msg="m('timeout')" kind="number" :min="0.1" :max="10" :step="0.1" unit="s" />
      <ConfigRow k="teleop.topic" :msg="m('topic')" placeholder="/cmd_vel" />
    </SettingsCard>

    <SettingsCard :title="t('settings.safety.cards.estop')" :icon="Warning" scope="console">
      <ConfigRow k="estop.zero_duration" :msg="m('zeroDuration')" kind="number" :min="0.2" :max="10" :step="0.5" unit="s" />
    </SettingsCard>
  </div>
</template>
