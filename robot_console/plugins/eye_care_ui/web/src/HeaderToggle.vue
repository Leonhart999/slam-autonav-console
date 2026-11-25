<!-- Compact header toggle button for the Eye Care anti-blue-light UI extension. -->
<script setup lang="ts">
import { onMounted } from 'vue'
import { ElButton, ElTooltip } from 'element-plus'
import { RC, THEME_ID, t } from './sdk'
import { initEyeCareRuntime, isEyeCareActive } from './themeDef'

onMounted(() => initEyeCareRuntime())

function toggle(e: MouseEvent) {
  const box = (e.currentTarget as HTMLElement).getBoundingClientRect()
  const origin = { x: box.left + box.width / 2, y: box.top + box.height / 2 }
  void RC().theme.set(isEyeCareActive.value ? 'light' : THEME_ID, origin)
}
</script>

<template>
  <ElTooltip :content="t('headerTip')" placement="bottom">
    <ElButton
      size="small"
      class="eye-care-btn"
      :class="{ active: isEyeCareActive }"
      :type="isEyeCareActive ? 'primary' : 'default'"
      @click="toggle"
    >
      <span class="eye-care-dot" />
      <span>{{ isEyeCareActive ? t('headerOn') : t('headerOff') }}</span>
    </ElButton>
  </ElTooltip>
</template>

<style scoped>
.eye-care-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}
.eye-care-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-right: 6px;
  border-radius: 50%;
  background: var(--warn);
}
.eye-care-btn.active .eye-care-dot {
  background: #fef3c7;
  box-shadow: 0 0 6px rgba(254, 243, 199, 0.85);
}
</style>
