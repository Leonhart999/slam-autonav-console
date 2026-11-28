<!-- Settings row with a glass slider and its value (ClipDeck's slider row) -->
<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  label: string
  description?: string
  value: number
  min: number
  max: number
  step?: number
  disabled?: boolean
  format?: (v: number) => string
}>(), { step: 1, disabled: false, description: '', format: (v: number) => String(v) })
const emit = defineEmits<{ change: [value: number] }>()

let seq = 0
const id = `aia-slider-${Math.random().toString(36).slice(2, 8)}-${++seq}`
const fill = computed(() => (props.max === props.min ? 0 : ((props.value - props.min) / (props.max - props.min)) * 100))
</script>

<template>
  <div class="aia-field-row aia-slider-row" :class="{ 'is-disabled': disabled }">
    <div class="aia-slider-head">
      <label class="aia-field-label" :for="id"><strong>{{ label }}</strong><span v-if="description">{{ description }}</span></label>
      <output class="aia-slider-value" :for="id">{{ format(value) }}</output>
    </div>
    <input :id="id" class="aia-gslider" type="range" :min="min" :max="max" :step="step" :value="value" :disabled="disabled"
           :style="{ '--fill': `${fill}%` }" @input="emit('change', Number(($event.target as HTMLInputElement).value))">
  </div>
</template>
