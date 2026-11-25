<!-- Eye-care anti-blue-light control & preview page (/ext/eye_care_ui/main). -->
<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { ElButton, ElDivider, ElRadioButton, ElRadioGroup, ElSlider, ElSwitch } from 'element-plus'
import { RC, THEME_ID, t } from './sdk'
import {
  activeSpec,
  estimatedBlueCut,
  estimatedKelvin,
  initEyeCareRuntime,
  isEyeCareActive,
  prefs,
  resetPrefs,
  savePrefs,
} from './themeDef'

const { GlassPanel, InfoTip, KpiTile } = RC().ui

onMounted(() => initEyeCareRuntime())

const warmthPresets = [1, 2, 3, 4, 5] as const

const swatches = computed(() => [
  { key: 'page', label: t('swatches.page'), bg: activeSpec.value.page, fg: activeSpec.value.ink },
  { key: 'glass', label: t('swatches.glass'), bg: activeSpec.value.cardSolid, fg: activeSpec.value.ink },
  { key: 'ink', label: t('swatches.ink'), bg: activeSpec.value.ink, fg: activeSpec.value.paper },
  { key: 'accent', label: t('swatches.accent'), bg: 'var(--accent)', fg: '#fff' },
  { key: 'ok', label: t('swatches.ok'), bg: 'var(--ok)', fg: '#fff' },
  { key: 'warn', label: t('swatches.warn'), bg: 'var(--warn)', fg: '#fff' },
])

function switchTheme(next: string, e: MouseEvent) {
  const box = (e.currentTarget as HTMLElement).getBoundingClientRect()
  void RC().theme.set(next, { x: box.left + box.width / 2, y: box.top + box.height / 2 })
}

function updateWarmth(val: number | string | boolean | undefined) {
  const n = Number(val)
  if (!Number.isFinite(n)) return
  savePrefs({ ...prefs.value, warmth: Math.min(5, Math.max(1, n)) })
}

function updateStrength(val: number | number[]) {
  const n = Array.isArray(val) ? val[0] : val
  savePrefs({ ...prefs.value, strength: Math.min(80, Math.max(0, Number(n) || 0)) })
}

function updateSoftContrast(val: string | number | boolean) {
  savePrefs({ ...prefs.value, softContrast: val === true })
}

function updateOverlayEnabled(val: string | number | boolean) {
  savePrefs({ ...prefs.value, overlayEnabled: val === true })
}
</script>

<template>
  <div class="ec-page">
    <GlassPanel :title="t('title')">
      <template #extra>
        <span class="chip" :class="isEyeCareActive ? 'success live' : 'info'">
          {{ isEyeCareActive ? t('statusActive') : t('statusInactive') }}
        </span>
        <ElButton
          v-if="!isEyeCareActive"
          size="small"
          type="primary"
          @click="switchTheme(THEME_ID, $event)"
        >
          {{ t('activateBtn') }}
        </ElButton>
        <template v-else>
          <ElButton size="small" @click="switchTheme('light', $event)">
            {{ t('restoreBtn') }}
          </ElButton>
          <ElButton size="small" @click="switchTheme('dark', $event)">
            {{ t('switchDarkBtn') }}
          </ElButton>
        </template>
      </template>

      <div class="ec-kpis">
        <KpiTile
          :label="t('kpi.blueCut')"
          :value="estimatedBlueCut"
          :total="100"
          :sub="t('kpi.blueCutSub')"
          :tone="isEyeCareActive ? 'ok' : 'idle'"
        />
        <KpiTile
          :label="t('kpi.colorTemp')"
          :value="estimatedKelvin"
          :sub="t('kpi.colorTempSub')"
          tone="warn"
        />
        <KpiTile
          :label="t('kpi.contrast')"
          :value="prefs.softContrast ? 95 : 100"
          :total="100"
          :sub="`${prefs.softContrast ? t('kpi.contrastSoft') : t('kpi.contrastStd')} · ${t('kpi.contrastSub')}`"
          tone="accent"
        />
      </div>
    </GlassPanel>

    <GlassPanel :title="t('controlsTitle')" :tip="t('controlsTip')">
      <template #extra>
        <ElButton size="small" @click="resetPrefs">{{ t('resetBtn') }}</ElButton>
      </template>

      <div class="ec-controls">
        <div class="ec-row">
          <div class="ec-label">
            <span>{{ t('warmthLabel') }}</span>
            <InfoTip :content="t('warmthTip')" />
          </div>
          <ElRadioGroup :model-value="prefs.warmth" size="small" class="ec-warmth-group" @update:model-value="updateWarmth">
            <ElRadioButton v-for="w in warmthPresets" :key="w" :value="w">
              {{ t(`presets.w${w}`) }}
            </ElRadioButton>
          </ElRadioGroup>
        </div>

        <ElDivider class="ec-div" />

        <div class="ec-grid">
          <div class="ec-field">
            <div class="ec-field-head">
              <span class="ec-label">
                <span>{{ t('strengthLabel') }}</span>
                <InfoTip :content="t('strengthTip')" />
              </span>
              <span class="ec-val mono">{{ prefs.strength }}%</span>
            </div>
            <ElSlider
              :model-value="prefs.strength"
              :min="0"
              :max="80"
              :step="5"
              :disabled="!prefs.overlayEnabled"
              :show-tooltip="false"
              size="small"
              @update:model-value="updateStrength"
            />
          </div>

          <div class="ec-toggles">
            <div class="ec-toggle-item">
              <span class="ec-label">
                <span>{{ t('overlayEnabledLabel') }}</span>
                <InfoTip :content="t('overlayEnabledTip')" />
              </span>
              <ElSwitch :model-value="prefs.overlayEnabled" @update:model-value="updateOverlayEnabled" />
            </div>
            <div class="ec-toggle-item">
              <span class="ec-label">
                <span>{{ t('softContrastLabel') }}</span>
                <InfoTip :content="t('softContrastTip')" />
              </span>
              <ElSwitch :model-value="prefs.softContrast" @update:model-value="updateSoftContrast" />
            </div>
          </div>
        </div>

        <p class="ec-hint">{{ t('savedHint') }}</p>
      </div>
    </GlassPanel>

    <GlassPanel :title="t('previewTitle')">
      <p class="ec-note">{{ t('previewNote') }}</p>
      <div class="ec-swatches">
        <div v-for="sw in swatches" :key="sw.key" class="ec-swatch" :style="{ background: sw.bg, color: sw.fg }">
          <span class="ec-swatch-name">{{ sw.label }}</span>
          <span class="ec-swatch-val mono">{{ sw.bg }}</span>
        </div>
      </div>
      <div class="ec-chips">
        <span class="chip success">{{ t('sampleChipOk') }}</span>
        <span class="chip warning">{{ t('sampleChipWarn') }}</span>
        <span class="chip primary live">{{ t('sampleChipPrimary') }}</span>
      </div>
    </GlassPanel>
  </div>
</template>

<style scoped>
.ec-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.ec-kpis {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}
.ec-controls {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.ec-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px 16px;
}
.ec-warmth-group {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.ec-div {
  margin: 4px 0;
}
.ec-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px 24px;
  align-items: center;
}
.ec-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ec-field-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.ec-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--ink-2);
}
.ec-val {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.ec-toggles {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.ec-toggle-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.ec-hint {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--ink-3);
}
.ec-note {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink-2);
}
.ec-swatches {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
}
.ec-swatch {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid var(--hairline);
}
.ec-swatch-name {
  font-size: 12.5px;
  font-weight: 600;
}
.ec-swatch-val {
  font-size: 11px;
  opacity: 0.85;
  overflow-wrap: anywhere;
}
.ec-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
</style>
