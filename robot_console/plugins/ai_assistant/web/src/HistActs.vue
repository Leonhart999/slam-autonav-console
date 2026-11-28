<!-- The actions of a history row (ClipDeck's card actions): in the glass skin they sit in a magnetic liquid capsule
     and a lens glides to the button under the pointer. Buttons in the slot carry data-lens. -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import LiquidGlass from './glass/LiquidGlass.vue'
import { useHoverLens } from './glass/lens'
import { isGlass } from './prefs'

const group = ref<HTMLElement | null>(null)
const lens = ref<InstanceType<typeof LiquidGlass> | null>(null)
const lensEl = computed(() => lens.value?.root ?? null)
useHoverLens(group, lensEl, isGlass)
</script>

<template>
  <span ref="group" class="aia-hist-acts aia-magnet-target" @click.stop>
    <LiquidGlass v-if="isGlass" class="aia-acts-liquid is-raised" magnetic />
    <slot />
    <LiquidGlass ref="lens" class="aia-button-lens" variant="lens" />
  </span>
</template>
