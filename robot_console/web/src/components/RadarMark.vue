<!-- 雷达扫描图标：同心圆 + 十字线 + 旋转的扫描扇区。侧边栏 logo 和空状态页共用
     size：像素尺寸；spin：是否转动 -->
<script setup lang="ts">
import { useId } from 'vue'

withDefaults(defineProps<{ size?: number; spin?: boolean }>(), { size: 40, spin: true })

// 页面上可能同时有好几个雷达图标，渐变的 id 不能重复
const uid = (useId() ?? 'r').replace(/[^\w-]/g, '')
const ringId = `radar-ring-${uid}`
const sweepId = `radar-sweep-${uid}`
</script>

<template>
  <svg :width="size" :height="size" viewBox="0 0 100 100" class="radar" :class="{ spin }" aria-hidden="true">
    <defs>
      <linearGradient :id="ringId" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" style="stop-color: var(--accent)" />
        <stop offset="1" style="stop-color: var(--accent-2)" />
      </linearGradient>
      <!-- 扇区从前沿（不透明）向后渐隐 -->
      <linearGradient :id="sweepId" gradientUnits="userSpaceOnUse" x1="72" y1="24" x2="80" y2="50">
        <stop offset="0" style="stop-color: var(--accent); stop-opacity: 0" />
        <stop offset="1" style="stop-color: var(--accent); stop-opacity: 0.55" />
      </linearGradient>
    </defs>

    <circle cx="50" cy="50" r="46" style="fill: rgba(var(--paper-rgb), 0.55)" :stroke="`url(#${ringId})`" stroke-width="2.5" />
    <circle cx="50" cy="50" r="31" fill="none" :stroke="`url(#${ringId})`" stroke-width="1.2" opacity="0.55" />
    <circle cx="50" cy="50" r="16" fill="none" :stroke="`url(#${ringId})`" stroke-width="1.2" opacity="0.4" />
    <path d="M50 6 V94 M6 50 H94" style="stroke: var(--accent)" stroke-width="0.8" opacity="0.25" />

    <g class="sweep">
      <path d="M50 50 L96 50 A46 46 0 0 0 89.84 27 Z" :fill="`url(#${sweepId})`" />
      <line x1="50" y1="50" x2="96" y2="50" style="stroke: var(--accent)" stroke-width="1.6" stroke-linecap="round" />
    </g>

    <!-- 回波点 -->
    <circle class="blip b1" cx="68" cy="72" r="2.4" style="fill: var(--accent-2)" />
    <circle class="blip b2" cx="30" cy="34" r="2" style="fill: var(--accent)" />
    <circle cx="50" cy="50" r="3.2" style="fill: var(--accent)" />
  </svg>
</template>

<style scoped>
.radar {
  display: block;
  flex: none;
  filter: drop-shadow(0 4px 10px rgba(var(--accent-rgb), 0.25));
}
.sweep {
  transform-origin: 50px 50px;
}
.spin .sweep {
  animation: radar-spin 4s linear infinite;
}
.spin .blip {
  animation: blip 4s ease-out infinite;
}
.spin .b2 {
  animation-delay: 2.2s;
}
@keyframes radar-spin {
  to {
    transform: rotate(360deg);
  }
}
@keyframes blip {
  0%,
  100% {
    opacity: 0.15;
  }
  12% {
    opacity: 1;
  }
}
</style>
