<!-- Model picker under the input (like the model menu of Claude Code / Codex): a chip with the model in use and its
     reasoning effort; the menu above it lists the models of every service, a reasoning effort track for the model
     in use, the connection test and the way to the service settings. Choices are saved on the backend (the active
     service and model, the model's effort), so every browser and the next question use them.
     A choice shows at once (the chip and the track move straight away) and is saved in the background; quick changes
     are merged into one request, and a failed save puts the last saved choice back. -->
<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { activateProvider, type Effort, type Status } from './api'
import { EFFORTS, avatarColor, initial } from './presets'
import { RC, t } from './sdk'
import { createSpring, type SpringConfig } from './spring'
import { usePanelMenu } from './popover'
import Icon from './Icon.vue'
import SwapText from './SwapText.vue'
import LiquidGlass from './glass/LiquidGlass.vue'

const props = defineProps<{ status: Status | null; busy: boolean; testing: boolean }>()
const emit = defineEmits<{ changed: []; test: []; manage: []; error: [text: string] }>()

const DEFAULT_EFFORTS: Record<string, Effort[]> = {
  anthropic: ['low', 'medium', 'high', 'xhigh', 'max'],
  openai: ['none', 'minimal', 'low', 'medium', 'high', 'xhigh'],
}
/** Effort knob: damping ratio 40 / (2 * sqrt(640)) = 0.79, settles in about 0.25 s with a barely visible overshoot */
const KNOB_SPRING: SpringConfig = { stiffness: 640, damping: 40, precision: 0.002 }

const root = ref<HTMLElement | null>(null)
const track = ref<HTMLElement | null>(null)
const scroller = ref<HTMLElement | null>(null)
const { open, menuStyle } = usePanelMenu(root, 290)

// ---- the choice on screen: the user's latest pick until the backend has it, else the saved one ----
interface Choice { profile: string; model?: string; effort?: Effort }
const want = ref<Choice | null>(null)
let sending = false

const profiles = computed(() => props.status?.profiles ?? [])
const activeId = computed(() => want.value?.profile ?? props.status?.active ?? null)
const active = computed(() => profiles.value.find((p) => p.id === activeId.value) ?? null)
const modelId = computed(() => want.value?.model ?? active.value?.model ?? '')
const model = computed(() => active.value?.models.find((m) => m.id === modelId.value) ?? null)
const label = computed(() => model.value?.label || (want.value ? modelId.value : props.status?.model) || t('composer.noModel'))
const effort = computed<Effort>(() => (want.value?.effort ?? model.value?.effort ?? (want.value ? '' : props.status?.effort) ?? '') as Effort)
// The track only offers real levels; '' (send no effort) stays possible for a model whose saved effort is unset, the
// track then shows no knob until a level is picked.
const levels = computed<Effort[]>(() => {
  const custom = model.value?.efforts?.filter(Boolean)
  const base: Effort[] = custom?.length
    ? [...custom]
    : [...(DEFAULT_EFFORTS[active.value?.format ?? 'openai'] ?? DEFAULT_EFFORTS.openai)]
  if (effort.value && !base.includes(effort.value)) {
    base.push(effort.value)
    base.sort((a, b) => EFFORTS.indexOf(a) - EFFORTS.indexOf(b))
  }
  return base
})
/** Index of the saved effort on the track, -1 when no effort is set */
const level = computed(() => levels.value.indexOf(effort.value))
const isOn = (pid: string, mid?: string) => pid === activeId.value && (mid === undefined || mid === modelId.value)

// a fresh status from the backend replaces the optimistic choice once nothing is being saved
watch(() => props.status, () => { if (!sending) want.value = null })

/** Send the latest choice; choices made while a request runs are merged and sent after it */
async function flush() {
  if (sending) return
  sending = true
  let sent: Choice | null = null
  try {
    while (want.value && want.value !== sent) {
      sent = want.value
      await activateProvider(sent.profile, sent.model, sent.effort)
    }
  } catch (e) {
    want.value = null
    emit('error', RC().errorText(e))
  } finally {
    sending = false
    emit('changed')
  }
}

function choose(c: Choice) {
  want.value = c
  void flush()
}

function pick(profileId: string, modelId?: string) {
  open.value = false
  if (isOn(profileId, modelId)) return
  choose({ profile: profileId, model: modelId })
}

function setEffort(e: Effort) {
  if (!active.value || e === effort.value) return
  choose({ profile: active.value.id, model: modelId.value || undefined, effort: e })
}

// ---- effort track: a knob on a spring; press and slide along the track, or click a dot, or use the arrow keys ----
const preview = ref<number | null>(null) // level under the pointer / finger while pressing or hovering
const pressing = ref(false)
const shownEffort = computed(() => (preview.value !== null ? levels.value[preview.value] ?? effort.value : effort.value))
const shownLevel = computed(() => preview.value ?? level.value)
const valueDir = ref(1)
watch(shownLevel, (now, before) => { valueDir.value = now >= before ? 1 : -1 })

// with no effort set the knob is hidden and parked at the first dot, so the first pick slides in from the left
const knob = createSpring(0, KNOB_SPRING, (k) => track.value?.style.setProperty('--k', k.toFixed(4)))
watch(track, (el) => { if (el) knob.jump(Math.max(0, level.value)) }, { flush: 'post' })
watch([level, () => levels.value.length], () => { if (!pressing.value) knob.to(Math.max(0, level.value)) })
onUnmounted(() => knob.stop())

/** Fractional level under the pointer (0 = first dot, n-1 = last dot) */
function levelAt(clientX: number) {
  const el = track.value
  if (!el) return 0
  const r = el.getBoundingClientRect()
  const n = levels.value.length
  const span = r.width - 22 // dot centres sit 11 px in from both ends
  return Math.max(0, Math.min(n - 1, ((clientX - r.left - 11) / Math.max(1, span)) * (n - 1)))
}

function onTrackDown(e: PointerEvent) {
  if (e.button !== 0 || !levels.value.length) return
  pressing.value = true
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  const k = levelAt(e.clientX)
  preview.value = Math.round(k)
  knob.to(k)
  e.preventDefault()
}
function onTrackMove(e: PointerEvent) {
  if (pressing.value) {
    const k = levelAt(e.clientX)
    preview.value = Math.round(k)
    knob.to(k) // follows the finger between the dots, snaps on release
  } else if (e.pointerType === 'mouse') {
    preview.value = Math.round(levelAt(e.clientX))
  }
}
function onTrackUp() {
  if (!pressing.value) return
  pressing.value = false
  const i = preview.value ?? level.value
  preview.value = null
  knob.to(i)
  const e = levels.value[i]
  if (e !== undefined) setEffort(e)
}
function onTrackLeave() {
  if (!pressing.value) preview.value = null
}
function onTrackKey(e: KeyboardEvent) {
  const step = e.key === 'ArrowRight' || e.key === 'ArrowUp' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? -1 : 0
  const to = e.key === 'Home' ? 0 : e.key === 'End' ? levels.value.length - 1 : level.value + step
  if (!step && e.key !== 'Home' && e.key !== 'End') return
  e.preventDefault()
  const i = Math.max(0, Math.min(levels.value.length - 1, to))
  setEffort(levels.value[i])
  void nextTick(() => track.value?.querySelectorAll<HTMLElement>('.aia-effort-dot')[i]?.focus())
}

// on opening, bring the model in use into view
watch(open, (o) => {
  if (!o) { preview.value = null; pressing.value = false; return }
  void nextTick(() => {
    const on = scroller.value?.querySelector<HTMLElement>('.aia-menu-item.on, .aia-menu-head.on')
    if (on && scroller.value && scroller.value.scrollHeight > scroller.value.clientHeight) {
      on.scrollIntoView({ block: 'nearest' })
    }
  })
})
</script>

<template>
  <div ref="root" class="aia-pick">
    <LiquidGlass class="aia-chip-glass is-raised" magnetic>
      <button type="button" class="aia-chipbtn" :class="{ open }" :title="t('composer.modelTitle')"
              :aria-expanded="open" :disabled="!profiles.length" @click="open = !open">
        <span v-if="active" class="aia-dotav sm" :style="{ background: avatarColor(active.color, active.name) }">{{ initial(active.name) }}</span>
        <SwapText class="aia-chip-text" :text="label" />
        <SwapText class="aia-chip-sub" :text="effort" :dir="valueDir" />
      </button>
    </LiquidGlass>
    <transition name="aia-pop">
      <div v-if="open" class="aia-menu up aia-model-menu" role="menu" :style="menuStyle">
        <div class="aia-menu-cap">{{ t('composer.modelTitle') }}</div>
        <div ref="scroller" class="aia-menu-scroll">
          <div v-for="p in profiles" :key="p.id" class="aia-menu-group">
            <button type="button" class="aia-menu-head" :class="{ on: isOn(p.id) && !p.models.length }" :disabled="busy"
                    @click="pick(p.id)">
              <span class="aia-dotav" :style="{ background: avatarColor(p.color, p.name) }">{{ initial(p.name) }}</span>
              <span class="aia-menu-name">{{ p.name }}</span>
              <Icon v-if="isOn(p.id) && !p.models.length" name="check" class="aia-menu-check" />
            </button>
            <button v-for="m in p.models" :key="m.id" type="button" class="aia-menu-item"
                    :class="{ on: isOn(p.id, m.id) }" :disabled="busy" :title="m.id"
                    @click="pick(p.id, m.id)">
              <span class="aia-menu-label">{{ m.label }}</span>
              <Icon v-if="isOn(p.id, m.id)" name="check" class="aia-menu-check" />
            </button>
          </div>
        </div>
        <div v-if="active" class="aia-effort">
          <div class="aia-effort-head">
            <Icon name="gauge" /><span>{{ t('composer.effort') }}</span>
            <SwapText class="aia-effort-val mono" :text="shownEffort" :dir="valueDir" />
          </div>
          <div ref="track" class="aia-effort-track" :class="{ pressing, unset: shownLevel < 0 }" role="radiogroup"
               :aria-label="t('composer.effort')" :style="{ '--n': levels.length }"
               @pointerdown="onTrackDown" @pointermove="onTrackMove" @pointerup="onTrackUp" @pointercancel="onTrackUp"
               @pointerleave="onTrackLeave" @keydown="onTrackKey">
            <span class="aia-effort-fill" />
            <button v-for="(e, i) in levels" :key="e" type="button" role="radio" class="aia-effort-dot"
                    :class="{ past: i < shownLevel, on: i === level }" :aria-checked="i === level"
                    :tabindex="i === Math.max(0, level) ? 0 : -1" :aria-label="e" :title="e"
                    @click="$event.detail === 0 && setEffort(e)" />
            <span class="aia-effort-knob" />
          </div>
          <div class="aia-effort-help">{{ t('composer.effortHelp') }}</div>
        </div>
        <div class="aia-menu-foot2">
          <button type="button" class="aia-menu-foot" :disabled="testing || busy" @click="open = false; emit('test')">
            <Icon name="bolt" />{{ testing ? t('composer.testing') : t('test') }}
          </button>
          <button type="button" class="aia-menu-foot" @click="open = false; emit('manage')">
            <Icon name="gear" />{{ t('switcher.manage') }}
          </button>
        </div>
      </div>
    </transition>
  </div>
</template>
