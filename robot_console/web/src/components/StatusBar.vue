<!-- 底部状态栏：所有页面常驻。急停按钮在任何页面都能按 -->
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import { pick } from '@/i18n'
import { errorText } from '@/messages'
import { useStatusStore } from '@/stores/status'

const { t } = useI18n()
const store = useStatusStore()
const s = computed(() => store.status)

const simTime = computed(() => {
  const c = s.value?.clock
  if (!c) return t('status.noClock')
  return `${c.sim_time.toFixed(1)} s${c.stale ? t('status.clockStopped') : ''}`
})
const running = computed(() => s.value?.stacks.filter((x) => x.state === 'running').length ?? 0)

// CPU、内存小仪表：超过 70% 变黄，超过 90% 变红
const meters = computed(() => {
  if (!s.value) return []
  const level = (v: number) => (v > 90 ? 'bad' : v > 70 ? 'warn' : 'ok')
  return [
    { key: 'cpu', label: t('status.cpu'), value: s.value.resources.cpu },
    { key: 'mem', label: t('status.mem'), value: s.value.resources.mem },
  ].map((m) => ({ ...m, level: level(m.value) }))
})

async function estop() {
  try {
    const r = await api.estop()
    const parts = [t('estop.zeroSent')]
    if (r.cancelled_actions.length) parts.push(t('estop.goalsCancelled'))
    if (r.stopped_stacks.length) {
      const labels = r.stopped_stacks.map((n) => {
        const st = s.value?.stacks.find((x) => x.name === n)
        return st ? pick(st.label) : n
      })
      parts.push(t('estop.stacksStopped', { names: labels.join(t('estop.listSep')) }))
    }
    ElMessage.warning(parts.join(t('estop.sep')))
  } catch (e) {
    ElMessage.error(t('estop.failed', { reason: errorText(e) }))
  }
}
</script>

<template>
  <footer class="bar glass" :class="{ offline: !store.connected }">
    <div class="link" :class="store.connected ? 'ok' : 'bad'">
      <span class="beacon" />
      <span>{{ store.connected ? t('status.connected') : t('status.reconnecting') }}</span>
    </div>
    <span class="sep" />
    <div class="cell">
      <span class="k">{{ t('status.simTime') }}</span>
      <span class="v mono">{{ simTime }}</span>
    </div>
    <div class="cell">
      <span class="k">{{ t('status.running') }}</span>
      <span class="v mono">{{ running }}</span>
    </div>
    <div v-for="m in meters" :key="m.key" class="cell">
      <span class="k">{{ m.label }}</span>
      <span class="gauge" :class="m.level"><i :style="{ width: `${Math.min(100, m.value)}%` }" /></span>
      <span class="v mono num">{{ m.value.toFixed(0) }}%</span>
    </div>
    <span class="spacer" />
    <button class="estop" :disabled="!store.connected" @click="estop">
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
        <path
          d="M8.1 2h7.8L22 8.1v7.8L15.9 22H8.1L2 15.9V8.1z" fill="none"
          stroke="currentColor" stroke-width="2" stroke-linejoin="round"
        />
        <path d="M12 7v6" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" />
        <circle cx="12" cy="16.6" r="1.4" fill="currentColor" />
      </svg>
      <span>{{ t('status.estop') }}</span>
    </button>
  </footer>
</template>

<style scoped>
.bar {
  display: flex;
  align-items: center;
  gap: 22px;
  min-height: 54px;
  padding: 0 8px 0 18px;
  border-radius: var(--radius);
  font-size: 13px;
  transition: background 0.3s, border-color 0.3s;
}
.bar.offline {
  background: rgba(254, 242, 242, 0.75);
  border-color: rgba(252, 165, 165, 0.7);
}
.link {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 500;
}
.link.ok { --c: var(--ok); }
.link.bad { --c: var(--bad); color: var(--bad); }
/* 连接指示灯：外圈一直向外扩散 */
.beacon {
  position: relative;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--c);
}
.beacon::after {
  content: '';
  position: absolute;
  inset: -4px;
  border-radius: 50%;
  border: 1.5px solid var(--c);
  animation: ripple 2s ease-out infinite;
}
@keyframes ripple {
  from { transform: scale(0.5); opacity: 0.9; }
  to { transform: scale(1.6); opacity: 0; }
}
.sep {
  width: 1px;
  height: 22px;
  background: var(--hairline);
}
.cell {
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}
.k {
  font-size: 12px;
  color: var(--ink-3);
}
.v {
  font-weight: 600;
  color: var(--ink);
}
.num {
  min-width: 3ch;
  text-align: right;
}
.gauge {
  --c: var(--ok);
  width: 56px;
  height: 4px;
  border-radius: 4px;
  background: var(--hairline);
  overflow: hidden;
}
.gauge.warn { --c: var(--warn); }
.gauge.bad { --c: var(--bad); }
.gauge i {
  display: block;
  height: 100%;
  border-radius: 4px;
  background: var(--c);
  box-shadow: 0 0 6px var(--c);
  transition: width 0.5s ease;
}
.spacer {
  flex: 1;
}

/* 急停：红色渐变 + 淡淡的警示斜纹 */
.estop {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 40px;
  padding: 0 22px 0 18px;
  border: none;
  border-radius: 12px;
  color: #fff;
  font: inherit;
  font-weight: 700;
  letter-spacing: 0.08em;
  cursor: pointer;
  background:
    repeating-linear-gradient(-45deg, rgba(255, 255, 255, 0.09) 0 6px, transparent 6px 12px),
    linear-gradient(135deg, #f43f5e, #dc2626 55%, #b91c1c);
  box-shadow: 0 0 0 1px rgba(185, 28, 28, 0.35), 0 1px 0 rgba(255, 255, 255, 0.35) inset,
    0 10px 22px -10px rgba(220, 38, 38, 0.9);
  transition: transform 0.12s, filter 0.2s, box-shadow 0.2s;
}
.estop:hover:not(:disabled) {
  filter: brightness(1.08);
  box-shadow: 0 0 0 4px rgba(244, 63, 94, 0.18), 0 1px 0 rgba(255, 255, 255, 0.35) inset,
    0 12px 26px -10px rgba(220, 38, 38, 1);
}
.estop:active:not(:disabled) {
  transform: scale(0.97);
}
.estop:focus-visible {
  outline: 2px solid #f43f5e;
  outline-offset: 3px;
}
.estop:disabled {
  cursor: not-allowed;
  filter: grayscale(0.85);
  opacity: 0.5;
}
</style>
