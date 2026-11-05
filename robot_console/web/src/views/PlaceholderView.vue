<!-- 还没实现的页面：雷达扫描动画 + 计划在开发顺序第几步实现 -->
<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import RadarMark from '@/components/RadarMark.vue'

const route = useRoute()
const { t } = useI18n()
const page = computed(() => t(String(route.meta.titleKey)))
</script>

<template>
  <div class="ph">
    <div class="card glass">
      <div class="scope">
        <RadarMark :size="176" />
      </div>
      <div class="kicker">{{ t('planned.step', { step: route.meta.step }) }}</div>
      <h2>{{ page }}</h2>
      <p>{{ t('placeholder', { page, step: route.meta.step }) }}</p>
    </div>
  </div>
</template>

<style scoped>
.ph {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100%;
}
.card {
  display: flex;
  flex-direction: column;
  align-items: center;
  max-width: 520px;
  padding: 44px 40px 40px;
  border-radius: 24px;
  text-align: center;
}
/* 雷达外面再套一圈虚线刻度环 */
.scope {
  position: relative;
  padding: 18px;
  margin-bottom: 22px;
}
.scope::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 50%;
  border: 1px dashed rgba(8, 145, 178, 0.35);
  animation: turn 40s linear infinite;
}
@keyframes turn {
  to {
    transform: rotate(-360deg);
  }
}
.kicker {
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: var(--accent);
  background: rgba(8, 145, 178, 0.08);
}
h2 {
  margin: 14px 0 8px;
  font-size: 26px;
  font-weight: 650;
}
p {
  margin: 0;
  color: var(--ink-2);
  line-height: 1.7;
}
</style>
