<!-- Settings of the assistant, inside the panel (laid out like ClipDeck's settings): a list of categories, each
     opening its own page; Back returns to the list, then to the conversation. Changes show in the panel at once and
     are kept with Save, or dropped with Cancel. "Model services" and "Requests and tools" open the console pages
     where those are set. -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import Icon from './Icon.vue'
import LiquidPreview from './settings/LiquidPreview.vue'
import SetChoice from './settings/SetChoice.vue'
import SetShortcut from './settings/SetShortcut.vue'
import SetSlider from './settings/SetSlider.vue'
import SetToggle from './settings/SetToggle.vue'
import OverlayScrollbar from './glass/OverlayScrollbar.vue'
import { t } from './sdk'
import { BLUR_MAX, DEFAULT_SHORTCUTS, GLASS_PRESETS, LIQUID_PRESETS, consoleDark, defaultPrefs, draft, matchPreset,
  savePrefs, textToneOf, type LiquidGlassPrefs, type Prefs, type ShortcutId, type WindowGlass } from './prefs'

const emit = defineEmits<{ close: [] }>()

type Category = 'general' | 'services' | 'shortcuts' | 'glass' | 'liquid' | 'advanced'
const CATEGORIES: { key: Category; icon: string; link?: string }[] = [
  { key: 'general', icon: 'sliders' },
  { key: 'services', icon: 'server', link: '#/ext/ai_assistant/providers' },
  { key: 'shortcuts', icon: 'keyboard' },
  { key: 'glass', icon: 'window' },
  { key: 'liquid', icon: 'drop' },
  { key: 'advanced', icon: 'wrench', link: '#/plugins' },
]
const SHORTCUTS: ShortcutId[] = ['togglePanel', 'toggleHistory', 'newChat']

const d = computed(() => draft.value as Prefs)
const page = ref<Category | null>(null)
const forward = ref(true)
const scroller = ref<HTMLElement | null>(null)

const themeKey = computed(() => (consoleDark.value ? 'dark' : 'light'))
const win = computed(() => d.value.glass[themeKey.value])
const liq = computed(() => d.value.liquid)

function open(c: (typeof CATEGORIES)[number]) {
  if (c.link) { location.hash = c.link; return }
  forward.value = true
  page.value = c.key
}
function back() {
  if (page.value) { forward.value = false; page.value = null }
  else cancel()
}
function cancel() {
  draft.value = null
  emit('close')
}
function save() {
  if (draft.value) savePrefs(draft.value)
  draft.value = null
  emit('close')
}

function setWin(p: Partial<WindowGlass>) { Object.assign(d.value.glass[themeKey.value], p) }
function setLiq(p: Partial<LiquidGlassPrefs>) { Object.assign(d.value.liquid, p) }
function resetLook() {
  const def = defaultPrefs()
  d.value.cornerRadius = def.cornerRadius
  d.value.animations = def.animations
  d.value.glass[themeKey.value] = { ...def.glass[themeKey.value] }
  d.value.liquid = { ...def.liquid }
}

const title = computed(() => (page.value ? t(`settings.categories.${page.value}.name`) : t('settings.title')))
const subtitle = computed(() => (page.value ? t(`settings.categories.${page.value}.desc`) : t('settings.subtitle')))

const glassPreset = computed(() => matchPreset(GLASS_PRESETS, win.value)?.id ?? null)
const liquidPreset = computed(() => matchPreset(LIQUID_PRESETS, liq.value)?.id ?? null)
const pickGlass = (id: string) => setWin(GLASS_PRESETS.find((p) => p.id === id)?.values ?? {})
const pickLiquid = (id: string) => setLiq(LIQUID_PRESETS.find((p) => p.id === id)?.values ?? {})
const presetDesc = (id: string | null) =>
  id ? t(`settings.presets.${id}.desc`) : t('settings.customHint')

const pct = (v: number) => `${Math.round(v)}%`
const px = (v: number) => `${Math.round(v)} px`
const fixed = (n: number) => (v: number) => v.toFixed(n)
const glassOff = computed(() => d.value.skin !== 'glass')
</script>

<template>
  <section class="aia-set-shell">
    <header class="aia-set-header">
      <button type="button" class="aia-icon-button" :aria-label="t('settings.back')" :title="t('settings.back')" @click="back">
        <Icon name="back" />
      </button>
      <div class="aia-set-title">
        <h1>{{ title }}</h1>
        <p>{{ subtitle }}</p>
      </div>
    </header>

    <div class="aia-scroll-area">
      <transition :name="forward ? 'aia-set-fwd' : 'aia-set-back'" mode="out-in">
        <div :key="page ?? 'home'" ref="scroller" class="aia-set-scroll">
          <!-- ---- the list of categories ---- -->
          <nav v-if="!page" class="aia-set-grid aia-set-nav" :aria-label="t('settings.title')">
            <button v-for="c in CATEGORIES" :key="c.key" type="button" class="aia-field-row aia-nav-row" @click="open(c)">
              <span class="aia-nav-icon" aria-hidden="true"><Icon :name="(c.icon as any)" /></span>
              <span class="aia-field-label">
                <strong>{{ t(`settings.categories.${c.key}.name`) }}</strong>
                <span>{{ t(`settings.categories.${c.key}.desc`) }}</span>
              </span>
              <Icon :name="c.link ? 'external' : 'chevron'" class="aia-nav-caret" />
            </button>
          </nav>

          <!-- ---- general ---- -->
          <div v-else-if="page === 'general'" class="aia-set-grid">
            <SetChoice :label="t('settings.skin.name')" :description="t(`settings.skin.${d.skin}`)" :value="d.skin"
                       :options="[{ value: 'glass', label: t('skin.glass') }, { value: 'classic', label: t('skin.classic') }]"
                       @change="d.skin = $event as Prefs['skin']" />
            <SetChoice :label="t('settings.sendKey.name')" :description="t(`settings.sendKey.${d.sendKey}`)" :value="d.sendKey"
                       :options="[{ value: 'enter', label: 'Enter' }, { value: 'ctrlEnter', label: 'Ctrl + Enter' }]"
                       @change="d.sendKey = $event as Prefs['sendKey']" />
            <SetToggle :label="t('settings.reveal.name')" :description="t('settings.reveal.desc')" :checked="d.reveal"
                       @toggle="d.reveal = !d.reveal" />
          </div>

          <!-- ---- shortcuts ---- -->
          <template v-else-if="page === 'shortcuts'">
            <div class="aia-set-grid">
              <SetShortcut v-for="k in SHORTCUTS" :key="k" :label="t(`settings.shortcuts.${k}`)" :value="d.shortcuts[k]"
                           :placeholder="t('settings.shortcuts.press')" @change="d.shortcuts[k] = $event" />
            </div>
            <p class="aia-set-hint">{{ t('settings.shortcuts.hint') }}</p>
            <button type="button" class="aia-ghost-button aia-section-reset" @click="d.shortcuts = { ...DEFAULT_SHORTCUTS }">
              <Icon name="reset" />{{ t('settings.shortcuts.restore') }}
            </button>
          </template>

          <!-- ---- window glass ---- -->
          <template v-else-if="page === 'glass'">
            <p class="aia-set-hint">
              {{ glassOff ? t('settings.glassOnly') : t('settings.perTheme', { theme: t(`settings.theme.${themeKey}`) }) }}
            </p>
            <section class="aia-set-section">
              <h2>{{ t('settings.window') }}</h2>
              <div class="aia-set-grid">
                <SetSlider :label="t('settings.cornerRadius.name')" :description="t('settings.cornerRadius.desc')"
                           :value="d.cornerRadius" :min="8" :max="40" :format="px" @change="d.cornerRadius = $event" />
                <SetToggle :label="t('settings.animations.name')" :description="t('settings.animations.desc')"
                           :checked="d.animations" @toggle="d.animations = !d.animations" />
              </div>
            </section>
            <section class="aia-set-section">
              <h2>{{ t('settings.glassHeading') }}</h2>
              <div class="aia-set-grid">
                <SetChoice preset :label="t('settings.glassTemplate')" :description="presetDesc(glassPreset)"
                           :value="glassPreset" :custom-label="t('settings.custom')"
                           :options="GLASS_PRESETS.map((p) => ({ value: p.id, label: t(`settings.presets.${p.id}.name`) }))"
                           @change="pickGlass" />
                <SetSlider :label="t('settings.whites.name')" :description="t('settings.whites.desc')" :value="win.whitePoint"
                           :min="4" :max="255" @change="setWin({ whitePoint: $event, blackPoint: Math.min(win.blackPoint, $event - 4) })" />
                <SetSlider :label="t('settings.blacks.name')" :description="t('settings.blacks.desc')" :value="win.blackPoint"
                           :min="0" :max="251" @change="setWin({ blackPoint: $event, whitePoint: Math.max(win.whitePoint, $event + 4) })" />
                <SetSlider :label="t('settings.blur.name')" :description="t('settings.blur.desc')" :value="win.backdropBlur"
                           :min="0" :max="BLUR_MAX" :format="px" @change="setWin({ backdropBlur: $event })" />
                <SetSlider :label="t('settings.saturation.name')" :description="t('settings.saturation.desc')"
                           :value="win.backdropSaturation" :min="50" :max="250" :format="pct"
                           @change="setWin({ backdropSaturation: $event })" />
              </div>
            </section>
            <section class="aia-set-section">
              <h2>{{ t('settings.edgesHeading') }}</h2>
              <div class="aia-set-grid">
                <SetSlider :label="t('settings.rimLight.name')" :description="t('settings.rimLight.desc')" :value="win.rimLight"
                           :min="0" :max="100" :format="pct" @change="setWin({ rimLight: $event })" />
                <SetSlider :label="t('settings.edgeHighlight.name')" :description="t('settings.edgeHighlight.desc')"
                           :value="win.edgeHighlight" :min="0" :max="100" :format="pct" @change="setWin({ edgeHighlight: $event })" />
                <SetChoice :label="t('settings.text.name')"
                           :description="win.textTone === 'auto' ? t(`settings.text.auto_${textToneOf({ ...win, textTone: 'auto' })}`) : t('settings.text.desc')"
                           :value="win.textTone"
                           :options="['auto', 'light', 'dark'].map((v) => ({ value: v, label: t(`settings.text.${v}`) }))"
                           @change="setWin({ textTone: $event as WindowGlass['textTone'] })" />
                <SetSlider :label="t('settings.textHalo.name')" :description="t('settings.textHalo.desc')" :value="win.textHalo"
                           :min="0" :max="100" :format="pct" @change="setWin({ textHalo: $event })" />
                <SetSlider :label="t('settings.secondaryText.name')" :description="t('settings.secondaryText.desc')"
                           :value="win.secondaryTextOpacity" :min="30" :max="100" :format="pct"
                           @change="setWin({ secondaryTextOpacity: $event })" />
              </div>
              <button type="button" class="aia-ghost-button aia-section-reset" @click="resetLook">
                <Icon name="reset" />{{ t('settings.reset') }}
              </button>
            </section>
          </template>

          <!-- ---- liquid glass ---- -->
          <section v-else-if="page === 'liquid'" class="aia-set-section aia-set-first" :aria-label="t('settings.categories.liquid.name')">
            <p v-if="glassOff" class="aia-set-hint">{{ t('settings.glassOnly') }}</p>
            <LiquidPreview />
            <div class="aia-set-grid">
              <SetToggle :label="t('settings.liquidEffect.name')" :description="t('settings.liquidEffect.desc')"
                         :checked="liq.liquidEnabled" @toggle="setLiq({ liquidEnabled: !liq.liquidEnabled })" />
              <SetChoice preset :label="t('settings.liquidTemplate')" :description="presetDesc(liquidPreset)"
                         :value="liquidPreset" :custom-label="t('settings.custom')" :disabled="!liq.liquidEnabled"
                         :options="LIQUID_PRESETS.map((p) => ({ value: p.id, label: t(`settings.presets.${p.id}.name`) }))"
                         @change="pickLiquid" />
              <SetChoice :label="t('settings.refraction.name')" :description="t('settings.refraction.desc')" :value="liq.liquidMode"
                         :disabled="!liq.liquidEnabled"
                         :options="['standard', 'polar', 'prominent', 'shader'].map((v) => ({ value: v, label: t(`settings.modes.${v}`) }))"
                         @change="setLiq({ liquidMode: $event as LiquidGlassPrefs['liquidMode'] })" />
              <SetSlider :label="t('settings.displacement.name')" :description="t('settings.displacement.desc')"
                         :value="liq.displacementScale" :min="0" :max="150" :disabled="!liq.liquidEnabled"
                         @change="setLiq({ displacementScale: $event })" />
              <SetSlider :label="t('settings.aberration.name')" :description="t('settings.aberration.live')"
                         :value="liq.aberrationIntensity" :min="0" :max="10" :step="0.1" :format="fixed(1)"
                         disabled @change="setLiq({ aberrationIntensity: $event })" />
              <SetSlider :label="t('settings.elasticity.name')" :description="t('settings.elasticity.desc')"
                         :value="liq.elasticity" :min="0" :max="0.5" :step="0.01" :format="fixed(2)"
                         :disabled="!liq.liquidEnabled || !d.animations" @change="setLiq({ elasticity: $event })" />
              <SetSlider :label="t('settings.frost.name')" :description="t('settings.frost.desc')" :value="liq.frost"
                         :min="0" :max="1" :step="0.01" :format="fixed(2)" @change="setLiq({ frost: $event })" />
              <SetSlider :label="t('settings.liquidSaturation.name')" :description="t('settings.liquidSaturation.desc')"
                         :value="liq.liquidSaturation" :min="50" :max="250" :format="pct"
                         @change="setLiq({ liquidSaturation: $event })" />
              <SetSlider :label="t('settings.shade.name')" :description="t('settings.shade.desc')" :value="liq.liquidShade"
                         :min="0" :max="50" :format="pct" @change="setLiq({ liquidShade: $event })" />
            </div>
            <button type="button" class="aia-ghost-button aia-section-reset" @click="resetLook">
              <Icon name="reset" />{{ t('settings.reset') }}
            </button>
          </section>
        </div>
      </transition>
      <OverlayScrollbar :scroller="scroller" />
    </div>

    <footer class="aia-set-footer">
      <button type="button" class="aia-ghost-button" @click="cancel"><Icon name="close" />{{ t('settings.cancel') }}</button>
      <button type="button" class="aia-primary-button" @click="save"><Icon name="save" />{{ t('settings.save') }}</button>
    </footer>
  </section>
</template>
