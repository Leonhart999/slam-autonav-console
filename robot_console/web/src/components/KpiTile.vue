<!-- 监控页顶部的指标卡：标签、大号数字（可带 /总数）、说明、比例条
     tone：整张卡的状态色（ok 全部正常、warn 部分异常、idle 没有运行、accent 普通信息） -->
<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  label: string
  value: number
  total?: number | null
  sub: string
  tone?: 'ok' | 'warn' | 'bad' | 'idle' | 'accent'
}>()

const ratio = computed(() => (props.total ? Math.min(1, props.value / props.total) : 0))
</script>

<template>
  <div class="kpi glass" :class="tone ?? 'accent'">
    <div class="top">
      <span class="label">{{ label }}</span>
      <span class="led" />
    </div>
    <div class="value mono">
      {{ value }}<span v-if="total != null" class="total">/{{ total }}</span>
    </div>
    <div class="sub">{{ sub }}</div>
    <div v-if="total != null" class="track"><i :style="{ width: `${ratio * 100}%` }" /></div>
  </div>
</template>

<style scoped>
.kpi {
  --tone: var(--accent);
  position: relative;
  min-width: 0;
  padding: 14px 18px 16px;
  border-radius: var(--radius);
  overflow: hidden;
}
/* 右上角一团状态色的柔光 */
.kpi::after {
  content: '';
  position: absolute;
  top: -40px;
  right: -40px;
  width: 120px;
  height: 120px;
  border-radius: 50%;
  background: radial-gradient(circle, color-mix(in srgb, var(--tone) 22%, transparent), transparent 70%);
  pointer-events: none;
}
.ok { --tone: var(--ok); }
.warn { --tone: var(--warn); }
.bad { --tone: var(--bad); }
.idle { --tone: var(--idle); }
.top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.label {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: var(--ink-2);
}
.led {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--tone);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--tone) 18%, transparent), 0 0 10px var(--tone);
}
.value {
  margin-top: 8px;
  font-size: 30px;
  font-weight: 600;
  line-height: 1.1;
  letter-spacing: -0.02em;
}
.total {
  margin-left: 2px;
  font-size: 18px;
  font-weight: 500;
  color: var(--ink-3);
}
.sub {
  margin-top: 4px;
  font-size: 12px;
  color: var(--ink-3);
}
.track {
  height: 3px;
  margin-top: 12px;
  border-radius: 3px;
  background: var(--hairline);
  overflow: hidden;
}
.track i {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: linear-gradient(90deg, color-mix(in srgb, var(--tone) 55%, #fff), var(--tone));
  box-shadow: 0 0 8px var(--tone);
  transition: width 0.6s ease;
}
</style>
