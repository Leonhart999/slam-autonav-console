<!-- Liquid glass preview (ClipDeck): rows drift under a search capsule and a round button, both magnetic, so the
     refraction, frost and elasticity can be judged while the sliders move -->
<script setup lang="ts">
import LiquidGlass from '../glass/LiquidGlass.vue'
import Icon from '../Icon.vue'
import { t } from '../sdk'

const TONES = ['accent', 'success', 'warning', 'danger', 'accent']
const ROWS = [0, 1, 2, 3, 4]
</script>

<template>
  <p class="aia-preview-hint">{{ t('settings.previewHint') }}</p>
  <div class="aia-preview-stage" aria-hidden="true">
    <div class="aia-preview-track">
      <div v-for="copy in 2" :key="copy" class="aia-preview-rows">
        <div v-for="i in ROWS" :key="i" class="aia-preview-row">
          <span class="aia-preview-dot" :class="`is-${TONES[i]}`" />
          <span class="aia-preview-text">
            <strong>{{ t(`settings.previewRows.r${i}.title`) }}</strong>
            <span>{{ t(`settings.previewRows.r${i}.meta`) }}</span>
          </span>
        </div>
      </div>
    </div>
    <div class="aia-preview-glass aia-magnet-group">
      <LiquidGlass class="aia-preview-search" magnetic>
        <Icon name="search" /><span>{{ t('history.search') }}</span>
      </LiquidGlass>
      <LiquidGlass class="aia-preview-button" magnetic><Icon name="pin" filled /></LiquidGlass>
    </div>
  </div>
</template>
