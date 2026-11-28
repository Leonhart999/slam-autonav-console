<!-- One row of a settings card: label (+ tags, info tip), one-line description, control on the right.
     An error from the last save attempt is shown under the description. -->
<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import InfoTip from '@/components/InfoTip.vue'
import { settingsMessages } from './messages'

defineProps<{
  label: string
  sub?: string
  tip?: string
  restart?: boolean
  readOnly?: boolean
  overridden?: boolean
  error?: string
  /** control under the text instead of beside it (long lists, wide inputs) */
  stacked?: boolean
  /** changed but not saved yet: accent marker on the left */
  dirty?: boolean
}>()
const { t } = useI18n({ messages: settingsMessages })
</script>

<template>
  <div class="row" :class="{ stacked, bad: !!error, dirty }">
    <div class="text">
      <div class="label">
        <span>{{ label }}</span>
        <InfoTip v-if="tip" :content="tip" />
        <span v-if="overridden" class="tag over">{{ t('settings.tag.overridden') }}</span>
        <span v-if="restart" class="tag restart">{{ t('settings.tag.restart') }}</span>
        <span v-if="readOnly" class="tag ro">{{ t('settings.tag.readOnly') }}</span>
        <slot name="label-extra" />
      </div>
      <div v-if="sub" class="sub">{{ sub }}</div>
      <div v-if="error" class="err">{{ error }}</div>
    </div>
    <div class="ctrl"><slot /></div>
  </div>
</template>

<style scoped>
.row {
  display: flex;
  align-items: center;
  gap: 16px;
  min-height: 58px;
  padding: 10px 18px;
  border-top: 1px solid var(--hairline);
  box-sizing: border-box;
}
.row { position: relative; transition: background 0.18s; }
.row:hover { background: rgba(var(--paper-rgb), 0.38); }
.row:first-child { border-top: 0; }
.row.dirty::before {
  content: '';
  position: absolute;
  left: 0;
  top: 10px;
  bottom: 10px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--accent-grad);
  box-shadow: 0 0 8px rgba(var(--accent-rgb), 0.6);
}
.row.bad::before { background: var(--bad); box-shadow: none; }
.row.stacked { flex-direction: column; align-items: stretch; gap: 8px; }
.text { flex: 1; min-width: 0; }
.label {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 14px;
  font-weight: 500;
  color: var(--ink);
}
.sub {
  margin-top: 3px;
  font-size: 12px;
  color: var(--ink-3);
}
.err {
  margin-top: 4px;
  font-size: 12px;
  color: var(--bad);
}
.tag {
  font-size: 10.5px;
  padding: 1px 6px;
  border-radius: 6px;
  white-space: nowrap;
}
.tag.restart { color: var(--warn); background: color-mix(in srgb, var(--warn) 13%, transparent); }
.tag.over { color: var(--accent); background: rgba(var(--accent-rgb), 0.1); }
.tag.ro { color: var(--ink-3); background: rgba(var(--tint-rgb), 0.07); }
.ctrl {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex: none;
  max-width: 62%;
}
.row.stacked .ctrl { max-width: none; justify-content: flex-start; }
.row.bad::before { content: ''; position: absolute; left: 0; top: 10px; bottom: 10px; width: 3px; border-radius: 0 3px 3px 0; }
.row.bad :deep(.el-input__wrapper),
.row.bad :deep(.el-select__wrapper) {
  box-shadow: 0 0 0 1px var(--bad) inset;
}
html.density-compact .row { min-height: 46px; padding: 6px 16px; }
</style>
