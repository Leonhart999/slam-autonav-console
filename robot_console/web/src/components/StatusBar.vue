<!-- 底部状态栏：所有页面常驻。重启、中断与急停按钮在任何页面都能按 -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import { isReadOnlyPreview } from '@/http'
import { pick } from '@/i18n'
import { errorText } from '@/messages'
import { useStatusStore } from '@/stores/status'

const RESTARTED_KEY = 'robot_console_restarted'

const { t } = useI18n()
const store = useStatusStore()
const preview = isReadOnlyPreview()
const s = computed(() => store.status)
const restarting = ref(false)
const interrupting = ref(false)
let restartFromBoot: string | null | undefined = null
let sawDisconnect = false
let restartTimer: number | undefined

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

function clearRestartWatch() {
  restarting.value = false
  restartFromBoot = null
  sawDisconnect = false
  if (restartTimer !== undefined) {
    window.clearTimeout(restartTimer)
    restartTimer = undefined
  }
}

function finishRestart() {
  clearRestartWatch()
  try { window.sessionStorage.setItem(RESTARTED_KEY, '1') } catch { /* ignore */ }
  // Reload once so enabled/updated plugins, themes and backend-backed views start cleanly on the fresh backend.
  window.location.reload()
}

function setRestartWatchTimeout() {
  if (restartTimer !== undefined) window.clearTimeout(restartTimer)
  restartTimer = window.setTimeout(() => {
    clearRestartWatch()
    ElMessage.error(t('consoleRestart.timeout'))
  }, 120000)
}

onMounted(() => {
  try {
    if (window.sessionStorage.getItem(RESTARTED_KEY)) {
      window.sessionStorage.removeItem(RESTARTED_KEY)
      ElMessage.success(t('consoleRestart.done'))
    }
  } catch { /* ignore */ }
})

watch(
  () => [store.connected, s.value?.boot_id, s.value?.restarting] as const,
  ([connected, bootId, serverRestarting]) => {
    if (!restarting.value && serverRestarting) {
      restarting.value = true
      restartFromBoot = bootId ?? null
      sawDisconnect = false
      setRestartWatchTimeout()
    }
    if (!restarting.value) return
    if (!connected) {
      sawDisconnect = true
      store.reconnectSoon()
      return
    }
    if (bootId && restartFromBoot && bootId !== restartFromBoot) {
      finishRestart()
    } else if (sawDisconnect && (!restartFromBoot || !bootId) && !serverRestarting) {
      finishRestart()
    }
  },
)

onUnmounted(() => {
  if (restartTimer !== undefined) window.clearTimeout(restartTimer)
})

async function restartConsole() {
  if (restarting.value) return
  restarting.value = true
  restartFromBoot = s.value?.boot_id ?? null
  sawDisconnect = false
  setRestartWatchTimeout()
  try {
    const r = await api.restartConsole()
    restartFromBoot = r.boot_id ?? restartFromBoot
    store.reconnectSoon()
    // Re-check immediately in case a new backend already pushed a different boot_id while the POST resolved.
    if (store.connected && s.value?.boot_id && restartFromBoot && s.value.boot_id !== restartFromBoot) {
      finishRestart()
      return
    }
    const parts = [t('consoleRestart.restarting')]
    if (r.stopped_stacks.length) {
      const labels = r.stopped_stacks.map((n) => {
        const st = s.value?.stacks.find((x) => x.name === n)
        return st ? pick(st.label) : n
      })
      parts.push(t('consoleRestart.stoppedStacks', { names: labels.join(t('estop.listSep')) }))
    }
    ElMessage.warning(parts.join(t('estop.sep')))
  } catch (e) {
    clearRestartWatch()
    ElMessage.error(t('consoleRestart.failed', { reason: errorText(e) }))
  }
}

async function interruptAll() {
  if (interrupting.value || restarting.value) return
  interrupting.value = true
  try {
    const r = await api.interruptAll()
    const bgCount = r.interrupted_tasks.length + r.cancelled_plugin_jobs.length
    if (!r.stopped_stacks.length && !r.cancelled_actions.length && !bgCount) {
      ElMessage.info(t('consoleInterrupt.idle'))
      return
    }
    const parts = [t('consoleInterrupt.done')]
    if (r.cancelled_actions.length) parts.push(t('estop.goalsCancelled'))
    if (r.stopped_stacks.length) {
      const labels = r.stopped_stacks.map((n) => {
        const st = s.value?.stacks.find((x) => x.name === n)
        return st ? pick(st.label) : n
      })
      parts.push(t('consoleInterrupt.stoppedStacks', { names: labels.join(t('estop.listSep')) }))
    }
    if (bgCount) parts.push(t('consoleInterrupt.tasksInterrupted', { n: bgCount }))
    ElMessage.warning(parts.join(t('estop.sep')))
  } catch (e) {
    ElMessage.error(t('consoleInterrupt.failed', { reason: errorText(e) }))
  } finally {
    interrupting.value = false
  }
}

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
  <footer class="bar glass" :class="{ offline: !store.connected && !restarting, restarting: restarting && !store.connected }">
    <div class="link" :class="store.connected ? 'ok' : restarting ? 'warn' : 'bad'">
      <span class="beacon" />
      <span>{{
        preview ? t('app.preview')
        : restarting && !store.connected ? t('status.restartingBackend')
        : store.connected ? t('status.connected')
        : t('status.reconnecting')
      }}</span>
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
    <div class="actions">
      <button
        class="action-btn restart" :class="{ busy: restarting }" :disabled="preview || !store.connected || restarting"
        :title="t('consoleRestart.title')" @click="restartConsole"
      >
        <!-- All three icons share one 24px grid: outer shape about r=9 around (12,12), 2px round strokes -->
        <svg
          viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"
          fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
        >
          <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" />
          <path d="M21 3v5h-5" />
        </svg>
        <span class="label">{{ t('status.restart') }}</span>
      </button>
      <button
        class="action-btn interrupt" :class="{ busy: interrupting }"
        :disabled="preview || !store.connected || restarting || interrupting"
        :title="t('consoleInterrupt.title')" @click="interruptAll"
      >
        <svg
          viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"
          fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
        >
          <circle cx="12" cy="12" r="9" />
          <rect class="stop-core" x="9" y="9" width="6" height="6" rx="1" fill="currentColor" stroke="none" />
        </svg>
        <span class="label">{{ t('status.interrupt') }}</span>
      </button>
      <button class="action-btn estop" :disabled="preview || !store.connected" @click="estop">
        <svg
          viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"
          fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
        >
          <path d="M8.27 3h7.46L21 8.27v7.46L15.73 21H8.27L3 15.73V8.27z" />
          <path d="M12 7.6v4.8" stroke-width="2.2" />
          <circle cx="12" cy="16.2" r="1.25" fill="currentColor" stroke="none" />
        </svg>
        <span class="label">{{ t('status.estop') }}</span>
      </button>
    </div>
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
  background: var(--offline-bg);
  border-color: var(--offline-edge);
}
.bar.restarting {
  background: var(--note-bg);
  border-color: var(--note-edge);
}
/* When a long English readout (offline + stopped clock) meets a narrow window, the text labels give way with an
   ellipsis instead of pushing the action buttons out of the bar. The connection text gives way first (its beacon
   color still tells the state); the readout labels only shrink a little, so they never collapse to one letter */
.link {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  font-weight: 500;
  white-space: nowrap;
}
.link > span:last-child {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.link.ok { --c: var(--ok); }
.link.warn { --c: var(--warn); color: var(--note-ink); }
.link.bad { --c: var(--bad); color: var(--bad); }
/* 连接指示灯：外圈一直向外扩散 */
.beacon {
  position: relative;
  flex: none;
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
  flex: none;
  width: 1px;
  height: 22px;
  background: var(--hairline);
}
.cell {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-shrink: 0.15;
  white-space: nowrap;
}
/* CPU / memory labels are short; keep them whole */
.cell:has(.gauge) {
  flex-shrink: 0;
}
.k {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12px;
  color: var(--ink-3);
}
.v {
  flex: none;
  font-weight: 600;
  color: var(--ink);
}
.num {
  min-width: 3ch;
  text-align: right;
}
.gauge {
  --c: var(--ok);
  flex: none;
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

/* Action cluster: three equal-size buttons. Each sits in its own 1fr grid track, so all three take the width of
   the widest label (Chinese labels are all two characters; English Restart / Abort / E-STOP are close). */
.actions {
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: minmax(var(--action-min, 96px), 1fr);
  gap: 8px;
  flex: none;
}

/* Shared tactile surface: tone gradient + faint hazard stripes, crisp 1px outer ring, top specular line and a
   tone-colored drop shadow. The surface is opaque, so it reads the same on the light and the dark theme. Every
   tone keeps white text at >= 4.5:1 (WCAG AA) on its lightest stop, even under a stripe band. */
.action-btn {
  --tone-a: #d81b45;
  --tone-b: #d02020;
  --tone-c: #a81b1b;
  --tone-rgb: 208, 32, 32;
  --tone-edge: 168, 27, 27;
  --tone-focus: #f43f5e;
  --ring: rgba(var(--tone-edge), 0.45);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  height: 40px;
  min-width: 0;
  padding: 0 16px;
  border: none;
  border-radius: 12px;
  color: #fff;
  font: inherit;
  font-weight: 700;
  line-height: 1;
  letter-spacing: 0.08em;
  white-space: nowrap;
  cursor: pointer;
  background:
    repeating-linear-gradient(-45deg, rgba(255, 255, 255, 0.07) 0 6px, transparent 6px 12px),
    linear-gradient(135deg, var(--tone-a), var(--tone-b) 55%, var(--tone-c));
  background-color: var(--tone-b);
  box-shadow:
    0 0 0 1px var(--ring),
    inset 0 1px 0 rgba(255, 255, 255, 0.35),
    inset 0 -1px 0 rgba(0, 0, 0, 0.18),
    0 10px 22px -10px rgba(var(--tone-rgb), 0.9);
  text-shadow: 0 1px 1px rgba(0, 0, 0, 0.22);
  transition: transform 0.12s, filter 0.2s, box-shadow 0.2s, opacity 0.2s;
}
/* On the dark theme a tone-colored ring disappears into the panel; a faint light ring keeps the edge crisp */
:global(html.dark) .action-btn {
  --ring: rgba(255, 255, 255, 0.16);
}
.action-btn svg {
  flex: none;
  filter: drop-shadow(0 1px 1px rgba(0, 0, 0, 0.22));
}
/* letter-spacing also trails the last glyph; pull it back so the label is optically centred */
.action-btn .label {
  margin-right: -0.08em;
}
/* Hover: glow ring + brighter specular line + richer color. No brightness() filter: lifting the lightest stops
   by 8% would drop white text below 4.5:1; saturate() keeps luminance about the same. */
.action-btn:hover:not(:disabled) {
  filter: saturate(1.15);
  box-shadow:
    0 0 0 1px var(--ring),
    0 0 0 4px rgba(var(--tone-rgb), 0.2),
    inset 0 1px 0 rgba(255, 255, 255, 0.5),
    inset 0 -1px 0 rgba(0, 0, 0, 0.18),
    0 12px 26px -10px rgba(var(--tone-rgb), 1);
}
.action-btn:active:not(:disabled) {
  transform: scale(0.97);
  box-shadow:
    0 0 0 1px var(--ring),
    inset 0 2px 6px rgba(0, 0, 0, 0.28),
    0 6px 14px -10px rgba(var(--tone-rgb), 0.9);
}
.action-btn:focus-visible {
  outline: 2px solid var(--tone-focus);
  outline-offset: 3px;
}
.action-btn:disabled {
  cursor: not-allowed;
  filter: grayscale(0.85);
  opacity: 0.5;
  box-shadow: 0 0 0 1px var(--ring), inset 0 1px 0 rgba(255, 255, 255, 0.25);
}
/* Working (restart in progress, abort request in flight): the button is disabled but stays in color and
   breathes, so it reads as "busy" rather than "unavailable" */
.action-btn.busy:disabled {
  cursor: progress;
  filter: none;
  opacity: 0.85;
  animation: busy-glow 1.1s ease-in-out infinite alternate;
}
@keyframes busy-glow {
  from {
    box-shadow: 0 0 0 1px var(--ring), 0 0 0 0 rgba(var(--tone-rgb), 0), inset 0 1px 0 rgba(255, 255, 255, 0.35);
  }
  to {
    box-shadow: 0 0 0 1px var(--ring), 0 0 0 4px rgba(var(--tone-rgb), 0.28), inset 0 1px 0 rgba(255, 255, 255, 0.35);
  }
}

/* Restart: cool cyan-to-blue, the console's HUD accent family */
.restart {
  --tone-a: #0b6a8c;
  --tone-b: #0a5f9c;
  --tone-c: #1d4ed8;
  --tone-rgb: 10, 95, 156;
  --tone-edge: 29, 78, 216;
  --tone-focus: var(--accent);
}
.restart.busy svg {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Abort: burnt amber to deep orange (milder than E-STOP, still a warning) */
.interrupt {
  --tone-a: #a94e0b;
  --tone-b: #b93e0c;
  --tone-c: #9a3412;
  --tone-rgb: 185, 62, 12;
  --tone-edge: 154, 52, 18;
  --tone-focus: var(--warn);
}
.interrupt.busy .stop-core {
  transform-box: fill-box;
  transform-origin: center;
  animation: core-pulse 0.7s ease-in-out infinite alternate;
}
@keyframes core-pulse {
  to { transform: scale(0.6); opacity: 0.65; }
}

/* E-STOP: crimson to deep red, uses the base tone above */

@media (max-width: 1280px) {
  .bar {
    gap: 12px;
    padding: 0 6px 0 12px;
    font-size: 12.5px;
  }
  .gauge {
    width: 40px;
  }
  .actions {
    --action-min: 84px;
    gap: 6px;
  }
  .action-btn {
    gap: 6px;
    padding: 0 12px;
  }
}
</style>
