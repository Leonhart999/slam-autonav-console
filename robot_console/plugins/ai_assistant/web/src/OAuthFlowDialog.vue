<!-- One sign-in through the embedded CPA engine: device code (code + verification page + countdown) or browser sign-in
     (open the page, wait for the callback, or paste the address the browser ended on). Also used for SIWC binding.
     CPA keeps the session; this dialog polls its state until ok / error and cancels it when closed early. -->
<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { ElButton, ElDialog, ElInput, ElOption, ElSelect } from 'element-plus'
import {
  oauthCallback, oauthCancel, oauthSiwc, oauthStart, oauthStatus, type Credential, type OAuthProvider, type OAuthStart,
} from './api'
import type { ProviderCard } from './oauthProviders'
import { avatarColor, initial } from './presets'
import { RC, t } from './sdk'
import Icon from './Icon.vue'
import CopyButton from './CopyButton.vue'

const props = defineProps<{
  modelValue: boolean
  /** the provider to sign in with, or 'siwc' to bind ChatGPT plan usage to a Codex credential */
  card: ProviderCard | 'siwc' | null
  /** credential to sign in again (keeps its file), or the Codex credentials for SIWC */
  target?: string
  codex?: Credential[]
}>()
const emit = defineEmits<{ 'update:modelValue': [v: boolean]; done: [] }>()

type Phase = 'pick' | 'preparing' | 'ready' | 'waiting' | 'success' | 'expired' | 'error'
const phase = ref<Phase>('preparing')
const start = ref<OAuthStart | null>(null)
const error = ref('')
const networkHint = ref(false)
const pasted = ref('')
const pasteOpen = ref(false)
const submitting = ref(false)
const siwcTarget = ref('')
const remaining = ref(0)
let poll = 0
let tick = 0
let pasteTimer = 0
let sessionState = ''
let finished = false
let attempt = 0

const isSiwc = computed(() => props.card === 'siwc')
const card = computed(() => (props.card && props.card !== 'siwc' ? props.card : null))
const flow = computed<'device' | 'browser'>(() => (isSiwc.value ? 'browser' : start.value?.flow ?? card.value?.flow ?? 'browser'))
const provider = computed<OAuthProvider>(() => (isSiwc.value ? 'codex' : card.value?.id ?? 'codex'))
const name = computed(() => {
  if (isSiwc.value) return 'SIWC'
  const c = card.value
  return c ? c.name + (c.domain ? ` · ${c.domain}` : '') : ''
})
const title = computed(() => (isSiwc.value ? t('oauth.siwc.title') : t('oauth.flow.title', { name: name.value })))
const pasteValid = computed(() => /^https?:\/\/\S+[?&](code|error)=/.test(pasted.value.trim()))
const total = computed(() => start.value?.expires_in ?? 0)
const clock = computed(() => {
  const s = Math.max(0, remaining.value)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
})
const ring = computed(() => (total.value ? Math.max(0, remaining.value) / total.value : 1))

function clearTimers() {
  window.clearInterval(poll)
  window.clearInterval(tick)
  window.clearTimeout(pasteTimer)
}

async function begin() {
  clearTimers()
  const myAttempt = ++attempt
  finished = false
  error.value = ''
  networkHint.value = false
  pasted.value = ''
  pasteOpen.value = false
  start.value = null
  sessionState = ''
  if (isSiwc.value && !siwcTarget.value) {
    const first = (props.codex ?? []).find((c) => c.name === props.target) ?? (props.codex ?? [])[0]
    siwcTarget.value = first?.name ?? ''
    phase.value = 'pick'
    return
  }
  phase.value = 'preparing'
  try {
    const s = isSiwc.value ? await oauthSiwc(siwcTarget.value) : await oauthStart(provider.value, props.target ? { name: props.target } : {})
    if (myAttempt !== attempt || !props.modelValue) {
      if (s.state) void oauthCancel(s.state).catch(() => undefined)
      return
    }
    start.value = s
    sessionState = s.state
    if (s.expires_in) {
      remaining.value = s.expires_in
      tick = window.setInterval(() => {
        remaining.value -= 1
        if (remaining.value <= 0 && phase.value === 'waiting' && flow.value === 'device') {
          phase.value = 'expired'
          clearTimers()
        }
      }, 1000)
    }
    phase.value = flow.value === 'device' ? 'waiting' : 'ready'
    poll = window.setInterval(check, 1500)
  } catch (e) {
    if (myAttempt !== attempt || !props.modelValue) return
    // the engine is running (the dialog only opens then): a failed start is the engine not reaching the provider
    error.value = RC().errorText(e)
    networkHint.value = true
    phase.value = 'error'
  }
}

function openPage() {
  if (!start.value?.url) return
  window.open(start.value.url, '_blank', 'noopener')
  if (phase.value === 'ready') phase.value = 'waiting'
  // the callback often cannot reach the WSL backend: offer the paste field after a while
  window.clearTimeout(pasteTimer)
  pasteTimer = window.setTimeout(() => { if (phase.value === 'waiting' && flow.value === 'browser') pasteOpen.value = true }, 20000)
}

async function check() {
  if (!sessionState || finished) return
  const st = sessionState
  const mine = attempt
  try {
    const r = await oauthStatus(st)
    // an answer for an earlier sign-in (the dialog was closed or restarted meanwhile) is ignored
    if (mine !== attempt || st !== sessionState || finished) return
    if (r.status === 'ok') succeed()
    else if (r.status === 'error') {
      finished = true
      clearTimers()
      error.value = r.error || t('oauth.flow.failed')
      phase.value = 'error'
    }
  } catch {
    /* a missed poll is retried */
  }
}

function succeed() {
  finished = true
  clearTimers()
  phase.value = 'success'
  emit('done')
  window.setTimeout(() => { if (phase.value === 'success') close() }, 1400)
}

async function submitPaste() {
  if (!pasteValid.value || submitting.value) return
  submitting.value = true
  try {
    await oauthCallback(provider.value, pasted.value.trim())
    phase.value = 'waiting'
    void check()
  } catch (e) {
    error.value = RC().errorText(e)
  } finally {
    submitting.value = false
  }
}

function close() {
  emit('update:modelValue', false)
}

/** closing before the end cancels CPA's waiting session (its callback port is freed) */
function cleanup() {
  ++attempt
  clearTimers()
  if (sessionState && !finished) void oauthCancel(sessionState).catch(() => undefined)
  sessionState = ''
}

watch(() => props.modelValue, (open) => {
  if (open && props.card) {
    siwcTarget.value = ''
    void begin()
  } else if (!open) cleanup()
}, { immediate: true })
onUnmounted(cleanup)
</script>

<template>
  <el-dialog :model-value="modelValue" :title="title" width="min(460px, 94vw)" append-to-body class="aip-dialog aio-flow"
             @update:model-value="emit('update:modelValue', $event)">
    <div class="aio-flow-body">
      <div class="aio-flow-who">
        <span class="aip-avatar" :style="{ background: avatarColor(card?.color ?? 'teal', card?.name ?? 'Codex') }">
          {{ initial(card?.name ?? 'Codex') }}
        </span>
        <span class="aio-flow-whotext">
          <span class="aip-item-name">{{ isSiwc ? t('oauth.siwc.name') : name }}</span>
          <span class="aip-item-meta">{{ isSiwc ? t('oauth.siwc.caption') : t(`oauth.method.${flow}`) }}</span>
        </span>
      </div>

      <!-- SIWC: which Codex credential gets the ChatGPT plan -->
      <template v-if="phase === 'pick'">
        <p class="aip-help">{{ t('oauth.siwc.help') }}</p>
        <div v-if="!(codex ?? []).length" class="aio-flow-note">{{ t('oauth.siwc.none') }}</div>
        <template v-else>
          <el-select v-model="siwcTarget" :placeholder="t('oauth.siwc.pick')">
            <el-option v-for="c in codex" :key="c.name" :value="c.name" :label="c.email || c.label || c.name" />
          </el-select>
          <div class="aip-row"><span class="aip-grow" />
            <el-button type="primary" :disabled="!siwcTarget" @click="begin">{{ t('oauth.flow.continue') }}</el-button>
          </div>
        </template>
      </template>

      <div v-else-if="phase === 'preparing'" class="aio-flow-wait"><span class="aio-dots"><i /><i /><i /></span>{{ t('oauth.flow.preparing') }}</div>

      <!-- device code -->
      <template v-else-if="flow === 'device' && (phase === 'waiting' || phase === 'expired')">
        <p class="aip-help">{{ t('oauth.flow.deviceHelp') }}</p>
        <div class="aio-code" :class="{ expired: phase === 'expired' }">
          <span class="aio-code-text mono">{{ start?.user_code || '—' }}</span>
          <CopyButton v-if="start?.user_code" :text="start.user_code" />
        </div>
        <div class="aip-row">
          <el-button type="primary" :disabled="phase === 'expired'" @click="openPage"><Icon name="external" />{{ t('oauth.flow.openVerify') }}</el-button>
          <span class="aip-grow" />
          <span v-if="total" class="aio-clock mono">
            <svg viewBox="0 0 16 16" class="aio-ring"><circle cx="8" cy="8" r="6.5" class="bg" />
              <circle cx="8" cy="8" r="6.5" class="fg" :style="{ strokeDashoffset: `${(1 - ring) * 40.84}` }" /></svg>
            {{ clock }}
          </span>
        </div>
        <div v-if="phase === 'waiting'" class="aio-flow-wait"><span class="aio-dots"><i /><i /><i /></span>{{ t('oauth.flow.waitDevice') }}</div>
        <div v-else class="aio-flow-note">{{ t('oauth.flow.expired') }}
          <el-button size="small" text type="primary" @click="begin">{{ t('oauth.flow.again') }}</el-button>
        </div>
      </template>

      <!-- browser sign-in -->
      <template v-else-if="phase === 'ready' || phase === 'waiting'">
        <p class="aip-help">{{ t(isSiwc ? 'oauth.siwc.browserHelp' : 'oauth.flow.browserHelp') }}</p>
        <div class="aip-row">
          <el-button type="primary" @click="openPage"><Icon name="external" />{{ t('oauth.flow.openSignIn') }}</el-button>
          <span class="aip-grow" />
          <CopyButton v-if="start?.url" :text="start.url" />
        </div>
        <div v-if="phase === 'waiting'" class="aio-flow-wait"><span class="aio-dots"><i /><i /><i /></span>{{ t('oauth.flow.waitBrowser') }}</div>
        <div class="aio-paste">
          <button type="button" class="aip-adv-head" :aria-expanded="pasteOpen" @click="pasteOpen = !pasteOpen">
            <Icon name="chevron" class="aip-caret" :class="{ open: pasteOpen }" />{{ t('oauth.flow.pasteTitle') }}
          </button>
          <div v-if="pasteOpen" class="aio-paste-body">
            <p class="aip-help">{{ t('oauth.flow.pasteHelp') }}</p>
            <el-input v-model="pasted" type="textarea" :rows="3" class="mono" :placeholder="t('oauth.flow.pastePlaceholder')" />
            <div class="aip-row">
              <span v-if="pasted.trim() && !pasteValid" class="aip-error">{{ t('oauth.flow.pasteInvalid') }}</span>
              <span class="aip-grow" />
              <el-button :disabled="!pasteValid" :loading="submitting" @click="submitPaste">{{ t('oauth.flow.submit') }}</el-button>
            </div>
          </div>
        </div>
      </template>

      <div v-else-if="phase === 'success'" class="aio-flow-ok"><Icon name="check" class="aio-flow-okicon" />{{ t('oauth.flow.success') }}</div>

      <template v-else-if="phase === 'error'">
        <div class="aio-flow-note error">{{ error }}</div>
        <p v-if="networkHint" class="aip-help">{{ t('oauth.flow.network') }}</p>
        <div class="aip-row"><span class="aip-grow" /><el-button @click="begin">{{ t('oauth.flow.again') }}</el-button></div>
      </template>

      <p v-if="card?.termsNote && phase !== 'success'" class="aio-terms">{{ t('oauth.termsNote') }}</p>
    </div>
  </el-dialog>
</template>
