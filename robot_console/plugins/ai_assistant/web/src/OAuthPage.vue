<!-- OAuth tab: sign in to subscription services through the embedded CPA engine (CLIProxyAPI). Top bar: engine state
     (download / start / logs) and the login proxy; then the providers by sign-in method; then the connected accounts,
     with one action that makes the engine a model service of the assistant. -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElButton, ElInput, ElSwitch, ElTooltip } from 'element-plus'
import {
  cpaAction, cpaInstall, cpaLogs, cpaSettings, cpaStatus, getProviders, listCredentials, refreshCredential, removeCredential,
  setCredentialDisabled, useCpa, verifyCredential, type Credential, type EngineStatus,
} from './api'
import { BROWSER, DEVICE, KIMI, VERTEX_COLOR, cardOf, type ProviderCard } from './oauthProviders'
import { avatarColor, initial } from './presets'
import { compact } from './format'
import { RC, t } from './sdk'
import Icon from './Icon.vue'
import OAuthFlowDialog from './OAuthFlowDialog.vue'
import VertexDialog from './VertexDialog.vue'

const emit = defineEmits<{ goServices: [] }>()
const { GlassPanel } = RC().ui

const engine = ref<EngineStatus | null>(null)
const engineError = ref('')
const creds = ref<Credential[]>([])
const credError = ref('')
const credLoading = ref(false)
const engineOpen = ref(false)
const proxyOpen = ref(false)
const proxyText = ref('')
const proxySaving = ref(false)
const logs = ref<string[]>([])
const acting = ref('')                   // engine action running
const rowBusy = ref<Record<string, string>>({})
const confirmDel = ref('')
const inUse = ref(false)                 // the CPA profile is the active model service
const using = ref(false)
const flowOpen = ref(false)
const flowCard = ref<ProviderCard | 'siwc' | null>(null)
const flowTarget = ref('')
const vertexOpen = ref(false)
let poll = 0
let delTimer = 0

const state = computed(() => engine.value?.state ?? 'missing')
const ready = computed(() => state.value === 'ready')
const progress = computed(() => {
  const p = engine.value?.progress
  return p && p.total ? Math.round((p.done / p.total) * 100) : null
})
const codexCreds = computed(() => creds.value.filter((c) => cardOf(c.provider || c.type)?.id === 'codex'))
const countBy = computed(() => {
  const out: Record<string, number> = {}
  for (const c of creds.value) {
    const card = cardOf(c.provider || c.type)
    if (card) out[card.id] = (out[card.id] ?? 0) + 1
  }
  return out
})
const proxyValid = computed(() => !proxyText.value.trim() || /^(https?|socks5h?):\/\/[^\s/]+/.test(proxyText.value.trim()))

async function loadEngine() {
  try {
    engine.value = await cpaStatus()
    engineError.value = ''
  } catch (e) {
    engineError.value = RC().errorText(e)
  }
  schedule()
}

/** poll faster while something is changing */
function schedule() {
  window.clearTimeout(poll)
  const s = state.value
  const fast = s === 'installing' || s === 'starting'
  poll = window.setTimeout(async () => {
    const before = state.value
    await loadEngine()
    if (before !== 'ready' && state.value === 'ready') void loadCreds()
  }, fast ? 1000 : 8000)
}

async function loadCreds() {
  if (!ready.value) {
    creds.value = []
    return
  }
  credLoading.value = true
  try {
    creds.value = (await listCredentials()).items
    credError.value = ''
  } catch (e) {
    credError.value = RC().errorText(e)
  } finally {
    credLoading.value = false
  }
  await checkInUse()
}

async function checkInUse() {
  try {
    const l = await getProviders()
    const active = l.profiles.find((p) => p.id === l.active)
    inUse.value = !!active && (!!active.managed || (/^http:\/\/127\.0\.0\.1:\d+\/v1$/.test(active.base_url) && active.name === 'CPA (OAuth)'))
  } catch {
    inUse.value = false
  }
}

async function engineDo(action: 'install' | 'start' | 'stop' | 'restart') {
  acting.value = action
  try {
    engine.value = action === 'install' ? await cpaInstall() : await cpaAction(action)
    if (action !== 'stop') void loadCreds()
  } catch (e) {
    RC().ui.ElMessage.error(RC().errorText(e))
  } finally {
    acting.value = ''
    schedule()
  }
}

async function openLogs() {
  engineOpen.value = !engineOpen.value
  if (engineOpen.value) await reloadLogs()
}
async function reloadLogs() {
  try {
    logs.value = (await cpaLogs(200)).lines
  } catch (e) {
    logs.value = [RC().errorText(e)]
  }
}

async function setAutostart(v: string | number | boolean) {
  try {
    engine.value = await cpaSettings({ autostart: !!v })
  } catch (e) {
    RC().ui.ElMessage.error(RC().errorText(e))
  }
}

function openProxy() {
  proxyText.value = engine.value?.proxy_url ?? ''
  proxyOpen.value = !proxyOpen.value
}
async function saveProxy() {
  if (!proxyValid.value) return
  // unchanged (also the masked form of a proxy with a password): nothing to save, no engine restart
  if (proxyText.value.trim() === (engine.value?.proxy_url ?? '')) {
    proxyOpen.value = false
    return
  }
  proxySaving.value = true
  try {
    engine.value = await cpaSettings({ proxy_url: proxyText.value.trim() })
    proxyOpen.value = false
    RC().ui.ElMessage.success(t('oauth.proxy.saved'))
  } catch (e) {
    RC().ui.ElMessage.error(RC().errorText(e))
  } finally {
    proxySaving.value = false
  }
}

function connect(card: ProviderCard, target = '') {
  if (!ready.value) return
  flowCard.value = card
  flowTarget.value = target
  flowOpen.value = true
}
function bindSiwc(target = '') {
  if (!ready.value) return
  flowCard.value = 'siwc'
  flowTarget.value = target
  flowOpen.value = true
}

async function onFlowDone() {
  await loadCreds()
  // Only sync the model list without changing the active provider when the user is currently using CPA.
  if (inUse.value) void useInAssistant({ activate: false, quiet: true })
}

async function rowDo(c: Credential, action: 'refresh' | 'verify' | 'toggle' | 'remove') {
  if (rowBusy.value[c.name]) return
  if (action === 'remove' && confirmDel.value !== c.name) {
    confirmDel.value = c.name
    window.clearTimeout(delTimer)
    delTimer = window.setTimeout(() => (confirmDel.value = ''), 3000)
    return
  }
  confirmDel.value = ''
  rowBusy.value = { ...rowBusy.value, [c.name]: action }
  try {
    if (action === 'refresh') {
      await refreshCredential(c.name)
      RC().ui.ElMessage.success(t('oauth.cred.refreshed'))
    } else if (action === 'verify') {
      await verifyCredential(c.name)
      RC().ui.ElMessage.success(t('oauth.cred.verified'))
    } else if (action === 'toggle') {
      await setCredentialDisabled(c.name, !c.disabled)
    } else {
      await removeCredential(c.name)
    }
    await loadCreds()
  } catch (e) {
    RC().ui.ElMessage.error(RC().errorText(e))
  } finally {
    const next = { ...rowBusy.value }
    delete next[c.name]
    rowBusy.value = next
  }
}

/** activate: make CPA the service in use (the "use" button); false only refreshes its model list, so a service the
 *  user switched to in the meantime stays in use */
async function useInAssistant({ activate, quiet = false }: { activate: boolean; quiet?: boolean }) {
  using.value = true
  try {
    const r = await useCpa(activate)
    if (activate) inUse.value = true
    else void checkInUse()
    window.dispatchEvent(new Event('aia:providers-changed'))
    if (!quiet && r.profile) {
      RC().ui.ElMessage.success(activate ? t('oauth.use.done', { n: r.profile.models.length })
        : t('oauth.use.refreshed', { n: r.profile.models.length }))
    }
  } catch (e) {
    if (!quiet) RC().ui.ElMessage.error(RC().errorText(e))
  } finally {
    using.value = false
  }
}

function credName(c: Credential) {
  return String(c.email || c.account || c.label || c.name)
}
function credProvider(c: Credential) {
  const card = cardOf(c.provider || c.type)
  if (card) return card.name + (card.domain ? ` · ${card.domain}` : '')
  return String(c.provider || c.type || '')
}
function credColor(c: Credential) {
  const card = cardOf(c.provider || c.type)
  return avatarColor(card?.color ?? (String(c.type) === 'vertex' ? VERTEX_COLOR : ''), credName(c))
}
type CredState = 'ok' | 'relogin' | 'off' | 'down' | 'refreshing'
function credState(c: Credential): CredState {
  if (rowBusy.value[c.name] === 'refresh') return 'refreshing'
  if (c.disabled) return 'off'
  if (c.requires_sign_in || c.next_action === 'sign_in') return 'relogin'
  if (c.unavailable || String(c.status || '').toLowerCase() === 'error') return 'down'
  return 'ok'
}

onMounted(async () => {
  await loadEngine()
  void loadCreds()
})
onUnmounted(() => {
  window.clearTimeout(poll)
  window.clearTimeout(delTimer)
})
</script>

<template>
  <div class="aio">
    <!-- top bar: engine and proxy -->
    <div class="aio-bar">
      <span class="aio-title">{{ t('oauth.title') }}</span>
      <span class="aip-grow" />
      <button type="button" class="aio-pill" :class="{ open: proxyOpen }" @click="openProxy">
        <Icon name="link" />{{ engine?.proxy_url ? t('oauth.proxy.pillSet', { host: engine.proxy_url.replace(/^\w+:\/\//, '') }) : t('oauth.proxy.pill') }}
      </button>
      <button type="button" class="aio-pill" :class="[`st-${state}`, { open: engineOpen }]" @click="openLogs">
        <span class="aio-led" />{{ t(`oauth.engine.${state}`) }}<template v-if="state === 'installing' && progress !== null"> {{ progress }}%</template>
      </button>
    </div>

    <p class="aip-help aio-intro">{{ t('oauth.intro') }}</p>
    <transition name="aio-fold">
      <div v-if="proxyOpen" class="aio-sheet">
        <p class="aip-help">{{ t('oauth.proxy.help') }}</p>
        <div class="aip-row">
          <el-input v-model="proxyText" size="small" class="mono" :placeholder="t('oauth.proxy.placeholder')" @keyup.enter="saveProxy" />
          <el-button size="small" type="primary" :disabled="!proxyValid" :loading="proxySaving" @click="saveProxy">{{ t('editor.save') }}</el-button>
        </div>
        <span v-if="!proxyValid" class="aip-error">{{ t('oauth.proxy.invalid') }}</span>
      </div>
    </transition>
    <transition name="aio-fold">
      <div v-if="engineOpen" class="aio-sheet">
        <dl class="aio-kv">
          <dt>{{ t('oauth.engine.version') }}</dt><dd class="mono">{{ engine?.version || '—' }}</dd>
          <dt>{{ t('oauth.engine.uptime') }}</dt>
          <dd class="mono">{{ engine?.uptime_s ? t('oauth.engine.uptimeValue', { m: Math.floor(engine.uptime_s / 60) }) : '—' }}</dd>
          <dt>{{ t('oauth.engine.accounts') }}</dt><dd class="mono">{{ creds.length }}</dd>
          <dt>{{ t('oauth.engine.autostart') }}</dt>
          <dd><el-switch :model-value="engine?.autostart ?? true" size="small" :disabled="state === 'missing'" @update:model-value="setAutostart" /></dd>
        </dl>
        <div class="aip-row">
          <el-button v-if="state === 'ready' || state === 'starting'" size="small" :loading="acting === 'restart'" @click="engineDo('restart')">
            <Icon name="refresh" />{{ t('oauth.engine.restart') }}
          </el-button>
          <el-button v-if="state === 'ready' || state === 'starting'" size="small" :loading="acting === 'stop'" @click="engineDo('stop')">
            <Icon name="power" />{{ t('oauth.engine.stop') }}
          </el-button>
          <span class="aip-grow" />
          <el-button size="small" text @click="reloadLogs"><Icon name="refresh" />{{ t('oauth.engine.reloadLogs') }}</el-button>
        </div>
        <pre class="aio-log mono">{{ logs.length ? logs.join('\n') : t('oauth.engine.noLogs') }}</pre>
      </div>
    </transition>

    <!-- engine not running: the one thing to do -->
    <div v-if="engineError" class="aio-banner error">{{ engineError }}</div>
    <div v-else-if="!ready" class="aio-banner" :class="`st-${state}`">
      <div class="aio-banner-text">
        <span class="aio-banner-title">{{ t(`oauth.banner.${state}.title`) }}</span>
        <span class="aip-help">{{ t(`oauth.banner.${state}.text`, { error: engine?.error?.detail || engine?.error?.code || '' }) }}</span>
        <div v-if="state === 'installing' && progress !== null" class="aio-progress"><i :style="{ width: `${progress}%` }" /></div>
      </div>
      <el-button v-if="state === 'missing'" type="primary" :loading="acting === 'install'" @click="engineDo('install')">
        <Icon name="download" />{{ t('oauth.engine.download') }}
      </el-button>
      <el-button v-else-if="state === 'stopped'" type="primary" :loading="acting === 'start'" @click="engineDo('start')">
        <Icon name="power" />{{ t('oauth.engine.start') }}
      </el-button>
      <template v-else-if="state === 'error'">
        <el-button text @click="openLogs">{{ t('oauth.engine.logs') }}</el-button>
        <el-button type="primary" :loading="acting === 'restart'" @click="engineDo(engine?.version ? 'restart' : 'install')">
          <Icon name="refresh" />{{ t(engine?.version ? 'oauth.engine.restart' : 'oauth.engine.retry') }}
        </el-button>
      </template>
    </div>

    <!-- providers -->
    <GlassPanel :title="t('oauth.sections.providers')">
      <div class="aio-sections" :class="{ off: !ready }">
        <section class="aio-sec">
          <div class="aio-sec-head"><span>{{ t('oauth.sections.kimi') }}</span><span class="aip-help">{{ t('oauth.sections.kimiHelp') }}</span></div>
          <div class="aio-kimi">
            <div v-for="c in KIMI" :key="c.id" class="aio-kimi-site">
              <span class="aip-avatar" :style="{ background: avatarColor(c.color, c.name) }">{{ initial(c.name) }}</span>
              <span class="aip-item-text">
                <span class="aip-item-name">{{ t(`oauth.kimi.${c.id}`) }}<span v-if="countBy[c.id]" class="aip-tag on">{{ t('oauth.connected', { n: countBy[c.id] }) }}</span></span>
                <span class="aip-item-meta mono">{{ c.domain }}</span>
              </span>
              <el-button size="small" :disabled="!ready" @click="connect(c)"><Icon name="plus" />{{ t('oauth.connect') }}</el-button>
            </div>
          </div>
        </section>

        <section v-for="sec in [{ key: 'device', list: DEVICE }, { key: 'browser', list: BROWSER }]" :key="sec.key" class="aio-sec">
          <div class="aio-sec-head"><span>{{ t(`oauth.sections.${sec.key}`) }}</span><span class="aip-help">{{ t(`oauth.sections.${sec.key}Help`) }}</span></div>
          <div class="aio-grid">
            <div v-for="c in sec.list" :key="c.id" class="aio-card">
              <span class="aip-avatar" :style="{ background: avatarColor(c.color, c.name) }">{{ initial(c.name) }}</span>
              <span class="aip-item-text">
                <span class="aip-item-name">{{ c.name }}<span v-if="countBy[c.id]" class="aip-tag on">{{ t('oauth.connected', { n: countBy[c.id] }) }}</span></span>
                <span class="aip-item-meta">{{ t(`oauth.method.${c.flow}`) }}</span>
              </span>
              <el-tooltip :content="t('oauth.engine.needReady')" :disabled="ready" placement="top" :show-after="300">
                <span><el-button size="small" text type="primary" :disabled="!ready" @click="connect(c)"><Icon name="plus" />{{ t('oauth.connect') }}</el-button></span>
              </el-tooltip>
            </div>
          </div>
          <p v-if="sec.key === 'browser'" class="aio-terms">{{ t('oauth.termsNote') }}</p>
        </section>

        <section class="aio-sec">
          <div class="aio-sec-head"><span>{{ t('oauth.sections.other') }}</span></div>
          <div class="aio-grid">
            <div class="aio-card">
              <span class="aip-avatar" :style="{ background: avatarColor(VERTEX_COLOR, 'Vertex') }"><Icon name="key" /></span>
              <span class="aip-item-text">
                <span class="aip-item-name">Vertex AI</span>
                <span class="aip-item-meta">{{ t('oauth.method.vertex') }}</span>
              </span>
              <el-button size="small" text type="primary" :disabled="!ready" @click="vertexOpen = true"><Icon name="upload" />{{ t('oauth.import') }}</el-button>
            </div>
            <div class="aio-card">
              <span class="aip-avatar" :style="{ background: avatarColor('teal', 'Codex') }"><Icon name="link" /></span>
              <span class="aip-item-text">
                <span class="aip-item-name">{{ t('oauth.siwc.name') }}</span>
                <span class="aip-item-meta">{{ codexCreds.length ? t('oauth.method.siwc') : t('oauth.siwc.needCodex') }}</span>
              </span>
              <el-button size="small" text type="primary" :disabled="!ready || !codexCreds.length" @click="bindSiwc()"><Icon name="link" />{{ t('oauth.bind') }}</el-button>
            </div>
          </div>
        </section>
      </div>
    </GlassPanel>

    <!-- connected accounts -->
    <GlassPanel :title="t('oauth.cred.title')">
      <div class="aio-use" :class="{ on: inUse }">
        <div class="aio-use-text">
          <span class="aip-item-name">{{ t(inUse ? 'oauth.use.onTitle' : 'oauth.use.title') }}</span>
          <span class="aip-help">{{ t(inUse ? 'oauth.use.onText' : 'oauth.use.text') }}</span>
        </div>
        <el-button v-if="!inUse" type="primary" :disabled="!ready || !creds.length" :loading="using" @click="useInAssistant({ activate: true })">{{ t('oauth.use.button') }}</el-button>
        <template v-else>
          <el-button :loading="using" @click="useInAssistant({ activate: false })"><Icon name="refresh" />{{ t('oauth.use.refresh') }}</el-button>
          <el-button text @click="emit('goServices')">{{ t('oauth.use.edit') }}</el-button>
        </template>
      </div>

      <div v-if="credError" class="aip-error">{{ credError }}</div>
      <div v-else-if="!ready" class="aip-empty">{{ t('oauth.cred.engineOff') }}</div>
      <div v-else-if="!creds.length && !credLoading" class="aip-empty">{{ t('oauth.cred.empty') }}</div>
      <div class="aio-creds">
        <div v-for="c in creds" :key="c.name" class="aio-cred" :class="[`cs-${credState(c)}`]">
          <span class="aip-avatar sm" :style="{ background: credColor(c) }">{{ initial(credName(c)) }}</span>
          <span class="aip-item-text">
            <span class="aip-item-name">{{ credName(c) }}</span>
            <span class="aip-item-meta">{{ credProvider(c) }}<template v-if="c.status_message"> · {{ c.status_message }}</template></span>
          </span>
          <span class="aio-state">{{ t(`oauth.cred.state.${credState(c)}`) }}</span>
          <span v-if="c.siwc_bound || c.use_siwc" class="aip-tag on" :title="t('oauth.siwc.boundHelp')">{{ t('oauth.siwc.bound') }}</span>
          <el-button v-if="credState(c) === 'relogin' && cardOf(c.provider || c.type)" size="small" @click="connect(cardOf(c.provider || c.type)!, c.name)">
            {{ t('oauth.cred.relogin') }}
          </el-button>
          <span class="aio-cred-acts">
            <button v-if="c.siwc_bind_supported && !c.siwc_bound" type="button" class="aip-ibtn" :title="t('oauth.siwc.bindThis')" @click="bindSiwc(c.name)"><Icon name="link" /></button>
            <button type="button" class="aip-ibtn" :title="t('oauth.cred.refresh')" :disabled="!!rowBusy[c.name]" @click="rowDo(c, 'refresh')"><Icon name="refresh" /></button>
            <button type="button" class="aip-ibtn" :title="t('oauth.cred.verify')" :disabled="!!rowBusy[c.name]" @click="rowDo(c, 'verify')"><Icon name="bolt" /></button>
            <button type="button" class="aip-ibtn del" :class="{ ask: confirmDel === c.name }" :title="t('oauth.cred.remove')"
                    :disabled="!!rowBusy[c.name]" @click="rowDo(c, 'remove')">
              <template v-if="confirmDel === c.name">{{ t('history.confirmDelete') }}</template><Icon v-else name="trash" />
            </button>
          </span>
          <el-switch :model-value="!c.disabled" size="small" :disabled="!!rowBusy[c.name]" :title="t('oauth.cred.enabled')"
                     @update:model-value="rowDo(c, 'toggle')" />
        </div>
      </div>
      <p v-if="creds.length" class="aip-help">{{ t('oauth.cred.count', { n: compact(creds.length) }) }}</p>
    </GlassPanel>

    <OAuthFlowDialog v-model="flowOpen" :card="flowCard" :target="flowTarget" :codex="codexCreds" @done="onFlowDone" />
    <VertexDialog v-model="vertexOpen" @done="onFlowDone" />
  </div>
</template>

<style>
/* OAuth tab (aio-): same surfaces as the services page (aip- variables), no outlines */
.aio { --aip-l1: rgba(var(--tint-rgb), 0.028); --aip-l2: rgba(var(--tint-rgb), 0.05); --aip-l3: rgba(var(--tint-rgb), 0.08);
  display: flex; flex-direction: column; gap: 14px; font-variant-numeric: tabular-nums; }
.dark .aio { font-weight: 360; }
.dark .aio .el-button { font-weight: 460; }
.aio .el-button { border: 0; }
.aio .el-button:not(.is-text):not(.is-link):not(.el-button--primary) { background: var(--aip-l2); }
.aio .el-button:not(.is-text):not(.is-link):not(.el-button--primary):hover { background: var(--aip-l3); }
.aio .aia-i, .aio-flow .aia-i { margin-right: 5px; }
.aio .aip-ibtn .aia-i, .aio .aip-avatar .aia-i { margin-right: 0; }
.aio-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.aio-intro { margin: -8px 0 0; }
.aio-title { font-size: 15px; font-weight: 600; color: var(--ink); }
.dark .aio-title { font-weight: 560; }
.aio-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 12px;
  border: 0;
  border-radius: 999px;
  background: var(--aip-l2);
  color: var(--ink-2);
  font: inherit;
  font-size: 12.5px;
  cursor: pointer;
  white-space: nowrap;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.aio-pill .aia-i { margin-right: 0; }
.aio-pill:hover, .aio-pill.open { background: var(--aip-l3); color: var(--ink); }
.aio-pill:focus-visible { outline: none; box-shadow: 0 0 0 3px rgba(var(--accent-rgb), 0.22); }
.aio-led { width: 7px; height: 7px; border-radius: 50%; background: var(--ink-3); }
.st-ready .aio-led { background: var(--ok); }
.st-starting .aio-led, .st-installing .aio-led { background: var(--warn); animation: aio-breathe 1.2s ease-in-out infinite; }
.st-error .aio-led { background: var(--bad); }
@keyframes aio-breathe { 50% { opacity: 0.35; } }
.aio-sheet { display: flex; flex-direction: column; gap: 8px; padding: 12px 14px; border-radius: 12px; background: var(--aip-l1); }
.aio-fold-enter-active, .aio-fold-leave-active { transition: opacity 0.18s ease, translate 0.18s ease; }
.aio-fold-enter-from, .aio-fold-leave-to { opacity: 0; translate: 0 -4px; }
.aio-kv { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 4px 14px; margin: 0; font-size: 12.5px; align-items: center; }
.aio-kv dt { color: var(--ink-3); }
.aio-kv dd { margin: 0; color: var(--ink); overflow-wrap: anywhere; }
.aio-log {
  max-height: 220px;
  overflow: auto;
  margin: 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--aip-l2);
  color: var(--ink-2);
  font-size: 11.5px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.aio-banner {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 16px;
  border-radius: 14px;
  background: rgba(var(--accent-rgb), 0.07);
}
.aio-banner.st-error, .aio-banner.error { background: color-mix(in srgb, var(--bad) 8%, transparent); }
.aio-banner.error { color: var(--bad); font-size: 13px; }
.aio-banner-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.aio-banner-title { font-size: 13.5px; font-weight: 600; color: var(--ink); }
.dark .aio-banner-title { font-weight: 560; }
.aio-progress { height: 4px; margin-top: 6px; border-radius: 2px; background: var(--aip-l3); overflow: hidden; }
.aio-progress i { display: block; height: 100%; border-radius: 2px; background: var(--accent); transition: width 0.4s ease; }
.aio-sections { display: flex; flex-direction: column; gap: 18px; transition: opacity 0.2s ease; }
.aio-sections.off { opacity: 0.6; }
.aio-sec { display: flex; flex-direction: column; gap: 8px; }
.aio-sec-head { display: flex; align-items: baseline; gap: 10px; font-size: 13px; font-weight: 600; color: var(--ink); }
.dark .aio-sec-head { font-weight: 560; }
.aio-sec-head .aip-help { font-weight: 400; margin: 0; }
.aio-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 8px; }
.aio-card, .aio-kimi-site {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 10px 11px 12px;
  border-radius: 13px;
  background: var(--aip-l1);
  transition: background-color 0.15s ease;
}
.aio-card:hover, .aio-kimi-site:hover { background: var(--aip-l2); }
.aio-kimi { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 8px; }
.aio-card .aip-item-name .aip-tag, .aio-kimi-site .aip-item-name .aip-tag { font-weight: 400; }
.aio-terms { margin: 0; font-size: 11.5px; line-height: 1.55; color: var(--ink-3); text-wrap: pretty; }
.aio-use {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--aip-l1);
}
.aio-use.on { background: rgba(var(--accent-rgb), 0.07); }
.aio-use-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.aio-use .el-button + .el-button { margin-left: 0; }
.aio-creds { display: flex; flex-direction: column; gap: 4px; }
.aio-cred {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 12px;
  background: var(--aip-l1);
  transition: background-color 0.15s ease, opacity 0.15s ease;
}
.aio-cred:hover { background: var(--aip-l2); }
.aio-cred.cs-off { opacity: 0.55; }
.aip-avatar.sm { width: 28px; height: 28px; border-radius: 8px; font-size: 13px; }
.aio-state { flex: none; font-size: 11.5px; padding: 1px 8px; border-radius: 999px; color: var(--ok); background: color-mix(in srgb, var(--ok) 12%, transparent); }
.cs-relogin .aio-state { color: var(--warn); background: color-mix(in srgb, var(--warn) 14%, transparent); }
.cs-down .aio-state { color: var(--bad); background: color-mix(in srgb, var(--bad) 12%, transparent); }
.cs-off .aio-state { color: var(--ink-3); background: var(--aip-l2); }
.cs-refreshing .aio-state { color: var(--accent); background: rgba(var(--accent-rgb), 0.1); }
.aio-cred-acts { display: flex; gap: 1px; opacity: 0; transition: opacity 0.12s ease; }
.aio-cred:hover .aio-cred-acts, .aio-cred:focus-within .aio-cred-acts { opacity: 1; }
.aio-cred-acts .aip-ibtn.del:hover, .aio-cred-acts .aip-ibtn.del.ask { color: var(--bad); }
.aio-cred-acts .aip-ibtn.del.ask { width: auto; padding: 0 8px; font-size: 11.5px; background: color-mix(in srgb, var(--bad) 12%, transparent); }

/* sign-in dialogs */
.aio-flow-body { display: flex; flex-direction: column; gap: 12px; }
.aio-flow-who { display: flex; align-items: center; gap: 10px; }
.aio-flow-whotext { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.aio-code {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 16px;
  border-radius: 12px;
  background: rgba(var(--tint-rgb), 0.05);
}
.aio-code.expired { opacity: 0.45; }
.aio-code-text { font-size: 30px; letter-spacing: 0.14em; color: var(--ink); user-select: all; }
.aio-clock { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--ink-2); }
.aio-ring { width: 14px; height: 14px; rotate: -90deg; }
.aio-ring circle { fill: none; stroke-width: 2; }
.aio-ring .bg { stroke: rgba(var(--tint-rgb), 0.12); }
.aio-ring .fg { stroke: var(--accent); stroke-dasharray: 40.84; transition: stroke-dashoffset 1s linear; }
.aio-flow-wait { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--ink-2); }
.aio-dots { display: inline-flex; gap: 3px; }
.aio-dots i { width: 4px; height: 4px; border-radius: 50%; background: var(--accent); animation: aio-dot 1.2s ease-in-out infinite; }
.aio-dots i:nth-child(2) { animation-delay: 0.2s; }
.aio-dots i:nth-child(3) { animation-delay: 0.4s; }
@keyframes aio-dot { 0%, 80%, 100% { opacity: 0.25; } 40% { opacity: 1; } }
.aio-flow-note { font-size: 12.5px; color: var(--ink-2); }
.aio-flow-note.error { color: var(--bad); overflow-wrap: anywhere; }
.aio-flow-ok { display: flex; align-items: center; gap: 8px; padding: 12px 0; font-size: 14px; color: var(--ink); }
.aio-flow-okicon { color: var(--ok); font-size: 20px; animation: aio-pop 0.2s cubic-bezier(0.2, 0.8, 0.2, 1); }
@keyframes aio-pop { from { scale: 0.8; } }
.aio-paste { display: flex; flex-direction: column; gap: 6px; }
.aio-paste-body { display: flex; flex-direction: column; gap: 8px; }
.aio-paste-body .el-textarea__inner { border: 0; box-shadow: none; background: rgba(var(--tint-rgb), 0.05); font-size: 12px; }
.aio-drop {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-height: 130px;
  border: 0;
  border-radius: 12px;
  background: rgba(var(--tint-rgb), 0.05);
  color: var(--ink-2);
  font: inherit;
  cursor: pointer;
  transition: background-color 0.15s ease, box-shadow 0.15s ease;
}
.aio-drop:hover, .aio-drop.over { background: rgba(var(--tint-rgb), 0.08); }
.aio-drop.over { box-shadow: inset 0 0 0 2px rgba(var(--accent-rgb), 0.35); }
.aio-drop.has .aio-drop-icon { color: var(--ok); }
.aio-drop-icon { font-size: 22px; color: var(--ink-3); }
.aio-drop-main { font-size: 13px; color: var(--ink); }
.aio-drop-sub { font-size: 11.5px; color: var(--ink-3); }
@media (prefers-reduced-motion: reduce) {
  .aio-led, .aio-dots i { animation: none; }
  .aio-fold-enter-active, .aio-fold-leave-active { transition: none; }
}
</style>
